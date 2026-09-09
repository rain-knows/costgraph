from __future__ import annotations

from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

REPORT_SCHEMA_VERSION = "2.0"


class ReportPart(BaseModel):
    model_config = ConfigDict(extra="forbid")

    part_id: str
    part_number: str
    part_description: str
    part_type: Literal[
        "raw_material",
        "purchased_semi_finished",
        "work_in_progress",
        "finished_good",
    ]
    product_family: str | None = None


class CostMetric(BaseModel):
    model_config = ConfigDict(extra="forbid")

    amount: Decimal = Field(ge=0, decimal_places=2)
    unit_cost: Decimal = Field(ge=0, decimal_places=2)


class ManufacturingCostLeaf(BaseModel):
    model_config = ConfigDict(extra="forbid")

    cost_code: str
    label: str
    metric: CostMetric
    share: Decimal = Field(ge=0, decimal_places=2)


class ManufacturingCostGroup(BaseModel):
    model_config = ConfigDict(extra="forbid")

    group_code: str
    group_label: str
    metric: CostMetric
    share: Decimal = Field(ge=0, decimal_places=2)
    leaves: list[ManufacturingCostLeaf]


class ManufacturingCostView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    groups: list[ManufacturingCostGroup] = Field(min_length=6, max_length=6)
    total: CostMetric


class MaterialLaborOverheadView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    material: CostMetric
    labor: CostMetric
    overhead: CostMetric
    total: CostMetric


class VariableFixedCostView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    variable_cost_1: CostMetric
    fixed_cost_1: CostMetric
    manufacturing_total: CostMetric
    after_sales_compensation: CostMetric
    transportation: CostMetric
    storage_fee: CostMetric
    variable_cost_2: CostMetric
    fixed_cost_2: CostMetric
    total_cost_2: CostMetric


class FinishedBatchCost(BaseModel):
    model_config = ConfigDict(extra="forbid")

    finished_batch_id: str
    event_id: str
    part: ReportPart
    period: str = Field(pattern=r"^\d{4}-(0[1-9]|1[0-2])$")
    completion_time: str
    cost_center_code: str | None = None
    cost_center_name: str | None = None
    work_order_number: str | None = None
    lot_number: str
    process_code: str | None = None
    process_name: str | None = None
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


class BatchSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    batch_count: int = Field(ge=1)
    completed_quantity: Decimal = Field(gt=0, decimal_places=4)
    qualified_quantity: Decimal = Field(ge=0, decimal_places=4)
    defective_quantity: Decimal = Field(ge=0, decimal_places=4)
    quality_rate: Decimal = Field(ge=0, le=100, decimal_places=2)


class SummaryCard(BaseModel):
    model_config = ConfigDict(extra="forbid")

    label: str
    value: Decimal | int
    unit: str


class InsightCard(BaseModel):
    model_config = ConfigDict(extra="forbid")

    label: str
    value: str
    description: str


class LineageTable(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: Literal[
        "cost_data.parts",
        "cost_data.cost_events",
        "cost_data.cost_event_inputs",
        "cost_data.cost_records",
    ]
    record_count: int = Field(ge=0)
    record_id_sample: list[str]


class Lineage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["2.0"]
    source: Literal["postgresql_cost_data"]
    query_scope: dict[str, Any]
    tables: list[LineageTable] = Field(min_length=4, max_length=4)
    data_snapshot_id: str = Field(pattern=r"^[0-9a-f]{64}$")


class PublicTraceCall(BaseModel):
    model_config = ConfigDict(extra="forbid")

    node: str | None = None
    purpose: str | None = None
    provider: str | None = None
    model: str | None = None
    status: str | None = None
    duration_ms: int | float | None = None
    error: str | None = None
    finish_reason: str | None = None
    usage: dict[str, Any] | None = None


class PublicAiTrace(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: str
    provider: str | None = None
    configured_model: str | None = None
    calls: list[PublicTraceCall]
    guardrails: list[str]
    route_selection: dict[str, Any] | None = None
    clarification_decision: dict[str, Any] | None = None
    deterministic_calculation: dict[str, Any]
    redacted_fields: list[str]
    runtime_version: str | None = None
    workflow_version: str | None = None
    provider_version: str | None = None
    tool_versions: dict[str, str] | None = None
    capabilities: list[str] | None = None
    context_summary: dict[str, Any] | None = None
    policy_decisions: list[dict[str, Any]] | None = None
    tool_calls: list[dict[str, Any]] | None = None


class ReportEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    node: str
    status: str
    summary: str
    started_at: str | None = None
    finished_at: str | None = None


class CostReportV2(BaseModel):
    model_config = ConfigDict(extra="forbid")

    report_schema_version: Literal["2.0"]
    rule_version: str
    prompt_version: str
    code_version: str
    data_snapshot_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    run_id: str
    part: ReportPart
    period: str = Field(pattern=r"^\d{4}-(0[1-9]|1[0-2])$")
    batch_summary: BatchSummary
    summary_cards: list[SummaryCard] = Field(min_length=3)
    manufacturing_view: ManufacturingCostView
    material_labor_overhead_view: MaterialLaborOverheadView
    variable_fixed_view: VariableFixedCostView
    finished_batches: list[FinishedBatchCost] = Field(min_length=1)
    insight_cards: list[InsightCard]
    calculation_formula: list[str]
    calculation_policy: dict[str, str]
    analysis_text: str
    source_summary: str
    lineage: Lineage
    model_info: dict[str, Any]
    ai_trace: PublicAiTrace
    agent_steps: list[ReportEvent]
