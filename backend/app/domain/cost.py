from __future__ import annotations

from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal
from graphlib import CycleError, TopologicalSorter
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

CALCULATION_RULE_VERSION = "cost-rollup-v2"
CURRENCY = "CNY"
MONEY_QUANTUM = Decimal("0.01")
QUANTITY_QUANTUM = Decimal("0.0001")
UNIT_COST_QUANTUM = Decimal("0.01")
PERCENT_QUANTUM = Decimal("0.01")
RATIO_QUANTUM = Decimal("0.000001")
ROUNDING_MODE = ROUND_HALF_UP

COST_GROUP_LABELS: dict[str, str] = {
    "direct_material": "直接材料",
    "direct_labor": "直接人工",
    "direct_energy": "直接能耗",
    "indirect_labor": "间接人工",
    "main_equipment_depreciation": "主设备折旧",
    "manufacturing_overhead": "制造费用",
    "post_manufacturing": "制造后费用",
}
COST_GROUPS: dict[str, tuple[str, ...]] = {
    "direct_material": (
        "raw_material",
        "purchased_semi_finished",
        "outsourced_processing",
    ),
    "direct_labor": (
        "direct_wages",
        "direct_welfare",
        "direct_social_insurance",
        "direct_housing_fund",
        "direct_commercial_insurance",
        "direct_disability_fund",
        "direct_employee_education",
    ),
    "direct_energy": ("water", "electricity", "natural_gas"),
    "indirect_labor": (
        "indirect_wages",
        "indirect_welfare",
        "indirect_social_insurance",
        "indirect_housing_fund",
        "indirect_commercial_insurance",
        "indirect_disability_fund",
        "indirect_employee_education",
        "allocated_indirect_labor",
    ),
    "main_equipment_depreciation": (
        "equipment_depreciation",
        "building_depreciation",
        "office_electronics_depreciation",
        "vehicle_depreciation",
        "intangible_asset_amortization",
    ),
    "manufacturing_overhead": (
        "workshop_depreciation_amortization",
        "allocated_indirect_depreciation",
        "workshop_indirect_energy",
        "allocated_indirect_energy",
        "allocated_indirect_overhead",
        "rent",
        "manufacturing_storage_fee",
        "loading_handling",
        "testing_inspection",
        "repair",
        "maintenance",
        "safety_environmental",
        "low_value_consumables",
        "machine_material_consumption",
        "labor_protection",
        "office_expense",
        "travel_expense",
        "business_entertainment",
        "other_manufacturing_overhead",
    ),
    "post_manufacturing": (
        "after_sales_compensation",
        "transportation",
        "post_manufacturing_storage_fee",
    ),
}
COST_CODE_LABELS: dict[str, str] = {
    "raw_material": "原材料",
    "purchased_semi_finished": "外购半成品",
    "outsourced_processing": "外协加工费",
    "direct_wages": "工资",
    "direct_welfare": "福利费",
    "direct_social_insurance": "社会保险费",
    "direct_housing_fund": "住房公积金",
    "direct_commercial_insurance": "员工商业保险",
    "direct_disability_fund": "残疾人保障金",
    "direct_employee_education": "职工教育经费",
    "water": "水费",
    "electricity": "电费",
    "natural_gas": "天然气费",
    "indirect_wages": "工资",
    "indirect_welfare": "福利费",
    "indirect_social_insurance": "社会保险费",
    "indirect_housing_fund": "住房公积金",
    "indirect_commercial_insurance": "员工商业保险",
    "indirect_disability_fund": "残疾人保障金",
    "indirect_employee_education": "职工教育经费",
    "allocated_indirect_labor": "分摊人工（间接部门）",
    "equipment_depreciation": "设备类",
    "building_depreciation": "房屋类",
    "office_electronics_depreciation": "办公电子类",
    "vehicle_depreciation": "运输类",
    "intangible_asset_amortization": "无形资产摊销",
    "workshop_depreciation_amortization": "车间折旧及摊销（除主设备折旧）",
    "allocated_indirect_depreciation": "分摊折旧（间接部门）",
    "workshop_indirect_energy": "车间间接能耗",
    "allocated_indirect_energy": "分摊能耗（间接部门）",
    "allocated_indirect_overhead": "分摊制费（间接部门）",
    "rent": "租赁费",
    "manufacturing_storage_fee": "仓储保管费",
    "loading_handling": "装卸搬运费",
    "testing_inspection": "试验检测费",
    "repair": "维修费",
    "maintenance": "维保费",
    "safety_environmental": "安全环保",
    "low_value_consumables": "低值易耗品",
    "machine_material_consumption": "机物料消耗",
    "labor_protection": "劳动保护费",
    "office_expense": "办公费",
    "travel_expense": "差旅费",
    "business_entertainment": "业务招待费",
    "other_manufacturing_overhead": "其他",
    "after_sales_compensation": "售后赔偿费",
    "transportation": "运输费",
    "post_manufacturing_storage_fee": "仓储保管费",
}

MANUFACTURING_GROUP_CODES = tuple(COST_GROUPS)[:6]
VARIABLE_1_GROUP_CODES = ("direct_material", "direct_labor", "direct_energy")
FIXED_1_GROUP_CODES = (
    "indirect_labor",
    "main_equipment_depreciation",
    "manufacturing_overhead",
)
COST_CODE_GROUP = {
    cost_code: group_code
    for group_code, cost_codes in COST_GROUPS.items()
    for cost_code in cost_codes
}
COST_CODES = tuple(COST_CODE_GROUP)


def as_decimal(value: Decimal | float | str) -> Decimal:
    return value if isinstance(value, Decimal) else Decimal(str(value))


def quantize_money(value: Decimal | float | str) -> Decimal:
    return as_decimal(value).quantize(MONEY_QUANTUM, rounding=ROUNDING_MODE)


def quantize_quantity(value: Decimal | float | str) -> Decimal:
    return as_decimal(value).quantize(QUANTITY_QUANTUM, rounding=ROUNDING_MODE)


def quantize_unit_cost(value: Decimal | float | str) -> Decimal:
    return as_decimal(value).quantize(UNIT_COST_QUANTUM, rounding=ROUNDING_MODE)


def quantize_percent(value: Decimal | float | str) -> Decimal:
    return as_decimal(value).quantize(PERCENT_QUANTUM, rounding=ROUNDING_MODE)


def decimal_to_number(value: Decimal) -> int | float:
    normalized = value.normalize()
    return int(normalized) if normalized == normalized.to_integral() else float(value)


def empty_cost_vector() -> dict[str, Decimal]:
    return {cost_code: Decimal("0.00") for cost_code in COST_CODES}


def add_cost_vectors(*vectors: dict[str, Decimal]) -> dict[str, Decimal]:
    return {
        cost_code: quantize_money(
            sum(
                (as_decimal(vector.get(cost_code, 0)) for vector in vectors),
                Decimal(0),
            )
        )
        for cost_code in COST_CODES
    }


def _allocate_amounts(
    total: Decimal, ratios: list[Decimal], *, full_allocation: bool
) -> list[Decimal]:
    """Round edge allocations without creating a negative rounding tail.

    Each edge is first rounded with the public money rule.  For a complete
    source consumption, the cent difference is then assigned to the last
    ordered edge (or, when half-up rounding overshoots, removed from the
    edges that rounded up first).  This keeps every allocation non-negative
    and makes the full allocation sum exactly equal to the source vector.
    """

    rounded = [quantize_money(total * ratio) for ratio in ratios]
    if not full_allocation or not rounded:
        return rounded

    delta = quantize_money(total - sum(rounded, Decimal(0)))
    if delta > 0:
        rounded[-1] = quantize_money(rounded[-1] + delta)
        return rounded
    if delta == 0:
        return rounded

    # Half-up can overshoot when several fractional cent values round up.
    # Remove the excess from the most-upward-rounded edges, preserving the
    # stable input order for ties and never allowing a negative amount.
    need_cents = int((-delta / MONEY_QUANTUM).to_integral_value())
    candidates = sorted(
        range(len(rounded)),
        key=lambda index: (rounded[index] - total * ratios[index], -index),
        reverse=True,
    )
    for index in candidates:
        if need_cents <= 0:
            break
        available_cents = int((rounded[index] / MONEY_QUANTUM).to_integral_value())
        reduction_cents = min(available_cents, need_cents)
        rounded[index] = quantize_money(
            rounded[index] - reduction_cents * MONEY_QUANTUM
        )
        need_cents -= reduction_cents
    if need_cents:
        raise ValueError("成本边分配舍入无法保持非负且守恒。")
    return rounded


def cost_vector_total(
    vector: dict[str, Decimal], groups: tuple[str, ...] | None = None
) -> Decimal:
    codes = (
        COST_CODES
        if groups is None
        else tuple(code for group in groups for code in COST_GROUPS[group])
    )
    return quantize_money(
        sum((as_decimal(vector.get(code, 0)) for code in codes), Decimal(0))
    )


def event_completed_quantity(event: dict[str, Any]) -> Decimal:
    return quantize_quantity(
        as_decimal(event["qualified_quantity"])
        + as_decimal(event["defective_quantity"])
    )


def event_period(event: dict[str, Any]) -> str:
    value = event["completion_time"]
    if isinstance(value, (date, datetime)):
        return value.strftime("%Y-%m")
    return str(value)[:7]


class PartRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    part_id: str = Field(min_length=1)
    part_number: str = Field(min_length=1)
    part_description: str = Field(min_length=1)
    part_type: str = Field(
        pattern=r"^(raw_material|purchased_semi_finished|work_in_progress|finished_good)$"
    )
    product_family: str | None = None
    unit: str = Field(min_length=1)


class CostRollupResult(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    order: list[str]
    direct: dict[str, dict[str, Decimal]]
    inherited: dict[str, dict[str, Decimal]]
    accumulated: dict[str, dict[str, Decimal]]
    edge_allocations: dict[str, dict[str, Decimal]]
    edge_ratios: dict[str, Decimal]


def roll_up_cost_graph(
    events: list[dict[str, Any]],
    inputs: list[dict[str, Any]],
    records: list[dict[str, Any]],
) -> CostRollupResult:
    """Roll costs in topological order and allocate source vectors per input edge."""

    event_by_id = {str(event["event_id"]): event for event in events}
    predecessors = {event_id: set() for event_id in event_by_id}
    outgoing: dict[str, list[dict[str, Any]]] = {
        event_id: [] for event_id in event_by_id
    }
    incoming: dict[str, list[dict[str, Any]]] = {
        event_id: [] for event_id in event_by_id
    }
    for edge in inputs:
        source_id = str(edge["source_event_id"])
        target_id = str(edge["event_id"])
        if source_id not in event_by_id or target_id not in event_by_id:
            raise ValueError("投入关系引用了不存在的成本事件。")
        predecessors[target_id].add(source_id)
        outgoing[source_id].append(edge)
        incoming[target_id].append(edge)
    try:
        order = list(TopologicalSorter(predecessors).static_order())
    except CycleError as exc:
        raise ValueError("成本事件投入关系不能形成环路。") from exc

    direct = {event_id: empty_cost_vector() for event_id in event_by_id}
    for record in records:
        event_id = str(record["event_id"])
        cost_code = str(record["cost_code"])
        if event_id not in event_by_id:
            raise ValueError("费用记录引用了不存在的成本事件。")
        if cost_code not in COST_CODE_GROUP:
            raise ValueError(f"未知费用代码：{cost_code}")
        direct[event_id][cost_code] = quantize_money(
            direct[event_id][cost_code] + as_decimal(record["amount"])
        )

    inherited = {event_id: empty_cost_vector() for event_id in event_by_id}
    accumulated = {event_id: empty_cost_vector() for event_id in event_by_id}
    edge_allocations: dict[str, dict[str, Decimal]] = {}
    edge_ratios: dict[str, Decimal] = {}

    for event_id in order:
        inherited[event_id] = (
            add_cost_vectors(
                *(
                    edge_allocations[str(edge["input_id"])]
                    for edge in incoming[event_id]
                )
            )
            if incoming[event_id]
            else empty_cost_vector()
        )
        accumulated[event_id] = add_cost_vectors(direct[event_id], inherited[event_id])

        source_event = event_by_id[event_id]
        qualified = quantize_quantity(source_event["qualified_quantity"])
        source_edges = sorted(
            outgoing[event_id], key=lambda item: str(item["input_id"])
        )
        if source_edges and qualified <= 0:
            raise ValueError(f"被领用事件 {event_id} 的合格数量必须大于 0。")
        consumed_total = quantize_quantity(
            sum(
                (as_decimal(edge["consumed_quantity"]) for edge in source_edges),
                Decimal(0),
            )
        )
        if consumed_total > qualified:
            raise ValueError(f"事件 {event_id} 的累计领用数量超过合格数量。")

        ratios = [
            quantize_quantity(edge["consumed_quantity"]) / qualified
            for edge in source_edges
        ]
        full_allocation = consumed_total == qualified
        allocations_by_code: dict[str, list[Decimal]] = {}
        for cost_code in COST_CODES:
            allocations_by_code[cost_code] = _allocate_amounts(
                accumulated[event_id][cost_code],
                ratios,
                full_allocation=full_allocation,
            )

        for index, edge in enumerate(source_edges):
            input_id = str(edge["input_id"])
            edge_ratios[input_id] = ratios[index].quantize(
                RATIO_QUANTUM, rounding=ROUNDING_MODE
            )
            edge_allocations[input_id] = {
                cost_code: allocations_by_code[cost_code][index]
                for cost_code in COST_CODES
            }

    return CostRollupResult(
        order=order,
        direct=direct,
        inherited=inherited,
        accumulated=accumulated,
        edge_allocations=edge_allocations,
        edge_ratios=edge_ratios,
    )


def calculation_policy_manifest() -> dict[str, str]:
    return {
        "rule_version": CALCULATION_RULE_VERSION,
        "currency": CURRENCY,
        "internal_number": "decimal",
        "money_scale": "2",
        "quantity_scale": "4",
        "unit_cost_scale": "2",
        "percentage_scale": "2",
        "rounding": "ROUND_HALF_UP",
        "display_unit_cost": (
            "accumulated cost / (qualified quantity + defective quantity)"
        ),
        "transfer_unit_cost": "accumulated cost / qualified quantity",
        "allocation": ("leaf cost vector * consumed quantity / qualified quantity"),
        "full_allocation_remainder": (
            "last ordered edge receives cent rounding remainder"
        ),
    }


def cost_result_to_public(value: Any) -> Any:
    def convert(item: Any) -> Any:
        if isinstance(item, Decimal):
            return decimal_to_number(item)
        if isinstance(item, (date, datetime)):
            return item.isoformat()
        if isinstance(item, BaseModel):
            return convert(item.model_dump(mode="python"))
        if isinstance(item, dict):
            return {key: convert(inner) for key, inner in item.items()}
        if isinstance(item, (list, tuple)):
            return [convert(inner) for inner in item]
        return item

    return convert(value)
