from fastapi.testclient import TestClient

from app.domain.authorization import ExecutionPrincipal
from app.main import app
from app.repositories.run_repository import _semantic_event
from app.services import runtime_service


def test_runtime_creation_is_unavailable_when_runtime_is_not_ready(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.services.runtime_service.health_snapshot",
        lambda _settings: {"runtime_ready": False},
    )
    client = TestClient(app)
    response = client.post(
        "/api/v2/conversations/conversation-1/runs",
        json={"message_id": "message-1", "content": "核算产品A"},
    )
    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "runtime_unavailable"


def test_runtime_create_uses_shared_request_and_returns_versioned_response(
    monkeypatch,
) -> None:
    captured = {}

    def fake_create(conversation_id, **kwargs):
        captured.update({"conversation_id": conversation_id, **kwargs})
        return _run_payload()

    monkeypatch.setattr("app.api.runtime_runs.create_run", fake_create)
    client = TestClient(app)
    response = client.post(
        "/api/v2/conversations/conversation-1/runs",
        json={
            "message_id": "message-1",
            "content": "核算产品A 2026年6月",
            "enabled_capabilities": ["cost_calculation"],
        },
    )
    assert response.status_code == 202
    body = response.json()
    assert body["schema_version"] == "2.0"
    assert body["trace_id"] == body["run_id"]
    assert body["budget"]["limits"]["max_model_calls"] == 3
    assert captured["question"] == "核算产品A 2026年6月"


def test_runtime_event_stream_uses_semantic_event_type(monkeypatch) -> None:
    monkeypatch.setattr("app.api.runtime_runs.get_run", lambda *args: _run_payload())
    monkeypatch.setattr(
        "app.api.runtime_runs.iter_run_events",
        lambda *args, **kwargs: iter(
            [
                {
                    "event_id": 7,
                    "type": "tool",
                    "payload": {
                        "schema_version": "2.0",
                        "sequence": 3,
                        "kind": "tool",
                        "name": "load_cost_inputs",
                        "status": "success",
                        "summary": "工具执行完成。",
                    },
                }
            ]
        ),
    )
    client = TestClient(app)
    response = client.get("/api/v2/conversations/conversation-1/runs/run-1/events")
    assert response.status_code == 200
    assert "id: 7" in response.text
    assert "event: tool" in response.text
    assert "load_cost_inputs" in response.text


def test_health_advertises_v2_and_reports_database_unavailable(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.main.health_snapshot",
        lambda _settings: {
            "status": "degraded",
            "service": "costgraph",
            "app_env": "test",
            "app_version": "test",
            "execution_backend": "postgres_worker",
            "durable_runs": True,
            "database": "unreachable",
            "worker": "unavailable",
            "runtime_api_versions": ["2.0"],
            "preferred_runtime_api": "2.0",
            "runtime_ready": False,
        },
    )
    response = TestClient(app).get("/api/health")
    assert response.status_code == 503
    assert response.json()["runtime_api_versions"] == ["2.0"]
    assert response.json()["preferred_runtime_api"] == "2.0"
    assert response.json()["execution_backend"] == "postgres_worker"
    assert response.json()["durable_runs"] is True
    assert response.json()["database"] == "unreachable"
    assert response.json()["runtime_ready"] is False


def test_runtime_service_does_not_accept_caller_selected_api_version(
    monkeypatch,
) -> None:
    calls: list[str] = []

    class FakeRepository:
        def get_active_run(self, *_args):
            calls.append("active")

        def get_run(self, *_args):
            calls.append("get")
            return _run_payload()

        def cancel(self, *_args):
            calls.append("cancel")
            return _run_payload()

    monkeypatch.setattr(
        "app.services.runtime_service._repository", lambda: FakeRepository()
    )
    from app.services import runtime_service

    principal = ExecutionPrincipal(
        principal_id="owner", tenant_id="tenant", roles=("cost_analyst",)
    )
    assert runtime_service.get_active_run("conversation-1", principal) is None
    assert (
        runtime_service.get_run("conversation-1", "run-1", principal)["api_version"]
        == "2.0"
    )
    assert (
        runtime_service.cancel_run("conversation-1", "run-1", principal)["api_version"]
        == "2.0"
    )
    assert calls == ["active", "get", "cancel"]


def test_runtime_result_and_error_events_keep_public_contract_fields() -> None:
    result = _semantic_event("result", {"result": {"run_id": "run-1"}})
    assert result["payload"]["result"] == {"run_id": "run-1"}

    error = _semantic_event(
        "error",
        {
            "code": "budget_exceeded",
            "message": "调用预算已用尽。",
            "request_id": "request-1",
        },
    )
    assert error["summary"] == "调用预算已用尽。"
    assert error["payload"] == {
        "error_code": "budget_exceeded",
        "request_id": "request-1",
    }


def test_terminal_sse_event_does_not_query_run_again(monkeypatch) -> None:
    class FakeRepository:
        def __init__(self) -> None:
            self.run_reads = 0

        def list_events(self, *_args, **_kwargs):
            return [
                {
                    "event_id": 7,
                    "type": "result",
                    "payload": {"schema_version": "2.0", "result": {}},
                }
            ]

        def get_run(self, *_args):
            self.run_reads += 1
            raise AssertionError("终态事件后不应再次读取 Run")

    repository = FakeRepository()
    monkeypatch.setattr(runtime_service, "_repository", lambda: repository)
    principal = ExecutionPrincipal(
        principal_id="owner", tenant_id="tenant", roles=("cost_analyst",)
    )

    events = list(
        runtime_service.iter_run_events(
            "conversation-1", "run-1", principal, after_event_id=0
        )
    )

    assert [event["event_id"] for event in events] == [7]
    assert repository.run_reads == 0


def _run_payload() -> dict:
    return {
        "schema_version": "2.0",
        "api_version": "2.0",
        "runtime_version": "runtime-harness-v2",
        "workflow_version": "cost-agent-graph-v2",
        "trace_id": "run-1",
        "run_id": "run-1",
        "conversation_id": "conversation-1",
        "turn_id": "turn-1",
        "message_id": "message-1",
        "status": "queued",
        "attempt_count": 0,
        "created_at": "2026-08-20T00:00:00+00:00",
        "updated_at": "2026-08-20T00:00:00+00:00",
        "request_id": "request-1",
        "budget": {
            "limits": {
                "max_model_calls": 3,
                "max_tool_calls": 12,
                "tool_timeout_seconds": 15,
                "run_timeout_seconds": 180,
            },
            "usage": {
                "model_calls": 0,
                "tool_calls": 0,
                "prompt_tokens": 0,
                "completion_tokens": 0,
                "total_tokens": 0,
            },
        },
        "events_url": "/api/v2/conversations/conversation-1/runs/run-1/events",
    }
