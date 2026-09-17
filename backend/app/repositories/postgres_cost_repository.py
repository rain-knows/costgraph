from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from sqlalchemy import and_, asc, cast, desc, distinct, func, select, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Session

from app.db.engine import get_session_factory
from app.db.models import DataLoadBatch, FinishedBatchCostProjection, Part
from app.domain.authorization import ExecutionContext
from app.domain.cost import CALCULATION_RULE_VERSION
from app.domain.errors import CostProjectionUnavailableError
from app.settings import get_settings


class PostgresCostRepository:
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

    @staticmethod
    def _published_batch(tenant_id: str):
        return (
            select(DataLoadBatch.batch_id, DataLoadBatch.calculation_rule_version)
            .where(
                DataLoadBatch.tenant_id == tenant_id,
                DataLoadBatch.status == "published",
            )
            .limit(1)
            .cte("published_batch")
        )

    def list_parts(self, execution_context: ExecutionContext) -> list[dict[str, Any]]:
        execution_context.require_capability("cost_calculation")
        owner = execution_context.principal
        batch = self._published_batch(owner.tenant_id)
        statement = (
            select(Part)
            .join(batch, Part.batch_id == batch.c.batch_id)
            .where(Part.tenant_id == owner.tenant_id)
            .order_by(Part.part_id)
        )
        allowed = tuple(execution_context.data_scope.allowed_part_ids)
        if allowed:
            statement = statement.where(Part.part_id.in_(allowed))
        with self._session() as session:
            rows = session.scalars(statement).all()
        return [_part_dict(row) for row in rows]

    def find_parts(
        self, part_text: str, execution_context: ExecutionContext
    ) -> list[dict[str, Any]]:
        normalized = part_text.replace(" ", "").casefold()
        rows = self.list_parts(execution_context)
        if not normalized:
            return []
        exact = [
            row
            for row in rows
            if normalized
            in {
                row["part_id"].replace(" ", "").casefold(),
                row["part_number"].replace(" ", "").casefold(),
            }
        ]
        partial = [
            row
            for row in rows
            if normalized
            in " ".join(
                str(row.get(key) or "")
                for key in (
                    "part_id",
                    "part_number",
                    "part_description",
                    "product_family",
                )
            )
            .replace(" ", "")
            .casefold()
        ]
        return exact or partial

    def overview_projection(
        self, period: str, execution_context: ExecutionContext
    ) -> dict[str, Any]:
        execution_context.require_capability("cost_calculation")
        self._require_period(execution_context, period)
        owner = execution_context.principal
        batch = self._published_batch(owner.tenant_id)
        projection = FinishedBatchCostProjection
        join_condition = and_(
            projection.tenant_id == owner.tenant_id,
            projection.batch_id == batch.c.batch_id,
            projection.period == period,
        )
        allowed = tuple(execution_context.data_scope.allowed_part_ids)
        if allowed:
            join_condition = and_(join_condition, projection.part_id.in_(allowed))
        statement = (
            select(
                batch.c.calculation_rule_version,
                func.count(projection.event_id),
                func.count(distinct(projection.part_id)),
                func.coalesce(func.sum(projection.completed_quantity), 0),
                func.coalesce(func.sum(projection.qualified_quantity), 0),
                func.coalesce(func.sum(projection.defective_quantity), 0),
                func.coalesce(func.sum(projection.manufacturing_cost), 0),
                func.coalesce(func.sum(projection.post_manufacturing_cost), 0),
                func.coalesce(func.sum(projection.total_cost), 0),
            )
            .select_from(batch)
            .outerjoin(projection, join_condition)
            .group_by(batch.c.calculation_rule_version)
        )
        with self._session() as session:
            row = session.execute(statement).one_or_none()
        self._require_projection(row[0] if row else None)
        return {
            "batch_count": row[1],
            "part_count": row[2],
            "completed_quantity": row[3],
            "qualified_quantity": row[4],
            "defective_quantity": row[5],
            "manufacturing_cost": row[6],
            "post_manufacturing_cost": row[7],
            "total_cost": row[8],
        }

    def list_finished_batch_projections(
        self,
        *,
        period: str,
        query: str | None,
        cost_center_code: str | None,
        sort: str,
        page: int,
        page_size: int,
        execution_context: ExecutionContext,
    ) -> tuple[list[dict[str, Any]], int]:
        execution_context.require_capability("cost_calculation")
        self._require_period(execution_context, period)
        owner = execution_context.principal
        batch = self._published_batch(owner.tenant_id)
        projection = FinishedBatchCostProjection
        candidates = (
            select(
                projection.summary_json.label("summary_json"),
                projection.finished_batch_id.label("finished_batch_id"),
                projection.completion_time.label("completion_time"),
                projection.total_unit_cost.label("total_unit_cost"),
            )
            .join(batch, projection.batch_id == batch.c.batch_id)
            .where(projection.tenant_id == owner.tenant_id, projection.period == period)
        )
        allowed = tuple(execution_context.data_scope.allowed_part_ids)
        if allowed:
            candidates = candidates.where(projection.part_id.in_(allowed))
        normalized = (query or "").strip().casefold()
        if normalized:
            candidates = candidates.where(projection.search_text.contains(normalized))
        if cost_center_code:
            candidates = candidates.where(
                projection.cost_center_code == cost_center_code
            )
        candidates_cte = candidates.cte("candidates")
        reverse = sort.endswith("_desc")
        primary = (
            candidates_cte.c.total_unit_cost
            if sort.startswith("unit_cost")
            else candidates_cte.c.completion_time
        )
        direction = desc if reverse else asc
        page_cte = (
            select(candidates_cte.c.summary_json)
            .order_by(direction(primary), direction(candidates_cte.c.finished_batch_id))
            .offset((page - 1) * page_size)
            .limit(page_size)
            .cte("page_rows")
        )
        statement = select(
            select(batch.c.calculation_rule_version).scalar_subquery(),
            select(func.count()).select_from(candidates_cte).scalar_subquery(),
            func.coalesce(
                select(func.jsonb_agg(page_cte.c.summary_json))
                .select_from(page_cte)
                .scalar_subquery(),
                cast([], JSONB),
            ),
        )
        with self._session() as session:
            rule_version, total, items = session.execute(statement).one()
        self._require_projection(rule_version)
        return [dict(item) for item in items], int(total)

    def load_finished_batch_projection(
        self, finished_batch_id: str, execution_context: ExecutionContext
    ) -> dict[str, Any] | None:
        execution_context.require_capability("cost_calculation")
        owner = execution_context.principal
        batch = self._published_batch(owner.tenant_id)
        projection = FinishedBatchCostProjection
        join_condition = and_(
            projection.tenant_id == owner.tenant_id,
            projection.batch_id == batch.c.batch_id,
            projection.finished_batch_id == finished_batch_id,
        )
        allowed = tuple(execution_context.data_scope.allowed_part_ids)
        if allowed:
            join_condition = and_(join_condition, projection.part_id.in_(allowed))
        statement = (
            select(
                batch.c.calculation_rule_version,
                projection.period,
                projection.summary_json,
                projection.trace_json,
            )
            .select_from(batch)
            .outerjoin(projection, join_condition)
        )
        with self._session() as session:
            row = session.execute(statement).one_or_none()
        self._require_projection(row[0] if row else None)
        if row[1] is None or not self._period_allowed(execution_context, row[1]):
            return None
        return {**dict(row[2]), "trace": dict(row[3])}

    def list_part_period_projections(
        self, part_id: str, period: str, execution_context: ExecutionContext
    ) -> list[dict[str, Any]]:
        execution_context.require_cost_scope(part_id, period)
        owner = execution_context.principal
        batch = self._published_batch(owner.tenant_id)
        projection = FinishedBatchCostProjection
        rows_cte = (
            select(projection.summary_json, projection.trace_json)
            .join(batch, projection.batch_id == batch.c.batch_id)
            .where(
                projection.tenant_id == owner.tenant_id,
                projection.part_id == part_id,
                projection.period == period,
            )
            .order_by(projection.completion_time, projection.event_id)
            .cte("part_period_rows")
        )
        statement = select(
            select(batch.c.calculation_rule_version).scalar_subquery(),
            func.coalesce(
                select(
                    func.jsonb_agg(
                        func.jsonb_build_object(
                            "summary",
                            rows_cte.c.summary_json,
                            "trace",
                            rows_cte.c.trace_json,
                        )
                    )
                )
                .select_from(rows_cte)
                .scalar_subquery(),
                cast([], JSONB),
            ),
        )
        with self._session() as session:
            rule_version, rows = session.execute(statement).one()
        self._require_projection(rule_version)
        return [{**dict(row["summary"]), "trace": dict(row["trace"])} for row in rows]

    @staticmethod
    def _require_projection(rule_version: str | None) -> None:
        if rule_version != CALCULATION_RULE_VERSION:
            raise CostProjectionUnavailableError(rule_version)

    @staticmethod
    def _period_allowed(context: ExecutionContext, period: str) -> bool:
        start = context.data_scope.allowed_period_start
        end = context.data_scope.allowed_period_end
        return not ((start and period < start) or (end and period > end))

    @classmethod
    def _require_period(cls, context: ExecutionContext, period: str) -> None:
        if not cls._period_allowed(context, period):
            raise PermissionError(f"期间不在授权数据范围内：{period}")


def _part_dict(row: Part) -> dict[str, Any]:
    return {
        "part_id": row.part_id,
        "part_number": row.part_number,
        "part_description": row.part_description,
        "part_type": row.part_type,
        "product_family": row.product_family,
        "unit": row.unit,
    }
