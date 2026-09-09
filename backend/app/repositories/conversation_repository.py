from __future__ import annotations

from typing import Any, Protocol

from app.domain.authorization import ExecutionPrincipal


class ConversationRepository(Protocol):
    def create_conversation(
        self,
        *,
        title: str,
        routing_mode: str,
        enabled_capabilities: list[str],
        principal: ExecutionPrincipal,
    ) -> dict[str, Any]: ...

    def list_conversations(
        self, principal: ExecutionPrincipal
    ) -> list[dict[str, Any]]: ...

    def update_conversation_title(
        self,
        conversation_id: str,
        title: str,
        principal: ExecutionPrincipal,
    ) -> dict[str, Any]: ...

    def delete_conversation(
        self, conversation_id: str, principal: ExecutionPrincipal
    ) -> None: ...

    def get_conversation(
        self, conversation_id: str, principal: ExecutionPrincipal
    ) -> dict[str, Any]: ...

    def get_context(
        self, conversation_id: str, principal: ExecutionPrincipal
    ) -> dict[str, Any]: ...

    def list_messages(
        self,
        conversation_id: str,
        *,
        limit: int,
        before: str | None,
        principal: ExecutionPrincipal,
    ) -> dict[str, Any]: ...


def get_conversation_repository() -> ConversationRepository:
    from app.repositories.postgres_conversation_repository import (
        PostgresConversationRepository,
    )

    return PostgresConversationRepository()
