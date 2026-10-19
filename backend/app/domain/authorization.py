from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.agent.capabilities import (
    ALL_CAPABILITY_IDS,
    CAPABILITY_REGISTRY,
    CapabilityId,
    RoutingMode,
    normalize_capabilities,
    validate_capability_ids,
)
from app.settings import get_settings

RoleId = Literal["viewer", "data_operator", "data_approver", "tenant_admin"]
PermissionId = Literal[
    "cost_data.read",
    "data_management.read",
    "data_import.submit",
    "data_import.approve",
    "iam.manage",
    "data_scope.manage",
    "audit.read",
]

_AGENT_CAPABILITIES: tuple[CapabilityId, ...] = (
    "system_help",
    "cost_calculation",
    "report_generation",
)

ROLE_CAPABILITIES: dict[RoleId, tuple[CapabilityId, ...]] = {
    "viewer": _AGENT_CAPABILITIES,
    "data_operator": _AGENT_CAPABILITIES,
    "data_approver": _AGENT_CAPABILITIES,
    "tenant_admin": _AGENT_CAPABILITIES,
}
ROLE_PERMISSIONS: dict[RoleId, tuple[PermissionId, ...]] = {
    "viewer": ("cost_data.read",),
    "data_operator": (
        "cost_data.read",
        "data_management.read",
        "data_import.submit",
    ),
    "data_approver": (
        "cost_data.read",
        "data_management.read",
        "data_import.approve",
        "audit.read",
    ),
    "tenant_admin": (
        "cost_data.read",
        "data_management.read",
        "data_import.submit",
        "data_import.approve",
        "iam.manage",
        "data_scope.manage",
        "audit.read",
    ),
}
ALL_PERMISSION_IDS: tuple[PermissionId, ...] = (
    "cost_data.read",
    "data_management.read",
    "data_import.submit",
    "data_import.approve",
    "iam.manage",
    "data_scope.manage",
    "audit.read",
)


def _validate_capability_role_metadata() -> None:
    for capability_id, spec in CAPABILITY_REGISTRY.items():
        metadata_roles = set(spec.required_roles)
        configured_roles = {
            role
            for role, capabilities in ROLE_CAPABILITIES.items()
            if capability_id in capabilities
        }
        if metadata_roles != configured_roles:
            raise RuntimeError(f"能力角色元数据与授权映射不一致：{capability_id}")


_validate_capability_role_metadata()


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


class ExecutionPrincipal(BaseModel):
    """已认证主体；数据范围随主体一起固化，供 Run/Import Job 快照复用。

    ``data_scope`` 空集表示未被限制，只能由管理员写入 ``iam_data_scopes``
    后收窄；它不是客户输入，而是服务端确定性的授权事实。
    """

    model_config = ConfigDict(frozen=True)

    principal_id: str = Field(min_length=1)
    tenant_id: str = Field(min_length=1)
    roles: tuple[RoleId, ...]
    data_scope: DataScope = DataScope()

    @model_validator(mode="after")
    def validate_roles(self) -> ExecutionPrincipal:
        if not self.roles:
            raise ValueError("执行主体至少需要一个角色")
        if len(set(self.roles)) != len(self.roles):
            raise ValueError("执行主体角色不能重复")
        return self


class ExecutionContext(BaseModel):
    model_config = ConfigDict(frozen=True)

    principal: ExecutionPrincipal
    requested_capabilities: tuple[CapabilityId, ...]
    requested_permissions: tuple[PermissionId, ...]
    authorized_capabilities: tuple[CapabilityId, ...]
    server_allowed_capabilities: tuple[CapabilityId, ...]
    effective_capabilities: tuple[CapabilityId, ...]
    authorized_permissions: tuple[PermissionId, ...]
    server_allowed_permissions: tuple[PermissionId, ...]
    effective_permissions: tuple[PermissionId, ...]
    data_scope: DataScope

    def has_capability(self, capability: CapabilityId) -> bool:
        return capability in self.effective_capabilities

    def require_capability(self, capability: CapabilityId) -> None:
        if not self.has_capability(capability):
            raise PermissionError(f"执行主体未获授权使用能力：{capability}")

    def has_permission(self, permission: PermissionId) -> bool:
        return permission in self.effective_permissions

    def require_permission(self, permission: PermissionId) -> None:
        if not self.has_permission(permission):
            raise PermissionError(f"执行主体未获授权使用权限：{permission}")

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
            "requested_permissions": list(self.requested_permissions),
            "authorized_permissions": list(self.authorized_permissions),
            "server_allowed_permissions": list(self.server_allowed_permissions),
            "effective_permissions": list(self.effective_permissions),
            "data_scope": self.data_scope.model_dump(mode="json"),
        }


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


def _authorized_permissions(principal: ExecutionPrincipal) -> list[PermissionId]:
    authorized: list[PermissionId] = []
    for permission in ALL_PERMISSION_IDS:
        if any(permission in ROLE_PERMISSIONS[role] for role in principal.roles):
            authorized.append(permission)
    return authorized


def _build_context_from_requests(
    principal: ExecutionPrincipal,
    requested: list[CapabilityId],
    requested_permissions: list[str] | None,
    data_scope: DataScope,
) -> ExecutionContext:
    authorized = _authorized_capabilities(principal)
    server_allowed = _server_allowed_capabilities()
    effective = [
        capability
        for capability in ALL_CAPABILITY_IDS
        if capability in requested
        and capability in authorized
        and capability in server_allowed
    ]
    requested_permission_values = list(requested_permissions or [])
    unknown_permissions = sorted(
        set(requested_permission_values) - set(ALL_PERMISSION_IDS)
    )
    if unknown_permissions:
        raise ValueError(f"未知权限：{', '.join(unknown_permissions)}")
    requested_permission_ids = [
        permission
        for permission in ALL_PERMISSION_IDS
        if permission in requested_permission_values
    ]
    authorized_permissions = _authorized_permissions(principal)
    server_allowed_permissions = list(ALL_PERMISSION_IDS)
    effective_permissions = [
        permission
        for permission in ALL_PERMISSION_IDS
        if permission in requested_permission_ids
        and permission in authorized_permissions
        and permission in server_allowed_permissions
    ]
    if "report_generation" in effective and "cost_calculation" not in effective:
        effective.remove("report_generation")
    return ExecutionContext(
        principal=principal,
        requested_capabilities=tuple(requested),
        requested_permissions=tuple(requested_permission_ids),
        authorized_capabilities=tuple(authorized),
        server_allowed_capabilities=tuple(server_allowed),
        effective_capabilities=tuple(effective),
        authorized_permissions=tuple(authorized_permissions),
        server_allowed_permissions=tuple(server_allowed_permissions),
        effective_permissions=tuple(effective_permissions),
        data_scope=data_scope,
    )


def build_execution_context(
    routing_mode: RoutingMode,
    enabled_capabilities: list[str] | None = None,
    *,
    requested_permissions: list[str] | None = None,
    principal: ExecutionPrincipal,
    data_scope: DataScope | None = None,
) -> ExecutionContext:
    requested = normalize_capabilities(routing_mode, enabled_capabilities)
    return _build_context_from_requests(
        principal,
        requested,
        requested_permissions,
        data_scope or principal.data_scope,
    )


def execution_context_from_state(
    value: object,
    *,
    authoritative_principal: ExecutionPrincipal | None = None,
    data_scope: DataScope | None = None,
) -> ExecutionContext:
    """Rebuild authorization from persisted requests, never trust derived fields.

    Serialized checkpoint state is an untrusted input boundary: its effective
    grants are never trusted, only re-derived from the persisted requests. When
    ``authoritative_principal`` is supplied the persisted principal must equal
    it, which is how the worker binds a run's checkpoint to the principal
    frozen in that run's row. Callers inside a run have no authoritative
    principal of their own and omit it, so identity is bound once at the run
    boundary instead of being re-resolved against ambient configuration.

    ``data_scope`` follows the same rule: the run boundary passes the scope
    frozen with the run, and API callers pass the authenticated principal's
    scope. Persisted state is only a fallback; it cannot widen the caller's
    own scope.
    """
    is_model = isinstance(value, ExecutionContext)
    raw = value.model_dump(mode="python") if is_model else value
    if not isinstance(raw, dict):
        raise TypeError("execution_context 状态格式无效")
    principal = ExecutionPrincipal.model_validate(raw.get("principal"))
    if authoritative_principal is not None and (
        principal.principal_id,
        principal.tenant_id,
        principal.roles,
        principal.data_scope,
    ) != (
        authoritative_principal.principal_id,
        authoritative_principal.tenant_id,
        authoritative_principal.roles,
        authoritative_principal.data_scope,
    ):
        raise PermissionError("主体与当前服务端主体不一致")
    requested = validate_capability_ids(list(raw.get("requested_capabilities") or []))
    return _build_context_from_requests(
        principal,
        requested,
        list(raw.get("requested_permissions") or []),
        data_scope or principal.data_scope,
    )
