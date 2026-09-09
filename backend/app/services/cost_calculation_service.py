import calendar
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from app.domain.cost import (
    CostInput,
    CostResult,
    as_decimal,
    calculation_policy_manifest,
    cost_input_to_internal,
    cost_result_to_public,
    decimal_to_number,
    quantize_money,
    quantize_quantity,
    quantize_unit_cost,
)

COST_FIELD_MAP = {
    "material_cost": "材料",
    "labor_cost": "人工",
    "equipment_cost": "设备",
    "energy_cost": "能耗",
    "overhead_cost": "制造费用",
}


def _period_bounds(period: str) -> tuple[date, date]:
    year, month = [int(part) for part in period.split("-")]
    last_day = calendar.monthrange(year, month)[1]
    return date(year, month, 1), date(year, month, last_day)


def _parse_date(value: str | date) -> date:
    return value if isinstance(value, date) else date.fromisoformat(value)


def _build_cost_result(
    output_qty: Decimal,
    process_totals: dict[str, dict[str, Decimal]],
    *,
    date_range: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if output_qty <= 0:
        raise ValueError("合格产量必须大于 0。")
    if not process_totals:
        raise ValueError("成本输入必须包含至少一个工序。")

    composition_totals = {label: Decimal(0) for label in COST_FIELD_MAP.values()}
    process_breakdown: list[dict[str, Any]] = []
    for process_name, totals in process_totals.items():
        fields = {field: quantize_money(totals[field]) for field in COST_FIELD_MAP}
        for field, label in COST_FIELD_MAP.items():
            composition_totals[label] += fields[field]
        total_cost = quantize_money(sum(fields.values(), Decimal(0)))
        process_breakdown.append(
            {"process_name": process_name, **fields, "total_cost": total_cost}
        )

    total_cost = quantize_money(
        sum((item["total_cost"] for item in process_breakdown), Decimal(0))
    )
    normalized_output = quantize_quantity(output_qty)
    if normalized_output <= 0:
        raise ValueError("合格产量按数量精度量化后必须大于 0。")
    unit_cost = quantize_unit_cost(total_cost / normalized_output)
    result = CostResult.model_validate(
        {
            "output_qty": normalized_output,
            "total_cost": total_cost,
            "unit_cost": unit_cost,
            "process_breakdown": process_breakdown,
            "cost_composition": [
                {"name": name, "value": quantize_money(value)}
                for name, value in composition_totals.items()
            ],
            "date_range": date_range,
            "calculation_policy": calculation_policy_manifest(),
        }
    )
    return cost_result_to_public(result)


def calculate_product_cost(cost_inputs: dict[str, Any] | CostInput) -> dict[str, Any]:
    validated = cost_input_to_internal(cost_inputs)
    process_totals: dict[str, dict[str, Decimal]] = {}
    for process in validated.process_costs:
        totals = process_totals.setdefault(
            process.process_name,
            {field: Decimal(0) for field in COST_FIELD_MAP},
        )
        for field in COST_FIELD_MAP:
            totals[field] += as_decimal(getattr(process, field))
    return _build_cost_result(validated.output_qty, process_totals)


def calculate_product_cost_for_date_range(
    cost_input_records: list[dict[str, Any] | CostInput],
    start_date: str,
    end_date: str,
) -> dict[str, Any]:
    start = _parse_date(start_date)
    end = _parse_date(end_date)
    if start > end:
        raise ValueError("开始日期不能晚于结束日期。")
    records = [cost_input_to_internal(record) for record in cost_input_records]
    if any(record.production_date is not None for record in records):
        return _calculate_daily_product_cost_for_date_range(records, start, end)
    return _calculate_allocated_product_cost_for_date_range(records, start, end)


def _calculate_allocated_product_cost_for_date_range(
    records: list[CostInput], start: date, end: date
) -> dict[str, Any]:
    process_totals: dict[str, dict[str, Decimal]] = {}
    output_qty = Decimal(0)
    allocation: list[dict[str, Any]] = []

    for record in records:
        month_start, month_end = _period_bounds(record.period)
        overlap_start = max(start, month_start)
        overlap_end = min(end, month_end)
        if overlap_start > overlap_end:
            continue

        days_in_month = (month_end - month_start).days + 1
        covered_days = (overlap_end - overlap_start).days + 1
        ratio = Decimal(covered_days) / Decimal(days_in_month)
        output_qty += record.output_qty * ratio
        allocation.append(
            {
                "period": record.period,
                "covered_start": overlap_start.isoformat(),
                "covered_end": overlap_end.isoformat(),
                "covered_days": covered_days,
                "days_in_month": days_in_month,
                "allocation_ratio": decimal_to_number(
                    ratio.quantize(Decimal("0.000001"))
                ),
                "allocation_method": "calendar_day_proration",
            }
        )

        for process in record.process_costs:
            totals = process_totals.setdefault(
                process.process_name,
                {field: Decimal(0) for field in COST_FIELD_MAP},
            )
            for field in COST_FIELD_MAP:
                totals[field] += as_decimal(getattr(process, field)) * ratio

    if not allocation:
        raise ValueError("选定日期范围内没有可用样例成本数据。")
    return _build_cost_result(
        output_qty,
        process_totals,
        date_range={
            "start_date": start.isoformat(),
            "end_date": end.isoformat(),
            "covered_days": (end - start).days + 1,
            "allocation": allocation,
        },
    )


def _calculate_daily_product_cost_for_date_range(
    records: list[CostInput], start: date, end: date
) -> dict[str, Any]:
    selected_records = [
        record
        for record in records
        if record.production_date is not None and start <= record.production_date <= end
    ]
    if not selected_records:
        raise ValueError("选定日期范围内没有可用日级成本数据。")

    process_totals: dict[str, dict[str, Decimal]] = {}
    output_qty = Decimal(0)
    source_output_ids: list[str] = []
    source_cost_entry_ids: list[str] = []
    allocation_map: dict[str, dict[str, Any]] = {}

    for record in selected_records:
        output_qty += record.output_qty
        source_output_ids.extend(record.source_tables.production_outputs)
        source_cost_entry_ids.extend(record.source_tables.process_cost_entries)
        production_date = record.production_date
        assert production_date is not None
        allocation_item = allocation_map.setdefault(
            record.period,
            {
                "period": record.period,
                "covered_start": production_date.isoformat(),
                "covered_end": production_date.isoformat(),
                "covered_days": 0,
                "production_days": 0,
                "daily_output_records": 0,
                "allocation_ratio": 1,
                "allocation_method": "exact_daily_sum",
            },
        )
        month_start, month_end = _period_bounds(record.period)
        overlap_start = max(start, month_start)
        overlap_end = min(end, month_end)
        allocation_item["covered_start"] = min(
            allocation_item["covered_start"], overlap_start.isoformat()
        )
        allocation_item["covered_end"] = max(
            allocation_item["covered_end"], overlap_end.isoformat()
        )
        allocation_item["covered_days"] = (overlap_end - overlap_start).days + 1
        allocation_item["production_days"] += 1
        allocation_item["daily_output_records"] += 1

        for process in record.process_costs:
            totals = process_totals.setdefault(
                process.process_name,
                {field: Decimal(0) for field in COST_FIELD_MAP},
            )
            for field in COST_FIELD_MAP:
                totals[field] += as_decimal(getattr(process, field))

    return _build_cost_result(
        output_qty,
        process_totals,
        date_range={
            "start_date": start.isoformat(),
            "end_date": end.isoformat(),
            "covered_days": (end - start).days + 1,
            "allocation": list(allocation_map.values()),
            "source_tables": {
                "production_outputs": source_output_ids,
                "process_cost_entries": source_cost_entry_ids,
            },
        },
    )


def compare_cost_results(
    current_result: dict[str, Any],
    previous_result: dict[str, Any],
    previous_period: str,
) -> dict[str, Any]:
    current_unit_cost = as_decimal(current_result["unit_cost"])
    previous_unit_cost = as_decimal(previous_result["unit_cost"])
    unit_cost_delta = quantize_unit_cost(current_unit_cost - previous_unit_cost)
    unit_cost_delta_rate = (
        (unit_cost_delta / previous_unit_cost * Decimal(100)).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )
        if previous_unit_cost
        else Decimal(0)
    )

    previous_process_map = {
        item["process_name"]: item for item in previous_result["process_breakdown"]
    }
    process_changes: list[dict[str, Any]] = []
    for current_process in current_result["process_breakdown"]:
        previous_process = previous_process_map.get(current_process["process_name"])
        if previous_process is None:
            continue
        current_total = as_decimal(current_process["total_cost"])
        previous_total = as_decimal(previous_process["total_cost"])
        delta = quantize_money(current_total - previous_total)
        delta_rate = (
            (delta / previous_total * Decimal(100)).quantize(
                Decimal("0.01"), rounding=ROUND_HALF_UP
            )
            if previous_total
            else Decimal(0)
        )
        process_changes.append(
            {
                "process_name": current_process["process_name"],
                "current_total_cost": decimal_to_number(current_total),
                "previous_total_cost": decimal_to_number(previous_total),
                "delta": decimal_to_number(delta),
                "delta_rate": decimal_to_number(delta_rate),
            }
        )

    top_increase = max(process_changes, key=lambda item: item["delta"], default=None)
    top_process = max(
        current_result["process_breakdown"],
        key=lambda item: item["total_cost"],
        default=None,
    )
    return {
        "previous_period": previous_period,
        "current_unit_cost": decimal_to_number(current_unit_cost),
        "previous_unit_cost": decimal_to_number(previous_unit_cost),
        "unit_cost_delta": decimal_to_number(unit_cost_delta),
        "unit_cost_delta_rate": decimal_to_number(unit_cost_delta_rate),
        "process_changes": process_changes,
        "top_increase_process": top_increase,
        "top_cost_process": top_process,
    }
