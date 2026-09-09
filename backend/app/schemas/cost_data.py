from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

Period = str
Currency = Literal["CNY"]
CostItemId = Literal["material", "labor", "equipment", "energy", "overhead"]


class PeriodComparison(BaseModel):
    model_config = ConfigDict(extra="forbid")

    previous_period: Period
    previous_total_cost: Decimal = Field(ge=0, decimal_places=2)
    previous_unit_cost: Decimal = Field(ge=0, decimal_places=2)
    total_cost_delta: Decimal = Field(decimal_places=2)
    total_cost_delta_rate: Decimal = Field(decimal_places=2)
    unit_cost_delta: Decimal = Field(decimal_places=2)
    unit_cost_delta_rate: Decimal = Field(decimal_places=2)


class OverviewComparison(BaseModel):
    model_config = ConfigDict(extra="forbid")

    previous_period: Period
    total_cost_delta: Decimal = Field(decimal_places=2)
    total_cost_delta_rate: Decimal = Field(decimal_places=2)
    unit_cost_delta: Decimal = Field(decimal_places=2)
    unit_cost_delta_rate: Decimal = Field(decimal_places=2)


class CostOverview(BaseModel):
    model_config = ConfigDict(extra="forbid")

    period: Period = Field(pattern=r"^\d{4}-(0[1-9]|1[0-2])$")
    currency: Currency = "CNY"
    product_count: int = Field(ge=0)
    total_output_qty: Decimal = Field(ge=0, decimal_places=2)
    total_cost: Decimal = Field(ge=0, decimal_places=2)
    average_unit_cost: Decimal | None = Field(default=None, ge=0, decimal_places=2)
    comparison: OverviewComparison | None = None


class ProductPeriodSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    product_id: str
    product_name: str
    spec: str | None = None
    period: Period = Field(pattern=r"^\d{4}-(0[1-9]|1[0-2])$")
    currency: Currency = "CNY"
    output_qty: Decimal = Field(gt=0, decimal_places=2)
    total_cost: Decimal = Field(ge=0, decimal_places=2)
    unit_cost: Decimal = Field(ge=0, decimal_places=2)
    comparison: PeriodComparison | None = None


class ProductPeriodList(BaseModel):
    model_config = ConfigDict(extra="forbid")

    period: Period = Field(pattern=r"^\d{4}-(0[1-9]|1[0-2])$")
    items: list[ProductPeriodSummary]
    page: int = Field(ge=1)
    page_size: int = Field(ge=1, le=100)
    total: int = Field(ge=0)


class CostItemDetail(BaseModel):
    model_config = ConfigDict(extra="forbid")

    cost_item: CostItemId
    label: str
    amount: Decimal = Field(ge=0, decimal_places=2)
    source_record_count: int = Field(ge=1)


class ProcessCostDetail(BaseModel):
    model_config = ConfigDict(extra="forbid")

    process_code: str
    process_name: str
    process_sort: int = Field(ge=1)
    total_cost: Decimal = Field(ge=0, decimal_places=2)
    items: list[CostItemDetail] = Field(min_length=1)


class CostSourceSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    production_output_count: int = Field(ge=1)
    process_cost_entry_count: int = Field(ge=1)
    start_date: date
    end_date: date
    source_systems: list[str] = Field(min_length=1)


class ProductIdentity(BaseModel):
    model_config = ConfigDict(extra="forbid")

    product_id: str
    product_name: str
    spec: str | None = None


class ProductPeriodDetail(BaseModel):
    model_config = ConfigDict(extra="forbid")

    product: ProductIdentity
    period: Period = Field(pattern=r"^\d{4}-(0[1-9]|1[0-2])$")
    currency: Currency = "CNY"
    output_qty: Decimal = Field(gt=0, decimal_places=2)
    total_cost: Decimal = Field(ge=0, decimal_places=2)
    unit_cost: Decimal = Field(ge=0, decimal_places=2)
    comparison: PeriodComparison | None = None
    processes: list[ProcessCostDetail] = Field(min_length=1)
    source_summary: CostSourceSummary
