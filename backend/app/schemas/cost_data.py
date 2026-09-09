from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

Period = str
Currency = Literal["CNY"]
PartType = Literal[
    "raw_material",
    "purchased_semi_finished",
    "work_in_progress",
    "finished_good",
]
EventType = Literal["purchase", "process"]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PartIdentity(StrictModel):
    part_id: str
    part_number: str
    part_description: str
    part_type: PartType
    product_family: str | None = None


class CostMetric(StrictModel):
    amount: Decimal = Field(ge=0, decimal_places=2)
    unit_cost: Decimal = Field(ge=0, decimal_places=2)


class CostLeaf(StrictModel):
    cost_code: str
    label: str
    metric: CostMetric
    share: Decimal = Field(ge=0, decimal_places=2)


class CostGroup(StrictModel):
    group_code: str
    group_label: str
    metric: CostMetric
    share: Decimal = Field(ge=0, decimal_places=2)
    leaves: list[CostLeaf]


class ManufacturingCostView(StrictModel):
    groups: list[CostGroup]
    total: CostMetric


class MaterialLaborOverheadView(StrictModel):
    material: CostMetric
    labor: CostMetric
    overhead: CostMetric
    total: CostMetric


class VariableFixedCostView(StrictModel):
    variable_cost_1: CostMetric
    fixed_cost_1: CostMetric
    manufacturing_total: CostMetric
    after_sales_compensation: CostMetric
    transportation: CostMetric
    storage_fee: CostMetric
    variable_cost_2: CostMetric
    fixed_cost_2: CostMetric
    total_cost_2: CostMetric


class CostOverview(StrictModel):
    period: Period = Field(pattern=r"^\d{4}-(0[1-9]|1[0-2])$")
    currency: Currency = "CNY"
    batch_count: int = Field(ge=0)
    part_count: int = Field(ge=0)
    completed_quantity: Decimal = Field(ge=0, decimal_places=4)
    qualified_quantity: Decimal = Field(ge=0, decimal_places=4)
    defective_quantity: Decimal = Field(ge=0, decimal_places=4)
    quality_rate: Decimal = Field(ge=0, le=100, decimal_places=2)
    manufacturing_cost: Decimal = Field(ge=0, decimal_places=2)
    post_manufacturing_cost: Decimal = Field(ge=0, decimal_places=2)
    total_cost: Decimal = Field(ge=0, decimal_places=2)


class FinishedBatchBase(StrictModel):
    finished_batch_id: str
    event_id: str
    part: PartIdentity
    period: Period = Field(pattern=r"^\d{4}-(0[1-9]|1[0-2])$")
    completion_time: datetime
    cost_center_code: str
    cost_center_name: str
    work_order_number: str
    lot_number: str
    process_code: str
    process_name: str
    qualified_quantity: Decimal = Field(ge=0, decimal_places=4)
    defective_quantity: Decimal = Field(ge=0, decimal_places=4)
    completed_quantity: Decimal = Field(gt=0, decimal_places=4)
    quality_rate: Decimal = Field(ge=0, le=100, decimal_places=2)
    unit: str
    machine_hours: Decimal = Field(ge=0, decimal_places=4)
    labor_hours: Decimal = Field(ge=0, decimal_places=4)
    manufacturing_view: ManufacturingCostView
    material_labor_overhead_view: MaterialLaborOverheadView
    variable_fixed_view: VariableFixedCostView


class FinishedBatchCostSummary(FinishedBatchBase):
    pass


class FinishedBatchCostList(StrictModel):
    period: Period = Field(pattern=r"^\d{4}-(0[1-9]|1[0-2])$")
    items: list[FinishedBatchCostSummary]
    page: int = Field(ge=1)
    page_size: int = Field(ge=1, le=100)
    total: int = Field(ge=0)


class CostVectorItem(StrictModel):
    cost_code: str
    cost_group: str
    label: str
    amount: Decimal = Field(ge=0, decimal_places=2)


class CostVector(StrictModel):
    items: list[CostVectorItem]
    total_amount: Decimal = Field(ge=0, decimal_places=2)


class CostTraceNode(StrictModel):
    event_id: str
    event_type: EventType
    output_batch_id: str
    part: PartIdentity
    completion_time: datetime
    cost_center_code: str | None = None
    cost_center_name: str | None = None
    work_order_number: str | None = None
    lot_number: str | None = None
    process_code: str | None = None
    process_name: str | None = None
    qualified_quantity: Decimal = Field(ge=0, decimal_places=4)
    defective_quantity: Decimal = Field(ge=0, decimal_places=4)
    completed_quantity: Decimal = Field(gt=0, decimal_places=4)
    unit: str
    direct_costs: CostVector
    inherited_costs: CostVector
    accumulated_costs: CostVector
    display_unit_cost: Decimal = Field(ge=0, decimal_places=2)
    transfer_unit_cost: Decimal | None = Field(default=None, ge=0, decimal_places=2)


class CostTraceEdge(StrictModel):
    input_id: str
    source_event_id: str
    target_event_id: str
    consumed_quantity: Decimal = Field(gt=0, decimal_places=4)
    unit: str
    # The API carries the ratio at six decimal places for stable display.  A
    # valid four-decimal input quantity can round to 0.000000 at that scale
    # (for example 0.0001 / 1000), while the server still allocates the exact
    # Decimal ratio before quantizing the public representation.
    allocation_ratio: Decimal = Field(ge=0, le=1, decimal_places=6)
    allocated_costs: CostVector


class CostTraceRecord(StrictModel):
    cost_record_id: str
    event_id: str
    cost_code: str
    cost_group: str
    cost_label: str
    amount: Decimal = Field(ge=0, decimal_places=2)
    currency: Currency = "CNY"
    incurred_at: datetime
    source_system: str
    source_document_no: str
    source_document_line: str | None = None
    source_record_id: str
    raw_payload: dict[str, Any] | None = None


class CostTraceGraph(StrictModel):
    root_event_id: str
    nodes: list[CostTraceNode]
    edges: list[CostTraceEdge]
    records: list[CostTraceRecord]


class FinishedBatchCostDetail(FinishedBatchBase):
    trace: CostTraceGraph
