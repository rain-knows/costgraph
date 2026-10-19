from __future__ import annotations

from app.domain.authorization import DataScope, ExecutionPrincipal, RoleId

DEFAULT_ROLES: tuple[RoleId, ...] = ("tenant_admin",)


def make_principal(
    roles: tuple[RoleId, ...] = DEFAULT_ROLES,
    *,
    principal_id: str = "test-user",
    tenant_id: str = "test-tenant",
    data_scope: DataScope | None = None,
) -> ExecutionPrincipal:
    return ExecutionPrincipal(
        principal_id=principal_id,
        tenant_id=tenant_id,
        roles=roles,
        data_scope=data_scope or DataScope(),
    )


TEST_PRINCIPAL = make_principal()
