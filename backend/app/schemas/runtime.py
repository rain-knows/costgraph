from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.agent.capabilities import RoutingMode, validate_capability_ids
from app.schemas.agent import AgentRunResponse


class RuntimeRunCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    message_id: str = Field(min_length=1)
    content: str = Field(min_length=1)
    routing_mode: RoutingMode | None = None
    enabled_capabilities: list[str] | None = None
    reply_to_clarification_id: str | None = None

    @model_validator(mode="after")
    def validate_enabled_capabilities(self) -> "RuntimeRunCreateRequest":
        if self.enabled_capabilities is not None:
            validate_capability_ids(self.enabled_capabilities)
        return self


class RuntimeBudget(BaseModel):
    model_config = ConfigDict(extra="forbid")

    limits: dict[str, Any]
    usage: dict[str, Any]


class RuntimeRunResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["2.0"] = "2.0"
    api_version: Literal["2.0"] = "2.0"
    runtime_version: str
    workflow_version: str
    trace_id: str
    run_id: str
    conversation_id: str
    turn_id: str
    message_id: str
    status: Literal[
        "queued",
        "running",
        "finalizing",
        "retry_wait",
        "succeeded",
        "failed",
        "cancelled",
    ]
    attempt_count: int = Field(default=0, ge=0)
    created_at: str
    updated_at: str | None = None
    started_at: str | None = None
    finished_at: str | None = None
    cancel_requested_at: str | None = None
    last_event_id: int | None = None
    request_id: str
    budget: RuntimeBudget
    events_url: str | None = None
    result: AgentRunResponse | None = None
    error: dict[str, Any] | None = None
