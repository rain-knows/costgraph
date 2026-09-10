from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.agent.capabilities import (
    ALL_CAPABILITY_IDS,
    CapabilityId,
    RoutingMode,
    normalize_capabilities,
    validate_capability_ids,
)
from app.settings import get_settings

RoleId = Literal["system_viewer", "cost_analyst", "agent_admin"]

ROLE_CAPABILITIES: dict[RoleId, tuple[CapabilityId, ...]] = {
    "system_viewer": ("system_help",),
    "cost_analyst": ("system_help", "cost_calculation", "report_generation"),
    "agent_admin": ALL_CAPABILITY_IDS,
}


class ExecutionPrincipal(BaseModel):
    model_config = ConfigDict(frozen=True)

    principal_id: str = Field(min_length=1)
    tenant_id: str = Field(min_length=1)
    roles: tuple[RoleId, ...]


class DataScope(BaseModel):
    model_config = ConfigDict(frozen=True)

    source: Literal["postgresql_cost_data"] = "postgresql_cost_data"
    allowed_part_ids: tuple[str, ...] = ()
    allowed_period_start: str | None = Field(
        default=None, pattern=r"^\d{4}-(0[1-9]|1[0-2])$"
    )
    allowed_period_end: str | None = Field(
        default=None, pattern=r"^\d{4}-(0[1-9]|1[0-2])$"
    )

    @model_validator(mode="after")
    def validate_period_range(self) -> DataScope:
        if (
            self.allowed_period_start
            and self.allowed_period_end
            and self.allowed_period_start > self.allowed_period_end
        ):
            raise ValueError("授权期间开始值不能晚于结束值")
        return self


class ExecutionContext(BaseModel):
    model_config = ConfigDict(frozen=True)

    principal: ExecutionPrincipal
    requested_capabilities: tuple[CapabilityId, ...]
    authorized_capabilities: tuple[CapabilityId, ...]
    server_allowed_capabilities: tuple[CapabilityId, ...]
    effective_capabilities: tuple[CapabilityId, ...]
    data_scope: DataScope

    def has_capability(self, capability: CapabilityId) -> bool:
        return capability in self.effective_capabilities

    def require_capability(self, capability: CapabilityId) -> None:
        if not self.has_capability(capability):
            raise PermissionError(f"执行主体未获授权使用能力：{capability}")

    def require_cost_scope(self, part_id: str, period: str | None = None) -> None:
        self.require_capability("cost_calculation")
        allowed_parts = self.data_scope.allowed_part_ids
        if allowed_parts and part_id not in allowed_parts:
            raise PermissionError(f"零件不在授权数据范围内：{part_id}")
        if (
            period
            and self.data_scope.allowed_period_start
            and period < self.data_scope.allowed_period_start
        ):
            raise PermissionError(f"期间早于授权数据范围：{period}")
        if (
            period
            and self.data_scope.allowed_period_end
            and period > self.data_scope.allowed_period_end
        ):
            raise PermissionError(f"期间晚于授权数据范围：{period}")

    def public_summary(self) -> dict[str, object]:
        return {
            "principal_id": self.principal.principal_id,
            "tenant_id": self.principal.tenant_id,
            "requested_capabilities": list(self.requested_capabilities),
            "authorized_capabilities": list(self.authorized_capabilities),
            "server_allowed_capabilities": list(self.server_allowed_capabilities),
            "effective_capabilities": list(self.effective_capabilities),
            "data_scope": self.data_scope.model_dump(mode="json"),
        }


def get_server_principal() -> ExecutionPrincipal:
    settings = get_settings()
    raw_roles = settings.csv_values("agent_principal_roles")
    unknown_roles = sorted(set(raw_roles) - set(ROLE_CAPABILITIES))
    if unknown_roles:
        raise RuntimeError(f"未知服务端 Agent 角色：{', '.join(unknown_roles)}")
    return ExecutionPrincipal(
        principal_id=settings.agent_principal_id,
        tenant_id=settings.agent_tenant_id,
        roles=tuple(raw_roles),
    )


def _authorized_capabilities(principal: ExecutionPrincipal) -> list[CapabilityId]:
    authorized: list[CapabilityId] = []
    for capability in ALL_CAPABILITY_IDS:
        if any(capability in ROLE_CAPABILITIES[role] for role in principal.roles):
            authorized.append(capability)
    return authorized


def _server_allowed_capabilities() -> list[CapabilityId]:
    settings = get_settings()
    return validate_capability_ids(
        settings.csv_values("agent_server_allowed_capabilities")
    )


def _data_scope() -> DataScope:
    settings = get_settings()
    return DataScope(
        source="postgresql_cost_data",
        allowed_part_ids=tuple(settings.csv_values("agent_allowed_part_ids")),
        allowed_period_start=settings.agent_allowed_period_start,
        allowed_period_end=settings.agent_allowed_period_end,
    )


def build_execution_context(
    routing_mode: RoutingMode,
    enabled_capabilities: list[str] | None = None,
    *,
    principal: ExecutionPrincipal | None = None,
) -> ExecutionContext:
    principal = principal or get_server_principal()
    requested = normalize_capabilities(routing_mode, enabled_capabilities)
    authorized = _authorized_capabilities(principal)
    server_allowed = _server_allowed_capabilities()
    effective = [
        capability
        for capability in ALL_CAPABILITY_IDS
        if capability in requested
        and capability in authorized
        and capability in server_allowed
    ]
    if "report_generation" in effective and "cost_calculation" not in effective:
        effective.remove("report_generation")
    return ExecutionContext(
        principal=principal,
        requested_capabilities=tuple(requested),
        authorized_capabilities=tuple(authorized),
        server_allowed_capabilities=tuple(server_allowed),
        effective_capabilities=tuple(effective),
        data_scope=_data_scope(),
    )


def execution_context_from_state(value: object) -> ExecutionContext:
    if isinstance(value, ExecutionContext):
        return value
    return ExecutionContext.model_validate(value)
