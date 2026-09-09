from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Annotated

from fastapi import APIRouter, Depends, Header, Query, Request
from fastapi.responses import StreamingResponse

from app.api.errors import domain_http_error, public_error
from app.domain.authorization import ExecutionPrincipal, get_server_principal
from app.domain.errors import AgentDomainError
from app.schemas.runtime import RuntimeRunCreateRequest, RuntimeRunResponse
from app.services.runtime_service import (
    cancel_run,
    create_run,
    get_active_run,
    get_run,
    iter_run_events,
)

router = APIRouter()
PrincipalDependency = Annotated[ExecutionPrincipal, Depends(get_server_principal)]


@router.post(
    "/{conversation_id}/runs", response_model=RuntimeRunResponse, status_code=202
)
def create_run_endpoint(
    conversation_id: str,
    payload: RuntimeRunCreateRequest,
    request: Request,
    principal: PrincipalDependency,
) -> RuntimeRunResponse:
    try:
        return RuntimeRunResponse(
            **create_run(
                conversation_id,
                message_id=payload.message_id,
                question=payload.content,
                routing_mode=payload.routing_mode,
                enabled_capabilities=payload.enabled_capabilities,
                reply_to_clarification_id=payload.reply_to_clarification_id,
                principal=principal,
                request_id=request.state.request_id,
            )
        )
    except AgentDomainError as exc:
        raise domain_http_error(exc) from exc


@router.get("/{conversation_id}/runs/active", response_model=RuntimeRunResponse | None)
def get_active_run_endpoint(
    conversation_id: str, principal: PrincipalDependency
) -> RuntimeRunResponse | None:
    try:
        run = get_active_run(conversation_id, principal)
        return RuntimeRunResponse(**run) if run else None
    except AgentDomainError as exc:
        raise domain_http_error(exc) from exc


@router.get("/{conversation_id}/runs/{run_id}", response_model=RuntimeRunResponse)
def get_run_endpoint(
    conversation_id: str, run_id: str, principal: PrincipalDependency
) -> RuntimeRunResponse:
    try:
        return RuntimeRunResponse(**get_run(conversation_id, run_id, principal))
    except AgentDomainError as exc:
        raise domain_http_error(exc) from exc


@router.post(
    "/{conversation_id}/runs/{run_id}/cancel", response_model=RuntimeRunResponse
)
def cancel_run_endpoint(
    conversation_id: str, run_id: str, principal: PrincipalDependency
) -> RuntimeRunResponse:
    try:
        return RuntimeRunResponse(**cancel_run(conversation_id, run_id, principal))
    except AgentDomainError as exc:
        raise domain_http_error(exc) from exc


@router.get("/{conversation_id}/runs/{run_id}/events")
def run_events_endpoint(
    conversation_id: str,
    run_id: str,
    request: Request,
    principal: PrincipalDependency,
    after_event_id: int = Query(default=0, ge=0),
    last_event_id: str | None = Header(default=None, alias="Last-Event-ID"),
) -> StreamingResponse:
    try:
        get_run(conversation_id, run_id, principal)
    except AgentDomainError as exc:
        raise domain_http_error(exc) from exc
    cursor = after_event_id
    if last_event_id:
        try:
            cursor = max(cursor, int(last_event_id))
        except ValueError:
            pass

    def event_stream() -> Iterator[str]:
        try:
            for event in iter_run_events(
                conversation_id, run_id, principal, after_event_id=cursor
            ):
                if event is None:
                    yield ": heartbeat\n\n"
                    continue
                yield (
                    f"id: {event['event_id']}\n"
                    f"event: {event['type']}\n"
                    f"data: {json.dumps(event['payload'], ensure_ascii=False)}\n\n"
                )
        except AgentDomainError as exc:
            body = public_error(exc, request.state.request_id)
            yield f"event: error\ndata: {json.dumps(body, ensure_ascii=False)}\n\n"
        except Exception:  # noqa: BLE001 - stable public boundary
            body = {
                "code": "internal_error",
                "message": "服务暂时不可用，请稍后重试。",
                "request_id": request.state.request_id,
                "details": {"run_id": run_id},
            }
            yield f"event: error\ndata: {json.dumps(body, ensure_ascii=False)}\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
