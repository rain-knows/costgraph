from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy import desc, select, text
from sqlalchemy.orm import Session

from app.db.engine import get_session_factory
from app.db.models import (
    DataLoadBatch,
    ProcessCostEntry,
    Product,
    ProductionOutput,
)
from app.domain.authorization import ExecutionContext
from app.domain.cost import (
    CostInput,
    decimal_to_number,
    quantize_money,
    quantize_quantity,
)
from app.settings import get_settings

COST_ENTRY_FIELD_MAP = {
    "material": "material_cost",
    "labor": "labor_cost",
    "equipment": "equipment_cost",
    "energy": "energy_cost",
    "overhead": "overhead_cost",
}


class PostgresCostRepository:
    """Read-only cost repository backed by published PostgreSQL batches."""

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
    def _published_batch_ids(tenant_id: str):
        return select(DataLoadBatch.batch_id).where(
            DataLoadBatch.tenant_id == tenant_id,
            DataLoadBatch.status == "published",
        )

    def list_products(
        self, execution_context: ExecutionContext
    ) -> list[dict[str, Any]]:
        execution_context.require_capability("cost_calculation")
        owner = execution_context.principal
        statement = (
            select(Product)
            .join(DataLoadBatch, DataLoadBatch.batch_id == Product.batch_id)
            .where(
                Product.tenant_id == owner.tenant_id,
                Product.batch_id.in_(self._published_batch_ids(owner.tenant_id)),
                DataLoadBatch.status == "published",
                Product.is_active.is_(True),
            )
            .order_by(Product.product_id)
        )
        with self._session() as session:
            products = session.scalars(statement).all()
        allowed = execution_context.data_scope.allowed_product_ids
        if allowed:
            products = [item for item in products if item.product_id in allowed]
        return [
            {
                "product_id": item.product_id,
                "product_name": item.product_name,
                "spec": item.spec,
            }
            for item in products
        ]

    def find_products(
        self, product_text: str, execution_context: ExecutionContext
    ) -> list[dict[str, Any]]:
        normalized = product_text.replace(" ", "").upper()
        if not normalized:
            return []
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
        with self._session() as session:
            outputs = self._load_outputs(session, execution_context, product_id, period)
            entries = self._load_entries(session, execution_context, product_id, period)
        if not outputs or not entries:
            return None
        return self._assemble_cost_input(product_id, period, outputs, entries)

    def load_product_period_source(
        self, product_id: str, period: str, execution_context: ExecutionContext
    ) -> dict[str, Any] | None:
        execution_context.require_cost_scope(product_id, period)
        owner = execution_context.principal
        with self._session() as session:
            product = session.scalar(
                select(Product)
                .join(DataLoadBatch, DataLoadBatch.batch_id == Product.batch_id)
                .where(
                    Product.tenant_id == owner.tenant_id,
                    Product.product_id == product_id,
                    Product.batch_id.in_(self._published_batch_ids(owner.tenant_id)),
                    DataLoadBatch.status == "published",
                    Product.is_active.is_(True),
                )
            )
            outputs = self._load_outputs(session, execution_context, product_id, period)
            entries = self._load_entries(session, execution_context, product_id, period)
            if product is None or not outputs or not entries:
                return None
            return {
                "product": {
                    "product_id": product.product_id,
                    "product_name": product.product_name,
                    "spec": product.spec,
                },
                "cost_inputs": self._assemble_cost_input(
                    product_id, period, outputs, entries
                ),
                "output_unit": outputs[0].unit,
                "process_entries": [
                    {
                        "cost_entry_id": entry.cost_entry_id,
                        "process_code": entry.process_code,
                        "process_name": entry.process_name,
                        "process_sort": entry.process_sort,
                        "cost_item": entry.cost_item,
                        "amount": Decimal(str(entry.amount)),
                    }
                    for entry in entries
                ],
                "source_systems": sorted(
                    {item.source_system for item in [*outputs, *entries]}
                ),
            }

    def load_previous_cost_inputs(
        self, product_id: str, period: str, execution_context: ExecutionContext
    ) -> dict[str, Any] | None:
        execution_context.require_cost_scope(product_id, period)
        owner = execution_context.principal
        statement = (
            select(ProductionOutput.period)
            .join(DataLoadBatch, DataLoadBatch.batch_id == ProductionOutput.batch_id)
            .where(
                ProductionOutput.tenant_id == owner.tenant_id,
                ProductionOutput.product_id == product_id,
                ProductionOutput.period < period,
                ProductionOutput.batch_id.in_(
                    self._published_batch_ids(owner.tenant_id)
                ),
                DataLoadBatch.status == "published",
            )
            .distinct()
            .order_by(desc(ProductionOutput.period))
        )
        with self._session() as session:
            periods = session.scalars(statement).all()
        for candidate in periods:
            try:
                execution_context.require_cost_scope(product_id, candidate)
            except PermissionError:
                continue
            result = self.load_cost_inputs(product_id, candidate, execution_context)
            if result is not None:
                return result
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
        with self._session() as session:
            outputs = self._load_outputs(
                session, execution_context, product_id, start_period, end_period
            )
            entries = self._load_entries(
                session, execution_context, product_id, start_period, end_period
            )
        entries_by_date: dict[date, list[ProcessCostEntry]] = defaultdict(list)
        for entry in entries:
            entries_by_date[entry.production_date].append(entry)
        outputs_by_date: dict[date, list[ProductionOutput]] = defaultdict(list)
        for output in outputs:
            outputs_by_date[output.production_date].append(output)
        results: list[dict[str, Any]] = []
        for production_date, daily_outputs in outputs_by_date.items():
            daily_entries = entries_by_date.get(production_date, [])
            if daily_entries:
                results.append(
                    self._assemble_cost_input(
                        product_id,
                        daily_outputs[0].period,
                        daily_outputs,
                        daily_entries,
                        production_date=production_date,
                    )
                )
        return sorted(results, key=lambda item: item["production_date"])

    def _load_outputs(
        self,
        session: Session,
        execution_context: ExecutionContext,
        product_id: str,
        start_period: str,
        end_period: str | None = None,
    ) -> list[ProductionOutput]:
        owner = execution_context.principal
        statement = (
            select(ProductionOutput)
            .join(DataLoadBatch, DataLoadBatch.batch_id == ProductionOutput.batch_id)
            .where(
                ProductionOutput.tenant_id == owner.tenant_id,
                ProductionOutput.product_id == product_id,
                ProductionOutput.period >= start_period,
                ProductionOutput.batch_id.in_(
                    self._published_batch_ids(owner.tenant_id)
                ),
                DataLoadBatch.status == "published",
            )
            .order_by(
                ProductionOutput.production_date,
                ProductionOutput.output_record_id,
            )
        )
        if end_period is not None:
            statement = statement.where(ProductionOutput.period <= end_period)
        else:
            statement = statement.where(ProductionOutput.period == start_period)
        return session.scalars(statement).all()

    def _load_entries(
        self,
        session: Session,
        execution_context: ExecutionContext,
        product_id: str,
        start_period: str,
        end_period: str | None = None,
    ) -> list[ProcessCostEntry]:
        owner = execution_context.principal
        statement = (
            select(ProcessCostEntry)
            .join(DataLoadBatch, DataLoadBatch.batch_id == ProcessCostEntry.batch_id)
            .where(
                ProcessCostEntry.tenant_id == owner.tenant_id,
                ProcessCostEntry.product_id == product_id,
                ProcessCostEntry.period >= start_period,
                ProcessCostEntry.batch_id.in_(
                    self._published_batch_ids(owner.tenant_id)
                ),
                DataLoadBatch.status == "published",
            )
            .order_by(
                ProcessCostEntry.production_date,
                ProcessCostEntry.process_sort,
                ProcessCostEntry.process_code,
                ProcessCostEntry.cost_item,
                ProcessCostEntry.cost_entry_id,
            )
        )
        if end_period is not None:
            statement = statement.where(ProcessCostEntry.period <= end_period)
        else:
            statement = statement.where(ProcessCostEntry.period == start_period)
        return session.scalars(statement).all()

    @staticmethod
    def _assemble_cost_input(
        product_id: str,
        period: str,
        outputs: list[ProductionOutput],
        entries: list[ProcessCostEntry],
        production_date: date | None = None,
    ) -> dict[str, Any]:
        process_map: dict[str, dict[str, Any]] = {}
        source_entry_ids: list[str] = []
        for entry in entries:
            process = process_map.setdefault(
                entry.process_code,
                {
                    "process_code": entry.process_code,
                    "process_name": entry.process_name,
                    "process_sort": entry.process_sort,
                    "material_cost": Decimal(0),
                    "labor_cost": Decimal(0),
                    "equipment_cost": Decimal(0),
                    "energy_cost": Decimal(0),
                    "overhead_cost": Decimal(0),
                },
            )
            process[COST_ENTRY_FIELD_MAP[entry.cost_item]] += Decimal(str(entry.amount))
            source_entry_ids.append(entry.cost_entry_id)

        process_costs = sorted(
            process_map.values(),
            key=lambda item: (item["process_sort"], item["process_code"]),
        )
        source_output_ids = [item.output_record_id for item in outputs]
        payload = {
            "product_id": product_id,
            "period": period,
            "output_qty": decimal_to_number(
                quantize_quantity(
                    sum(
                        (Decimal(str(item.qualified_output_qty)) for item in outputs),
                        Decimal(0),
                    )
                )
            ),
            "process_costs": [
                {
                    "process_name": item["process_name"],
                    "material_cost": decimal_to_number(
                        quantize_money(item["material_cost"])
                    ),
                    "labor_cost": decimal_to_number(quantize_money(item["labor_cost"])),
                    "equipment_cost": decimal_to_number(
                        quantize_money(item["equipment_cost"])
                    ),
                    "energy_cost": decimal_to_number(
                        quantize_money(item["energy_cost"])
                    ),
                    "overhead_cost": decimal_to_number(
                        quantize_money(item["overhead_cost"])
                    ),
                }
                for item in process_costs
            ],
            "source_tables": {
                "production_outputs": source_output_ids,
                "process_cost_entries": source_entry_ids,
            },
            "source_summary": {
                "production_outputs": len(source_output_ids),
                "process_cost_entries": len(source_entry_ids),
                "process_count": len(process_costs),
                "start_date": outputs[0].production_date.isoformat(),
                "end_date": outputs[-1].production_date.isoformat(),
            },
        }
        if production_date is not None:
            payload["production_date"] = production_date.isoformat()
        CostInput.model_validate(payload)
        return payload
