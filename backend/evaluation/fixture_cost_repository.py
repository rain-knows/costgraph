from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.domain.authorization import ExecutionContext
from app.domain.cost import as_decimal
from app.services.cost_calculation_service import (
    aggregate_finished_batch_costs,
    calculate_finished_batch_cost,
)
from app.services.cost_source_builder import build_finished_batch_sources

SAMPLE_ROOT = Path(__file__).resolve().parents[2] / "data" / "samples"


class CostFixtureRepository:
    """In-memory repository for the canonical v2 sample snapshot."""

    def __init__(self, sample_root: Path = SAMPLE_ROOT) -> None:
        self.parts = _load(sample_root / "parts.json")
        self.events = _load(sample_root / "cost_events.json")
        self.inputs = _load(sample_root / "cost_event_inputs.json")
        self.records = _load(sample_root / "cost_records.json")
        for row in self.parts + self.events + self.inputs + self.records:
            row.setdefault("source_system", "costgraph_samples")
            row.setdefault("raw_payload", dict(row))
        for row in self.records:
            row.setdefault("source_document_line", None)
        self.projections = [
            calculate_finished_batch_cost(source)
            for source in build_finished_batch_sources(
                self.parts,
                self.events,
                self.inputs,
                self.records,
                source_system="costgraph_samples",
            )
        ]

    def _allowed_projections(
        self, execution_context: ExecutionContext
    ) -> list[dict[str, Any]]:
        allowed = set(execution_context.data_scope.allowed_part_ids)
        return [
            dict(item)
            for item in self.projections
            if not allowed or item["part"]["part_id"] in allowed
        ]

    def overview_projection(
        self, period: str, execution_context: ExecutionContext
    ) -> dict[str, Any]:
        if not self._period_allowed(execution_context, period):
            raise PermissionError(f"期间不在授权数据范围内：{period}")
        items = [
            item
            for item in self._allowed_projections(execution_context)
            if item["period"] == period
        ]
        aggregate = aggregate_finished_batch_costs(items)
        return {
            **aggregate,
            "batch_count": len(items),
            "part_count": len({item["part"]["part_id"] for item in items}),
            "total_cost": aggregate["manufacturing_cost"]
            + aggregate["post_manufacturing_cost"],
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
        if not self._period_allowed(execution_context, period):
            raise PermissionError(f"期间不在授权数据范围内：{period}")
        items = [
            item
            for item in self._allowed_projections(execution_context)
            if item["period"] == period
        ]
        normalized = (query or "").strip().casefold()
        if normalized:
            items = [item for item in items if normalized in str(item).casefold()]
        if cost_center_code:
            items = [
                item
                for item in items
                if item.get("cost_center_code") == cost_center_code
            ]
        reverse = sort.endswith("_desc")
        if sort.startswith("unit_cost"):
            key = lambda item: (
                as_decimal(item["variable_fixed_view"]["total_cost_2"]["unit_cost"]),
                item["finished_batch_id"],
            )
        else:
            key = lambda item: (
                str(item["completion_time"]),
                item["finished_batch_id"],
            )
        items.sort(key=key, reverse=reverse)
        total = len(items)
        start = (page - 1) * page_size
        return [
            {key: value for key, value in item.items() if key != "trace"}
            for item in items[start : start + page_size]
        ], total

    def load_finished_batch_projection(
        self, finished_batch_id: str, execution_context: ExecutionContext
    ) -> dict[str, Any] | None:
        return next(
            (
                item
                for item in self._allowed_projections(execution_context)
                if item["finished_batch_id"] == finished_batch_id
                and self._period_allowed(execution_context, item["period"])
            ),
            None,
        )

    def list_part_period_projections(
        self, part_id: str, period: str, execution_context: ExecutionContext
    ) -> list[dict[str, Any]]:
        execution_context.require_cost_scope(part_id, period)
        return [
            item
            for item in self._allowed_projections(execution_context)
            if item["part"]["part_id"] == part_id and item["period"] == period
        ]

    def list_parts(self, execution_context: ExecutionContext) -> list[dict[str, Any]]:
        execution_context.require_capability("cost_calculation")
        allowed = getattr(execution_context.data_scope, "allowed_part_ids", ())
        rows = (
            self.parts
            if not allowed
            else [row for row in self.parts if row["part_id"] in allowed]
        )
        return [dict(row) for row in rows]

    def find_parts(
        self, part_text: str, execution_context: ExecutionContext
    ) -> list[dict[str, Any]]:
        normalized = part_text.replace(" ", "").casefold()
        if not normalized:
            return []
        rows = self.list_parts(execution_context)
        exact = [
            row
            for row in rows
            if normalized
            in {
                str(row["part_id"]).replace(" ", "").casefold(),
                str(row["part_number"]).replace(" ", "").casefold(),
            }
        ]
        partial = [
            row
            for row in rows
            if normalized
            in " ".join(
                str(row.get(key, ""))
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

    @staticmethod
    def _period_allowed(execution_context: ExecutionContext, period: str) -> bool:
        start = execution_context.data_scope.allowed_period_start
        end = execution_context.data_scope.allowed_period_end
        return not ((start and period < start) or (end and period > end))


def _load(path: Path) -> list[dict[str, Any]]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, list):
        raise TypeError(f"样例必须是数组：{path}")
    return [dict(item) for item in value]
