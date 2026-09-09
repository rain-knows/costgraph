from __future__ import annotations

import json
from collections import defaultdict
from decimal import Decimal
from pathlib import Path
from typing import Any

from app.domain.authorization import ExecutionContext
from app.domain.cost import decimal_to_number, quantize_money, quantize_quantity

SAMPLE_ROOT = Path(__file__).resolve().parents[2] / "data" / "samples"
COST_ENTRY_FIELD_MAP = {
    "material": "material_cost",
    "labor": "labor_cost",
    "equipment": "equipment_cost",
    "energy": "energy_cost",
    "overhead": "overhead_cost",
}


class CostFixtureRepository:
    """In-memory fixture used only by tests and offline evaluation."""

    def __init__(self, sample_root: Path = SAMPLE_ROOT) -> None:
        self.products = _load(sample_root / "products.json")
        self.outputs = _load(sample_root / "production_outputs.json")
        self.entries = _load(sample_root / "process_cost_entries.json")

    def list_products(
        self, execution_context: ExecutionContext
    ) -> list[dict[str, Any]]:
        execution_context.require_capability("cost_calculation")
        allowed = execution_context.data_scope.allowed_product_ids
        products = [
            {
                "product_id": item["product_id"],
                "product_name": item["product_name"],
                "spec": item.get("spec"),
            }
            for item in self.products
        ]
        return (
            products
            if not allowed
            else [item for item in products if item["product_id"] in allowed]
        )

    def find_products(
        self, product_text: str, execution_context: ExecutionContext
    ) -> list[dict[str, Any]]:
        normalized = product_text.replace(" ", "").upper()
        exact: list[dict[str, Any]] = []
        partial: list[dict[str, Any]] = []
        for product in self.list_products(execution_context):
            name = str(product["product_name"]).replace(" ", "").upper()
            product_id = str(product["product_id"]).replace(" ", "").upper()
            if normalized in {name, product_id}:
                exact.append(product)
            elif normalized in name or name in normalized or normalized in product_id:
                partial.append(product)
        return exact or partial

    def load_cost_inputs(
        self, product_id: str, period: str, execution_context: ExecutionContext
    ) -> dict[str, Any] | None:
        execution_context.require_cost_scope(product_id, period)
        outputs = self._outputs(product_id, period)
        entries = self._entries(product_id, period)
        if not outputs or not entries:
            return None
        return _assemble(product_id, period, outputs, entries)

    def load_previous_cost_inputs(
        self, product_id: str, period: str, execution_context: ExecutionContext
    ) -> dict[str, Any] | None:
        periods = sorted(
            {
                str(item["period"])
                for item in self.outputs
                if item["product_id"] == product_id and item["period"] < period
            },
            reverse=True,
        )
        for candidate in periods:
            value = self.load_cost_inputs(product_id, candidate, execution_context)
            if value is not None:
                return value
        return None

    def load_cost_inputs_in_period_range(
        self,
        product_id: str,
        start_period: str,
        end_period: str,
        execution_context: ExecutionContext,
    ) -> list[dict[str, Any]]:
        execution_context.require_cost_scope(product_id, start_period)
        execution_context.require_cost_scope(product_id, end_period)
        outputs_by_date: dict[str, list[dict[str, Any]]] = defaultdict(list)
        entries_by_date: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for output in self.outputs:
            if (
                output["product_id"] == product_id
                and start_period <= output["period"] <= end_period
            ):
                outputs_by_date[str(output["production_date"])].append(output)
        for entry in self.entries:
            if (
                entry["product_id"] == product_id
                and start_period <= entry["period"] <= end_period
            ):
                entries_by_date[str(entry["production_date"])].append(entry)
        return [
            _assemble(
                product_id,
                str(outputs[0]["period"]),
                outputs,
                entries_by_date[production_date],
                production_date=production_date,
            )
            for production_date, outputs in sorted(outputs_by_date.items())
            if entries_by_date[production_date]
        ]

    def load_product_period_source(
        self, product_id: str, period: str, execution_context: ExecutionContext
    ) -> dict[str, Any] | None:
        inputs = self.load_cost_inputs(product_id, period, execution_context)
        product = next(
            (
                item
                for item in self.list_products(execution_context)
                if item["product_id"] == product_id
            ),
            None,
        )
        entries = self._entries(product_id, period)
        if inputs is None or product is None or not entries:
            return None
        return {
            "product": product,
            "cost_inputs": inputs,
            "output_unit": self._outputs(product_id, period)[0]["unit"],
            "process_entries": entries,
            "source_systems": ["costgraph_samples"],
        }

    def _outputs(self, product_id: str, period: str) -> list[dict[str, Any]]:
        return sorted(
            [
                item
                for item in self.outputs
                if item["product_id"] == product_id and item["period"] == period
            ],
            key=lambda item: (item["production_date"], item["output_record_id"]),
        )

    def _entries(self, product_id: str, period: str) -> list[dict[str, Any]]:
        return sorted(
            [
                item
                for item in self.entries
                if item["product_id"] == product_id and item["period"] == period
            ],
            key=lambda item: (
                item["production_date"],
                item["process_sort"],
                item["process_code"],
                item["cost_item"],
                item["cost_entry_id"],
            ),
        )


def _load(path: Path) -> list[dict[str, Any]]:
    value = json.loads(path.read_text(encoding="utf-8"))
    return [dict(item) for item in value]


def _assemble(
    product_id: str,
    period: str,
    outputs: list[dict[str, Any]],
    entries: list[dict[str, Any]],
    *,
    production_date: str | None = None,
) -> dict[str, Any]:
    process_map: dict[str, dict[str, Any]] = {}
    for entry in entries:
        process = process_map.setdefault(
            str(entry["process_code"]),
            {
                "process_code": entry["process_code"],
                "process_name": entry["process_name"],
                "process_sort": entry["process_sort"],
                **{field: Decimal(0) for field in COST_ENTRY_FIELD_MAP.values()},
            },
        )
        process[COST_ENTRY_FIELD_MAP[str(entry["cost_item"])]] += Decimal(
            str(entry["amount"])
        )
    process_costs = sorted(
        process_map.values(),
        key=lambda item: (item["process_sort"], item["process_code"]),
    )
    payload = {
        "product_id": product_id,
        "period": period,
        "output_qty": decimal_to_number(
            quantize_quantity(
                sum(
                    (Decimal(str(item["qualified_output_qty"])) for item in outputs),
                    Decimal(0),
                )
            )
        ),
        "process_costs": [
            {
                "process_name": item["process_name"],
                **{
                    field: decimal_to_number(quantize_money(item[field]))
                    for field in COST_ENTRY_FIELD_MAP.values()
                },
            }
            for item in process_costs
        ],
        "source_tables": {
            "production_outputs": [item["output_record_id"] for item in outputs],
            "process_cost_entries": [item["cost_entry_id"] for item in entries],
        },
        "source_summary": {
            "production_outputs": len(outputs),
            "process_cost_entries": len(entries),
            "process_count": len(process_costs),
            "start_date": outputs[0]["production_date"],
            "end_date": outputs[-1]["production_date"],
        },
    }
    if production_date:
        payload["production_date"] = production_date
    return payload
