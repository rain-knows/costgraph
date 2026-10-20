from __future__ import annotations

from typing import Annotated, cast

from fastapi import Depends, Header

from app.auth.errors import (
    INVALID_TOKEN,
    MISSING_BEARER_TOKEN,
    PERMISSION_DENIED,
    PRINCIPAL_HAS_NO_ROLE,
    PRINCIPAL_NOT_IN_TENANT,
    auth_http_error,
)
from app.auth.oidc import OIDCError, TokenClaims, get_oidc_verifier
from app.domain.authorization import (
    DataScope,
    ExecutionPrincipal,
    PermissionId,
    RoleId,
    build_execution_context,
)
from app.repositories.postgres_iam_repository import PostgresIamRepository
from app.settings import get_settings

LOCAL_DEMO_ACCESS_TOKEN = "costgraph-local-demo"
LOCAL_DEMO_PRINCIPAL_ID = "local-costgraph-user"
LOCAL_DEMO_TENANT_ID = "local-costgraph-tenant"
LOCAL_DEMO_ROLES: tuple[RoleId, ...] = ("tenant_admin",)


def get_current_principal(
    authorization: Annotated[str | None, Header()] = None,
) -> ExecutionPrincipal:
    """用 Bearer 令牌认证主体，并按租户内的成员关系收窄角色与数据范围。"""
    token = _bearer_token(authorization)
    settings = get_settings()
    if (
        settings.app_env == "demo"
        and settings.local_demo_auth_enabled
        and token == LOCAL_DEMO_ACCESS_TOKEN
    ):
        return _local_demo_principal()
    try:
        claims = get_oidc_verifier().verify(token)
    except OIDCError as exc:
        raise auth_http_error(401, INVALID_TOKEN) from exc
    return _authorize(PostgresIamRepository(), claims)


def _local_demo_principal() -> ExecutionPrincipal:
    """返回显式开启的本地演示主体；该路径不参与任何非 demo 环境。"""
    return ExecutionPrincipal(
        principal_id=LOCAL_DEMO_PRINCIPAL_ID,
        tenant_id=LOCAL_DEMO_TENANT_ID,
        roles=LOCAL_DEMO_ROLES,
    )


PrincipalDependency = Annotated[ExecutionPrincipal, Depends(get_current_principal)]


def require_permission(principal: ExecutionPrincipal, permission: PermissionId) -> None:
    """在路由边界拒绝缺少权限的主体；403 与 401 严格区分。"""
    context = build_execution_context(
        "manual", [], requested_permissions=[permission], principal=principal
    )
    try:
        context.require_permission(permission)
    except PermissionError as exc:
        raise auth_http_error(403, PERMISSION_DENIED) from exc


def _bearer_token(authorization: str | None) -> str:
    scheme, _, value = (authorization or "").partition(" ")
    token = value.strip()
    if scheme.lower() != "bearer" or not token:
        raise auth_http_error(401, MISSING_BEARER_TOKEN)
    return token


def _authorize(
    repository: PostgresIamRepository, claims: TokenClaims
) -> ExecutionPrincipal:
    membership = repository.get_membership(claims.tenant_id, claims.subject)
    if membership is None:
        raise auth_http_error(403, PRINCIPAL_NOT_IN_TENANT)
    roles = tuple(sorted(set(claims.roles) & set(membership["roles"])))
    if not roles:
        raise auth_http_error(403, PRINCIPAL_HAS_NO_ROLE)
    raw_scope = repository.get_data_scope(claims.tenant_id, claims.subject) or {}
    return ExecutionPrincipal(
        principal_id=claims.subject,
        tenant_id=claims.tenant_id,
        roles=cast("tuple[RoleId, ...]", roles),
        data_scope=DataScope(
            allowed_part_ids=tuple(raw_scope.get("allowed_part_ids") or ()),
            allowed_period_start=raw_scope.get("allowed_period_start"),
            allowed_period_end=raw_scope.get("allowed_period_end"),
        ),
    )
