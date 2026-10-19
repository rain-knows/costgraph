from __future__ import annotations

from typing import Any

from app.agent.capabilities import RoutingMode, normalize_capabilities
from app.domain.authorization import ExecutionPrincipal
from app.repositories.conversation_repository import get_conversation_repository


def create_conversation(
    *,
    principal: ExecutionPrincipal,
    title: str = "新建成本 Agent 对话",
    routing_mode: RoutingMode = "auto",
    enabled_capabilities: list[str] | None = None,
) -> dict[str, Any]:
    return get_conversation_repository().create_conversation(
        title=title,
        routing_mode=routing_mode,
        enabled_capabilities=normalize_capabilities(
            routing_mode, enabled_capabilities=enabled_capabilities
        ),
        principal=principal,
    )


def list_conversations(
    principal: ExecutionPrincipal,
) -> list[dict[str, Any]]:
    return get_conversation_repository().list_conversations(principal)


def update_conversation_title(
    conversation_id: str,
    *,
    title: str,
    principal: ExecutionPrincipal,
) -> dict[str, Any]:
    return get_conversation_repository().update_conversation_title(
        conversation_id,
        title,
        principal,
    )


def delete_conversation(
    conversation_id: str,
    principal: ExecutionPrincipal,
) -> None:
    get_conversation_repository().delete_conversation(conversation_id, principal)


def get_conversation(
    conversation_id: str,
    principal: ExecutionPrincipal,
) -> dict[str, Any]:
    return get_conversation_repository().get_conversation(conversation_id, principal)


def list_conversation_messages(
    conversation_id: str,
    *,
    limit: int,
    principal: ExecutionPrincipal,
    before: str | None = None,
) -> dict[str, Any]:
    return get_conversation_repository().list_messages(
        conversation_id,
        limit=limit,
        before=before,
        principal=principal,
    )
