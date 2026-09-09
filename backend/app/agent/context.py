"""Bounded runtime context assembly.

The context object is a control-plane summary.  Nodes still receive the
existing LangGraph state, while model prompts should only use this bounded
projection rather than the complete request or repository records.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.domain.authorization import ExecutionContext, execution_context_from_state
from app.domain.conversation import ConversationContext, empty_conversation_context

MAX_RECENT_MESSAGES = 6


class RuntimeContext(BaseModel):
    model_config = ConfigDict(extra="forbid")

    conversation_id: str | None = None
    principal_id: str
    tenant_id: str
    routing_mode: str
    effective_route: str = "pending"
    effective_capabilities: list[str] = Field(default_factory=list)
    conversation_slots: dict[str, Any] = Field(default_factory=dict)
    recent_messages: list[dict[str, Any]] = Field(default_factory=list)
    data_scope: str = "postgresql_cost_data"

    def public_summary(self) -> dict[str, Any]:
        return {
            "conversation_id": self.conversation_id,
            "principal_id": self.principal_id,
            "tenant_id": self.tenant_id,
            "routing_mode": self.routing_mode,
            "effective_route": self.effective_route,
            "effective_capabilities": list(self.effective_capabilities),
            "conversation_slots": {
                key: value
                for key, value in self.conversation_slots.items()
                if key
                in {
                    "current_part_text",
                    "current_part_id",
                    "current_part_number",
                    "current_period",
                    "current_date_range",
                    "last_intent",
                    "last_effective_route",
                    "pending_clarification",
                }
            },
            "recent_message_count": len(self.recent_messages),
            "data_scope": self.data_scope,
        }


def build_runtime_context(
    execution_context: ExecutionContext | dict[str, Any],
    *,
    conversation_id: str | None = None,
    routing_mode: str = "auto",
    conversation_context: ConversationContext | dict[str, Any] | None = None,
    recent_messages: list[dict[str, Any]] | None = None,
    effective_route: str = "pending",
) -> RuntimeContext:
    context = execution_context_from_state(execution_context)
    slots = {**empty_conversation_context(), **(conversation_context or {})}
    bounded_messages = list(recent_messages or [])[-MAX_RECENT_MESSAGES:]
    # Keep only fields needed for continuity.  In particular, do not carry
    # arbitrary prompt text or repository payloads into the runtime summary.
    safe_messages = [
        {
            key: item.get(key)
            for key in ("message_id", "role", "content", "created_at")
            if key in item
        }
        for item in bounded_messages
        if isinstance(item, dict)
    ]
    return RuntimeContext(
        conversation_id=conversation_id,
        principal_id=context.principal.principal_id,
        tenant_id=context.principal.tenant_id,
        routing_mode=routing_mode,
        effective_route=effective_route,
        effective_capabilities=list(context.effective_capabilities),
        conversation_slots=slots,
        recent_messages=safe_messages,
        data_scope=context.data_scope.source,
    )
