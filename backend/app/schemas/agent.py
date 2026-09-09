from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.agent.capabilities import CapabilityId, RoutingMode, validate_capability_ids
from app.domain.report import CostReportV1


class AgentEvent(BaseModel):
    node: str
    status: str
    summary: str
    started_at: str | None = None
    finished_at: str | None = None


class AgentRunResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: str
    conversation_id: str | None = None
    turn_id: str | None = None
    message_id: str | None = None
    final_message: str
    report_json: CostReportV1 | None = None
    events: list[AgentEvent]
    status_bar: dict[str, Any] | None = None
    outcome: Literal["completed", "needs_clarification", "blocked", "failed"] = (
        "completed"
    )
    clarification: dict[str, Any] | None = None
    context_used: dict[str, Any] = Field(default_factory=dict)


class ConversationCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(default="新建成本 Agent 对话", min_length=1, max_length=80)
    routing_mode: RoutingMode = "auto"
    enabled_capabilities: list[str] | None = None

    @model_validator(mode="after")
    def validate_enabled_capabilities(self) -> "ConversationCreateRequest":
        if self.enabled_capabilities is not None:
            validate_capability_ids(self.enabled_capabilities)
        return self


class ConversationUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    title: str = Field(min_length=1, max_length=80)


class ConversationMessage(BaseModel):
    message_id: str
    turn_id: str | None = None
    role: Literal["user", "assistant"]
    content: str
    run: AgentRunResponse | None = None
    created_at: str


class ConversationRunSummary(BaseModel):
    run_id: str
    outcome: Literal["completed", "needs_clarification", "blocked", "failed"]
    clarification: dict[str, Any] | None = None
    event_count: int = Field(ge=0)
    inherited_product: str | None = None
    has_report: bool


class ConversationMessageSummary(BaseModel):
    message_id: str
    turn_id: str | None = None
    role: Literal["user", "assistant"]
    content: str
    run: ConversationRunSummary | None = None
    created_at: str


class ConversationMessagesResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[ConversationMessageSummary]
    next_cursor: str | None = None
    has_more: bool


class ConversationSummaryResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    conversation_id: str
    title: str
    routing_mode: RoutingMode
    enabled_capabilities: list[CapabilityId]
    message_count: int = Field(default=0, ge=0)
    created_at: str
    updated_at: str


class ConversationResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    conversation_id: str
    title: str
    routing_mode: RoutingMode
    enabled_capabilities: list[CapabilityId]
    context: dict[str, Any]
    messages: list[ConversationMessage] = Field(default_factory=list)
    message_count: int = Field(default=0, ge=0)
    created_at: str
    updated_at: str
