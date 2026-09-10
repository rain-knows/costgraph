import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))

from app.domain.authorization import build_execution_context
from app.services.cost_calculation_service import calculate_finished_batch_cost
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
        "auto", ["system_help", "cost_calculation", "report_generation"]
    )
    source = CostFixtureRepository().load_finished_batch_source("FG-A-2026-06", context)
    if source is None:
        raise RuntimeError("找不到黄金产成品批次 FG-A-2026-06")
    calculation = calculate_finished_batch_cost(source)
    analysis = generate_cost_analysis_with_llm(
        calculation["part"], "2026-06", calculation
    )
    print(f"analysis={analysis}")
