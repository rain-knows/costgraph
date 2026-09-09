from typing import Any

from app.domain.cost import CALCULATION_RULE_VERSION
from app.domain.report import REPORT_SCHEMA_VERSION, CostReportV1
from app.services.llm_service import PROMPT_VERSION
from app.settings import get_settings

FORMULAS = [
    "工序成本 = 材料成本 + 人工成本 + 设备成本 + 能耗成本 + 制造费用",
    "产品总成本 = 所有工序成本之和",
    "单位成本 = 产品总成本 / 合格产量",
    "区间成本 = 查询日期范围内日级成本记录逐日求和",
]


def build_report_json(
    run_id: str,
    product: dict[str, Any],
    period: str,
    calculation_result: dict[str, Any],
    agent_steps: list[dict[str, Any]],
    analysis_text: str,
    model_info: dict[str, Any] | None = None,
    ai_trace: dict[str, Any] | None = None,
    comparison_result: dict[str, Any] | None = None,
    date_range: dict[str, str] | None = None,
    lineage: dict[str, Any] | None = None,
) -> dict[str, Any]:
    period_label = (
        f"{date_range['start_date']} 至 {date_range['end_date']}"
        if date_range
        else period
    )
    top_composition = max(
        calculation_result["cost_composition"], key=lambda item: item["value"]
    )
    top_process = max(
        calculation_result["process_breakdown"], key=lambda item: item["total_cost"]
    )
    insight_cards = [
        {
            "label": "最高工序",
            "value": top_process["process_name"],
            "description": f"{top_process['total_cost']} 元",
        },
        {
            "label": "最大构成",
            "value": top_composition["name"],
            "description": f"{top_composition['value']} 元",
        },
    ]
    if comparison_result:
        delta = comparison_result["unit_cost_delta"]
        direction = "上升" if delta > 0 else "下降" if delta < 0 else "持平"
        insight_cards.insert(
            0,
            {
                "label": "单位成本环比",
                "value": f"{direction} {abs(delta):.2f} 元/件",
                "description": (
                    f"较 {comparison_result['previous_period']} "
                    f"{comparison_result['unit_cost_delta_rate']:+.2f}%"
                ),
            },
        )
    if date_range:
        range_meta = calculation_result.get("date_range", {})
        insight_cards.insert(
            0,
            {
                "label": "核算区间",
                "value": f"{range_meta.get('covered_days', 0)} 天",
                "description": period_label,
            },
        )
    if lineage is None:
        raise ValueError("结构化报表必须提供数据血缘")
    if not isinstance(analysis_text, str) or not analysis_text.strip():
        raise ValueError("模型分析文本不能为空。")
    report = {
        "report_schema_version": REPORT_SCHEMA_VERSION,
        "rule_version": CALCULATION_RULE_VERSION,
        "prompt_version": PROMPT_VERSION,
        "code_version": get_settings().app_version,
        "data_snapshot_id": lineage["data_snapshot_id"],
        "run_id": run_id,
        "product": {
            "product_id": product["product_id"],
            "product_name": product["product_name"],
            "spec": product.get("spec"),
        },
        "period": period_label,
        "date_range": date_range,
        "summary_cards": [
            {
                "label": "单位成本",
                "value": calculation_result["unit_cost"],
                "unit": "元/件",
            },
            {
                "label": "产品总成本",
                "value": calculation_result["total_cost"],
                "unit": "元",
            },
            {
                "label": "合格产量",
                "value": calculation_result["output_qty"],
                "unit": "件",
            },
        ],
        "process_cost_breakdown": [
            {
                "process_name": item["process_name"],
                "total_cost": item["total_cost"],
                "material_cost": item["material_cost"],
                "labor_cost": item["labor_cost"],
                "equipment_cost": item["equipment_cost"],
                "energy_cost": item["energy_cost"],
                "overhead_cost": item["overhead_cost"],
            }
            for item in calculation_result["process_breakdown"]
        ],
        "cost_composition_chart": calculation_result["cost_composition"],
        "insight_cards": insight_cards,
        "comparison": comparison_result,
        "calculation_formula": FORMULAS,
        "calculation_policy": calculation_result["calculation_policy"],
        "analysis_text": analysis_text,
        "source_summary": _source_summary(lineage["source"], bool(date_range)),
        "model_info": model_info or {},
        "ai_trace": ai_trace or {},
        "lineage": lineage,
        "agent_steps": agent_steps,
    }
    return CostReportV1.model_validate(report).model_dump(mode="json")


def _source_summary(source: str, is_date_range: bool) -> str:
    aggregation = (
        "区间核算按生产日期精确汇总日级记录。"
        if is_date_range
        else "月度结果由日级记录聚合得到。"
    )
    if source != "postgresql_cost_data":
        raise ValueError(f"不支持的成本事实来源：{source}")
    return f"成本事实来自 PostgreSQL cost_data 标准事实表；{aggregation}"
