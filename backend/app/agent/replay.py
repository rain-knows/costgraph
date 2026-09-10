"""Offline fixture replay for deterministic runtime and policy assertions."""

from __future__ import annotations

import json
import re
from collections.abc import Iterator
from copy import deepcopy
from hashlib import sha256
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.agent.harness import summarize_tool_arguments
from app.agent.runtime_contracts import (
    RUNTIME_VERSION,
    WORKFLOW_VERSION,
    RuntimeEvent,
    RuntimeRunRequest,
    RuntimeRunResult,
)


class ReplayToolCall(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tool_id: str
    arguments_summary: dict[str, Any] = Field(default_factory=dict)
    response: Any = None
    error: str | None = None


class ReplayFixture(BaseModel):
    model_config = ConfigDict(extra="forbid")

    fixture_version: str = "runtime-replay-1.0"
    runtime_version: str = RUNTIME_VERSION
    workflow_version: str = WORKFLOW_VERSION
    request: dict[str, Any]
    event_nodes: list[str] = Field(default_factory=list)
    capability_snapshot: dict[str, Any] = Field(default_factory=dict)
    policy_snapshot: dict[str, Any] = Field(default_factory=dict)
    model_responses: dict[str, Any] = Field(default_factory=dict)
    tool_calls: list[ReplayToolCall] = Field(default_factory=list)
    result: dict[str, Any]
    result_digest: str


def _result_model(result: RuntimeRunResult | dict[str, Any]) -> RuntimeRunResult:
    if isinstance(result, RuntimeRunResult):
        return result
    raw_events = [
        RuntimeEvent(
            event_type="node",
            node=item.get("node"),
            status=item.get("status", "success"),
            summary=item.get("summary", ""),
            started_at=item.get("started_at"),
            finished_at=item.get("finished_at"),
            error=item.get("error"),
        )
        for item in result.get("events", [])
    ]
    return RuntimeRunResult(
        run_id=result["run_id"],
        conversation_id=result.get("conversation_id"),
        turn_id=result.get("turn_id"),
        message_id=result.get("message_id"),
        final_message=result.get("final_message", ""),
        report_json=result.get("report_json"),
        events=raw_events,
        status_bar=result.get("status_bar"),
        outcome=result.get("outcome", "completed"),
        clarification=result.get("clarification"),
        context_summary=result.get("context_used", {}),
    )


def _digest(result: dict[str, Any]) -> str:
    payload = json.dumps(
        result, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str
    )
    return sha256(payload.encode("utf-8")).hexdigest()


def build_replay_fixture(
    result: RuntimeRunResult | dict[str, Any],
    *,
    request: RuntimeRunRequest | dict[str, Any],
    model_responses: dict[str, Any] | None = None,
    tool_calls: list[ReplayToolCall | dict[str, Any]] | None = None,
) -> ReplayFixture:
    runtime_result = _result_model(result)
    public = runtime_result.to_public_dict()
    request_payload = (
        request.model_dump(mode="json")
        if isinstance(request, RuntimeRunRequest)
        else deepcopy(request)
    )
    return ReplayFixture(
        fixture_version="runtime-replay-1.0",
        runtime_version=RUNTIME_VERSION,
        workflow_version=WORKFLOW_VERSION,
        request=request_payload,
        event_nodes=[
            event.node or event.tool_name or event.event_type
            for event in runtime_result.events
        ],
        capability_snapshot=_capability_snapshot(public),
        policy_snapshot=_policy_snapshot(public),
        model_responses=deepcopy(model_responses or {}),
        tool_calls=deepcopy(tool_calls or []),
        result=public,
        result_digest=_digest(public),
    )


def replay_fixture(fixture: ReplayFixture | dict[str, Any]) -> dict[str, Any]:
    """Replay a fixture by executing the graph with fixed dependencies."""

    parsed = (
        fixture
        if isinstance(fixture, ReplayFixture)
        else ReplayFixture.model_validate(fixture)
    )
    if parsed.fixture_version != "runtime-replay-1.0":
        raise ValueError("不支持的 Replay Fixture 版本。")
    return _replay_graph(parsed)


def _replay_graph(fixture: ReplayFixture) -> dict[str, Any]:
    from app.agent.graph import (
        _new_run_state_from_request,
        _result_from_state,
        create_cost_agent_graph,
    )
    from app.agent.harness import AgentHarness, ToolRegistry
    from app.agent.runtime_contracts import RuntimeBudgetLimits
    from app.agent.runtime_services import (
        MemoryBudgetStore,
        MemoryEventSink,
        RuntimeServices,
    )

    request = RuntimeRunRequest.model_validate(fixture.request)
    registry = ToolRegistry()
    tool_fixtures = _FixtureToolResponses(fixture.tool_calls)
    defaults = AgentHarness().registry
    for spec in defaults.specs():

        def fixture_tool(
            *_args: Any, _tool_id: str = spec.tool_id, **_kwargs: Any
        ) -> Any:
            return tool_fixtures.take(_tool_id, _kwargs)

        registry.register(spec, fixture_tool)

    provider = _FixtureProvider(fixture.model_responses)
    services = RuntimeServices(
        harness=AgentHarness(registry),
        model_provider=provider,
        limits=RuntimeBudgetLimits(),
        event_sink=MemoryEventSink(),
        budget_store=MemoryBudgetStore(),
    )
    graph = create_cost_agent_graph(runtime_services=services)
    state = _new_run_state_from_request(request)
    services.start_run(state["run_id"])
    result_state = graph.invoke(state)
    result = _result_from_state(result_state)
    tool_fixtures.assert_complete()
    if provider.unconsumed:
        raise AssertionError(f"Replay 存在未消费模型响应：{provider.unconsumed!r}")
    _assert_replay_result(fixture, result)
    result["replay"] = {
        "fixture_version": fixture.fixture_version,
        "runtime_version": fixture.runtime_version,
        "workflow_version": fixture.workflow_version,
        "live_model_called": provider.live_called,
        "live_repository_called": False,
        "unconsumed_model_responses": {},
        "unconsumed_tool_responses": [],
    }
    if provider.live_called:
        raise AssertionError("Replay 禁止访问真实模型。")
    return result


class _FixtureToolResponses:
    def __init__(self, calls: list[ReplayToolCall]) -> None:
        self.calls = [item.model_copy(deep=True) for item in calls]
        self.failures: list[str] = []

    def take(self, tool_id: str, arguments: dict[str, Any]) -> Any:
        if not self.calls:
            self.failures.append(f"出现未声明工具调用：{tool_id}")
            raise ValueError(f"Replay Fixture 未提供工具响应：{tool_id}")

        expected = self.calls.pop(0)
        actual_summary = summarize_tool_arguments(arguments)
        if expected.tool_id != tool_id:
            self.failures.append(
                f"工具调用顺序不一致：expected={expected.tool_id}, actual={tool_id}"
            )
        if expected.arguments_summary != actual_summary:
            self.failures.append(
                "工具参数摘要不一致："
                f"tool={tool_id}, expected={expected.arguments_summary!r}, "
                f"actual={actual_summary!r}"
            )
        if expected.error is not None:
            raise ValueError(expected.error)
        return deepcopy(expected.response)

    def assert_complete(self) -> None:
        if self.calls:
            remaining = [item.tool_id for item in self.calls]
            self.failures.append(f"存在未消费工具响应：{remaining!r}")
        if self.failures:
            raise AssertionError("；".join(self.failures))


class _FixtureProvider:
    provider_id = "fixture"
    provider_version = "fixture-provider-v1"
    model_id = "fixture-model"

    def __init__(self, responses: dict[str, Any]) -> None:
        self.responses = {
            key: list(value) if isinstance(value, list) else [value]
            for key, value in responses.items()
        }
        self.live_called = False

    @property
    def unconsumed(self) -> dict[str, int]:
        return {key: len(values) for key, values in self.responses.items() if values}

    def _take(self, operation: str, default_response: Any) -> Any:
        values = self.responses.get(operation)
        if values:
            value = deepcopy(values.pop(0))
        else:
            value = default_response
        if isinstance(value, dict) and "llm_call" not in value:
            value["llm_call"] = {"provider": "fixture", "response": {"usage": {}}}
        return value

    def classify_route(
        self, question: str, *_args: Any, **_kwargs: Any
    ) -> dict[str, Any]:
        route = (
            "report_generation"
            if any(word in question for word in ("报表", "报告", "展示型", "周期对比"))
            else (
                "cost_calculation"
                if any(word in question for word in ("成本", "核算", "产品"))
                else "system_help"
            )
        )
        return self._take(
            "classify_route",
            {
                "route": route,
                "reason": "fixture",
                "llm_call": {"provider": "fixture", "response": {"usage": {}}},
            },
        )

    def parse_cost_question(
        self, question: str, *_args: Any, **_kwargs: Any
    ) -> dict[str, Any]:
        return self._take("parse_cost_question", _replay_parse_cost_question(question))

    def generate_cost_analysis(self, *_args: Any, **_kwargs: Any) -> dict[str, Any]:
        return self._take(
            "generate_cost_analysis",
            {
                "analysis_text": "离线回放分析。",
                "llm_call": {"provider": "fixture", "response": {"usage": {}}},
            },
        )


def _replay_parse_cost_question(question: str) -> dict[str, Any]:
    """Build a deterministic response only when a Replay fixture omits one."""

    from app.services.llm_service import (
        extract_date_range_from_question,
        extract_latest_period_from_question,
        extract_periods_from_question,
        infer_report_style,
    )

    compact_query = question.replace(" ", "")
    part_match = re.search(
        r"(?:产品[A-Za-z一二三四五六七八九十]+|零件[A-Za-z一二三四五六七八九十]+|[A-Z]{1,4}-\d{3})",
        compact_query,
        re.IGNORECASE,
    )
    part_text = part_match.group(0) if part_match else ""
    if part_text == "产品A":
        part_text = "FG-001"
    date_range = extract_date_range_from_question(question)
    period = extract_latest_period_from_question(question)
    periods = extract_periods_from_question(question)
    report_style = infer_report_style(question)
    if date_range:
        period = date_range["end_date"][:7]
    if any(keyword in compact_query for keyword in ("对比", "变化", "环比", "原因")):
        intent = "variance_analysis"
    elif "构成" in compact_query or "最高" in compact_query:
        intent = "cost_breakdown"
    else:
        intent = "cost_query"
    return {
        "intent": intent,
        "report_style": report_style,
        "part_text": part_text,
        "period": period,
        "comparison_period": (
            periods[-2]
            if report_style == "period_comparison" and len(periods) >= 2
            else ""
        ),
        "date_range": date_range,
        "model": "fixture-model",
        "llm_call": {
            "node": "understand_question",
            "purpose": "Replay fixture deterministic response",
            "provider": "fixture",
            "model": "fixture-model",
            "status": "success",
            "response": {"usage": {}},
        },
    }


def _assert_replay_result(fixture: ReplayFixture, result: dict[str, Any]) -> None:
    actual_nodes = [event.get("node") for event in result.get("events", [])]
    if fixture.event_nodes and actual_nodes != fixture.event_nodes:
        raise ValueError(
            f"Replay 事件顺序不一致：expected={fixture.event_nodes!r}, actual={actual_nodes!r}"
        )
    expected = fixture.result
    if result.get("outcome") != expected.get("outcome"):
        raise ValueError("Replay outcome 不一致")
    if (
        fixture.capability_snapshot
        and _capability_snapshot(result) != fixture.capability_snapshot
    ):
        raise ValueError("Replay capability/policy 授权结果不一致")
    if fixture.policy_snapshot and _policy_snapshot(result) != fixture.policy_snapshot:
        raise ValueError("Replay policy 结果不一致")
    if _stable_report(result.get("report_json")) != _stable_report(
        expected.get("report_json")
    ):
        raise ValueError("Replay report_json 不一致")


def _capability_snapshot(result: dict[str, Any]) -> dict[str, Any]:
    authorization = (result.get("status_bar") or {}).get("authorization") or {}
    return {
        key: deepcopy(authorization.get(key, []))
        for key in (
            "requested_capabilities",
            "authorized_capabilities",
            "server_allowed_capabilities",
            "effective_capabilities",
        )
    }


def _policy_snapshot(result: dict[str, Any]) -> dict[str, Any]:
    status_bar = result.get("status_bar") or {}
    return {
        "effective_route": status_bar.get("effective_route"),
        "outcome": result.get("outcome"),
    }


def _stable_report(report: Any) -> Any:
    """Exclude event timestamps already verified by the event-order assertion."""

    if not isinstance(report, dict):
        return report
    stable = deepcopy(report)
    stable.pop("agent_steps", None)
    return stable


class ReplayRuntime:
    """Play a captured run as deterministic events without live dependencies."""

    def run(self, fixture: ReplayFixture | dict[str, Any]) -> dict[str, Any]:
        return replay_fixture(fixture)

    def stream(
        self, fixture: ReplayFixture | dict[str, Any]
    ) -> Iterator[dict[str, Any]]:
        result = replay_fixture(fixture)
        for event in result.get("events", []):
            yield {"type": "event", "event": deepcopy(event)}
        yield {"type": "result", "result": result}
