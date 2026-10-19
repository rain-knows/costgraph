from __future__ import annotations

from typing import Any

from app.auth.context import require_permission
from app.domain.authorization import ExecutionPrincipal
from app.domain.errors import IamPrincipalNotFoundError
from app.repositories.postgres_iam_repository import PostgresIamRepository
from app.schemas.iam import (
    AuditEventList,
    IamDataScope,
    IamPrincipal,
    IamPrincipalList,
    RolesUpdateRequest,
    ScopeUpdateRequest,
)


class IamService:
    """租户内角色与数据范围的治理入口；所有写入都留审计。"""

    def __init__(self, repository: PostgresIamRepository | None = None) -> None:
        self.repository = repository or PostgresIamRepository()

    def list_principals(
        self,
        principal: ExecutionPrincipal,
        *,
        page: int,
        page_size: int,
    ) -> IamPrincipalList:
        require_permission(principal, "iam.manage")
        items, total = self.repository.list_memberships(
            principal.tenant_id, limit=page_size, offset=(page - 1) * page_size
        )
        return IamPrincipalList(
            items=[_principal(item) for item in items],
            page=page,
            page_size=page_size,
            total=total,
        )

    def set_roles(
        self,
        principal: ExecutionPrincipal,
        target_principal_id: str,
        payload: RolesUpdateRequest,
        *,
        request_id: str | None,
    ) -> IamPrincipal:
        require_permission(principal, "iam.manage")
        roles = sorted(set(payload.roles))
        self.repository.set_roles(
            principal.tenant_id,
            target_principal_id,
            list(roles),
            updated_by=principal.principal_id,
        )
        self.repository.append_audit_event(
            tenant_id=principal.tenant_id,
            actor_principal_id=principal.principal_id,
            action="iam.roles_updated",
            target_type="principal",
            target_id=target_principal_id,
            outcome="success",
            request_id=request_id,
            detail={"roles": roles},
        )
        return _principal(
            self.repository.get_membership(principal.tenant_id, target_principal_id)
        )

    def set_scope(
        self,
        principal: ExecutionPrincipal,
        target_principal_id: str,
        payload: ScopeUpdateRequest,
        *,
        request_id: str | None,
    ) -> IamPrincipal:
        require_permission(principal, "data_scope.manage")
        if (
            self.repository.get_membership(principal.tenant_id, target_principal_id)
            is None
        ):
            raise IamPrincipalNotFoundError(target_principal_id)
        part_ids = sorted(set(payload.allowed_part_ids))
        self.repository.set_data_scope(
            principal.tenant_id,
            target_principal_id,
            part_ids=part_ids,
            period_start=payload.allowed_period_start,
            period_end=payload.allowed_period_end,
            updated_by=principal.principal_id,
        )
        self.repository.append_audit_event(
            tenant_id=principal.tenant_id,
            actor_principal_id=principal.principal_id,
            action="iam.scope_updated",
            target_type="principal",
            target_id=target_principal_id,
            outcome="success",
            request_id=request_id,
            detail={
                "allowed_part_ids": part_ids,
                "allowed_period_start": payload.allowed_period_start,
                "allowed_period_end": payload.allowed_period_end,
            },
        )
        return _principal(
            self.repository.get_membership(principal.tenant_id, target_principal_id)
        )

    def list_audit_events(
        self,
        principal: ExecutionPrincipal,
        *,
        page: int,
        page_size: int,
        actor_principal_id: str | None = None,
        action: str | None = None,
    ) -> AuditEventList:
        require_permission(principal, "audit.read")
        items, total = self.repository.list_audit_events(
            principal.tenant_id,
            limit=page_size,
            offset=(page - 1) * page_size,
            subject_id=actor_principal_id,
            action=action,
        )
        return AuditEventList(items=items, page=page, page_size=page_size, total=total)


def _principal(record: dict[str, Any] | None) -> IamPrincipal:
    if record is None:  # pragma: no cover - 由调用方先做存在性校验
        raise IamPrincipalNotFoundError("")
    scope = record.get("data_scope") or {}
    return IamPrincipal(
        principal_id=record["principal_id"],
        roles=record["roles"],
        data_scope=IamDataScope(
            allowed_part_ids=list(scope.get("allowed_part_ids") or []),
            allowed_period_start=scope.get("allowed_period_start"),
            allowed_period_end=scope.get("allowed_period_end"),
        ),
        updated_by=record["updated_by"],
        updated_at=record["updated_at"],
    )


def get_iam_service() -> IamService:
    return IamService()
