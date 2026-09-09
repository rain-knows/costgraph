from __future__ import annotations

from collections import defaultdict
from decimal import Decimal
from typing import Any

from app.domain.cost import (
    COST_CODE_GROUP,
    COST_CODE_LABELS,
    COST_GROUP_LABELS,
    COST_GROUPS,
    FIXED_1_GROUP_CODES,
    MANUFACTURING_GROUP_CODES,
    VARIABLE_1_GROUP_CODES,
    as_decimal,
    calculation_policy_manifest,
    cost_vector_total,
    event_completed_quantity,
    quantize_money,
    quantize_percent,
    quantize_quantity,
    quantize_unit_cost,
    roll_up_cost_graph,
)


def _vector_total(vector: dict[str, Decimal], groups: tuple[str, ...]) -> Decimal:
    return cost_vector_total(vector, groups)


def _metric(amount: Decimal, completed_quantity: Decimal) -> dict[str, Decimal]:
    normalized_amount = quantize_money(amount)
    unit_cost = (
        quantize_unit_cost(normalized_amount / completed_quantity)
        if completed_quantity > 0
        else Decimal("0.00")
    )
    return {"amount": normalized_amount, "unit_cost": unit_cost}


def _part_identity(part: dict[str, Any]) -> dict[str, Any]:
    return {
        "part_id": str(part["part_id"]),
        "part_number": str(part["part_number"]),
        "part_description": str(part["part_description"]),
        "part_type": str(part["part_type"]),
        "product_family": part.get("product_family"),
    }


def _vector_payload(vector: dict[str, Decimal]) -> dict[str, Any]:
    items = [
        {
            "cost_code": cost_code,
            "cost_group": COST_CODE_GROUP[cost_code],
            "label": COST_CODE_LABELS[cost_code],
            "amount": quantize_money(vector[cost_code]),
        }
        for cost_code in COST_CODE_GROUP
    ]
    return {
        "items": items,
        "total_amount": quantize_money(
            sum((item["amount"] for item in items), Decimal(0))
        ),
    }


def calculate_finished_batch_cost(source: dict[str, Any]) -> dict[str, Any]:
    events = [dict(item) for item in source.get("events", [])]
    inputs = [dict(item) for item in source.get("inputs", [])]
    records = [dict(item) for item in source.get("records", [])]
    parts = {str(item["part_id"]): dict(item) for item in source.get("parts", [])}
    root_event_id = str(source.get("root_event_id") or "")
    if root_event_id not in {str(item["event_id"]) for item in events}:
        raise ValueError("成本追溯根事件不存在。")

    rollup = roll_up_cost_graph(events, inputs, records)
    event_by_id = {str(event["event_id"]): event for event in events}
    input_by_id = {str(edge["input_id"]): edge for edge in inputs}
    record_by_event: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for record in records:
        record_by_event[str(record["event_id"])].append(record)
    root = event_by_id[root_event_id]
    root_completed = event_completed_quantity(root)
    root_accumulated = rollup.accumulated[root_event_id]
    root_groups = {
        group_code: _vector_total(root_accumulated, (group_code,))
        for group_code in COST_GROUPS
    }

    groups: list[dict[str, Any]] = []
    manufacturing_total = _vector_total(root_accumulated, MANUFACTURING_GROUP_CODES)
    # The manufacturing view is intentionally limited to the six manufacturing
    # groups.  Manufacturing-after costs are exposed only by the
    # variable/fixed view and must never be mixed into manufacturing totals.
    for group_code in MANUFACTURING_GROUP_CODES:
        group_amount = root_groups[group_code]
        leaves: list[dict[str, Any]] = []
        for cost_code in COST_GROUPS[group_code]:
            leaf_amount = quantize_money(root_accumulated[cost_code])
            leaves.append(
                {
                    "cost_code": cost_code,
                    "label": COST_CODE_LABELS[cost_code],
                    "metric": _metric(leaf_amount, root_completed),
                    "share": quantize_percent(
                        leaf_amount / manufacturing_total * Decimal(100)
                    )
                    if manufacturing_total
                    else Decimal("0.00"),
                }
            )
        groups.append(
            {
                "group_code": group_code,
                "group_label": COST_GROUP_LABELS[group_code],
                "metric": _metric(group_amount, root_completed),
                "share": quantize_percent(
                    group_amount / manufacturing_total * Decimal(100)
                )
                if manufacturing_total
                else Decimal("0.00"),
                "leaves": leaves,
            }
        )

    material = _vector_total(root_accumulated, ("direct_material",))
    labor = _vector_total(root_accumulated, ("direct_labor", "indirect_labor"))
    overhead = _vector_total(
        root_accumulated,
        ("direct_energy", "main_equipment_depreciation", "manufacturing_overhead"),
    )
    variable_1 = _vector_total(root_accumulated, VARIABLE_1_GROUP_CODES)
    fixed_1 = _vector_total(root_accumulated, FIXED_1_GROUP_CODES)
    after_sales = quantize_money(root_accumulated["after_sales_compensation"])
    transportation = quantize_money(root_accumulated["transportation"])
    storage = quantize_money(root_accumulated["post_manufacturing_storage_fee"])
    variable_2 = quantize_money(variable_1 + after_sales + transportation)
    fixed_2 = quantize_money(fixed_1 + storage)
    total_2 = quantize_money(variable_2 + fixed_2)

    node_payloads: list[dict[str, Any]] = []
    for event_id in rollup.order:
        event = event_by_id[event_id]
        part = parts.get(
            str(event["part_id"]),
            {
                "part_id": event["part_id"],
                "part_number": event["part_id"],
                "part_description": event["part_id"],
                "part_type": "work_in_progress",
            },
        )
        completed = event_completed_quantity(event)
        direct = rollup.direct[event_id]
        inherited = rollup.inherited[event_id]
        accumulated = rollup.accumulated[event_id]
        node_payloads.append(
            {
                "event_id": event_id,
                "event_type": event["event_type"],
                "output_batch_id": event["output_batch_id"],
                "part": _part_identity(part),
                "completion_time": event["completion_time"],
                "cost_center_code": event.get("cost_center_code"),
                "cost_center_name": event.get("cost_center_name"),
                "work_order_number": event.get("work_order_number"),
                "lot_number": event.get("lot_number"),
                "process_code": event.get("process_code"),
                "process_name": event.get("process_name"),
                "qualified_quantity": quantize_quantity(event["qualified_quantity"]),
                "defective_quantity": quantize_quantity(event["defective_quantity"]),
                "completed_quantity": completed,
                "unit": event["unit"],
                "direct_costs": _vector_payload(direct),
                "inherited_costs": _vector_payload(inherited),
                "accumulated_costs": _vector_payload(accumulated),
                "display_unit_cost": _metric(cost_vector_total(accumulated), completed)[
                    "unit_cost"
                ],
                "transfer_unit_cost": (
                    _metric(
                        cost_vector_total(accumulated),
                        quantize_quantity(event["qualified_quantity"]),
                    )["unit_cost"]
                    if as_decimal(event["qualified_quantity"]) > 0
                    else None
                ),
            }
        )
    edge_payloads: list[dict[str, Any]] = []
    for input_id, allocation in rollup.edge_allocations.items():
        edge = input_by_id[input_id]
        edge_payloads.append(
            {
                "input_id": input_id,
                "source_event_id": edge["source_event_id"],
                "target_event_id": edge["event_id"],
                "consumed_quantity": quantize_quantity(edge["consumed_quantity"]),
                "unit": edge["unit"],
                "allocation_ratio": rollup.edge_ratios[input_id],
                "allocated_costs": _vector_payload(allocation),
            }
        )
    record_payloads = [
        {
            "cost_record_id": record["cost_record_id"],
            "event_id": record["event_id"],
            "cost_code": record["cost_code"],
            "cost_group": COST_CODE_GROUP[record["cost_code"]],
            "cost_label": COST_CODE_LABELS[record["cost_code"]],
            "amount": quantize_money(record["amount"]),
            "currency": record["currency"],
            "incurred_at": record["incurred_at"],
            "source_system": record["source_system"],
            "source_document_no": record["source_document_no"],
            "source_document_line": record.get("source_document_line"),
            "source_record_id": record["source_record_id"],
            "raw_payload": record.get("raw_payload"),
        }
        for record in records
    ]
    quality_rate = (
        quantize_percent(
            as_decimal(root["qualified_quantity"]) / root_completed * Decimal(100)
        )
        if root_completed
        else Decimal("0.00")
    )
    return {
        "finished_batch_id": root["output_batch_id"],
        "event_id": root_event_id,
        "part": _part_identity(parts[str(root["part_id"])]),
        "period": root["period"],
        "completion_time": root["completion_time"],
        "cost_center_code": root.get("cost_center_code") or "",
        "cost_center_name": root.get("cost_center_name") or "",
        "work_order_number": root.get("work_order_number") or "",
        "lot_number": root.get("lot_number") or "",
        "process_code": root.get("process_code") or "",
        "process_name": root.get("process_name") or "",
        "qualified_quantity": quantize_quantity(root["qualified_quantity"]),
        "defective_quantity": quantize_quantity(root["defective_quantity"]),
        "completed_quantity": root_completed,
        "quality_rate": quality_rate,
        "unit": root["unit"],
        "machine_hours": quantize_quantity(root.get("machine_hours", 0)),
        "labor_hours": quantize_quantity(root.get("labor_hours", 0)),
        "manufacturing_view": {
            "groups": groups,
            "total": _metric(manufacturing_total, root_completed),
        },
        "material_labor_overhead_view": {
            "material": _metric(material, root_completed),
            "labor": _metric(labor, root_completed),
            "overhead": _metric(overhead, root_completed),
            "total": _metric(
                quantize_money(material + labor + overhead), root_completed
            ),
        },
        "variable_fixed_view": {
            "variable_cost_1": _metric(variable_1, root_completed),
            "fixed_cost_1": _metric(fixed_1, root_completed),
            "manufacturing_total": _metric(manufacturing_total, root_completed),
            "after_sales_compensation": _metric(after_sales, root_completed),
            "transportation": _metric(transportation, root_completed),
            "storage_fee": _metric(storage, root_completed),
            "variable_cost_2": _metric(variable_2, root_completed),
            "fixed_cost_2": _metric(fixed_2, root_completed),
            "total_cost_2": _metric(total_2, root_completed),
        },
        "trace": {
            "root_event_id": root_event_id,
            "nodes": node_payloads,
            "edges": edge_payloads,
            "records": record_payloads,
        },
    }


def aggregate_finished_batch_costs(
    items: list[dict[str, Any]],
) -> dict[str, Decimal]:
    return {
        "completed_quantity": quantize_quantity(
            sum((as_decimal(item["completed_quantity"]) for item in items), Decimal(0))
        ),
        "qualified_quantity": quantize_quantity(
            sum((as_decimal(item["qualified_quantity"]) for item in items), Decimal(0))
        ),
        "defective_quantity": quantize_quantity(
            sum((as_decimal(item["defective_quantity"]) for item in items), Decimal(0))
        ),
        "manufacturing_cost": quantize_money(
            sum(
                (
                    as_decimal(item["manufacturing_view"]["total"]["amount"])
                    for item in items
                ),
                Decimal(0),
            )
        ),
        "post_manufacturing_cost": quantize_money(
            sum(
                (
                    as_decimal(
                        item["variable_fixed_view"]["after_sales_compensation"][
                            "amount"
                        ]
                    )
                    + as_decimal(
                        item["variable_fixed_view"]["transportation"]["amount"]
                    )
                    + as_decimal(item["variable_fixed_view"]["storage_fee"]["amount"])
                    for item in items
                ),
                Decimal(0),
            )
        ),
    }


def aggregate_part_period_costs(
    items: list[dict[str, Any]],
) -> dict[str, Any]:
    """Aggregate all final batches for one part and completion period.

    The aggregation is performed from server-calculated batch amounts.  It
    deliberately rebuilds the three views from the stable six-group/leaf
    vectors instead of averaging unit costs, so defective quantities and
    differing batch sizes retain their correct weighting.
    """

    if not items:
        raise ValueError("零件期间没有可汇总的产成品批次。")

    first = items[0]
    completed = quantize_quantity(
        sum((as_decimal(item["completed_quantity"]) for item in items), Decimal(0))
    )
    qualified = quantize_quantity(
        sum((as_decimal(item["qualified_quantity"]) for item in items), Decimal(0))
    )
    defective = quantize_quantity(
        sum((as_decimal(item["defective_quantity"]) for item in items), Decimal(0))
    )

    group_amounts: dict[str, Decimal] = {
        group: quantize_money(
            sum(
                (
                    as_decimal(
                        next(
                            (
                                group_item["metric"]["amount"]
                                for group_item in item["manufacturing_view"]["groups"]
                                if group_item["group_code"] == group
                            ),
                            Decimal(0),
                        )
                    )
                    for item in items
                ),
                Decimal(0),
            )
        )
        for group in MANUFACTURING_GROUP_CODES
    }
    leaf_amounts: dict[str, Decimal] = {
        code: quantize_money(
            sum(
                (
                    as_decimal(leaf["metric"]["amount"])
                    for item in items
                    for group in item["manufacturing_view"]["groups"]
                    for leaf in group["leaves"]
                    if leaf["cost_code"] == code
                ),
                Decimal(0),
            )
        )
        for group in MANUFACTURING_GROUP_CODES
        for code in COST_GROUPS[group]
    }
    manufacturing_total = quantize_money(sum(group_amounts.values(), Decimal(0)))

    groups: list[dict[str, Any]] = []
    for group in MANUFACTURING_GROUP_CODES:
        amount = group_amounts[group]
        leaves = [
            {
                "cost_code": code,
                "label": COST_CODE_LABELS[code],
                "metric": _metric(leaf_amounts[code], completed),
                "share": quantize_percent(leaf_amounts[code] / amount * Decimal(100))
                if amount
                else Decimal("0.00"),
            }
            for code in COST_GROUPS[group]
        ]
        groups.append(
            {
                "group_code": group,
                "group_label": COST_GROUP_LABELS[group],
                "metric": _metric(amount, completed),
                "share": quantize_percent(amount / manufacturing_total * Decimal(100))
                if manufacturing_total
                else Decimal("0.00"),
                "leaves": leaves,
            }
        )

    def amount_for(view_key: str, metric_key: str) -> Decimal:
        return quantize_money(
            sum(
                (as_decimal(item[view_key][metric_key]["amount"]) for item in items),
                Decimal(0),
            )
        )

    material = quantize_money(group_amounts["direct_material"])
    labor = quantize_money(
        group_amounts["direct_labor"] + group_amounts["indirect_labor"]
    )
    overhead = quantize_money(
        group_amounts["direct_energy"]
        + group_amounts["main_equipment_depreciation"]
        + group_amounts["manufacturing_overhead"]
    )
    variable_1 = quantize_money(
        group_amounts["direct_material"]
        + group_amounts["direct_labor"]
        + group_amounts["direct_energy"]
    )
    fixed_1 = quantize_money(
        group_amounts["indirect_labor"]
        + group_amounts["main_equipment_depreciation"]
        + group_amounts["manufacturing_overhead"]
    )
    after_sales = amount_for("variable_fixed_view", "after_sales_compensation")
    transportation = amount_for("variable_fixed_view", "transportation")
    storage = amount_for("variable_fixed_view", "storage_fee")
    variable_2 = quantize_money(variable_1 + after_sales + transportation)
    fixed_2 = quantize_money(fixed_1 + storage)
    total_2 = quantize_money(variable_2 + fixed_2)

    summaries = [
        {key: value for key, value in item.items() if key != "trace"} for item in items
    ]
    return {
        "part": first["part"],
        "period": first["period"],
        "unit": first["unit"],
        "batch_summary": {
            "batch_count": len(items),
            "completed_quantity": completed,
            "qualified_quantity": qualified,
            "defective_quantity": defective,
            "quality_rate": quantize_percent(qualified / completed * Decimal(100))
            if completed
            else Decimal("0.00"),
        },
        "manufacturing_view": {
            "groups": groups,
            "total": _metric(manufacturing_total, completed),
        },
        "material_labor_overhead_view": {
            "material": _metric(material, completed),
            "labor": _metric(labor, completed),
            "overhead": _metric(overhead, completed),
            "total": _metric(quantize_money(material + labor + overhead), completed),
        },
        "variable_fixed_view": {
            "variable_cost_1": _metric(variable_1, completed),
            "fixed_cost_1": _metric(fixed_1, completed),
            "manufacturing_total": _metric(manufacturing_total, completed),
            "after_sales_compensation": _metric(after_sales, completed),
            "transportation": _metric(transportation, completed),
            "storage_fee": _metric(storage, completed),
            "variable_cost_2": _metric(variable_2, completed),
            "fixed_cost_2": _metric(fixed_2, completed),
            "total_cost_2": _metric(total_2, completed),
        },
        "finished_batches": summaries,
        "manufacturing_cost": manufacturing_total,
        "post_manufacturing_cost": quantize_money(
            after_sales + transportation + storage
        ),
        "calculation_policy": {
            **calculation_policy_manifest(),
            "aggregation": "sum batch amounts; derive unit costs from total completed quantity",
        },
    }
