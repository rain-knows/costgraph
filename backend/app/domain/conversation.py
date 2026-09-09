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
    current_part_text: str
    current_part_id: str
    current_part_number: str
    current_period: str
    current_date_range: dict[str, str] | None
    last_intent: str
    last_effective_route: str
    last_report_run_id: str
    pending_clarification: Clarification | None
    partial_slots: dict[str, Any]


class ContextUsed(TypedDict, total=False):
    inherited_part: str | None
    inherited_period: str | None
    inherited_date_range: dict[str, str] | None
    recent_message_count: int


def empty_conversation_context() -> ConversationContext:
    return {
        "current_part_text": "",
        "current_part_id": "",
        "current_part_number": "",
        "current_period": "",
        "current_date_range": None,
        "last_intent": "",
        "last_effective_route": "",
        "last_report_run_id": "",
        "pending_clarification": None,
        "partial_slots": {},
    }
