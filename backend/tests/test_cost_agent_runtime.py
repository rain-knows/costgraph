import app.agent.tools as agent_tools
from app.agent.graph import run_runtime, stream_runtime
from app.agent.providers import DeepSeekProviderAdapter
from app.agent.routes import cost_nodes
from app.agent.runtime_contracts import HarnessCallOutcome, RuntimeRunRequest
from app.agent.runtime_services import RuntimeServices
from app.services import llm_service
from app.services.llm_service import LLMUnavailableError
from evaluation.fixture_cost_repository import CostFixtureRepository
from evaluation.fixture_model_provider import (
    FixtureModelProvider,
    build_fixture_runtime_services,
)


def _request(question: str) -> RuntimeRunRequest:
    return RuntimeRunRequest(
        question=question,
        requested_capabilities=["system_help", "cost_calculation"],
    )


def test_runtime_calculates_finished_part_and_builds_v2_report(monkeypatch) -> None:
    monkeypatch.setattr(agent_tools, "cost_repository", CostFixtureRepository())
    result = run_runtime(
        _request("查询产品A 2026年6月单位成本，并说明成本构成"),
        runtime_services=build_fixture_runtime_services(),
    )

    assert result["outcome"] == "completed"
    # The Agent report is schema 2.0 and delegates all money arithmetic to the
    # deterministic cost service.  Keep this runtime assertion intentionally
    # focused on the public shape; exact golden amounts are covered by the API
    # tests.
    assert result["report_json"]["report_schema_version"] == "2.0"
    assert result["report_json"]["finished_batches"]
    assert "load_finished_batches" in [event["node"] for event in result["events"]]


def test_report_node_uses_registered_analysis_provider_signature(monkeypatch) -> None:
    class CapturingServices:
        model_args = None

        def ensure_run(self, _run_id: str) -> None:
            pass

        def invoke_model(self, operation: str, *, node: str, args: tuple):
            assert operation == "generate_cost_analysis"
            assert node == "build_report_json"
            self.model_args = args
            return HarnessCallOutcome(
                call_id="analysis-call",
                kind="model",
                name=operation,
                status="success",
                value={
                    "analysis_text": "契约回归测试。",
                    "llm_call": {"status": "success"},
                },
            )

        def invoke_tool(self, *_args, **_kwargs):
            return HarnessCallOutcome(
                call_id="report-call",
                kind="tool",
                name="build_report",
                status="error",
                error="stop after model contract assertion",
            )

    monkeypatch.setattr(cost_nodes, "build_lineage", lambda **_kwargs: {})
    monkeypatch.setattr(cost_nodes, "update_status_bar", lambda *_args: {})
    services = CapturingServices()
    part = {
        "part_id": "P-FG-001",
        "part_number": "FG-001",
        "part_description": "测试产成品",
        "part_type": "finished_good",
    }
    calculation_result = {"part": part}

    cost_nodes.build_report_json(
        {
            "run_id": "run-contract-test",
            "period": "2026-06",
            "part": part,
            "calculation_result": calculation_result,
            "execution_context": {},
            "status_bar": {"phase": "build_report_json"},
        },
        services,
    )

    assert services.model_args is not None
    assert len(services.model_args) == 4
    assert services.model_args[:3] == (part, "2026-06", calculation_result)
    assert services.model_args[3]["current_step"] == "build_report_json"


def test_missing_slots_clarify_before_cost_read(monkeypatch) -> None:
    fixture = CostFixtureRepository()
    calls = 0
    original = fixture.list_finished_batch_sources

    def tracked(*args, **kwargs):
        nonlocal calls
        calls += 1
        return original(*args, **kwargs)

    monkeypatch.setattr(fixture, "list_finished_batch_sources", tracked)
    monkeypatch.setattr(agent_tools, "cost_repository", fixture)
    result = run_runtime(
        _request("查一下成本"), runtime_services=build_fixture_runtime_services()
    )

    assert result["outcome"] == "needs_clarification"
    assert result["report_json"] is None
    assert calls == 0


def test_stream_runtime_finishes_with_canonical_result(monkeypatch) -> None:
    monkeypatch.setattr(agent_tools, "cost_repository", CostFixtureRepository())
    events = list(
        stream_runtime(
            _request("这个系统如何核算成本？"),
            runtime_services=build_fixture_runtime_services(),
        )
    )
    assert events[0]["type"] == "status"
    assert events[-1]["type"] == "result"


def test_model_unavailable_fails_without_deterministic_report_fallback(
    monkeypatch,
) -> None:
    class UnavailableProvider(FixtureModelProvider):
        def parse_cost_question(self, *_args, **_kwargs):
            raise LLMUnavailableError(
                "provider unavailable", {"error_code": "llm_unavailable"}
            )

    monkeypatch.setattr(agent_tools, "cost_repository", CostFixtureRepository())
    services: RuntimeServices = build_fixture_runtime_services()
    services.model_provider = UnavailableProvider()

    result = run_runtime(
        _request("查询产品A 2026年6月单位成本"), runtime_services=services
    )

    assert result["outcome"] == "failed"
    assert result["report_json"] is None
    assert "fallback" not in str(result).lower()


def test_analysis_model_unavailable_does_not_publish_report(monkeypatch) -> None:
    class UnavailableProvider(FixtureModelProvider):
        def generate_cost_analysis(self, *_args, **_kwargs):
            raise LLMUnavailableError(
                "provider unavailable", {"error_code": "llm_unavailable"}
            )

    monkeypatch.setattr(agent_tools, "cost_repository", CostFixtureRepository())
    services: RuntimeServices = build_fixture_runtime_services()
    services.model_provider = UnavailableProvider()

    result = run_runtime(
        _request("查询产品A 2026年6月单位成本"), runtime_services=services
    )

    assert result["outcome"] == "failed"
    assert result["report_json"] is None
    assert all(event["node"] != "final_answer" for event in result["events"])


def test_production_provider_without_key_fails_before_cost_read(monkeypatch) -> None:
    fixture = CostFixtureRepository()
    cost_reads = 0

    original_load = fixture.list_finished_batch_sources

    def tracked_load(*args, **kwargs):
        nonlocal cost_reads
        cost_reads += 1
        return original_load(*args, **kwargs)

    monkeypatch.setattr(fixture, "list_finished_batch_sources", tracked_load)
    monkeypatch.setattr(agent_tools, "cost_repository", fixture)
    monkeypatch.setattr(
        llm_service,
        "_api_key_state",
        lambda: {
            "configured": False,
            "valid_shape": False,
            "reason": "missing",
            "message": "未配置 DEEPSEEK_API_KEY。",
        },
    )

    services: RuntimeServices = build_fixture_runtime_services()
    services.model_provider = DeepSeekProviderAdapter()
    result = run_runtime(
        _request("查询产品A 2026年6月单位成本"), runtime_services=services
    )

    assert result["outcome"] == "failed"
    assert result["report_json"] is None
    assert cost_reads == 0
    assert all(event["node"] != "build_report_json" for event in result["events"])
