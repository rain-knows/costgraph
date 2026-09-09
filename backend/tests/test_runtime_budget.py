from time import sleep

import pytest

from app.agent.harness import AgentHarness, ToolRegistry
from app.agent.runtime_contracts import RuntimeBudgetLimits, ToolSpec
from app.agent.runtime_services import (
    PostgresBudgetStore,
    PostgresEventSink,
    RuntimeServiceError,
    RuntimeServices,
    require_call_value,
)
from app.domain.authorization import build_execution_context
from app.services.llm_service import LLMUnavailableError


class FixtureProvider:
    provider_id = "fixture"
    provider_version = "fixture-v1"
    model_id = "fixture-model"

    def classify_route(self) -> dict:
        return {
            "route": "system_help",
            "llm_call": {
                "provider": "fixture",
                "response": {
                    "usage": {
                        "prompt_tokens": 2,
                        "completion_tokens": 3,
                        "total_tokens": 5,
                    }
                },
            },
        }


def _services(
    *, model_calls: int = 3, tool_calls: int = 12, timeout: float = 1
) -> RuntimeServices:
    registry = ToolRegistry()
    registry.register(
        ToolSpec(tool_id="echo", required_capabilities=["system_help"]),
        lambda value, execution_context: value,
    )
    services = RuntimeServices(
        harness=AgentHarness(registry),
        model_provider=FixtureProvider(),
        limits=RuntimeBudgetLimits(
            max_model_calls=model_calls,
            max_tool_calls=tool_calls,
            tool_timeout_seconds=timeout,
            model_timeout_seconds=timeout,
            run_timeout_seconds=10,
        ),
    )
    services.start_run("run-test")
    return services


def test_tool_budget_is_reserved_before_execution() -> None:
    services = _services(tool_calls=1)
    context = build_execution_context("manual", ["system_help"])
    first = services.invoke_tool("echo", {"value": "ok"}, context, node="node")
    second = services.invoke_tool("echo", {"value": "no"}, context, node="node")
    assert first.value == "ok"
    assert second.error_code == "budget_exceeded"
    assert services.usage.tool_calls == 1


def test_model_budget_and_usage_are_recorded() -> None:
    services = _services(model_calls=1)
    first = services.invoke_model("classify_route", node="select_route")
    second = services.invoke_model("classify_route", node="select_route")
    assert first.status == "success"
    assert second.error_code == "budget_exceeded"
    assert services.usage.model_calls == 1
    assert services.usage.total_tokens == 5
    assert [event.status for event in services.event_sink.events] == [
        "running",
        "success",
        "denied",
    ]
    assert services.event_sink.events[1].details["execution_duration_ms"] >= 0


def test_postgres_start_event_is_not_emitted_twice_or_followed_by_usage_read() -> None:
    from app.agent.runtime_contracts import (
        RuntimeBudgetReservation,
        RuntimeBudgetUsage,
    )

    class FakeRepository:
        def __init__(self) -> None:
            self.events: list[dict] = []
            self.usage_reads = 0

        def reserve_runtime_call(self, run_id, kind, *, event):
            usage = RuntimeBudgetUsage(model_calls=1)
            self.events.append({"event_key": event["event_key"], "status": "running"})
            return RuntimeBudgetReservation(usage=usage, start_event_persisted=True)

        def add_runtime_tokens(self, run_id, prompt, completion, total):
            return RuntimeBudgetUsage(
                model_calls=1,
                prompt_tokens=prompt,
                completion_tokens=completion,
                total_tokens=total,
            )

        def get_runtime_usage(self, run_id):
            self.usage_reads += 1
            raise AssertionError("持久化 Runtime 不应逐调用读取 usage")

        def append_runtime_event(self, run_id, **event):
            self.events.append(event)

    repository = FakeRepository()
    services = RuntimeServices(
        harness=AgentHarness(ToolRegistry()),
        model_provider=FixtureProvider(),
        limits=RuntimeBudgetLimits(),
        event_sink=PostgresEventSink(repository),
        budget_store=PostgresBudgetStore(repository),
    )
    services.start_run("run-test", initial_usage=RuntimeBudgetUsage())

    outcome = services.invoke_model("classify_route", node="select_route")

    assert outcome.status == "success"
    assert repository.usage_reads == 0
    start_events = [item for item in repository.events if item["status"] == "running"]
    assert len(start_events) == 1
    assert len(repository.events) == 2


def test_tool_timeout_has_stable_error() -> None:
    services = _services(timeout=0.01)
    services.harness.registry.register(
        ToolSpec(
            tool_id="slow",
            required_capabilities=["system_help"],
            retry_policy="transient",
        ),
        lambda execution_context: sleep(0.05),
    )
    context = build_execution_context("manual", ["system_help"])
    outcome = services.invoke_tool("slow", {}, context, node="node")
    assert outcome.error_code == "tool_timeout"
    assert outcome.retryable is True


def test_deadline_and_cancel_are_checked_before_calls() -> None:
    services = _services()
    services.deadline = services.clock() - 1
    with pytest.raises(RuntimeServiceError) as timeout:
        services.invoke_model("classify_route", node="select_route")
    assert timeout.value.code == "run_timeout"

    services = _services()
    services.cancel_check = lambda: True
    with pytest.raises(RuntimeServiceError) as cancelled:
        services.invoke_model("classify_route", node="select_route")
    assert cancelled.value.code == "run_cancelled"


def test_unauthorized_tool_does_not_execute_function() -> None:
    executed = False

    def restricted(execution_context):
        nonlocal executed
        executed = True

    registry = ToolRegistry()
    registry.register(
        ToolSpec(tool_id="restricted", required_capabilities=["cost_calculation"]),
        restricted,
    )
    services = RuntimeServices(
        harness=AgentHarness(registry),
        model_provider=FixtureProvider(),
        limits=RuntimeBudgetLimits(),
    )
    services.start_run("run-test")
    context = build_execution_context("manual", ["system_help"])
    outcome = services.invoke_tool("restricted", {}, context, node="node")
    assert outcome.error_code == "tool_access_denied"
    assert executed is False
    assert services.usage.tool_calls == 0


def test_invalid_tool_input_does_not_consume_budget() -> None:
    services = _services(tool_calls=1)
    context = build_execution_context("manual", ["system_help"])
    outcome = services.invoke_tool("echo", {}, context, node="node")
    assert outcome.error_code == "tool_validation_error"
    assert services.usage.tool_calls == 0


def test_unknown_model_operation_does_not_consume_budget() -> None:
    services = _services(model_calls=1)
    outcome = services.invoke_model("delete_all", node="select_route")
    assert outcome.error_code == "runtime_internal_error"
    assert services.usage.model_calls == 0


def test_invalid_model_result_has_stable_contract_error() -> None:
    class InvalidProvider(FixtureProvider):
        def classify_route(self) -> dict:
            return {"unexpected": True}

    services = _services(model_calls=1)
    services.model_provider = InvalidProvider()
    outcome = services.invoke_model("classify_route", node="select_route")
    assert outcome.error_code == "model_unavailable"
    assert outcome.error == "模型结果不符合结构化契约。"
    assert services.usage.model_calls == 1


def test_model_retryability_preserves_provider_error_classification() -> None:
    class TransientProvider(FixtureProvider):
        def classify_route(self) -> dict:
            raise LLMUnavailableError("sensitive", {"error_code": "llm_http_503"})

    services = _services(model_calls=1)
    services.model_provider = TransientProvider()
    outcome = services.invoke_model("classify_route", node="select_route")
    assert outcome.error_code == "model_unavailable"
    assert outcome.retryable is True

    with pytest.raises(RuntimeServiceError) as raised:
        require_call_value(outcome)
    assert raised.value.retryable is True


def test_lease_loss_is_checked_before_calls() -> None:
    services = _services()
    services.lease_valid_check = lambda: False
    with pytest.raises(RuntimeServiceError) as raised:
        services.invoke_model("classify_route", node="select_route")
    assert raised.value.code == "repository_unavailable"
    assert raised.value.retryable is True
    assert services.usage.model_calls == 0
