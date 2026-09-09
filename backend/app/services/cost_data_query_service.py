from __future__ import annotations

from collections import defaultdict
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Literal

from app.domain.authorization import (
    ExecutionContext,
    ExecutionPrincipal,
    build_execution_context,
)
from app.domain.cost import (
    as_decimal,
    quantize_money,
    quantize_quantity,
    quantize_unit_cost,
)
from app.domain.errors import CostDataNotFoundError
from app.repositories.cost_repository import CostRepository, get_cost_repository
from app.schemas.cost_data import (
    CostOverview,
    OverviewComparison,
    PeriodComparison,
    ProductPeriodDetail,
    ProductPeriodList,
    ProductPeriodSummary,
)
from app.services.cost_calculation_service import (
    calculate_product_cost,
    compare_cost_results,
)

ProductSort = Literal[
    "product_id",
    "product_name",
    "total_cost_asc",
    "total_cost_desc",
    "unit_cost_asc",
    "unit_cost_desc",
]
ITEM_LABELS = {
    "material": "材料",
    "labor": "人工",
    "equipment": "设备",
    "energy": "能耗",
    "overhead": "制造费用",
}


class CostDataQueryService:
    def __init__(self, repository: CostRepository | None = None) -> None:
        self.repository = repository or get_cost_repository()

    def overview(self, period: str, principal: ExecutionPrincipal) -> CostOverview:
        context = self._context(principal)
        products = self.repository.list_products(context)
        current_results = self._results_for_products(products, period, context)
        total_output = quantize_quantity(
            sum((item[1]["output_qty"] for item in current_results), Decimal(0))
        )
        total_cost = quantize_money(
            sum((item[1]["total_cost"] for item in current_results), Decimal(0))
        )
        average_unit = (
            quantize_unit_cost(total_cost / total_output) if total_output else None
        )

        comparison = None
        previous = previous_period(period)
        previous_results = self._results_for_products(
            [item[0] for item in current_results], previous, context
        )
        if previous_results:
            previous_output = sum(
                (item[1]["output_qty"] for item in previous_results), Decimal(0)
            )
            previous_cost = sum(
                (item[1]["total_cost"] for item in previous_results), Decimal(0)
            )
            previous_average = (
                quantize_unit_cost(previous_cost / previous_output)
                if previous_output
                else Decimal(0)
            )
            unit_delta = quantize_unit_cost(
                (average_unit or Decimal(0)) - previous_average
            )
            comparison = OverviewComparison(
                previous_period=previous,
                total_cost_delta=quantize_money(total_cost - previous_cost),
                total_cost_delta_rate=_rate(total_cost - previous_cost, previous_cost),
                unit_cost_delta=unit_delta,
                unit_cost_delta_rate=_rate(unit_delta, previous_average),
            )

        return CostOverview(
            period=period,
            product_count=len(current_results),
            total_output_qty=total_output,
            total_cost=total_cost,
            average_unit_cost=average_unit,
            comparison=comparison,
        )

    def list_products(
        self,
        *,
        period: str,
        query: str | None,
        sort: ProductSort,
        page: int,
        page_size: int,
        principal: ExecutionPrincipal,
    ) -> ProductPeriodList:
        context = self._context(principal)
        products = self.repository.list_products(context)
        normalized_query = (query or "").strip().casefold()
        if normalized_query:
            products = [
                item
                for item in products
                if normalized_query
                in " ".join(
                    str(item.get(field) or "")
                    for field in ("product_id", "product_name", "spec")
                ).casefold()
            ]
        summaries = [
            self._summary(product, period, context)
            for product in products
            if self.repository.load_cost_inputs(
                str(product["product_id"]), period, context
            )
            is not None
        ]
        summaries.sort(key=_sort_key(sort), reverse=sort.endswith("_desc"))
        total = len(summaries)
        start = (page - 1) * page_size
        return ProductPeriodList(
            period=period,
            items=summaries[start : start + page_size],
            page=page,
            page_size=page_size,
            total=total,
        )

    def product_detail(
        self, product_id: str, period: str, principal: ExecutionPrincipal
    ) -> ProductPeriodDetail:
        context = self._context(principal)
        source = self.repository.load_product_period_source(product_id, period, context)
        if source is None:
            raise CostDataNotFoundError(product_id, period)
        result = _calculation(source["cost_inputs"])
        comparison = self._comparison(product_id, period, result, context)

        grouped: dict[tuple[str, str, int], dict[str, dict[str, Any]]] = defaultdict(
            dict
        )
        for entry in source["process_entries"]:
            process_key = (
                str(entry["process_code"]),
                str(entry["process_name"]),
                int(entry["process_sort"]),
            )
            cost_item = str(entry["cost_item"])
            item = grouped[process_key].setdefault(
                cost_item, {"amount": Decimal(0), "source_record_count": 0}
            )
            item["amount"] += as_decimal(entry["amount"])
            item["source_record_count"] += 1

        processes: list[dict[str, Any]] = []
        for (code, name, order), items in sorted(
            grouped.items(), key=lambda item: (item[0][2], item[0][0])
        ):
            item_rows = [
                {
                    "cost_item": cost_item,
                    "label": ITEM_LABELS[cost_item],
                    "amount": quantize_money(value["amount"]),
                    "source_record_count": value["source_record_count"],
                }
                for cost_item, value in sorted(
                    items.items(), key=lambda item: list(ITEM_LABELS).index(item[0])
                )
            ]
            processes.append(
                {
                    "process_code": code,
                    "process_name": name,
                    "process_sort": order,
                    "total_cost": quantize_money(
                        sum((item["amount"] for item in item_rows), Decimal(0))
                    ),
                    "items": item_rows,
                }
            )

        summary = source["cost_inputs"]["source_summary"]
        return ProductPeriodDetail(
            product=source["product"],
            period=period,
            output_qty=result["output_qty"],
            total_cost=result["total_cost"],
            unit_cost=result["unit_cost"],
            comparison=comparison,
            processes=processes,
            source_summary={
                "production_output_count": summary["production_outputs"],
                "process_cost_entry_count": summary["process_cost_entries"],
                "start_date": summary["start_date"],
                "end_date": summary["end_date"],
                "source_systems": source["source_systems"],
            },
        )

    def _summary(
        self,
        product: dict[str, Any],
        period: str,
        context: ExecutionContext,
    ) -> ProductPeriodSummary:
        product_id = str(product["product_id"])
        inputs = self.repository.load_cost_inputs(product_id, period, context)
        if inputs is None:
            raise CostDataNotFoundError(product_id, period)
        result = _calculation(inputs)
        return ProductPeriodSummary(
            **product,
            period=period,
            output_qty=result["output_qty"],
            total_cost=result["total_cost"],
            unit_cost=result["unit_cost"],
            comparison=self._comparison(product_id, period, result, context),
        )

    def _comparison(
        self,
        product_id: str,
        period: str,
        current: dict[str, Decimal],
        context: ExecutionContext,
    ) -> PeriodComparison | None:
        previous = previous_period(period)
        inputs = self.repository.load_cost_inputs(product_id, previous, context)
        if inputs is None:
            return None
        previous_result = _calculation(inputs)
        unit_comparison = compare_cost_results(current, previous_result, previous)
        total_delta = quantize_money(
            current["total_cost"] - previous_result["total_cost"]
        )
        return PeriodComparison(
            previous_period=previous,
            previous_total_cost=previous_result["total_cost"],
            previous_unit_cost=previous_result["unit_cost"],
            total_cost_delta=total_delta,
            total_cost_delta_rate=_rate(total_delta, previous_result["total_cost"]),
            unit_cost_delta=unit_comparison["unit_cost_delta"],
            unit_cost_delta_rate=unit_comparison["unit_cost_delta_rate"],
        )

    def _results_for_products(
        self,
        products: list[dict[str, Any]],
        period: str,
        context: ExecutionContext,
    ) -> list[tuple[dict[str, Any], dict[str, Decimal]]]:
        results: list[tuple[dict[str, Any], dict[str, Decimal]]] = []
        for product in products:
            inputs = self.repository.load_cost_inputs(
                str(product["product_id"]), period, context
            )
            if inputs is not None:
                results.append((product, _calculation(inputs)))
        return results

    @staticmethod
    def _context(principal: ExecutionPrincipal) -> ExecutionContext:
        return build_execution_context(
            "auto", ["cost_calculation"], principal=principal
        )


def get_cost_data_query_service() -> CostDataQueryService:
    return CostDataQueryService()


def previous_period(period: str) -> str:
    year, month = (int(part) for part in period.split("-"))
    if month == 1:
        return f"{year - 1}-12"
    return f"{year}-{month - 1:02d}"


def _calculation(inputs: dict[str, Any]) -> dict[str, Decimal]:
    result = calculate_product_cost(inputs)
    return {
        "output_qty": quantize_quantity(result["output_qty"]),
        "total_cost": quantize_money(result["total_cost"]),
        "unit_cost": quantize_unit_cost(result["unit_cost"]),
        "process_breakdown": result["process_breakdown"],
    }


def _rate(delta: Decimal, base: Decimal) -> Decimal:
    if not base:
        return Decimal(0).quantize(Decimal("0.01"))
    return (delta / base * Decimal(100)).quantize(
        Decimal("0.01"), rounding=ROUND_HALF_UP
    )


def _sort_key(sort: ProductSort):
    if sort == "product_name":
        return lambda item: (item.product_name.casefold(), item.product_id)
    if sort.startswith("total_cost"):
        return lambda item: (item.total_cost, item.product_id)
    if sort.startswith("unit_cost"):
        return lambda item: (item.unit_cost, item.product_id)
    return lambda item: item.product_id
