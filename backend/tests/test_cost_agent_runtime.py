import app.agent.tools as agent_tools
from app.agent.graph import run_runtime, stream_runtime
from app.agent.providers import DeepSeekProviderAdapter
from app.agent.runtime_contracts import RuntimeRunRequest
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


def test_runtime_calculates_product_a_and_builds_report(monkeypatch) -> None:
    monkeypatch.setattr(agent_tools, "cost_repository", CostFixtureRepository())
    result = run_runtime(
        _request("查询产品A 2026年6月单位成本，并说明成本构成"),
        runtime_services=build_fixture_runtime_services(),
    )

    assert result["outcome"] == "completed"
    assert result["report_json"]["summary_cards"][0]["value"] == 13.9
    assert result["report_json"]["summary_cards"][1]["value"] == 139000
    assert "load_cost_inputs" in [event["node"] for event in result["events"]]


def test_missing_slots_clarify_before_cost_read(monkeypatch) -> None:
    fixture = CostFixtureRepository()
    calls = 0
    original = fixture.load_cost_inputs

    def tracked(*args, **kwargs):
        nonlocal calls
        calls += 1
        return original(*args, **kwargs)

    monkeypatch.setattr(fixture, "load_cost_inputs", tracked)
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

    original_load = fixture.load_cost_inputs

    def tracked_load(*args, **kwargs):
        nonlocal cost_reads
        cost_reads += 1
        return original_load(*args, **kwargs)

    monkeypatch.setattr(fixture, "load_cost_inputs", tracked_load)
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
