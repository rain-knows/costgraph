from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.db.engine import get_session_factory
from app.db.models import GovernanceAuditEvent, IamDataScope, IamPrincipalRole
from app.db.tenant import scoped_session
from app.settings import get_settings


class PostgresIamRepository:
    """租户内主体、角色与数据范围的事实来源；审计记录只读。"""

    def __init__(self, session_factory=None) -> None:
        self._session_factory = session_factory or get_session_factory()

    @contextmanager
    def _session(self, tenant_id: str) -> Iterator[Session]:
        with scoped_session(
            self._session_factory,
            tenant_id,
            statement_timeout_ms=int(get_settings().agent_tool_timeout_seconds * 1000),
        ) as session:
            yield session

    def get_membership(
        self, tenant_id: str, principal_id: str
    ) -> dict[str, Any] | None:
        with self._session(tenant_id) as session:
            row = session.scalar(
                select(IamPrincipalRole).where(
                    IamPrincipalRole.tenant_id == tenant_id,
                    IamPrincipalRole.principal_id == principal_id,
                )
            )
        if row is None:
            return None
        scope = self.get_data_scope(tenant_id, principal_id) or {}
        return {
            "principal_id": row.principal_id,
            "roles": list(row.roles),
            "data_scope": scope,
            "updated_by": row.updated_by,
            "updated_at": row.updated_at,
        }

    def list_memberships(
        self, tenant_id: str, *, limit: int, offset: int
    ) -> tuple[list[dict[str, Any]], int]:
        filters = (IamPrincipalRole.tenant_id == tenant_id,)
        with self._session(tenant_id) as session:
            total = (
                session.scalar(
                    select(func.count()).select_from(IamPrincipalRole).where(*filters)
                )
                or 0
            )
            rows = session.scalars(
                select(IamPrincipalRole)
                .where(*filters)
                .order_by(IamPrincipalRole.principal_id)
                .offset(offset)
                .limit(limit)
            ).all()
            scopes = {
                scope.principal_id: scope
                for scope in session.scalars(
                    select(IamDataScope).where(
                        IamDataScope.tenant_id == tenant_id,
                        IamDataScope.principal_id.in_(
                            [row.principal_id for row in rows]
                        ),
                    )
                ).all()
            }
        return [
            {
                "principal_id": row.principal_id,
                "roles": list(row.roles),
                "data_scope": _scope_dict(scopes.get(row.principal_id)),
                "updated_by": row.updated_by,
                "updated_at": row.updated_at,
            }
            for row in rows
        ], int(total)

    def set_roles(
        self,
        tenant_id: str,
        principal_id: str,
        roles: list[str],
        *,
        updated_by: str,
    ) -> None:
        statement = (
            insert(IamPrincipalRole)
            .values(
                tenant_id=tenant_id,
                principal_id=principal_id,
                roles=roles,
                updated_by=updated_by,
                updated_at=datetime.now(UTC),
            )
            .on_conflict_do_update(
                index_elements=[
                    IamPrincipalRole.tenant_id,
                    IamPrincipalRole.principal_id,
                ],
                set_={
                    "roles": roles,
                    "updated_by": updated_by,
                    "updated_at": datetime.now(UTC),
                },
            )
        )
        with self._session(tenant_id) as session:
            session.execute(statement)
            session.commit()

    def get_data_scope(
        self, tenant_id: str, principal_id: str
    ) -> dict[str, Any] | None:
        with self._session(tenant_id) as session:
            row = session.scalar(
                select(IamDataScope).where(
                    IamDataScope.tenant_id == tenant_id,
                    IamDataScope.principal_id == principal_id,
                )
            )
        if row is None:
            return None
        return _scope_dict(row) or {}

    def set_data_scope(
        self,
        tenant_id: str,
        principal_id: str,
        *,
        part_ids: list[str],
        period_start: str | None,
        period_end: str | None,
        updated_by: str,
    ) -> None:
        values = {
            "tenant_id": tenant_id,
            "principal_id": principal_id,
            "allowed_part_ids": part_ids,
            "allowed_period_start": period_start,
            "allowed_period_end": period_end,
            "updated_by": updated_by,
        }
        statement = (
            insert(IamDataScope)
            .values(**values, updated_at=datetime.now(UTC))
            .on_conflict_do_update(
                index_elements=[IamDataScope.tenant_id, IamDataScope.principal_id],
                set_={
                    "allowed_part_ids": part_ids,
                    "allowed_period_start": period_start,
                    "allowed_period_end": period_end,
                    "updated_by": updated_by,
                    "updated_at": datetime.now(UTC),
                },
            )
        )
        with self._session(tenant_id) as session:
            session.execute(statement)
            session.commit()

    def list_audit_events(
        self,
        tenant_id: str,
        *,
        limit: int,
        offset: int,
        subject_id: str | None = None,
        action: str | None = None,
    ) -> tuple[list[dict[str, Any]], int]:
        filters = [GovernanceAuditEvent.tenant_id == tenant_id]
        if subject_id:
            filters.append(GovernanceAuditEvent.actor_principal_id == subject_id)
        if action:
            filters.append(GovernanceAuditEvent.action == action)
        with self._session(tenant_id) as session:
            total = (
                session.scalar(
                    select(func.count())
                    .select_from(GovernanceAuditEvent)
                    .where(*filters)
                )
                or 0
            )
            rows = session.scalars(
                select(GovernanceAuditEvent)
                .where(*filters)
                .order_by(
                    GovernanceAuditEvent.created_at.desc(),
                    GovernanceAuditEvent.event_id.desc(),
                )
                .offset(offset)
                .limit(limit)
            ).all()
        return [_audit_event_dict(row) for row in rows], int(total)

    def append_audit_event(
        self,
        *,
        tenant_id: str,
        actor_principal_id: str,
        action: str,
        target_type: str,
        target_id: str,
        outcome: str,
        request_id: str | None,
        detail: dict[str, Any],
    ) -> None:
        """追加一条治理审计事件；表上的触发器拒绝任何后续修改。"""
        with self._session(tenant_id) as session:
            session.execute(
                GovernanceAuditEvent.__table__.insert().values(
                    tenant_id=tenant_id,
                    actor_principal_id=actor_principal_id,
                    action=action,
                    target_type=target_type,
                    target_id=target_id,
                    outcome=outcome,
                    request_id=request_id,
                    detail=detail,
                    created_at=datetime.now(UTC),
                )
            )
            session.commit()


def _scope_dict(row: IamDataScope | None) -> dict[str, Any] | None:
    if row is None:
        return None
    return {
        "allowed_part_ids": list(row.allowed_part_ids or []),
        "allowed_period_start": row.allowed_period_start,
        "allowed_period_end": row.allowed_period_end,
    }


def _audit_event_dict(row: GovernanceAuditEvent) -> dict[str, Any]:
    return {
        "event_id": row.event_id,
        "job_id": str(row.job_id) if row.job_id else None,
        "batch_id": str(row.batch_id) if row.batch_id else None,
        "actor_principal_id": row.actor_principal_id,
        "action": row.action,
        "target_type": row.target_type,
        "target_id": row.target_id,
        "outcome": row.outcome,
        "request_id": row.request_id,
        "detail": dict(row.detail or {}),
        "created_at": row.created_at,
    }
