from __future__ import annotations

from decimal import Decimal
from typing import Any, Literal

from app.domain.authorization import (
    ExecutionContext,
    ExecutionPrincipal,
    build_execution_context,
)
from app.domain.cost import quantize_percent
from app.domain.errors import CostDataNotFoundError
from app.repositories.cost_repository import CostRepository, get_cost_repository
from app.schemas.cost_data import (
    CostOverview,
    FinishedBatchCostDetail,
    FinishedBatchCostList,
    FinishedBatchCostSummary,
)
from app.services.cost_calculation_service import aggregate_part_period_costs

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
        aggregate = self.repository.overview_projection(period, context)
        completed = aggregate["completed_quantity"]
        qualified = aggregate["qualified_quantity"]
        return CostOverview(
            period=period,
            batch_count=aggregate["batch_count"],
            part_count=aggregate["part_count"],
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
            total_cost=aggregate["total_cost"],
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
        summaries, total = self.repository.list_finished_batch_projections(
            period=period,
            query=query,
            cost_center_code=cost_center_code,
            sort=sort,
            page=page,
            page_size=page_size,
            execution_context=context,
        )
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
        projection = self.repository.load_finished_batch_projection(
            finished_batch_id, context
        )
        if projection is None:
            raise CostDataNotFoundError(finished_batch_id)
        return FinishedBatchCostDetail.model_validate(projection)

    def part_period_aggregate(
        self, part_id: str, period: str, principal: ExecutionPrincipal
    ) -> dict[str, Any]:
        """Return the deterministic report input for one part and period."""
        context = self._context(principal)
        context.require_cost_scope(part_id, period)
        projections = self.repository.list_part_period_projections(
            part_id, period, context
        )
        if not projections:
            raise CostDataNotFoundError(f"{part_id}:{period}")
        return aggregate_part_period_costs(projections)

    @staticmethod
    def _context(principal: ExecutionPrincipal) -> ExecutionContext:
        return build_execution_context(
            "auto", ["cost_calculation"], principal=principal
        )


def get_cost_data_query_service() -> CostDataQueryService:
    return CostDataQueryService()
