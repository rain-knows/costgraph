from __future__ import annotations

from decimal import Decimal
from typing import Any, Literal

from app.domain.authorization import (
    ExecutionContext,
    ExecutionPrincipal,
    build_execution_context,
)
from app.domain.cost import (
    as_decimal,
    quantize_money,
    quantize_percent,
)
from app.domain.errors import CostDataNotFoundError
from app.repositories.cost_repository import CostRepository, get_cost_repository
from app.schemas.cost_data import (
    CostOverview,
    FinishedBatchCostDetail,
    FinishedBatchCostList,
    FinishedBatchCostSummary,
)
from app.services.cost_calculation_service import (
    aggregate_finished_batch_costs,
    calculate_finished_batch_cost,
)

FinishedBatchSort = Literal[
    "completion_time_asc",
    "completion_time_desc",
    "unit_cost_asc",
    "unit_cost_desc",
]


class CostDataQueryService:
    def __init__(self, repository: CostRepository | None = None) -> None:
        self.repository = repository or get_cost_repository()

    def overview(self, period: str, principal: ExecutionPrincipal) -> CostOverview:
        context = self._context(principal)
        context.require_capability("cost_calculation")
        sources = self.repository.list_finished_batch_sources(period, context)
        items = [calculate_finished_batch_cost(source) for source in sources]
        aggregate = aggregate_finished_batch_costs(items)
        completed = aggregate["completed_quantity"]
        qualified = aggregate["qualified_quantity"]
        return CostOverview(
            period=period,
            batch_count=len(items),
            part_count=len({item["part"]["part_id"] for item in items}),
            completed_quantity=completed,
            qualified_quantity=qualified,
            defective_quantity=aggregate["defective_quantity"],
            quality_rate=(
                quantize_percent(qualified / completed * Decimal(100))
                if completed
                else Decimal("0.00")
            ),
            manufacturing_cost=aggregate["manufacturing_cost"],
            post_manufacturing_cost=aggregate["post_manufacturing_cost"],
            total_cost=quantize_money(
                aggregate["manufacturing_cost"] + aggregate["post_manufacturing_cost"]
            ),
        )

    def list_finished_batches(
        self,
        *,
        period: str,
        query: str | None,
        cost_center_code: str | None,
        sort: FinishedBatchSort,
        page: int,
        page_size: int,
        principal: ExecutionPrincipal,
    ) -> FinishedBatchCostList:
        context = self._context(principal)
        context.require_capability("cost_calculation")
        items = [
            calculate_finished_batch_cost(source)
            for source in self.repository.list_finished_batch_sources(period, context)
        ]
        normalized = (query or "").strip().casefold()
        if normalized:
            items = [
                item
                for item in items
                if normalized
                in " ".join(
                    str(item.get(field) or "")
                    for field in (
                        "finished_batch_id",
                        "event_id",
                        "work_order_number",
                        "lot_number",
                        "cost_center_code",
                        "cost_center_name",
                        "process_code",
                        "process_name",
                        "part",
                    )
                ).casefold()
            ]
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
        summaries = [
            {key: value for key, value in item.items() if key != "trace"}
            for item in items[start : start + page_size]
        ]
        return FinishedBatchCostList(
            period=period,
            items=[FinishedBatchCostSummary.model_validate(item) for item in summaries],
            page=page,
            page_size=page_size,
            total=total,
        )

    def finished_batch_detail(
        self, finished_batch_id: str, principal: ExecutionPrincipal
    ) -> FinishedBatchCostDetail:
        context = self._context(principal)
        context.require_capability("cost_calculation")
        source = self.repository.load_finished_batch_source(finished_batch_id, context)
        if source is None:
            raise CostDataNotFoundError(finished_batch_id)
        return FinishedBatchCostDetail.model_validate(
            calculate_finished_batch_cost(source)
        )

    def part_period_aggregate(
        self, part_id: str, period: str, principal: ExecutionPrincipal
    ) -> dict[str, Any]:
        """Return the deterministic report input for one part and period."""
        from app.services.cost_calculation_service import aggregate_part_period_costs

        context = self._context(principal)
        context.require_cost_scope(part_id, period)
        sources = self.repository.list_part_period_sources(part_id, period, context)
        if not sources:
            raise CostDataNotFoundError(f"{part_id}:{period}")
        return aggregate_part_period_costs(
            [calculate_finished_batch_cost(source) for source in sources]
        )

    @staticmethod
    def _context(principal: ExecutionPrincipal) -> ExecutionContext:
        return build_execution_context(
            "auto", ["cost_calculation"], principal=principal
        )


def get_cost_data_query_service() -> CostDataQueryService:
    return CostDataQueryService()
