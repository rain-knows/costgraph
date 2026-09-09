import json

import httpx
import pytest

from app.services import llm_service


def _install_transport(monkeypatch, handler) -> None:
    real_client = httpx.Client
    transport = httpx.MockTransport(handler)
    monkeypatch.setattr(
        llm_service.httpx,
        "Client",
        lambda **kwargs: real_client(transport=transport, **kwargs),
    )


@pytest.mark.parametrize("api_key", ["", "sk-your-deepseek-api-key"])
def test_model_test_rejects_invalid_configuration_without_request(
    monkeypatch, api_key: str
) -> None:
    settings = llm_service.get_settings().model_copy(
        update={"deepseek_api_key": api_key}
    )
    monkeypatch.setattr(llm_service, "get_settings", lambda: settings)

    def unexpected_client(**kwargs):
        raise AssertionError("invalid configuration must not issue an HTTP request")

    monkeypatch.setattr(llm_service.httpx, "Client", unexpected_client)

    result = llm_service.test_model_connection()

    assert result["ok"] is False
    assert result["stage"] == "configuration"


def test_model_test_reports_authentication_failure(monkeypatch) -> None:
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test-secret")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, request=request)

    _install_transport(monkeypatch, handler)

    result = llm_service.test_model_connection()

    assert result["ok"] is False
    assert result["stage"] == "models"
    assert result["message"] == "DeepSeek API Key 无效或无权限。"


def test_model_test_reports_network_timeout(monkeypatch) -> None:
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test-secret")

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timed out", request=request)

    _install_transport(monkeypatch, handler)

    result = llm_service.test_model_connection()

    assert result["ok"] is False
    assert result["stage"] == "models"
    assert "超时" in result["message"]


def test_model_test_reports_unavailable_configured_model(monkeypatch) -> None:
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test-secret")
    monkeypatch.setattr(llm_service, "DEEPSEEK_MODEL", "deepseek-v4-flesh")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"data": [{"id": "deepseek-v4-flash"}, {"id": "deepseek-v4-pro"}]},
            request=request,
        )

    _install_transport(monkeypatch, handler)

    result = llm_service.test_model_connection()

    assert result["ok"] is False
    assert result["stage"] == "models"
    assert "deepseek-v4-flesh" in result["message"]
    assert "DEEPSEEK_MODEL" in result["message"]


def test_model_test_reports_completion_failure(monkeypatch) -> None:
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test-secret")
    monkeypatch.setattr(llm_service, "DEEPSEEK_MODEL", "deepseek-v4-flash")

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/models"):
            return httpx.Response(
                200,
                json={"data": [{"id": "deepseek-v4-flash"}]},
                request=request,
            )
        return httpx.Response(400, request=request)

    _install_transport(monkeypatch, handler)

    result = llm_service.test_model_connection()

    assert result["ok"] is False
    assert result["stage"] == "completion"
    assert "HTTP 400" in result["message"]


def test_model_test_runs_minimal_completion_and_redacts_result(
    monkeypatch,
) -> None:
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test-secret")
    monkeypatch.setattr(llm_service, "DEEPSEEK_MODEL", "deepseek-v4-flash")
    completion_payload = None

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal completion_payload
        if request.url.path.endswith("/models"):
            return httpx.Response(
                200,
                json={"data": [{"id": "deepseek-v4-flash"}]},
                request=request,
            )
        completion_payload = json.loads(request.content)
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": "OK"}}]},
            request=request,
        )

    _install_transport(monkeypatch, handler)

    body = llm_service.test_model_connection()

    assert body["ok"] is True
    assert body["stage"] == "completion"
    assert body["model"] == "deepseek-v4-flash"
    assert body["duration_ms"] >= 0
    assert completion_payload["max_tokens"] == 8
    serialized = json.dumps(body, ensure_ascii=False, default=str)
    assert "sk-test-secret" not in serialized
    assert "Authorization" not in serialized
    assert "Reply with exactly OK" not in serialized
