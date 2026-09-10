from decimal import Decimal
from typing import Any

from app.domain.cost import CALCULATION_RULE_VERSION
from app.domain.report import REPORT_SCHEMA_VERSION, CostReportV3, ReportStyle
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
    report_style: ReportStyle = "presentation",
    comparison_period: str | None = None,
    comparison_calculation_result: dict[str, Any] | None = None,
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
    comparison = (
        _build_period_comparison(
            current_period=period,
            current_result=calculation_result,
            baseline_period=comparison_period,
            baseline_result=comparison_calculation_result,
        )
        if report_style == "period_comparison"
        else None
    )
    report = {
        "report_schema_version": REPORT_SCHEMA_VERSION,
        "report_style": report_style,
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
        "comparison": comparison,
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
    return CostReportV3.model_validate(report).model_dump(mode="json")


def _build_period_comparison(
    *,
    current_period: str,
    current_result: dict[str, Any],
    baseline_period: str | None,
    baseline_result: dict[str, Any] | None,
) -> dict[str, Any]:
    if not baseline_period or baseline_result is None:
        raise ValueError("周期对比报表必须提供基准期间和基准期计算结果")
    if baseline_period == current_period:
        raise ValueError("基准期间不能与目标期间相同")

    current_summary = current_result["batch_summary"]
    baseline_summary = baseline_result["batch_summary"]
    current_manufacturing = current_result["manufacturing_view"]
    baseline_manufacturing = baseline_result["manufacturing_view"]
    current_variable_fixed = current_result["variable_fixed_view"]
    baseline_variable_fixed = baseline_result["variable_fixed_view"]
    unit = current_result["unit"]

    headline_metrics = [
        _comparison_metric(
            "total_unit_cost_2",
            "合计单位成本2",
            f"元/{unit}",
            baseline_variable_fixed["total_cost_2"]["unit_cost"],
            current_variable_fixed["total_cost_2"]["unit_cost"],
        ),
        _comparison_metric(
            "manufacturing_unit_cost",
            "制造单位成本",
            f"元/{unit}",
            baseline_manufacturing["total"]["unit_cost"],
            current_manufacturing["total"]["unit_cost"],
        ),
        _comparison_metric(
            "completed_quantity",
            "完工数量",
            unit,
            baseline_summary["completed_quantity"],
            current_summary["completed_quantity"],
        ),
        _comparison_metric(
            "quality_rate",
            "合格率",
            "%",
            baseline_summary["quality_rate"],
            current_summary["quality_rate"],
        ),
    ]
    baseline_groups = {
        item["group_code"]: item for item in baseline_manufacturing["groups"]
    }
    manufacturing_groups = [
        _comparison_metric(
            f"manufacturing_group:{item['group_code']}",
            item["group_label"],
            f"元/{unit}",
            baseline_groups[item["group_code"]]["metric"]["unit_cost"],
            item["metric"]["unit_cost"],
        )
        for item in current_manufacturing["groups"]
    ]
    return {
        "baseline_period": baseline_period,
        "current_period": current_period,
        "baseline_batch_summary": baseline_summary,
        "baseline_manufacturing_view": baseline_manufacturing,
        "baseline_material_labor_overhead_view": baseline_result[
            "material_labor_overhead_view"
        ],
        "baseline_variable_fixed_view": baseline_variable_fixed,
        "baseline_finished_batches": baseline_result["finished_batches"],
        "headline_metrics": headline_metrics,
        "manufacturing_groups": manufacturing_groups,
    }


def _comparison_metric(
    metric_id: str,
    label: str,
    unit: str,
    baseline_value: Any,
    current_value: Any,
) -> dict[str, Any]:
    baseline = Decimal(str(baseline_value))
    current = Decimal(str(current_value))
    delta = current - baseline
    return {
        "metric_id": metric_id,
        "label": label,
        "unit": unit,
        "baseline_value": baseline,
        "current_value": current,
        "delta": delta,
        "change_rate": None if baseline == 0 else (delta / baseline * Decimal(100)),
    }
