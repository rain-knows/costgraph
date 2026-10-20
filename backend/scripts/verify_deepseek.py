import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))

from app.domain.authorization import ExecutionPrincipal, build_execution_context
from app.services.cost_calculation_service import aggregate_part_period_costs
from app.services.llm_service import (
    generate_cost_analysis_with_llm,
    get_deepseek_model,
    parse_cost_question_with_llm,
)
from evaluation.fixture_cost_repository import CostFixtureRepository

if __name__ == "__main__":
    question = "查询 FG-001 2026年6月单位成本，并说明成本构成"
    parsed = parse_cost_question_with_llm(question)
    print(f"model={get_deepseek_model()}")
    print(f"parsed={parsed}")

    context = build_execution_context(
        "auto",
        ["system_help", "cost_calculation", "report_generation"],
        principal=ExecutionPrincipal(
            principal_id="verification-user",
            tenant_id="verification-tenant",
            roles=("tenant_admin",),
        ),
    )
    batch = CostFixtureRepository().load_finished_batch_projection(
        "FG-A-2026-06", context
    )
    if batch is None:
        raise RuntimeError("找不到黄金产成品批次 FG-A-2026-06")
    # The analysis prompt consumes the same aggregated shape as the report
    # service, even when this verification fixture contains only one batch.
    calculation = aggregate_part_period_costs([batch])
    analysis = generate_cost_analysis_with_llm(
        calculation["part"], "2026-06", calculation
    )
    print(f"analysis={analysis}")
