from decimal import Decimal
from typing import Any

from app.domain.cost import CALCULATION_RULE_VERSION
from app.domain.report import REPORT_SCHEMA_VERSION, CostReportV2
from app.services.llm_service import PROMPT_VERSION
from app.settings import get_settings

FORMULAS = [
    "料 = 直接材料",
    "工 = 直接人工 + 间接人工",
    "费 = 直接能耗 + 主设备折旧 + 制造费用",
    "变动成本1 = 直接材料 + 直接人工 + 直接能耗",
    "固定成本1 = 间接人工 + 主设备折旧 + 制造费用",
    "变动成本2 = 变动成本1 + 售后赔偿费 + 运输费",
    "固定成本2 = 固定成本1 + 仓储保管费",
    "展示单位成本 = 批次累计成本 / (合格量 + 不良量)",
    "投入边可转移成本 = 上游累计成本 / 上游合格量 * 领用量",
]


def build_report_json(
    run_id: str,
    part: dict[str, Any],
    period: str,
    calculation_result: dict[str, Any],
    agent_steps: list[dict[str, Any]],
    analysis_text: str,
    model_info: dict[str, Any] | None = None,
    ai_trace: dict[str, Any] | None = None,
    lineage: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if lineage is None:
        raise ValueError("结构化报表必须提供数据血缘")
    if not isinstance(analysis_text, str) or not analysis_text.strip():
        raise ValueError("模型分析文本不能为空。")

    manufacturing_view = calculation_result["manufacturing_view"]
    variable_fixed_view = calculation_result["variable_fixed_view"]
    batch_summary = calculation_result["batch_summary"]
    top_group = max(
        manufacturing_view["groups"],
        key=lambda item: Decimal(str(item["metric"]["amount"])),
    )
    insight_cards = [
        {
            "label": "最大制造成本组",
            "value": top_group["group_label"],
            "description": f"{top_group['metric']['amount']} 元",
        },
        {
            "label": "完工批次",
            "value": str(batch_summary["batch_count"]),
            "description": f"{batch_summary['completed_quantity']} {calculation_result['unit']}",
        },
        {
            "label": "制造后费用",
            "value": f"{calculation_result['post_manufacturing_cost']} 元",
            "description": "售后赔偿费、运输费与仓储保管费",
        },
    ]
    report = {
        "report_schema_version": REPORT_SCHEMA_VERSION,
        "rule_version": CALCULATION_RULE_VERSION,
        "prompt_version": PROMPT_VERSION,
        "code_version": get_settings().app_version,
        "data_snapshot_id": lineage["data_snapshot_id"],
        "run_id": run_id,
        "part": {
            "part_id": part["part_id"],
            "part_number": part["part_number"],
            "part_description": part["part_description"],
            "part_type": part["part_type"],
            "product_family": part.get("product_family"),
        },
        "period": period,
        "batch_summary": batch_summary,
        "summary_cards": [
            {
                "label": "合计单位成本2",
                "value": variable_fixed_view["total_cost_2"]["unit_cost"],
                "unit": f"元/{calculation_result['unit']}",
            },
            {
                "label": "制造成本",
                "value": manufacturing_view["total"]["amount"],
                "unit": "元",
            },
            {
                "label": "合格数量",
                "value": batch_summary["qualified_quantity"],
                "unit": calculation_result["unit"],
            },
        ],
        "manufacturing_view": manufacturing_view,
        "material_labor_overhead_view": calculation_result[
            "material_labor_overhead_view"
        ],
        "variable_fixed_view": variable_fixed_view,
        "finished_batches": calculation_result["finished_batches"],
        "insight_cards": insight_cards,
        "calculation_formula": FORMULAS,
        "calculation_policy": calculation_result["calculation_policy"],
        "analysis_text": analysis_text.strip(),
        "source_summary": (
            "成本事实来自当前已发布的 PostgreSQL cost_data 快照；"
            "报告按产成品零件和最终批次完工期间汇总。"
        ),
        "model_info": model_info or {},
        "ai_trace": ai_trace or {},
        "lineage": lineage,
        "agent_steps": agent_steps,
    }
    return CostReportV2.model_validate(report).model_dump(mode="json")
