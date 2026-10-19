"""导入任务 PostgreSQL 持久层（规格 3.2、3.4、第 4 节）。

设计要点：

- 所有读路径强制 ``tenant_id`` 边界；未找到统一返回 ``None``。
- 状态机非法迁移抛 :class:`ImportJobStateConflictError`（``import_job_state_conflict``），
  由 HTTP 层转 409；不存在抛 :class:`ImportJobNotFoundError`，转 404。
- Worker 领取沿用 ``run_repository`` 的租约模式：``FOR UPDATE SKIP LOCKED`` +
  ``lease_owner`` / ``lease_expires_at`` + ``attempt_count`` / ``available_at`` 退避。
  额外回收租约已过期的 ``validating`` / ``publishing`` 行（同一状态重试），
  避免依赖 ``publishing -> validating`` 这类非法迁移。
- 审计事件只追加；``detail`` 只接受计数/错误码/状态等非敏感字段。
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import PurePath, PureWindowsPath
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import func, or_, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.db.engine import get_session_factory
from app.db.models import (
    DataImportJob,
    DataLoadBatch,
    DataLoadError,
    GovernanceAuditEvent,
)
from app.db.tenant import privileged_session, scoped_session
from app.domain.authorization import ExecutionContext
from app.repositories.import_job_repository import (
    CLAIM_TARGET_STATUS,
    CLAIMABLE_JOB_STATUSES,
    DECISION_TO_STATUS,
    IMPORT_JOB_STATUSES,
    WORKING_JOB_STATUSES,
    ImportJobLeaseLostError,
    ImportJobNotFoundError,
    ImportJobStateConflictError,
    validate_job_transition,
)
from app.repositories.postgres_data_admin_repository import _safe_error_message
from app.settings import get_settings

#: Worker 导入任务租约时长；与 agent run 租约保持同一量级但独立演进。
IMPORT_JOB_LEASE_SECONDS = 90
#: 可重试失败的退避序列（秒）。
RETRY_BACKOFF_SECONDS = (1, 5, 30)
#: 审计 detail 允许的键；其余键（尤其载荷）一律丢弃。
AUDIT_DETAIL_ALLOWED_KEYS = frozenset(
    {
        "status",
        "previous_status",
        "total_rows",
        "valid_rows",
        "error_rows",
        "attempt_count",
        "error_code",
        "retryable",
        "error_codes",
        "batch_id",
        "job_id",
        "decision",
    }
)
STORE_ERROR_MESSAGE_LIMIT = 500
TERMINAL_JOB_STATUSES = (
    "validation_failed",
    "rejected",
    "published",
    "cancelled",
)
ACTIVE_JOB_STATUSES = tuple(
    status for status in IMPORT_JOB_STATUSES if status not in TERMINAL_JOB_STATUSES
)


def utc_now() -> datetime:
    return datetime.now(UTC)


class PostgresImportJobRepository:
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

    @contextmanager
    def _worker_session(self) -> Iterator[Session]:
        with privileged_session(
            self._session_factory,
            statement_timeout_ms=int(get_settings().agent_tool_timeout_seconds * 1000),
        ) as session:
            yield session

    # ---------------------------------------------------------------- create

    def create_job(
        self,
        *,
        tenant_id: str,
        principal_id: str,
        principal_roles: list[str],
        source_system: str,
        source_object_key: str,
        source_object_size: int,
        source_object_sha256: str,
        snapshot_hash: str,
        calculation_rule_version: str,
        idempotency_key: str,
        total_rows: int,
    ) -> dict[str, Any]:
        now = utc_now()
        with self._session(tenant_id) as session:
            existing = self._find_duplicate(
                session,
                tenant_id=tenant_id,
                idempotency_key=idempotency_key,
                source_system=source_system,
                snapshot_hash=snapshot_hash,
                calculation_rule_version=calculation_rule_version,
            )
            if existing is not None:
                session.rollback()
                return {**_job_dict(existing), "duplicate": True}
            statement = (
                pg_insert(DataImportJob)
                .values(
                    job_id=uuid4(),
                    tenant_id=tenant_id,
                    principal_id=principal_id,
                    principal_roles=list(principal_roles),
                    source_system=source_system,
                    source_object_key=source_object_key,
                    source_object_size=source_object_size,
                    source_object_sha256=source_object_sha256,
                    snapshot_hash=snapshot_hash,
                    calculation_rule_version=calculation_rule_version,
                    idempotency_key=idempotency_key,
                    status="submitted",
                    total_rows=total_rows,
                    valid_rows=0,
                    error_rows=0,
                    error_summary=None,
                    attempt_count=0,
                    available_at=now,
                    created_at=now,
                )
                .on_conflict_do_nothing()
                .returning(DataImportJob.job_id)
            )
            created_job_id = session.scalar(statement)
            if created_job_id is None:
                # 并发提交命中唯一约束：返回已存在任务。
                session.rollback()
                row = self._find_by_idempotency_key(
                    session, tenant_id=tenant_id, idempotency_key=idempotency_key
                )
                if row is None:
                    raise ImportJobStateConflictError("concurrent_submit", "existing")
                return {**_job_dict(row), "duplicate": True}
            session.commit()
            row = session.scalar(
                select(DataImportJob).where(DataImportJob.job_id == created_job_id)
            )
        if row is None:
            raise ImportJobStateConflictError("submitted", "created")
        return {**_job_dict(row), "duplicate": False}

    @staticmethod
    def _find_by_idempotency_key(
        session: Session, *, tenant_id: str, idempotency_key: str
    ) -> DataImportJob | None:
        return session.scalar(
            select(DataImportJob).where(
                DataImportJob.tenant_id == tenant_id,
                DataImportJob.idempotency_key == idempotency_key,
            )
        )

    def _find_duplicate(
        self,
        session: Session,
        *,
        tenant_id: str,
        idempotency_key: str,
        source_system: str,
        snapshot_hash: str,
        calculation_rule_version: str,
    ) -> DataImportJob | None:
        by_key = self._find_by_idempotency_key(
            session, tenant_id=tenant_id, idempotency_key=idempotency_key
        )
        if by_key is not None:
            return by_key
        return session.scalar(
            select(DataImportJob)
            .where(
                DataImportJob.tenant_id == tenant_id,
                DataImportJob.source_system == source_system,
                DataImportJob.snapshot_hash == snapshot_hash,
                DataImportJob.calculation_rule_version == calculation_rule_version,
            )
            .order_by(DataImportJob.created_at)
            .limit(1)
        )

    # ------------------------------------------------------------ read paths

    def list_jobs(
        self,
        context: ExecutionContext,
        *,
        status: str | None,
        page: int,
        page_size: int,
    ) -> tuple[list[dict[str, Any]], int]:
        filters = [DataImportJob.tenant_id == context.principal.tenant_id]
        if status == "active":
            filters.append(DataImportJob.status.in_(ACTIVE_JOB_STATUSES))
        elif status:
            filters.append(DataImportJob.status == status)
        with self._session(context.principal.tenant_id) as session:
            total = (
                session.scalar(
                    select(func.count()).select_from(DataImportJob).where(*filters)
                )
                or 0
            )
            rows = session.scalars(
                select(DataImportJob)
                .where(*filters)
                .order_by(DataImportJob.created_at.desc(), DataImportJob.job_id.desc())
                .offset((page - 1) * page_size)
                .limit(page_size)
            ).all()
        return [_job_dict(row) for row in rows], int(total)

    def get_job(self, context: ExecutionContext, job_id: UUID) -> dict[str, Any] | None:
        tenant_id = context.principal.tenant_id
        with self._session(context.principal.tenant_id) as session:
            row = session.scalar(
                select(DataImportJob).where(
                    DataImportJob.tenant_id == tenant_id,
                    DataImportJob.job_id == job_id,
                )
            )
            if row is None:
                return None
            counts = session.execute(
                select(DataLoadError.error_code, func.count())
                .where(
                    DataLoadError.tenant_id == tenant_id,
                    DataLoadError.batch_id == job_id,
                )
                .group_by(DataLoadError.error_code)
                .order_by(DataLoadError.error_code)
            ).all()
        return {
            **_job_dict(row),
            "error_codes": [
                {"error_code": code, "count": count} for code, count in counts
            ],
        }

    def list_job_errors(
        self, context: ExecutionContext, job_id: UUID, *, page: int, page_size: int
    ) -> tuple[list[dict[str, Any]], int] | None:
        tenant_id = context.principal.tenant_id
        with self._session(context.principal.tenant_id) as session:
            exists = session.scalar(
                select(DataImportJob.job_id).where(
                    DataImportJob.tenant_id == tenant_id,
                    DataImportJob.job_id == job_id,
                )
            )
            if exists is None:
                return None
            filters = (
                DataLoadError.tenant_id == tenant_id,
                DataLoadError.batch_id == job_id,
            )
            total = (
                session.scalar(
                    select(func.count()).select_from(DataLoadError).where(*filters)
                )
                or 0
            )
            rows = session.scalars(
                select(DataLoadError)
                .where(*filters)
                .order_by(DataLoadError.error_id)
                .offset((page - 1) * page_size)
                .limit(page_size)
            ).all()
        return (
            [
                {
                    "error_id": row.error_id,
                    "source_table": row.source_table,
                    "source_record_id": row.source_record_id,
                    "error_code": row.error_code,
                    "error_message": _safe_error_message(row.error_message),
                    "created_at": row.created_at,
                }
                for row in rows
            ],
            int(total),
        )

    # ------------------------------------------------------- state transitions

    def decide(
        self,
        context: ExecutionContext,
        job_id: UUID,
        *,
        decision: str,
        reason: str | None,
        actor_principal_id: str,
    ) -> dict[str, Any]:
        target = DECISION_TO_STATUS.get(decision)
        if target is None:
            raise ValueError(f"未知审批决策：{decision}")
        tenant_id = context.principal.tenant_id
        with self._session(context.principal.tenant_id) as session:
            row = self._locked_job(session, tenant_id=tenant_id, job_id=job_id)
            validate_job_transition(row.status, target)
            now = utc_now()
            row.status = target
            row.decision = target
            row.decision_reason = reason
            row.decided_by = actor_principal_id
            row.decided_at = now
            row.finished_at = now if target == "rejected" else row.finished_at
            session.commit()
            session.refresh(row)
            return _job_dict(row)

    def cancel(
        self, context: ExecutionContext, job_id: UUID, *, actor_principal_id: str
    ) -> dict[str, Any]:
        tenant_id = context.principal.tenant_id
        with self._session(context.principal.tenant_id) as session:
            row = self._locked_job(session, tenant_id=tenant_id, job_id=job_id)
            validate_job_transition(row.status, "cancelled")
            now = utc_now()
            row.status = "cancelled"
            row.finished_at = now
            row.lease_owner = None
            row.lease_expires_at = None
            session.commit()
            session.refresh(row)
            return _job_dict(row)

    def _locked_job(
        self, session: Session, *, tenant_id: str, job_id: UUID
    ) -> DataImportJob:
        row = session.scalar(
            select(DataImportJob)
            .where(
                DataImportJob.tenant_id == tenant_id,
                DataImportJob.job_id == job_id,
            )
            .with_for_update()
        )
        if row is None:
            raise ImportJobNotFoundError(job_id)
        return row

    # --------------------------------------------------------- worker leases

    def claim_next_job(self, worker_id: str) -> dict[str, Any] | None:
        now = utc_now()
        lease_expires_at = now + timedelta(seconds=IMPORT_JOB_LEASE_SECONDS)
        statement = (
            select(DataImportJob)
            .where(
                DataImportJob.available_at <= now,
                or_(
                    DataImportJob.status.in_(CLAIMABLE_JOB_STATUSES),
                    (
                        DataImportJob.status.in_(WORKING_JOB_STATUSES)
                        & or_(
                            DataImportJob.lease_expires_at.is_(None),
                            DataImportJob.lease_expires_at < now,
                        )
                    ),
                ),
            )
            .order_by(DataImportJob.created_at)
            .with_for_update(skip_locked=True)
            .limit(1)
        )
        with self._worker_session() as session:
            row = session.scalar(statement)
            if row is None:
                session.rollback()
                return None
            previous_status = row.status
            target_status = CLAIM_TARGET_STATUS.get(previous_status, previous_status)
            if target_status != previous_status:
                validate_job_transition(previous_status, target_status)
                row.status = target_status
            row.attempt_count += 1
            row.lease_owner = worker_id
            row.lease_expires_at = lease_expires_at
            row.started_at = row.started_at or now
            session.commit()
            session.refresh(row)
            value = _job_dict(row)
            value["claimed_from"] = previous_status
            return value

    def renew_job_lease(self, job_id: UUID, worker_id: str) -> bool:
        now = utc_now()
        with self._worker_session() as session:
            result = session.execute(
                update(DataImportJob)
                .where(
                    DataImportJob.job_id == job_id,
                    DataImportJob.lease_owner == worker_id,
                    DataImportJob.status.in_(WORKING_JOB_STATUSES),
                )
                .values(
                    lease_expires_at=now + timedelta(seconds=IMPORT_JOB_LEASE_SECONDS)
                )
            )
            session.commit()
            return bool(result.rowcount)

    def complete_validation(self, job_id: UUID, worker_id: str, *, outcome: Any) -> str:
        fields = _validation_fields(outcome)
        target = "approval_pending" if fields["valid"] else "validation_failed"
        with self._worker_session() as session:
            row = self._leased_job(
                session, job_id=job_id, worker_id=worker_id, expected="validating"
            )
            validate_job_transition(row.status, target)
            now = utc_now()
            session.execute(
                DataLoadError.__table__.delete().where(
                    DataLoadError.tenant_id == row.tenant_id,
                    DataLoadError.batch_id == row.job_id,
                )
            )
            if fields["errors"]:
                self._ensure_error_batch(session, row, fields, now)
                session.execute(
                    DataLoadError.__table__.insert(),
                    [
                        {
                            "tenant_id": row.tenant_id,
                            "batch_id": row.job_id,
                            "source_table": item["source_table"],
                            "source_record_id": item["source_record_id"],
                            "error_code": item["error_code"],
                            "error_message": item["error_message"],
                            "raw_payload": None,
                            "created_at": now,
                        }
                        for item in fields["errors"]
                    ],
                )
            row.status = target
            row.total_rows = fields["total_rows"]
            row.valid_rows = fields["valid_rows"]
            row.error_rows = fields["error_rows"]
            row.error_summary = _error_summary(fields["errors"])
            row.lease_owner = None
            row.lease_expires_at = None
            row.finished_at = now if target == "validation_failed" else None
            session.commit()
            return target

    def complete_publish(
        self, job_id: UUID, worker_id: str, *, published_batch_id: UUID
    ) -> str:
        with self._worker_session() as session:
            row = self._leased_job(
                session, job_id=job_id, worker_id=worker_id, expected="publishing"
            )
            validate_job_transition(row.status, "published")
            now = utc_now()
            row.status = "published"
            row.published_batch_id = published_batch_id
            row.lease_owner = None
            row.lease_expires_at = None
            row.finished_at = now
            session.commit()
            return "published"

    def fail_job(
        self, job_id: UUID, worker_id: str, *, retryable: bool, error_code: str
    ) -> str:
        with self._worker_session() as session:
            row = self._leased_job(session, job_id=job_id, worker_id=worker_id)
            current = row.status
            if current not in WORKING_JOB_STATUSES:
                raise ImportJobStateConflictError(current, "failed")
            now = utc_now()
            if retryable:
                backoff = RETRY_BACKOFF_SECONDS[
                    min(max(row.attempt_count - 1, 0), len(RETRY_BACKOFF_SECONDS) - 1)
                ]
                row.available_at = now + timedelta(seconds=backoff)
                target = current
            else:
                target = (
                    "validation_failed" if current == "validating" else "publish_failed"
                )
                validate_job_transition(current, target)
                row.status = target
                row.finished_at = now
            row.lease_owner = None
            row.lease_expires_at = None
            session.commit()
            return target

    @staticmethod
    def _ensure_error_batch(
        session: Session, row: DataImportJob, fields: dict[str, Any], now: datetime
    ) -> None:
        """懒创建任务自己的批次行（``batch_id = job_id``）。

        ``data_load_errors.batch_id`` 有指向 ``data_load_batches`` 的外键，
        因此校验错误行必须归属一个批次行。导入任务与快照批次一一对应，任务
        自身的 ``job_id`` 就是它的批次标识，这样 ``/imports/{job_id}/errors``
        与 ``/batches/{batch_id}/errors`` 指向同一批行。
        """
        exists = session.scalar(
            select(DataLoadBatch.batch_id).where(DataLoadBatch.batch_id == row.job_id)
        )
        if exists is not None:
            return
        session.execute(
            pg_insert(DataLoadBatch)
            .values(
                batch_id=row.job_id,
                tenant_id=row.tenant_id,
                source_system=row.source_system,
                source_file=_safe_source_name(row.source_object_key),
                source_snapshot_hash=row.snapshot_hash,
                calculation_rule_version=row.calculation_rule_version,
                status="failed",
                total_rows=fields["total_rows"],
                valid_rows=fields["valid_rows"],
                error_rows=fields["error_rows"],
                error_summary=None,
                started_at=row.started_at or now,
                finished_at=now,
                created_at=row.created_at,
            )
            .on_conflict_do_nothing(index_elements=[DataLoadBatch.batch_id])
        )
        created = session.scalar(
            select(DataLoadBatch.batch_id).where(DataLoadBatch.batch_id == row.job_id)
        )
        if created is None:
            raise RuntimeError("无法为导入任务创建校验错误批次行。")

    def _leased_job(
        self,
        session: Session,
        *,
        job_id: UUID,
        worker_id: str,
        expected: str | None = None,
    ) -> DataImportJob:
        row = session.scalar(
            select(DataImportJob)
            .where(
                DataImportJob.job_id == job_id,
                DataImportJob.lease_owner == worker_id,
            )
            .with_for_update()
        )
        if row is None:
            raise ImportJobLeaseLostError(job_id)
        if expected is not None and row.status != expected:
            raise ImportJobLeaseLostError(job_id, status=row.status)
        return row

    # ----------------------------------------------------------- audit trail

    def append_audit_event(
        self,
        *,
        tenant_id: str,
        actor_principal_id: str,
        action: str,
        target_type: str,
        target_id: str,
        outcome: str,
        job_id: UUID | None = None,
        batch_id: UUID | None = None,
        request_id: str | None = None,
        detail: Mapping[str, Any] | None = None,
    ) -> None:
        with self._session(tenant_id) as session:
            session.execute(
                GovernanceAuditEvent.__table__.insert(),
                [
                    {
                        "tenant_id": tenant_id,
                        "job_id": job_id,
                        "batch_id": batch_id,
                        "actor_principal_id": actor_principal_id,
                        "action": action,
                        "target_type": target_type,
                        "target_id": target_id,
                        "outcome": outcome,
                        "request_id": request_id,
                        "detail": _safe_audit_detail(detail),
                        "created_at": utc_now(),
                    }
                ],
            )
            session.commit()


def _job_dict(row: DataImportJob) -> dict[str, Any]:
    return {
        "job_id": row.job_id,
        "tenant_id": row.tenant_id,
        "principal_id": row.principal_id,
        "principal_roles": list(row.principal_roles or []),
        "source_system": row.source_system,
        "source_file_name": _safe_source_name(row.source_object_key),
        "source_object_key": row.source_object_key,
        "source_object_size": row.source_object_size,
        "source_object_sha256": row.source_object_sha256,
        "snapshot_hash": row.snapshot_hash,
        "calculation_rule_version": row.calculation_rule_version,
        "idempotency_key": row.idempotency_key,
        "status": row.status,
        "total_rows": row.total_rows,
        "valid_rows": row.valid_rows,
        "error_rows": row.error_rows,
        "error_summary": row.error_summary,
        "decision": row.decision,
        "decision_reason": row.decision_reason,
        "decided_by": row.decided_by,
        "decided_at": row.decided_at,
        "published_batch_id": row.published_batch_id,
        "attempt_count": row.attempt_count,
        "available_at": row.available_at,
        "started_at": row.started_at,
        "finished_at": row.finished_at,
        "created_at": row.created_at,
    }


def _safe_source_name(object_key: str | None) -> str | None:
    if not object_key:
        return None
    return PurePath(PureWindowsPath(object_key).name).name[:128] or None


def _validation_fields(outcome: Any) -> dict[str, Any]:
    """把 ``validate_payload`` 的结果归一化为计数 + 错误列表。"""
    if isinstance(outcome, str):
        if outcome not in {"valid", "invalid"}:
            raise ValueError("校验结果字符串必须是 valid 或 invalid")
        return {
            "valid": outcome == "valid",
            "total_rows": 0,
            "valid_rows": 0,
            "error_rows": 0,
            "errors": (),
        }
    value = outcome.as_dict() if hasattr(outcome, "as_dict") else dict(outcome)
    errors = tuple(
        {
            "source_table": str(item.get("source_table") or "")[:64],
            "source_record_id": (
                str(item["source_record_id"])[:128]
                if item.get("source_record_id") is not None
                else None
            ),
            "error_code": str(item.get("error_code") or "")[:64],
            "error_message": str(item.get("error_message") or "")[
                :STORE_ERROR_MESSAGE_LIMIT
            ],
        }
        for item in value.get("errors") or ()
    )
    status = value.get("status")
    if status in {"valid", "invalid"}:
        valid = status == "valid"
    elif "valid" in value:
        valid = bool(value["valid"])
    else:
        valid = not errors and int(value.get("error_rows") or 0) == 0
    return {
        "valid": valid,
        "total_rows": int(value.get("total_rows") or 0),
        "valid_rows": int(value.get("valid_rows") or 0),
        "error_rows": int(value.get("error_rows") or 0),
        "errors": errors,
    }


def _error_summary(errors: tuple[dict[str, Any], ...]) -> dict[str, int] | None:
    if not errors:
        return None
    summary: dict[str, int] = {}
    for item in errors:
        code = item["error_code"]
        summary[code] = summary.get(code, 0) + 1
    return summary


def _safe_audit_detail(detail: Mapping[str, Any] | None) -> dict[str, Any]:
    """只保留计数/错误码/状态等非敏感字段，其余一律丢弃。"""
    if not detail:
        return {}
    safe: dict[str, Any] = {}
    for key, value in detail.items():
        if key not in AUDIT_DETAIL_ALLOWED_KEYS:
            continue
        if isinstance(value, UUID):
            safe[key] = str(value)
        elif isinstance(value, (str, int, float, bool)) or value is None:
            safe[key] = value
        elif isinstance(value, (list, tuple, dict)):
            continue
        else:
            safe[key] = str(value)
    return safe
