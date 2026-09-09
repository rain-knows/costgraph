from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

REPORT_SCHEMA_VERSION = "1.0"


class ReportProduct(BaseModel):
    model_config = ConfigDict(extra="forbid")

    product_id: str
    product_name: str
    spec: str | None = None


class SummaryCard(BaseModel):
    model_config = ConfigDict(extra="forbid")

    label: str
    value: int | float
    unit: str


class InsightCard(BaseModel):
    model_config = ConfigDict(extra="forbid")

    label: str
    value: str
    description: str


class ProcessCost(BaseModel):
    model_config = ConfigDict(extra="forbid")

    process_name: str
    total_cost: int | float
    material_cost: int | float
    labor_cost: int | float
    equipment_cost: int | float
    energy_cost: int | float
    overhead_cost: int | float


class CompositionItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    value: int | float


class ReportDateRange(BaseModel):
    model_config = ConfigDict(extra="forbid")

    start_date: str
    end_date: str


class ProcessChange(BaseModel):
    model_config = ConfigDict(extra="forbid")

    process_name: str
    current_total_cost: int | float
    previous_total_cost: int | float
    delta: int | float
    delta_rate: int | float


class CostComparison(BaseModel):
    model_config = ConfigDict(extra="forbid")

    previous_period: str
    current_unit_cost: int | float
    previous_unit_cost: int | float
    unit_cost_delta: int | float
    unit_cost_delta_rate: int | float
    process_changes: list[ProcessChange]
    top_increase_process: ProcessChange | None = None
    top_cost_process: ProcessCost | None = None


class LineageTable(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    record_count: int = Field(ge=0)
    record_id_sample: list[str]


class Lineage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: str
    source: str
    query_scope: dict[str, Any]
    tables: list[LineageTable]
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


class CostReportV1(BaseModel):
    model_config = ConfigDict(extra="forbid")

    report_schema_version: Literal["1.0"]
    rule_version: str
    prompt_version: str
    code_version: str
    data_snapshot_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    run_id: str
    product: ReportProduct
    period: str
    date_range: ReportDateRange | None = None
    summary_cards: list[SummaryCard] = Field(min_length=3)
    process_cost_breakdown: list[ProcessCost] = Field(min_length=1)
    cost_composition_chart: list[CompositionItem] = Field(min_length=1)
    insight_cards: list[InsightCard]
    comparison: CostComparison | None = None
    calculation_formula: list[str]
    calculation_policy: dict[str, str]
    analysis_text: str
    source_summary: str
    lineage: Lineage
    model_info: dict[str, Any]
    ai_trace: PublicAiTrace
    agent_steps: list[ReportEvent]
