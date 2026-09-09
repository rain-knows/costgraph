from __future__ import annotations

from typing import Any

from app.agent.capabilities import RoutingMode, normalize_capabilities
from app.domain.authorization import ExecutionPrincipal, get_server_principal
from app.repositories.conversation_repository import get_conversation_repository


def create_conversation(
    *,
    title: str = "新建成本 Agent 对话",
    routing_mode: RoutingMode = "auto",
    enabled_capabilities: list[str] | None = None,
    principal: ExecutionPrincipal | None = None,
) -> dict[str, Any]:
    owner = principal or get_server_principal()
    return get_conversation_repository().create_conversation(
        title=title,
        routing_mode=routing_mode,
        enabled_capabilities=normalize_capabilities(
            routing_mode, enabled_capabilities=enabled_capabilities
        ),
        principal=owner,
    )


def list_conversations(
    principal: ExecutionPrincipal | None = None,
) -> list[dict[str, Any]]:
    return get_conversation_repository().list_conversations(
        principal or get_server_principal()
    )


def update_conversation_title(
    conversation_id: str,
    *,
    title: str,
    principal: ExecutionPrincipal | None = None,
) -> dict[str, Any]:
    return get_conversation_repository().update_conversation_title(
        conversation_id,
        title,
        principal or get_server_principal(),
    )


def delete_conversation(
    conversation_id: str,
    principal: ExecutionPrincipal | None = None,
) -> None:
    get_conversation_repository().delete_conversation(
        conversation_id,
        principal or get_server_principal(),
    )


def get_conversation(
    conversation_id: str,
    principal: ExecutionPrincipal | None = None,
) -> dict[str, Any]:
    return get_conversation_repository().get_conversation(
        conversation_id, principal or get_server_principal()
    )


def list_conversation_messages(
    conversation_id: str,
    *,
    limit: int,
    before: str | None = None,
    principal: ExecutionPrincipal | None = None,
) -> dict[str, Any]:
    return get_conversation_repository().list_messages(
        conversation_id,
        limit=limit,
        before=before,
        principal=principal or get_server_principal(),
    )
