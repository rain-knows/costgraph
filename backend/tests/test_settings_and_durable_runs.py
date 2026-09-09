from __future__ import annotations

import json
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.agent.trace import build_audit_trace
from app.main import app
from app.repositories.run_repository import validate_run_transition
from app.services.llm_service import LLMUnavailableError
from app.settings import AppSettings, get_settings, reset_settings
from app.worker import classify_run_exception


def test_settings_defaults_and_database_contract() -> None:
    settings = AppSettings(
        _env_file=None,
        app_env="demo",
        cost_database_url="postgresql://user:pass@localhost:5432/example",
    )
    assert settings.app_env == "demo"
    assert settings.agent_run_timeout_seconds == 180
    assert settings.agent_run_lease_seconds == 90
    assert settings.agent_run_heartbeat_seconds == 15
    assert settings.agent_run_max_attempts == 3
    assert settings.agent_worker_concurrency == 1
    assert settings.agent_checkpoint_retention_hours == 24
    assert settings.audit_trace_mode == "metadata"

    assert settings.resolved_database_url == (
        "postgresql+psycopg://user:pass@localhost:5432/example"
    )
    assert settings.psycopg_database_url == (
        "postgresql://user:pass@localhost:5432/example"
    )
    assert "database_url" not in AppSettings.model_fields
    assert "agent_storage_backend" not in AppSettings.model_fields
    assert "cost_data_backend" not in AppSettings.model_fields
    assert "agent_execution_backend" not in AppSettings.model_fields


@pytest.mark.parametrize("app_env", ["staging", "production"])
def test_deployment_profiles_enforce_production_gates(app_env: str) -> None:
    with pytest.raises(ValidationError):
        AppSettings(_env_file=None, app_env=app_env)

    valid = AppSettings(
        _env_file=None,
        app_env=app_env,
        app_version="2026.08.14",
        cost_database_url="postgresql://user:pass@localhost/example",
        audit_trace_mode="metadata",
        cors_allowed_origins="https://cost.example.com",
    )
    assert valid.resolved_database_url.startswith("postgresql+psycopg://")


def test_settings_cache_can_be_reset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_VERSION", "one")
    reset_settings()
    assert get_settings().app_version == "one"
    monkeypatch.setenv("APP_VERSION", "two")
    assert get_settings().app_version == "one"
    reset_settings()
    assert get_settings().app_version == "two"


def test_empty_optional_scope_environment_values_use_defaults(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AGENT_ALLOWED_PERIOD_START", "")
    monkeypatch.setenv("AGENT_ALLOWED_PERIOD_END", "")

    settings = AppSettings(_env_file=None)

    assert settings.agent_allowed_period_start is None
    assert settings.agent_allowed_period_end is None


def test_run_state_machine_rejects_illegal_transitions() -> None:
    validate_run_transition("queued", "running")
    validate_run_transition("running", "retry_wait")
    validate_run_transition("running", "finalizing")
    validate_run_transition("finalizing", "succeeded")
    with pytest.raises(ValueError, match="非法 Run 状态转换"):
        validate_run_transition("succeeded", "running")
    with pytest.raises(ValueError, match="非法 Run 状态转换"):
        validate_run_transition("queued", "succeeded")


@pytest.mark.parametrize(
    ("error_code", "retryable"),
    [
        ("llm_transport_error", True),
        ("llm_http_429", True),
        ("llm_http_503", True),
        ("llm_http_400", False),
        ("llm_unavailable", False),
    ],
)
def test_worker_model_retry_classification(error_code: str, retryable: bool) -> None:
    error = LLMUnavailableError("sensitive provider detail", {"error_code": error_code})
    classified, public_code, public_message = classify_run_exception(error)
    assert classified is retryable
    assert public_code in {"model_unavailable", "run_invalid"}
    assert "sensitive provider detail" not in public_message

    assert classify_run_exception(TimeoutError())[0] is False
    assert classify_run_exception(ValueError())[0] is False


def test_audit_v3_contains_hashes_not_sensitive_content() -> None:
    canary = "AUDIT_CANARY_4f88"
    trace = {
        "provider": "test",
        "configured_model": "test-model",
        "calls": [
            {
                "node": "understand_question",
                "purpose": "test",
                "provider": "test",
                "model": "test-model",
                "status": "success",
                "request_sha256": "a" * 64,
                "response": {
                    "response_sha256": "b" * 64,
                    "finish_reason": "stop",
                    "usage": {"total_tokens": 3},
                },
            }
        ],
        "deterministic_calculation": {
            "input_sha256": "c" * 64,
            "result": {"safe": True},
        },
    }
    audit = build_audit_trace(trace)
    serialized = json.dumps(audit, ensure_ascii=False)
    assert audit["schema_version"] == "3.0"
    assert "runtime" in audit
    assert "tool_calls_sha256" in audit
    assert audit["calls"][0]["request_sha256"] == "a" * 64
    assert canary not in serialized
    assert "prompt" not in serialized.lower()


def test_health_and_error_contract_do_not_expose_secrets(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    canary = "SECRET_CANARY_74c1"
    monkeypatch.setenv("DEEPSEEK_API_KEY", canary)
    unavailable_snapshot = {
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
    }
    monkeypatch.setattr(
        "app.main.health_snapshot", lambda _settings: unavailable_snapshot
    )
    monkeypatch.setattr(
        "app.services.runtime_service.health_snapshot",
        lambda _settings: unavailable_snapshot,
    )
    reset_settings()
    client = TestClient(app, raise_server_exceptions=False)

    health = client.get("/api/health")
    assert health.status_code == 503
    assert health.json()["durable_runs"] is True
    assert health.json()["execution_backend"] == "postgres_worker"
    assert health.json()["runtime_api_versions"] == ["2.0"]
    assert health.json()["preferred_runtime_api"] == "2.0"
    assert health.json()["runtime_ready"] is False
    assert canary not in health.text

    request_id = str(uuid4())
    unavailable = client.post(
        "/api/v2/conversations/missing/runs",
        headers={"X-Request-ID": request_id},
        json={"message_id": "m1", "content": "test"},
    )
    assert unavailable.status_code == 503
    assert unavailable.headers["X-Request-ID"] == request_id
    detail = unavailable.json()["detail"]
    assert detail["code"] == "runtime_unavailable"
    assert detail["request_id"] == request_id
    assert canary not in unavailable.text

    invalid = client.get("/api/livez", headers={"X-Request-ID": "not-a-uuid"})
    assert invalid.status_code == 200
    assert invalid.headers["X-Request-ID"] != "not-a-uuid"
