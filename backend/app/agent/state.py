from typing import Any, Literal, TypedDict

from app.agent.capabilities import CapabilityId, RouteId, RoutingMode
from app.domain.conversation import (
    Clarification,
    ContextUsed,
    ConversationContext,
    RunOutcome,
)

WorkflowMode = RouteId


class CostAgentState(TypedDict, total=False):
    run_id: str
    conversation_id: str
    turn_id: str
    message_id: str
    user_query: str
    routing_mode: RoutingMode
    requested_capabilities: list[CapabilityId]
    authorized_capabilities: list[CapabilityId]
    server_allowed_capabilities: list[CapabilityId]
    enabled_capabilities: list[CapabilityId]
    execution_context: dict[str, Any]
    runtime_context: dict[str, Any]
    runtime_metadata: dict[str, Any]
    workflow_mode: WorkflowMode
    requested_route: RouteId
    effective_route: RouteId
    route_reason: str
    status_plan: str
    outcome: RunOutcome
    readonly_answer: str
    intent: Literal["cost_query", "cost_breakdown", "variance_analysis", "unknown"]
    part_text: str
    part: dict[str, Any]
    period: str
    date_range: dict[str, str]
    explicit_part: bool
    explicit_period: bool
    explicit_date_range: bool
    resolved_slots: dict[str, Any]
    missing_slots: list[str]
    invalid_slots: list[str]
    clarification: Clarification
    conversation_context: ConversationContext
    context_used: ContextUsed
    recent_messages: list[dict[str, Any]]
    batch_sources: list[dict[str, Any]]
    calculation_result: dict[str, Any]
    previous_calculation_result: dict[str, Any]
    comparison_result: dict[str, Any]
    report_json: dict[str, Any]
    model_info: dict[str, Any]
    analysis_text: str
    ai_trace: dict[str, Any]
    audit_trace: dict[str, Any]
    lineage: dict[str, Any]
    status_bar: dict[str, Any]
    events: list[dict[str, Any]]
    errors: list[str]
    final_message: str
