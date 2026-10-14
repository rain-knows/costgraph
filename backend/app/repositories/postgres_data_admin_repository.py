from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import PurePath, PureWindowsPath
from typing import Any
from uuid import UUID

from sqlalchemy import and_, func, select, text
from sqlalchemy.orm import Session

from app.db.engine import get_session_factory
from app.db.models import (
    CostEvent,
    CostEventInput,
    CostRecord,
    DataLoadBatch,
    DataLoadError,
    FinishedBatchCostProjection,
    Part,
)
from app.domain.authorization import ExecutionContext
from app.domain.cost import CALCULATION_RULE_VERSION
from app.domain.errors import CostProjectionUnavailableError
from app.settings import get_settings


class PostgresDataAdminRepository:
    def __init__(self, session_factory=None) -> None:
        self._session_factory = session_factory or get_session_factory()

    @contextmanager
    def _session(self) -> Iterator[Session]:
        session = self._session_factory()
        try:
            timeout_ms = max(1, int(get_settings().agent_tool_timeout_seconds * 1000))
            session.execute(text(f"SET LOCAL statement_timeout = {timeout_ms}"))
            yield session
        finally:
            session.close()

    def list_batches(
        self,
        context: ExecutionContext,
        *,
        status: str | None,
        page: int,
        page_size: int,
    ) -> tuple[list[dict[str, Any]], int]:
        owner = context.principal
        filters = [DataLoadBatch.tenant_id == owner.tenant_id]
        if status:
            filters.append(DataLoadBatch.status == status)
        with self._session() as session:
            total = (
                session.scalar(
                    select(func.count()).select_from(DataLoadBatch).where(*filters)
                )
                or 0
            )
            rows = session.scalars(
                select(DataLoadBatch)
                .where(*filters)
                .order_by(
                    DataLoadBatch.created_at.desc(), DataLoadBatch.batch_id.desc()
                )
                .offset((page - 1) * page_size)
                .limit(page_size)
            ).all()
        return [_batch_dict(row) for row in rows], int(total)

    def get_batch(
        self, context: ExecutionContext, batch_id: UUID
    ) -> dict[str, Any] | None:
        owner = context.principal
        with self._session() as session:
            batch = session.scalar(
                select(DataLoadBatch).where(
                    DataLoadBatch.tenant_id == owner.tenant_id,
                    DataLoadBatch.batch_id == batch_id,
                )
            )
            if batch is None:
                return None
            counts = session.execute(
                select(DataLoadError.error_code, func.count())
                .where(
                    DataLoadError.tenant_id == owner.tenant_id,
                    DataLoadError.batch_id == batch_id,
                )
                .group_by(DataLoadError.error_code)
                .order_by(DataLoadError.error_code)
            ).all()
        return {
            **_batch_dict(batch),
            "error_codes": [
                {"error_code": code, "count": count} for code, count in counts
            ],
        }

    def list_batch_errors(
        self, context: ExecutionContext, batch_id: UUID, *, page: int, page_size: int
    ) -> tuple[list[dict[str, Any]], int] | None:
        owner = context.principal
        batch_filter = (
            DataLoadBatch.tenant_id == owner.tenant_id,
            DataLoadBatch.batch_id == batch_id,
        )
        with self._session() as session:
            exists = session.scalar(select(DataLoadBatch.batch_id).where(*batch_filter))
            if exists is None:
                return None
            filters = (
                DataLoadError.tenant_id == owner.tenant_id,
                DataLoadError.batch_id == batch_id,
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

    def get_published_snapshot(self, context: ExecutionContext) -> dict[str, Any]:
        owner = context.principal
        with self._session() as session:
            batch = session.scalar(
                select(DataLoadBatch)
                .where(
                    DataLoadBatch.tenant_id == owner.tenant_id,
                    DataLoadBatch.status == "published",
                )
                .limit(1)
            )
            if batch is None:
                return {
                    "batch": None,
                    "fact_rows": {
                        "parts": 0,
                        "cost_events": 0,
                        "cost_event_inputs": 0,
                        "cost_records": 0,
                    },
                    "projection_rows": 0,
                    "part_count": 0,
                    "period_start": None,
                    "period_end": None,
                    "rule_version_matches": None,
                }
            tenant_id, batch_id = owner.tenant_id, batch.batch_id
            projection = FinishedBatchCostProjection
            integrity = session.execute(
                select(
                    func.count(projection.event_id),
                    func.count(projection.rule_version.distinct()),
                    func.min(projection.rule_version),
                    func.max(projection.rule_version),
                    func.count(projection.batch_id.distinct()),
                ).where(
                    projection.tenant_id == tenant_id,
                    projection.batch_id == batch_id,
                )
            ).one()
            projection_rows_all, rule_count, min_rule, max_rule, batch_count = integrity
            mismatched_event_rows = (
                session.scalar(
                    select(func.count())
                    .select_from(projection)
                    .join(
                        CostEvent,
                        and_(
                            CostEvent.tenant_id == projection.tenant_id,
                            CostEvent.event_id == projection.event_id,
                        ),
                    )
                    .where(
                        projection.tenant_id == tenant_id,
                        projection.batch_id == batch_id,
                        CostEvent.batch_id != batch_id,
                    )
                )
                or 0
            )
            if (
                batch.calculation_rule_version != CALCULATION_RULE_VERSION
                or projection_rows_all == 0
                or rule_count != 1
                or min_rule != CALCULATION_RULE_VERSION
                or max_rule != CALCULATION_RULE_VERSION
                or batch_count != 1
                or mismatched_event_rows > 0
            ):
                raise CostProjectionUnavailableError(batch.calculation_rule_version)

            scope = _projection_scope(projection, context)
            scoped_projection = and_(
                projection.tenant_id == tenant_id,
                projection.batch_id == batch_id,
                *scope,
            )
            fact_rows = {
                "parts": int(
                    session.scalar(
                        select(func.count())
                        .select_from(Part)
                        .where(
                            Part.tenant_id == tenant_id,
                            Part.batch_id == batch_id,
                            *_part_scope(Part, context),
                        )
                    )
                    or 0
                ),
                "cost_events": int(
                    session.scalar(
                        select(func.count())
                        .select_from(CostEvent)
                        .where(
                            CostEvent.tenant_id == tenant_id,
                            CostEvent.batch_id == batch_id,
                            *_event_scope(CostEvent, context),
                        )
                    )
                    or 0
                ),
                "cost_event_inputs": int(
                    session.scalar(
                        select(func.count())
                        .select_from(CostEventInput)
                        .join(
                            CostEvent,
                            and_(
                                CostEvent.tenant_id == CostEventInput.tenant_id,
                                CostEvent.event_id == CostEventInput.event_id,
                            ),
                        )
                        .where(
                            CostEventInput.tenant_id == tenant_id,
                            CostEventInput.batch_id == batch_id,
                            *_event_scope(CostEvent, context),
                        )
                    )
                    or 0
                ),
                "cost_records": int(
                    session.scalar(
                        select(func.count())
                        .select_from(CostRecord)
                        .join(
                            CostEvent,
                            and_(
                                CostEvent.tenant_id == CostRecord.tenant_id,
                                CostEvent.event_id == CostRecord.event_id,
                            ),
                        )
                        .where(
                            CostRecord.tenant_id == tenant_id,
                            CostRecord.batch_id == batch_id,
                            *_event_scope(CostEvent, context),
                        )
                    )
                    or 0
                ),
            }
            stats = session.execute(
                select(
                    func.count(projection.event_id.distinct()),
                    func.count(projection.part_id.distinct()),
                    func.min(projection.period),
                    func.max(projection.period),
                ).where(scoped_projection)
            ).one()
        return {
            "batch": _batch_dict(batch),
            "fact_rows": fact_rows,
            "projection_rows": int(stats[0]),
            "part_count": int(stats[1]),
            "period_start": stats[2],
            "period_end": stats[3],
            "rule_version_matches": True,
        }


def _batch_dict(row: DataLoadBatch) -> dict[str, Any]:
    return {
        "batch_id": row.batch_id,
        "status": row.status,
        "source_system": row.source_system,
        "source_file": _safe_source_file(row.source_file),
        "source_snapshot_hash": row.source_snapshot_hash,
        "calculation_rule_version": row.calculation_rule_version,
        "total_rows": row.total_rows,
        "valid_rows": row.valid_rows,
        "error_rows": row.error_rows,
        "started_at": row.started_at,
        "finished_at": row.finished_at,
        "created_at": row.created_at,
    }


def _projection_scope(model, context: ExecutionContext) -> list[Any]:
    scope: list[Any] = []
    allowed_parts = context.data_scope.allowed_part_ids
    if allowed_parts:
        scope.append(model.part_id.in_(allowed_parts))
    if context.data_scope.allowed_period_start:
        scope.append(model.period >= context.data_scope.allowed_period_start)
    if context.data_scope.allowed_period_end:
        scope.append(model.period <= context.data_scope.allowed_period_end)
    return scope


def _part_scope(model, context: ExecutionContext) -> list[Any]:
    if context.data_scope.allowed_part_ids:
        return [model.part_id.in_(context.data_scope.allowed_part_ids)]
    return []


def _event_scope(model, context: ExecutionContext) -> list[Any]:
    scope = _part_scope(model, context)
    if context.data_scope.allowed_period_start:
        scope.append(model.period >= context.data_scope.allowed_period_start)
    if context.data_scope.allowed_period_end:
        scope.append(model.period <= context.data_scope.allowed_period_end)
    return scope


def _safe_source_file(value: str | None) -> str | None:
    if not value:
        return None
    # A source location is useful only as a basename in the governance view.
    return PurePath(PureWindowsPath(value).name).name[:128]


def _safe_error_message(message: str) -> str:
    """Return a stable message without reflecting importer-controlled text."""
    # The detailed importer text may contain paths, credentials or raw input.
    # The error code and source record remain available as separate fields.
    return "导入校验错误详情已脱敏。"
