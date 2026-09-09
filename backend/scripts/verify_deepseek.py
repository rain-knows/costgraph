import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))

from app.services.llm_service import (
    generate_cost_analysis_with_llm,
    get_deepseek_model,
    parse_cost_question_with_llm,
)

if __name__ == "__main__":
    parsed = parse_cost_question_with_llm("查询产品A 2026年6月单位成本，并说明成本构成")
    print(f"model={get_deepseek_model()}")
    print(f"parsed={parsed}")

    analysis = generate_cost_analysis_with_llm(
        {"product_name": "产品A"},
        "2026-06",
        {
            "output_qty": 10000,
            "total_cost": 139000,
            "unit_cost": 13.9,
            "process_breakdown": [
                {"process_name": "注塑", "total_cost": 61200},
                {"process_name": "喷漆", "total_cost": 29000},
                {"process_name": "电镀", "total_cost": 26400},
                {"process_name": "装配", "total_cost": 22400},
            ],
            "cost_composition": [
                {"name": "材料", "value": 80000},
                {"name": "人工", "value": 29300},
                {"name": "设备", "value": 12400},
                {"name": "能耗", "value": 7100},
                {"name": "制造费用", "value": 10200},
            ],
        },
    )
    print(f"analysis={analysis}")
