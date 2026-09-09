from __future__ import annotations

from typing import Any

from fastapi import HTTPException

from app.domain.errors import AgentDomainError


def domain_status_code(code: str) -> int:
    if code in {
        "conversation_not_found",
        "conversation_message_cursor_not_found",
        "conversation_turn_not_found",
        "artifact_not_found",
        "run_not_found",
        "cost_data_not_found",
    }:
        return 404
    if code in {
        "artifact_persistence_failed",
        "runtime_unavailable",
        "database_unavailable",
    }:
        return 503
    return 409


def domain_http_error(exc: AgentDomainError) -> HTTPException:
    return HTTPException(
        status_code=domain_status_code(exc.code),
        detail={"code": exc.code, "message": exc.message, "details": exc.details},
    )


def public_error(exc: AgentDomainError, request_id: str) -> dict[str, Any]:
    return {
        "code": exc.code,
        "message": exc.message,
        "request_id": request_id,
        "details": exc.details,
    }
