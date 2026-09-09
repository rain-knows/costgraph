from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.domain.authorization import ExecutionContext

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

    def _allowed_events(
        self, execution_context: ExecutionContext
    ) -> list[dict[str, Any]]:
        allowed_parts = set(execution_context.data_scope.allowed_part_ids)
        return [
            dict(row)
            for row in self.events
            if not allowed_parts or row["part_id"] in allowed_parts
        ]

    @staticmethod
    def _period_allowed(execution_context: ExecutionContext, period: str) -> bool:
        start = execution_context.data_scope.allowed_period_start
        end = execution_context.data_scope.allowed_period_end
        return not ((start and period < start) or (end and period > end))

    def _source_for_root(
        self, root: dict[str, Any], execution_context: ExecutionContext
    ) -> dict[str, Any]:
        events = [dict(row) for row in self.events]
        root_id = str(root["event_id"])
        needed = {root_id}
        changed = True
        while changed:
            changed = False
            for edge in self.inputs:
                if (
                    str(edge["event_id"]) in needed
                    and str(edge["source_event_id"]) not in needed
                ):
                    needed.add(str(edge["source_event_id"]))
                    changed = True
        sub_events = [row for row in events if str(row["event_id"]) in needed]
        if {str(row["event_id"]) for row in sub_events} != needed:
            return {}
        allowed_parts = set(execution_context.data_scope.allowed_part_ids)
        if any(
            (allowed_parts and row["part_id"] not in allowed_parts)
            or not self._period_allowed(execution_context, str(row["period"]))
            for row in sub_events
        ):
            return {}
        sub_inputs = [
            dict(row)
            for row in self.inputs
            if str(row["event_id"]) in needed and str(row["source_event_id"]) in needed
        ]
        sub_records = [
            dict(row) for row in self.records if str(row["event_id"]) in needed
        ]
        part_ids = {str(row["part_id"]) for row in sub_events}
        parts = [dict(row) for row in self.parts if str(row["part_id"]) in part_ids]
        if {str(row["part_id"]) for row in parts} != part_ids:
            return {}
        return {
            "root_event_id": root_id,
            "parts": parts,
            "events": sorted(sub_events, key=lambda row: str(row["event_id"])),
            "inputs": sorted(sub_inputs, key=lambda row: str(row["input_id"])),
            "records": sorted(sub_records, key=lambda row: str(row["cost_record_id"])),
        }

    def load_cost_snapshot(
        self, execution_context: ExecutionContext
    ) -> dict[str, Any] | None:
        events = self._allowed_events(execution_context)
        return {
            "parts": [dict(item) for item in self.parts],
            "events": events,
            "inputs": [
                dict(item)
                for item in self.inputs
                if item["event_id"] in {e["event_id"] for e in events}
            ],
            "records": [
                dict(item)
                for item in self.records
                if item["event_id"] in {e["event_id"] for e in events}
            ],
        }

    def list_finished_batch_sources(
        self, period: str, execution_context: ExecutionContext
    ) -> list[dict[str, Any]]:
        execution_context.require_capability("cost_calculation")
        if period and not self._period_allowed(execution_context, period):
            raise PermissionError(f"期间不在授权数据范围内：{period}")
        finished_part_ids = {
            str(part["part_id"])
            for part in self.parts
            if part.get("part_type") == "finished_good"
        }
        events = [
            row
            for row in self.events
            if (not period or row["period"] == period)
            and row["event_type"] == "process"
            and str(row["part_id"]) in finished_part_ids
            and (
                not execution_context.data_scope.allowed_part_ids
                or row["part_id"] in execution_context.data_scope.allowed_part_ids
            )
            and not any(
                edge["source_event_id"] == row["event_id"] for edge in self.inputs
            )
        ]
        return [
            source
            for row in events
            if (source := self._source_for_root(row, execution_context))
        ]

    def load_finished_batch_source(
        self, finished_batch_id: str, execution_context: ExecutionContext
    ) -> dict[str, Any] | None:
        execution_context.require_capability("cost_calculation")
        finished_part_ids = {
            str(part["part_id"])
            for part in self.parts
            if part.get("part_type") == "finished_good"
        }
        for row in self.events:
            if (
                row["output_batch_id"] == finished_batch_id
                and row["event_type"] == "process"
                and str(row["part_id"]) in finished_part_ids
                and not any(
                    edge["source_event_id"] == row["event_id"] for edge in self.inputs
                )
            ):
                source = self._source_for_root(row, execution_context)
                return source or None
        return None

    def list_part_period_sources(
        self, part_id: str, period: str, execution_context: ExecutionContext
    ) -> list[dict[str, Any]]:
        return [
            source
            for source in self.list_finished_batch_sources(period, execution_context)
            if next(
                (
                    event
                    for event in source["events"]
                    if event["event_id"] == source["root_event_id"]
                ),
                {},
            ).get("part_id")
            == part_id
        ]


def _load(path: Path) -> list[dict[str, Any]]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, list):
        raise TypeError(f"样例必须是数组：{path}")
    return [dict(item) for item in value]
