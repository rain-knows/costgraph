from __future__ import annotations

from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

CALCULATION_RULE_VERSION = "cost-calculation-v1"
CURRENCY = "CNY"
MONEY_QUANTUM = Decimal("0.01")
QUANTITY_QUANTUM = Decimal("0.01")
UNIT_COST_QUANTUM = Decimal("0.01")
ROUNDING_MODE = ROUND_HALF_UP


def as_decimal(value: Decimal | float | str) -> Decimal:
    return value if isinstance(value, Decimal) else Decimal(str(value))


def quantize_money(value: Decimal | float | str) -> Decimal:
    return as_decimal(value).quantize(MONEY_QUANTUM, rounding=ROUNDING_MODE)


def quantize_quantity(value: Decimal | float | str) -> Decimal:
    return as_decimal(value).quantize(QUANTITY_QUANTUM, rounding=ROUNDING_MODE)


def quantize_unit_cost(value: Decimal | float | str) -> Decimal:
    return as_decimal(value).quantize(UNIT_COST_QUANTUM, rounding=ROUNDING_MODE)


def decimal_to_number(value: Decimal) -> int | float:
    normalized = value.normalize()
    return int(normalized) if normalized == normalized.to_integral() else float(value)


class ProductRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    product_id: str = Field(min_length=1)
    product_name: str = Field(min_length=1)
    spec: str | None = None


class SourceTables(BaseModel):
    model_config = ConfigDict(extra="forbid")

    production_outputs: list[str] = Field(min_length=1)
    process_cost_entries: list[str] = Field(min_length=1)


class SourceSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    production_outputs: int = Field(ge=0)
    process_cost_entries: int = Field(ge=0)
    process_count: int = Field(ge=0)
    start_date: date
    end_date: date

    @model_validator(mode="after")
    def validate_date_range(self) -> SourceSummary:
        if self.start_date > self.end_date:
            raise ValueError("来源开始日期不能晚于结束日期")
        return self


class ProcessCostInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    process_name: str = Field(min_length=1)
    material_cost: Decimal = Field(ge=0, decimal_places=2)
    labor_cost: Decimal = Field(ge=0, decimal_places=2)
    equipment_cost: Decimal = Field(ge=0, decimal_places=2)
    energy_cost: Decimal = Field(ge=0, decimal_places=2)
    overhead_cost: Decimal = Field(ge=0, decimal_places=2)


class CostInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    product_id: str = Field(min_length=1)
    period: str = Field(pattern=r"^\d{4}-(0[1-9]|1[0-2])$")
    production_date: date | None = None
    output_qty: Decimal = Field(gt=0)
    process_costs: list[ProcessCostInput] = Field(min_length=1)
    source_tables: SourceTables
    source_summary: SourceSummary

    @model_validator(mode="after")
    def validate_source_consistency(self) -> CostInput:
        if (
            len(self.source_tables.production_outputs)
            != self.source_summary.production_outputs
        ):
            raise ValueError("产量来源记录数量与来源摘要不一致")
        if (
            len(self.source_tables.process_cost_entries)
            != self.source_summary.process_cost_entries
        ):
            raise ValueError("成本来源记录数量与来源摘要不一致")
        if len(self.process_costs) != self.source_summary.process_count:
            raise ValueError("工序数量与来源摘要不一致")
        if self.production_date and not (
            self.source_summary.start_date
            <= self.production_date
            <= self.source_summary.end_date
        ):
            raise ValueError("生产日期不在来源日期范围内")
        return self


class ProcessCostResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    process_name: str
    material_cost: Decimal
    labor_cost: Decimal
    equipment_cost: Decimal
    energy_cost: Decimal
    overhead_cost: Decimal
    total_cost: Decimal


class CompositionItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    value: Decimal


class CostResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    output_qty: Decimal = Field(gt=0)
    total_cost: Decimal
    unit_cost: Decimal
    process_breakdown: list[ProcessCostResult] = Field(min_length=1)
    cost_composition: list[CompositionItem] = Field(min_length=1)
    date_range: dict[str, Any] | None = None
    calculation_policy: dict[str, str]


def calculation_policy_manifest() -> dict[str, str]:
    return {
        "rule_version": CALCULATION_RULE_VERSION,
        "currency": CURRENCY,
        "internal_number": "decimal",
        "money_scale": "2",
        "quantity_scale": "2",
        "unit_cost_scale": "2",
        "rounding": "ROUND_HALF_UP",
        "process_total": "quantize components to 0.01 then sum displayed components",
        "product_total": "sum quantized process totals then quantize to 0.01",
        "unit_cost": "product total / qualified output then quantize to 0.01",
    }


def cost_input_to_internal(value: dict[str, Any] | CostInput) -> CostInput:
    return value if isinstance(value, CostInput) else CostInput.model_validate(value)


def cost_input_to_state(value: dict[str, Any]) -> dict[str, Any]:
    return CostInput.model_validate(value).model_dump(mode="json")


def cost_result_to_public(value: CostResult) -> dict[str, Any]:
    result = value.model_dump(mode="python")

    def convert(item: Any) -> Any:
        if isinstance(item, Decimal):
            return decimal_to_number(item)
        if isinstance(item, date):
            return item.isoformat()
        if isinstance(item, dict):
            return {key: convert(inner) for key, inner in item.items()}
        if isinstance(item, list):
            return [convert(inner) for inner in item]
        return item

    return convert(result)
