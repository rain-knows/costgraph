from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

RoleId = Literal["viewer", "data_operator", "data_approver", "tenant_admin"]
PERIOD_PATTERN = r"^\d{4}-(0[1-9]|1[0-2])$"


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class IamDataScope(StrictModel):
    allowed_part_ids: list[str] = Field(default_factory=list)
    allowed_period_start: str | None = Field(default=None, pattern=PERIOD_PATTERN)
    allowed_period_end: str | None = Field(default=None, pattern=PERIOD_PATTERN)

    @model_validator(mode="after")
    def validate_period_range(self) -> IamDataScope:
        if (
            self.allowed_period_start
            and self.allowed_period_end
            and self.allowed_period_start > self.allowed_period_end
        ):
            raise ValueError("授权期间开始值不能晚于结束值")
        return self


class IamPrincipal(StrictModel):
    principal_id: str = Field(min_length=1, max_length=128)
    roles: list[RoleId] = Field(min_length=1)
    data_scope: IamDataScope
    updated_by: str
    updated_at: datetime


class IamPrincipalList(StrictModel):
    items: list[IamPrincipal]
    page: int
    page_size: int
    total: int


class RolesUpdateRequest(StrictModel):
    roles: list[RoleId] = Field(min_length=1)


class ScopeUpdateRequest(StrictModel):
    allowed_part_ids: list[str] = Field(default_factory=list)
    allowed_period_start: str | None = Field(default=None, pattern=PERIOD_PATTERN)
    allowed_period_end: str | None = Field(default=None, pattern=PERIOD_PATTERN)

    @model_validator(mode="after")
    def validate_period_range(self) -> ScopeUpdateRequest:
        if (
            self.allowed_period_start
            and self.allowed_period_end
            and self.allowed_period_start > self.allowed_period_end
        ):
            raise ValueError("授权期间开始值不能晚于结束值")
        return self


AuditOutcome = Literal["success", "failure"]


class AuditEvent(StrictModel):
    event_id: int
    job_id: str | None = None
    batch_id: str | None = None
    actor_principal_id: str
    action: str
    target_type: str
    target_id: str
    outcome: AuditOutcome
    request_id: str | None = None
    detail: dict[str, object]
    created_at: datetime


class AuditEventList(StrictModel):
    items: list[AuditEvent]
    page: int
    page_size: int
    total: int
