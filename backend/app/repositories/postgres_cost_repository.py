from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.db.engine import get_session_factory
from app.db.models import CostEvent, CostEventInput, CostRecord, DataLoadBatch, Part
from app.domain.authorization import ExecutionContext
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

    def _published_batch_ids(self, tenant_id: str):
        return select(DataLoadBatch.batch_id).where(
            DataLoadBatch.tenant_id == tenant_id,
            DataLoadBatch.status == "published",
        )

    def list_parts(self, execution_context: ExecutionContext) -> list[dict[str, Any]]:
        execution_context.require_capability("cost_calculation")
        owner = execution_context.principal
        with self._session() as session:
            rows = session.scalars(
                select(Part)
                .where(
                    Part.tenant_id == owner.tenant_id,
                    Part.batch_id.in_(self._published_batch_ids(owner.tenant_id)),
                )
                .order_by(Part.part_id)
            ).all()
        allowed = getattr(execution_context.data_scope, "allowed_part_ids", ())
        if allowed:
            rows = [row for row in rows if row.part_id in allowed]
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

    def list_finished_batch_sources(
        self, period: str, execution_context: ExecutionContext
    ) -> list[dict[str, Any]]:
        execution_context.require_capability("cost_calculation")
        owner = execution_context.principal
        allowed_parts = set(execution_context.data_scope.allowed_part_ids)
        if period and not self._period_is_allowed(execution_context, period):
            raise PermissionError(f"期间不在授权数据范围内：{period}")
        with self._session() as session:
            statement = (
                select(CostEvent)
                .join(
                    Part,
                    (Part.tenant_id == CostEvent.tenant_id)
                    & (Part.part_id == CostEvent.part_id)
                    & (Part.batch_id == CostEvent.batch_id),
                )
                .where(
                    CostEvent.tenant_id == owner.tenant_id,
                    CostEvent.event_type == "process",
                    Part.part_type == "finished_good",
                    CostEvent.batch_id.in_(self._published_batch_ids(owner.tenant_id)),
                )
            )
            if period:
                statement = statement.where(CostEvent.period == period)
            events = session.scalars(
                statement.order_by(CostEvent.completion_time, CostEvent.event_id)
            ).all()
            edges = session.scalars(
                select(CostEventInput).where(
                    CostEventInput.tenant_id == owner.tenant_id,
                    CostEventInput.batch_id.in_(
                        self._published_batch_ids(owner.tenant_id)
                    ),
                )
            ).all()
            roots = [
                event
                for event in events
                if event.event_id
                not in {
                    edge.source_event_id
                    for edge in edges
                    if edge.batch_id == event.batch_id
                }
                and (not allowed_parts or event.part_id in allowed_parts)
            ]
            sources = [
                self._source(session, event, execution_context) for event in roots
            ]
            return [source for source in sources if source]

    def load_finished_batch_source(
        self, finished_batch_id: str, execution_context: ExecutionContext
    ) -> dict[str, Any] | None:
        execution_context.require_capability("cost_calculation")
        owner = execution_context.principal
        with self._session() as session:
            event = session.scalar(
                select(CostEvent)
                .join(
                    Part,
                    (Part.tenant_id == CostEvent.tenant_id)
                    & (Part.part_id == CostEvent.part_id)
                    & (Part.batch_id == CostEvent.batch_id),
                )
                .where(
                    CostEvent.tenant_id == owner.tenant_id,
                    CostEvent.output_batch_id == finished_batch_id,
                    CostEvent.event_type == "process",
                    Part.part_type == "finished_good",
                    CostEvent.batch_id.in_(self._published_batch_ids(owner.tenant_id)),
                )
            )
            if event is None:
                return None
            # A finished batch is a graph root: its output cannot be consumed
            # by a later event in the same published snapshot.
            downstream = session.scalar(
                select(CostEventInput.input_id).where(
                    CostEventInput.tenant_id == owner.tenant_id,
                    CostEventInput.source_event_id == event.event_id,
                    CostEventInput.batch_id == event.batch_id,
                )
            )
            if downstream is not None:
                return None
            source = self._source(session, event, execution_context)
            return source or None

    def list_part_period_sources(
        self, part_id: str, period: str, execution_context: ExecutionContext
    ) -> list[dict[str, Any]]:
        execution_context.require_cost_scope(part_id, period)
        return [
            source
            for source in self.list_finished_batch_sources(period, execution_context)
            if next(
                (
                    e
                    for e in source["events"]
                    if e["event_id"] == source["root_event_id"]
                ),
                {},
            ).get("part_id")
            == part_id
        ]

    def load_cost_snapshot(
        self, execution_context: ExecutionContext
    ) -> dict[str, Any] | None:
        sources = self.list_finished_batch_sources("", execution_context)
        return sources[0] if sources else None

    def _source(
        self, session: Session, root: CostEvent | None, context: ExecutionContext
    ) -> dict[str, Any]:
        if root is None:
            return {}
        owner = context.principal
        allowed_parts = set(context.data_scope.allowed_part_ids)
        if allowed_parts and root.part_id not in allowed_parts:
            return {}
        if not self._period_is_allowed(context, root.period):
            return {}
        # Every row in a trace must come from the root's exact published
        # snapshot.  Filtering by the root batch (rather than a tenant-wide
        # "published" subquery) prevents cross-snapshot joins if malformed
        # data ever leaves more than one published row visible.
        batch_id = root.batch_id
        events = session.scalars(
            select(CostEvent)
            .where(
                CostEvent.tenant_id == owner.tenant_id,
                CostEvent.batch_id == batch_id,
            )
            .order_by(CostEvent.completion_time, CostEvent.event_id)
        ).all()
        event_map = {event.event_id: event for event in events}
        edges = session.scalars(
            select(CostEventInput)
            .where(
                CostEventInput.tenant_id == owner.tenant_id,
                CostEventInput.batch_id == batch_id,
            )
            .order_by(CostEventInput.input_id)
        ).all()
        needed = {root.event_id}
        changed = True
        while changed:
            changed = False
            for edge in edges:
                if edge.event_id in needed and edge.source_event_id not in needed:
                    needed.add(edge.source_event_id)
                    changed = True
        # A malformed/partially published graph must not leak a dangling edge
        # or raise an internal KeyError to the API caller.
        if any(event_id not in event_map for event_id in needed):
            return {}
        # Preserve the explicit database order.  Feeding a set iteration into
        # the topological sorter would make node/edge order (and therefore
        # serialized trace hashes) depend on Python hash randomization.
        selected_events = [event for event in events if event.event_id in needed]
        if any(
            (allowed_parts and event.part_id not in allowed_parts)
            or not self._period_is_allowed(context, event.period)
            for event in selected_events
        ):
            return {}
        selected_edges = [
            edge
            for edge in edges
            if edge.event_id in needed and edge.source_event_id in needed
        ]
        records = session.scalars(
            select(CostRecord)
            .where(
                CostRecord.tenant_id == owner.tenant_id,
                CostRecord.event_id.in_(needed),
                CostRecord.batch_id == batch_id,
            )
            .order_by(CostRecord.event_id, CostRecord.cost_record_id)
        ).all()
        part_ids = {event.part_id for event in selected_events}
        parts = session.scalars(
            select(Part)
            .where(
                Part.tenant_id == owner.tenant_id,
                Part.part_id.in_(part_ids),
                Part.batch_id == batch_id,
            )
            .order_by(Part.part_id)
        ).all()
        if len({part.part_id for part in parts}) != len(part_ids):
            return {}
        return {
            "root_event_id": root.event_id,
            "parts": [_part_dict(item) for item in parts],
            "events": [_event_dict(item) for item in selected_events],
            "inputs": [_input_dict(item) for item in selected_edges],
            "records": [_record_dict(item) for item in records],
        }

    @staticmethod
    def _period_is_allowed(context: ExecutionContext, period: str) -> bool:
        start = context.data_scope.allowed_period_start
        end = context.data_scope.allowed_period_end
        return not ((start and period < start) or (end and period > end))


def _part_dict(row: Part) -> dict[str, Any]:
    return {
        "part_id": row.part_id,
        "part_number": row.part_number,
        "part_description": row.part_description,
        "part_type": row.part_type,
        "product_family": row.product_family,
        "unit": row.unit,
    }


def _event_dict(row: CostEvent) -> dict[str, Any]:
    return {
        key: getattr(row, key)
        for key in (
            "event_id",
            "event_type",
            "output_batch_id",
            "part_id",
            "period",
            "cost_center_code",
            "cost_center_name",
            "work_order_number",
            "lot_number",
            "process_code",
            "process_name",
            "completion_time",
            "qualified_quantity",
            "defective_quantity",
            "unit",
            "machine_hours",
            "labor_hours",
        )
    }


def _input_dict(row: CostEventInput) -> dict[str, Any]:
    return {
        key: getattr(row, key)
        for key in (
            "input_id",
            "event_id",
            "source_event_id",
            "consumed_quantity",
            "unit",
        )
    }


def _record_dict(row: CostRecord) -> dict[str, Any]:
    return {
        key: getattr(row, key)
        for key in (
            "cost_record_id",
            "event_id",
            "cost_code",
            "amount",
            "currency",
            "incurred_at",
            "source_system",
            "source_document_no",
            "source_document_line",
            "source_record_id",
            "raw_payload",
        )
    }
