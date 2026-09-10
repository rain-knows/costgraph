from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from sqlalchemy import case, func, or_, select, text, update
from sqlalchemy.orm import Session

from app.agent.capabilities import validate_capability_ids
from app.db.engine import get_session_factory
from app.db.models import (
    RuntimeAgentRun,
    RuntimeConversation,
    RuntimeMessage,
)
from app.domain.authorization import ExecutionPrincipal, get_server_principal
from app.domain.conversation import empty_conversation_context
from app.domain.errors import (
    ConversationBusyError,
    ConversationMessageCursorNotFoundError,
    ConversationNotFoundError,
)


def _now() -> datetime:
    return datetime.now(UTC)


def _resolve_principal(
    principal: ExecutionPrincipal | None,
) -> ExecutionPrincipal:
    return principal or get_server_principal()


class PostgresConversationRepository:
    """PostgreSQL-backed conversation and message repository."""

    def __init__(self, session_factory=None) -> None:
        self._session_factory = session_factory or get_session_factory()

    @contextmanager
    def _session(self) -> Iterator[Session]:
        session: Session = self._session_factory()
        try:
            yield session
        finally:
            session.close()

    def create_conversation(
        self,
        *,
        title: str = "新建成本 Agent 对话",
        routing_mode: str = "auto",
        enabled_capabilities: list[str] | None = None,
        principal: ExecutionPrincipal | None = None,
    ) -> dict[str, Any]:
        owner = _resolve_principal(principal)
        conversation_id = f"conv_{uuid4().hex}"
        created_at = _now()
        capabilities = validate_capability_ids(
            enabled_capabilities
            if enabled_capabilities is not None
            else ["system_help", "cost_calculation", "report_generation"]
        )
        with self._session() as session:
            session.add(
                RuntimeConversation(
                    conversation_id=conversation_id,
                    principal_id=owner.principal_id,
                    tenant_id=owner.tenant_id,
                    title=title,
                    routing_mode=routing_mode,
                    enabled_capabilities_json=capabilities,
                    context_json=empty_conversation_context(),
                    created_at=created_at,
                    updated_at=created_at,
                )
            )
            session.commit()
        return self.get_conversation(conversation_id, owner)

    def list_conversations(
        self, principal: ExecutionPrincipal | None = None
    ) -> list[dict[str, Any]]:
        owner = _resolve_principal(principal)
        with self._session() as session:
            message_count = (
                select(func.count(RuntimeMessage.message_id))
                .where(
                    RuntimeMessage.conversation_id
                    == RuntimeConversation.conversation_id
                )
                .correlate(RuntimeConversation)
                .scalar_subquery()
            )
            rows = session.execute(
                select(RuntimeConversation, message_count.label("message_count"))
                .where(
                    RuntimeConversation.principal_id == owner.principal_id,
                    RuntimeConversation.tenant_id == owner.tenant_id,
                )
                .order_by(RuntimeConversation.updated_at.desc())
            ).all()
            return [
                self._conversation_summary_from_row(conversation, count)
                for conversation, count in rows
            ]

    def update_conversation_title(
        self,
        conversation_id: str,
        title: str,
        principal: ExecutionPrincipal | None = None,
    ) -> dict[str, Any]:
        owner = _resolve_principal(principal)
        normalized_title = title.strip()
        if not normalized_title:
            raise ValueError("会话标题不能为空")
        with self._session() as session:
            result = session.execute(
                update(RuntimeConversation)
                .where(
                    RuntimeConversation.conversation_id == conversation_id,
                    RuntimeConversation.principal_id == owner.principal_id,
                    RuntimeConversation.tenant_id == owner.tenant_id,
                )
                .values(title=normalized_title, updated_at=_now())
            )
            if result.rowcount == 0:
                raise ConversationNotFoundError(conversation_id)
            session.commit()
        return self.get_conversation(conversation_id, owner)

    def delete_conversation(
        self,
        conversation_id: str,
        principal: ExecutionPrincipal | None = None,
    ) -> None:
        owner = _resolve_principal(principal)
        with self._session() as session:
            conversation = self._owned_conversation(
                session, conversation_id, owner, for_update=True
            )
            if conversation is None:
                raise ConversationNotFoundError(conversation_id)
            active_run = session.scalar(
                select(RuntimeAgentRun.run_id).where(
                    RuntimeAgentRun.conversation_id == conversation_id,
                    RuntimeAgentRun.status.in_(
                        ("queued", "running", "retry_wait", "finalizing")
                    ),
                )
            )
            if active_run is not None:
                raise ConversationBusyError(conversation_id)
            run_ids = session.scalars(
                select(RuntimeAgentRun.run_id).where(
                    RuntimeAgentRun.conversation_id == conversation_id
                )
            ).all()
            if run_ids:
                for table_name in (
                    "checkpoint_writes",
                    "checkpoint_blobs",
                    "checkpoints",
                ):
                    session.execute(
                        text(
                            f"DELETE FROM agent_checkpoint.{table_name} "
                            "WHERE thread_id = ANY(:run_ids)"
                        ),
                        {"run_ids": list(run_ids)},
                    )
            session.delete(conversation)
            session.commit()

    def get_conversation(
        self,
        conversation_id: str,
        principal: ExecutionPrincipal | None = None,
    ) -> dict[str, Any]:
        owner = _resolve_principal(principal)
        with self._session() as session:
            conversation = self._owned_conversation(session, conversation_id, owner)
            if conversation is None:
                raise ConversationNotFoundError(conversation_id)
            messages = session.scalars(
                select(RuntimeMessage)
                .where(RuntimeMessage.conversation_id == conversation_id)
                .order_by(RuntimeMessage.created_at, RuntimeMessage.message_id)
            ).all()
            return {
                "conversation_id": conversation.conversation_id,
                "title": conversation.title,
                "routing_mode": conversation.routing_mode,
                "enabled_capabilities": list(conversation.enabled_capabilities_json),
                "context": conversation.context_json,
                "messages": [self._message_from_row(item) for item in messages],
                "message_count": len(messages),
                "created_at": conversation.created_at.isoformat(),
                "updated_at": conversation.updated_at.isoformat(),
            }

    @staticmethod
    def _conversation_summary_from_row(
        row: RuntimeConversation, message_count: int
    ) -> dict[str, Any]:
        return {
            "conversation_id": row.conversation_id,
            "title": row.title,
            "routing_mode": row.routing_mode,
            "enabled_capabilities": list(row.enabled_capabilities_json),
            "message_count": int(message_count),
            "created_at": row.created_at.isoformat(),
            "updated_at": row.updated_at.isoformat(),
        }

    def get_context(
        self,
        conversation_id: str,
        principal: ExecutionPrincipal | None = None,
    ) -> dict[str, Any]:
        return self.get_conversation(conversation_id, principal)["context"]

    def list_messages(
        self,
        conversation_id: str,
        *,
        limit: int,
        before: str | None = None,
        principal: ExecutionPrincipal | None = None,
    ) -> dict[str, Any]:
        owner = _resolve_principal(principal)
        role_order = case((RuntimeMessage.role == "user", 0), else_=1)
        with self._session() as session:
            if self._owned_conversation(session, conversation_id, owner) is None:
                raise ConversationNotFoundError(conversation_id)

            filters = [RuntimeMessage.conversation_id == conversation_id]
            if before:
                cursor = session.scalar(
                    select(RuntimeMessage).where(
                        RuntimeMessage.message_id == before,
                        RuntimeMessage.conversation_id == conversation_id,
                    )
                )
                if cursor is None:
                    raise ConversationMessageCursorNotFoundError(
                        conversation_id, before
                    )
                cursor_role_order = 0 if cursor.role == "user" else 1
                filters.append(
                    or_(
                        RuntimeMessage.created_at < cursor.created_at,
                        (
                            (RuntimeMessage.created_at == cursor.created_at)
                            & (role_order < cursor_role_order)
                        ),
                        (
                            (RuntimeMessage.created_at == cursor.created_at)
                            & (role_order == cursor_role_order)
                            & (RuntimeMessage.message_id < cursor.message_id)
                        ),
                    )
                )

            run_json = RuntimeMessage.run_json
            rows = session.execute(
                select(
                    RuntimeMessage.message_id,
                    RuntimeMessage.turn_id,
                    RuntimeMessage.role,
                    RuntimeMessage.content,
                    RuntimeMessage.created_at,
                    run_json["run_id"].as_string().label("run_id"),
                    run_json["outcome"].as_string().label("outcome"),
                    run_json["clarification"].label("clarification"),
                    func.coalesce(func.jsonb_array_length(run_json["events"]), 0).label(
                        "event_count"
                    ),
                    run_json["context_used"]["inherited_part"]
                    .as_string()
                    .label("inherited_part"),
                    run_json["report_json"]["run_id"]
                    .as_string()
                    .is_not(None)
                    .label("has_report"),
                )
                .where(*filters)
                .order_by(
                    RuntimeMessage.created_at.desc(),
                    role_order.desc(),
                    RuntimeMessage.message_id.desc(),
                )
                .limit(limit + 1)
            ).all()
            has_more = len(rows) > limit
            page_rows = rows[:limit]
            items = [self._message_summary_from_projection(row) for row in page_rows]
            items.reverse()
            return {
                "items": items,
                "next_cursor": items[0]["message_id"] if has_more else None,
                "has_more": has_more,
            }

    @staticmethod
    def _owned_conversation(
        session: Session,
        conversation_id: str,
        owner: ExecutionPrincipal,
        *,
        for_update: bool = False,
    ) -> RuntimeConversation | None:
        statement = select(RuntimeConversation).where(
            RuntimeConversation.conversation_id == conversation_id,
            RuntimeConversation.principal_id == owner.principal_id,
            RuntimeConversation.tenant_id == owner.tenant_id,
        )
        if for_update:
            statement = statement.with_for_update()
        return session.scalar(statement)

    @staticmethod
    def _message_from_row(row: RuntimeMessage) -> dict[str, Any]:
        return {
            "message_id": row.message_id,
            "turn_id": row.turn_id,
            "role": row.role,
            "content": row.content,
            "run": row.run_json,
            "created_at": row.created_at.isoformat(),
        }

    @staticmethod
    def _message_summary_from_projection(row) -> dict[str, Any]:
        run = None
        if row.run_id:
            run = {
                "run_id": row.run_id,
                "outcome": row.outcome or "completed",
                "clarification": row.clarification,
                "event_count": int(row.event_count),
                "inherited_part": row.inherited_part,
                "has_report": bool(row.has_report),
            }
        return {
            "message_id": row.message_id,
            "turn_id": row.turn_id,
            "role": row.role,
            "content": row.content,
            "run": run,
            "created_at": row.created_at.isoformat(),
        }
