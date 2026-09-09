from __future__ import annotations

from typing import Any, Literal, TypedDict

RunOutcome = Literal["completed", "needs_clarification", "blocked", "failed"]
RunState = Literal["running", "waiting_for_user", "completed", "blocked", "failed"]


class ClarificationOption(TypedDict):
    label: str
    value: str


class Clarification(TypedDict, total=False):
    id: str
    question: str
    missing_slots: list[str]
    invalid_slots: list[str]
    options: list[ClarificationOption]


class ConversationContext(TypedDict, total=False):
    current_product_text: str
    current_product_id: str
    current_product_name: str
    current_period: str
    current_date_range: dict[str, str] | None
    last_intent: str
    last_effective_route: str
    last_report_run_id: str
    pending_clarification: Clarification | None
    partial_slots: dict[str, Any]


class ContextUsed(TypedDict, total=False):
    inherited_product: str | None
    inherited_period: str | None
    inherited_date_range: dict[str, str] | None
    recent_message_count: int


def empty_conversation_context() -> ConversationContext:
    return {
        "current_product_text": "",
        "current_product_id": "",
        "current_product_name": "",
        "current_period": "",
        "current_date_range": None,
        "last_intent": "",
        "last_effective_route": "",
        "last_report_run_id": "",
        "pending_clarification": None,
        "partial_slots": {},
    }
