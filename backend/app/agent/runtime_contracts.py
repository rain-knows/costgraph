"""Internal contracts shared by the Agent runtime and harness.

These models are intentionally separate from ``schemas.agent``.  They describe
the internal execution boundary and are not a client authorization contract.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

from app.agent.capabilities import (
    CapabilityId,
    RoutingMode,
    validate_capability_ids,
)
from app.domain.authorization import ExecutionPrincipal
from app.domain.conversation import RunOutcome

RUNTIME_VERSION = "runtime-harness-v2"
WORKFLOW_VERSION = "cost-agent-graph-v2"
RUNTIME_API_VERSION = "2.0"

RuntimeEventType = Literal[
    "status",
    "node",
    "tool",
    "policy",
    "model",
    "budget",
    "clarification",
    "result",
    "error",
]
RuntimeEventStatus = Literal[
    "pending",
    "running",
    "success",
    "waiting",
    "error",
    "blocked",
    "denied",
    "cancelled",
]
ToolCallStatus = Literal["pending", "running", "success", "error", "denied"]
ToolErrorCategory = Literal[
    "authorization",
    "validation",
    "not_found",
    "timeout",
    "repository",
    "internal",
]
RetryPolicy = Literal["never", "transient"]
RuntimeErrorCode = Literal[
    "budget_exceeded",
    "tool_timeout",
    "tool_access_denied",
    "tool_validation_error",
    "repository_unavailable",
    "model_unavailable",
    "run_timeout",
    "runtime_internal_error",
    "run_cancelled",
]


class RuntimeBudgetLimits(BaseModel):
    model_config = ConfigDict(extra="forbid")

    max_model_calls: int = Field(default=3, ge=0)
    max_tool_calls: int = Field(default=12, ge=0)
    model_timeout_seconds: float = Field(default=30, gt=0)
    tool_timeout_seconds: float = Field(default=15, gt=0)
    run_timeout_seconds: float = Field(default=180, gt=0)


class RuntimeBudgetUsage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    model_calls: int = Field(default=0, ge=0)
    tool_calls: int = Field(default=0, ge=0)
    prompt_tokens: int = Field(default=0, ge=0)
    completion_tokens: int = Field(default=0, ge=0)
    total_tokens: int = Field(default=0, ge=0)


class RuntimeBudgetReservation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    usage: RuntimeBudgetUsage
    start_event_persisted: bool = False


class ModelCall(BaseModel):
    model_config = ConfigDict(extra="forbid")

    call_id: str
    operation: Literal[
        "classify_route", "parse_cost_question", "generate_cost_analysis"
    ]
    node: str
    provider: str | None = None
    model: str | None = None
    provider_version: str | None = None
    timeout_seconds: float | None = Field(default=None, gt=0)
    status: ToolCallStatus = "pending"
    started_at: str | datetime | None = None
    finished_at: str | datetime | None = None
    trace_ref: str | None = None


class ModelResult(BaseModel):
    model_config = ConfigDict(extra="forbid", arbitrary_types_allowed=True)

    call_id: str
    operation: str
    provider: str | None = None
    model: str | None = None
    provider_version: str | None = None
    status: Literal["success", "error", "denied"]
    value: Any = Field(default=None, exclude=True)
    result_summary: dict[str, Any] = Field(default_factory=dict)
    error: str | None = None
    error_code: RuntimeErrorCode | None = None
    retryable: bool = False
    started_at: str | datetime | None = None
    finished_at: str | datetime | None = None


class HarnessCallOutcome(BaseModel):
    model_config = ConfigDict(extra="forbid", arbitrary_types_allowed=True)

    call_id: str
    kind: Literal["model", "tool"]
    name: str
    status: Literal["success", "error", "denied"]
    value: Any = Field(default=None, exclude=True)
    summary: dict[str, Any] = Field(default_factory=dict)
    error: str | None = None
    error_code: RuntimeErrorCode | None = None
    retryable: bool = False
    budget: RuntimeBudgetUsage = Field(default_factory=RuntimeBudgetUsage)
    started_at: str | datetime | None = None
    finished_at: str | datetime | None = None
    execution_duration_ms: float | None = Field(default=None, ge=0)


class RuntimeRunRequest(BaseModel):
    """Normalized request passed to the internal runtime boundary."""

    model_config = ConfigDict(extra="forbid")

    question: str = Field(min_length=1)
    run_id: str | None = None
    conversation_id: str | None = None
    turn_id: str | None = None
    message_id: str | None = None
    principal: ExecutionPrincipal | None = None
    routing_mode: RoutingMode = "auto"
    requested_capabilities: list[str] | None = None
    conversation_context: dict[str, Any] = Field(default_factory=dict)
    recent_messages: list[dict[str, Any]] = Field(default_factory=list)
    include_internal: bool = False

    @field_validator("requested_capabilities")
    @classmethod
    def validate_capabilities(cls, value: list[str] | None) -> list[str] | None:
        if value is not None:
            validate_capability_ids(value)
        return value


class RuntimeEvent(BaseModel):
    """A normalized internal event; public SSE events remain unchanged."""

    model_config = ConfigDict(extra="forbid")

    schema_version: str = "2.0"
    event_key: str | None = None
    sequence: int | None = Field(default=None, ge=1)
    event_type: RuntimeEventType = "node"
    name: str | None = None
    node: str | None = None
    tool_name: str | None = None
    status: RuntimeEventStatus
    summary: str = ""
    started_at: str | datetime | None = None
    finished_at: str | datetime | None = None
    error: str | None = None
    error_category: ToolErrorCategory | None = None
    error_code: RuntimeErrorCode | None = None
    budget: RuntimeBudgetUsage | None = None
    details: dict[str, Any] = Field(default_factory=dict)
    trace_ref: str | None = None

    @model_validator(mode="after")
    def validate_event_target(self) -> RuntimeEvent:
        if self.event_type == "tool" and not self.tool_name:
            raise ValueError("工具事件必须包含 tool_name")
        if self.event_type in {"node", "policy", "model"} and not self.node:
            raise ValueError("运行事件必须包含 node")
        return self


class ToolSpec(BaseModel):
    """Metadata used by the harness before a tool is exposed or invoked."""

    model_config = ConfigDict(extra="forbid")

    tool_id: str = Field(min_length=1)
    description: str = ""
    input_schema: dict[str, Any] = Field(default_factory=dict)
    output_schema: dict[str, Any] = Field(default_factory=dict)
    read_only: bool = True
    deterministic: bool = True
    required_capabilities: list[CapabilityId] = Field(default_factory=list)
    data_scope: str = "none"
    idempotent: bool = True
    timeout_ms: int = Field(default=10_000, gt=0)
    version: str = "v1"
    schema_version: str = "2020-12"
    owner: str = "agent-runtime"
    risk_level: Literal["low", "medium", "high"] = "low"
    retry_policy: RetryPolicy = "never"
    enabled: bool = True

    @field_validator("required_capabilities")
    @classmethod
    def validate_required_capabilities(
        cls, value: list[CapabilityId]
    ) -> list[CapabilityId]:
        validate_capability_ids(value)
        return value

    @field_validator("input_schema", "output_schema")
    @classmethod
    def validate_schema_objects(cls, value: dict[str, Any]) -> dict[str, Any]:
        if value and not isinstance(value, dict):
            raise ValueError("工具 Schema 必须是对象")
        return value


class ToolCall(BaseModel):
    model_config = ConfigDict(extra="forbid")

    call_id: str = Field(min_length=1)
    tool_id: str = Field(min_length=1)
    arguments_summary: dict[str, Any] = Field(default_factory=dict)
    status: ToolCallStatus = "pending"
    started_at: str | datetime | None = None
    finished_at: str | datetime | None = None
    trace_ref: str | None = None


class ToolResult(BaseModel):
    model_config = ConfigDict(extra="forbid", arbitrary_types_allowed=True)

    call_id: str = Field(min_length=1)
    tool_id: str = Field(min_length=1)
    status: Literal["success", "error", "denied"]
    value: Any = Field(default=None, exclude=True)
    result_summary: Any = None
    error: str | None = None
    error_category: ToolErrorCategory | None = None
    retryable: bool = False
    trace_ref: str | None = None
    started_at: str | datetime | None = None
    finished_at: str | datetime | None = None


class RuntimeRunResult(BaseModel):
    """Internal result that can be projected to the existing API response."""

    model_config = ConfigDict(extra="forbid")

    run_id: str
    conversation_id: str | None = None
    turn_id: str | None = None
    message_id: str | None = None
    final_message: str = ""
    report_json: dict[str, Any] | None = None
    events: list[RuntimeEvent] = Field(default_factory=list)
    status_bar: dict[str, Any] | None = None
    outcome: RunOutcome = "completed"
    clarification: dict[str, Any] | None = None
    context_summary: dict[str, Any] = Field(default_factory=dict)
    trace_ref: str | None = None
    internal_trace: dict[str, Any] | None = None

    def to_public_dict(self) -> dict[str, Any]:
        """Project without adding fields to ``AgentRunResponse``."""

        return {
            "run_id": self.run_id,
            "conversation_id": self.conversation_id,
            "turn_id": self.turn_id,
            "message_id": self.message_id,
            "final_message": self.final_message,
            "report_json": self.report_json,
            "events": [
                event.model_dump(
                    mode="json",
                    include={"node", "status", "summary", "started_at", "finished_at"},
                )
                for event in self.events
            ],
            "status_bar": self.status_bar,
            "outcome": self.outcome,
            "clarification": self.clarification,
            "context_used": self.context_summary,
        }
