from __future__ import annotations

import time
from collections.abc import Iterator
from typing import Any

from app.domain.authorization import ExecutionPrincipal
from app.domain.errors import AgentDomainError
from app.repositories.run_repository import TERMINAL_RUN_STATUSES, PostgresRunRepository
from app.services.health_service import health_snapshot
from app.settings import get_settings


def _repository() -> PostgresRunRepository:
    return PostgresRunRepository(settings=get_settings())


def _ensure_runtime_ready() -> None:
    if not health_snapshot(get_settings())["runtime_ready"]:
        raise AgentDomainError(
            "runtime_unavailable",
            "Runtime 依赖的数据库、迁移或 Worker 尚未就绪。",
        )


def create_run(
    conversation_id: str,
    *,
    message_id: str,
    question: str,
    routing_mode: str | None,
    enabled_capabilities: list[str] | None,
    reply_to_clarification_id: str | None,
    principal: ExecutionPrincipal,
    request_id: str,
) -> dict[str, Any]:
    _ensure_runtime_ready()
    run = _repository().create_run(
        conversation_id=conversation_id,
        message_id=message_id,
        question=question,
        routing_mode=routing_mode,
        enabled_capabilities=enabled_capabilities,
        reply_to_clarification_id=reply_to_clarification_id,
        principal=principal,
        request_id=request_id,
    )
    return {
        **run,
        "events_url": f"/api/v2/conversations/{conversation_id}/runs/{run['run_id']}/events",
    }


def get_run(
    conversation_id: str, run_id: str, principal: ExecutionPrincipal
) -> dict[str, Any]:
    return _repository().get_run(conversation_id, run_id, principal)


def get_active_run(
    conversation_id: str, principal: ExecutionPrincipal
) -> dict[str, Any] | None:
    return _repository().get_active_run(conversation_id, principal)


def cancel_run(
    conversation_id: str, run_id: str, principal: ExecutionPrincipal
) -> dict[str, Any]:
    return _repository().cancel(conversation_id, run_id, principal)


def iter_run_events(
    conversation_id: str,
    run_id: str,
    principal: ExecutionPrincipal,
    *,
    after_event_id: int = 0,
) -> Iterator[dict[str, Any] | None]:
    repository = _repository()
    cursor = after_event_id
    last_heartbeat = time.monotonic()
    while True:
        for event in repository.list_events(
            conversation_id,
            run_id,
            principal,
            after_event_id=cursor,
        ):
            cursor = event["event_id"]
            yield event
            if _is_terminal_event(event):
                return
        run = repository.get_run(conversation_id, run_id, principal)
        if run["status"] in TERMINAL_RUN_STATUSES and cursor >= int(
            run.get("last_event_id") or 0
        ):
            return
        if time.monotonic() - last_heartbeat >= 15:
            yield None
            last_heartbeat = time.monotonic()
        time.sleep(0.5)


def _is_terminal_event(event: dict[str, Any]) -> bool:
    if event.get("type") in {"result", "error"}:
        return True
    payload = event.get("payload") or {}
    return event.get("type") == "status" and payload.get("status") == "cancelled"
