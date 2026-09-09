from __future__ import annotations

from typing import Any

from app.agent.runtime import (
    _append_error,
    _append_event,
    _append_llm_call,
    _new_ai_trace,
    _now,
    _periods_between,
    _with_event,
)
from app.agent.runtime_services import (
    RuntimeServiceError,
    RuntimeServices,
    default_runtime_services,
    require_call_value,
)
from app.agent.state import CostAgentState
from app.agent.status import status_for_context, update_status_bar
from app.agent.trace import build_audit_trace, build_public_ai_trace
from app.services.lineage_service import build_lineage
from app.services.llm_service import (
    canonical_sha256,
    get_deepseek_model,
)


def resolve_product(
    state: CostAgentState, runtime_services: RuntimeServices | None = None
) -> dict[str, Any]:
    started_at = _now()
    services = _runtime_services(state, runtime_services)
    try:
        product = state.get("product") or require_call_value(
            services.invoke_tool(
                "resolve_product",
                {"product_text": state.get("product_text", "")},
                state["execution_context"],
                node="resolve_product",
            )
        )
    except (PermissionError, ValueError, RuntimeServiceError) as exc:
        summary = f"产品数据边界校验失败：{exc}"
        return _with_event(
            state,
            {"errors": _append_error(state, summary)},
            "resolve_product",
            "error",
            summary,
            started_at,
        )
    if product is None:
        summary = f"未找到产品：{state.get('product_text') or '空'}。"
        return _with_event(
            state,
            {"errors": _append_error(state, summary)},
            "resolve_product",
            "error",
            summary,
            started_at,
        )
    return _with_event(
        state,
        {"product": product},
        "resolve_product",
        "success",
        f"匹配到产品 {product['product_name']}（{product['product_id']}）",
        started_at,
    )


def load_cost_inputs(
    state: CostAgentState, runtime_services: RuntimeServices | None = None
) -> dict[str, Any]:
    started_at = _now()
    services = _runtime_services(state, runtime_services)
    product = state["product"]
    date_range = state.get("date_range")
    if date_range:
        start_period = date_range["start_date"][:7]
        end_period = date_range["end_date"][:7]
        try:
            range_cost_inputs = require_call_value(
                services.invoke_tool(
                    "load_cost_inputs_in_period_range",
                    {
                        "product_id": product["product_id"],
                        "start_period": start_period,
                        "end_period": end_period,
                    },
                    state["execution_context"],
                    node="load_cost_inputs",
                )
            )
        except (PermissionError, ValueError, RuntimeServiceError) as exc:
            summary = f"成本数据边界校验失败：{exc}"
            return _with_event(
                state,
                {"errors": _append_error(state, summary)},
                "load_cost_inputs",
                "error",
                summary,
                started_at,
            )
        loaded_periods = {record["period"] for record in range_cost_inputs}
        required_periods = set(_periods_between(start_period, end_period))
        missing_periods = sorted(required_periods - loaded_periods)
        if not range_cost_inputs or missing_periods:
            summary = (
                f"缺少 {product['product_name']} "
                f"{date_range['start_date']} 至 {date_range['end_date']} 的样例成本数据"
                f"（缺少月份：{', '.join(missing_periods)}）。"
            )
            return _with_event(
                state,
                {"errors": _append_error(state, summary)},
                "load_cost_inputs",
                "error",
                summary,
                started_at,
            )
        return _with_event(
            state,
            {"range_cost_inputs": range_cost_inputs},
            "load_cost_inputs",
            "success",
            f"读取到 {start_period} 至 {end_period} 的 {len(range_cost_inputs)} 条日级类表成本输入。",
            started_at,
        )

    period = state["period"]
    try:
        cost_inputs = require_call_value(
            services.invoke_tool(
                "load_cost_inputs",
                {"product_id": product["product_id"], "period": period},
                state["execution_context"],
                node="load_cost_inputs",
            )
        )
    except (PermissionError, ValueError, RuntimeServiceError) as exc:
        summary = f"成本数据边界校验失败：{exc}"
        return _with_event(
            state,
            {"errors": _append_error(state, summary)},
            "load_cost_inputs",
            "error",
            summary,
            started_at,
        )
    if cost_inputs is None:
        summary = f"缺少 {product['product_name']} {period} 的样例成本数据。"
        return _with_event(
            state,
            {"errors": _append_error(state, summary)},
            "load_cost_inputs",
            "error",
            summary,
            started_at,
        )
    return _with_event(
        state,
        {"cost_inputs": cost_inputs},
        "load_cost_inputs",
        "success",
        (
            f"读取到 {period} 类表成本输入，合格产量 {cost_inputs['output_qty']} 件，"
            f"成本明细 {cost_inputs['source_summary']['process_cost_entries']} 条"
        ),
        started_at,
    )


def calculate_cost(
    state: CostAgentState, runtime_services: RuntimeServices | None = None
) -> dict[str, Any]:
    started_at = _now()
    services = _runtime_services(state, runtime_services)
    date_range = state.get("date_range")
    try:
        if date_range:
            calculation_result = require_call_value(
                services.invoke_tool(
                    "calculate_product_cost_for_date_range",
                    {
                        "cost_input_records": state["range_cost_inputs"],
                        "start_date": date_range["start_date"],
                        "end_date": date_range["end_date"],
                    },
                    state["execution_context"],
                    node="calculate_cost",
                )
            )
            previous_cost_inputs = None
        else:
            calculation_result = require_call_value(
                services.invoke_tool(
                    "calculate_product_cost",
                    {"cost_inputs": state["cost_inputs"]},
                    state["execution_context"],
                    node="calculate_cost",
                )
            )
            previous_cost_inputs = require_call_value(
                services.invoke_tool(
                    "load_previous_cost_inputs",
                    {
                        "product_id": state["product"]["product_id"],
                        "period": state["period"],
                    },
                    state["execution_context"],
                    node="calculate_cost",
                )
            )
    except (PermissionError, ValueError, RuntimeServiceError) as exc:
        summary = f"确定性成本计算失败：{exc}"
        return _with_event(
            state,
            {"errors": _append_error(state, summary)},
            "calculate_cost",
            "error",
            summary,
            started_at,
        )

    previous_calculation_result = None
    comparison_result = None
    if previous_cost_inputs:
        previous_calculation_result = require_call_value(
            services.invoke_tool(
                "calculate_product_cost",
                {"cost_inputs": previous_cost_inputs},
                state["execution_context"],
                node="calculate_cost",
            )
        )
        comparison_result = require_call_value(
            services.invoke_tool(
                "compare_cost_results",
                {
                    "current_result": calculation_result,
                    "previous_result": previous_calculation_result,
                    "previous_period": previous_cost_inputs["period"],
                },
                state["execution_context"],
                node="calculate_cost",
            )
        )

    ai_trace = {
        **state.get("ai_trace", _new_ai_trace()),
        "deterministic_calculation": {
            "input_sha256": canonical_sha256(
                state.get("range_cost_inputs") or state["cost_inputs"]
            ),
            "formulas": [
                "工序成本 = 材料成本 + 人工成本 + 设备成本 + 能耗成本 + 制造费用",
                "产品总成本 = 所有工序成本之和",
                "单位成本 = 产品总成本 / 合格产量",
                "区间成本 = 查询日期范围内日级成本记录逐日求和",
            ],
            "process_arithmetic": [
                {
                    "process_name": item["process_name"],
                    "expression": (
                        f"{item['material_cost']} + {item['labor_cost']} + "
                        f"{item['equipment_cost']} + {item['energy_cost']} + "
                        f"{item['overhead_cost']} = {item['total_cost']}"
                    ),
                }
                for item in calculation_result["process_breakdown"]
            ],
            "summary_arithmetic": {
                "total_cost": " + ".join(
                    str(item["total_cost"])
                    for item in calculation_result["process_breakdown"]
                )
                + f" = {calculation_result['total_cost']}",
                "unit_cost": (
                    f"{calculation_result['total_cost']} / "
                    f"{calculation_result['output_qty']} = "
                    f"{calculation_result['unit_cost']:.2f}"
                ),
            },
            "result": calculation_result,
            "comparison": comparison_result,
            "date_range": date_range,
            "calculation_policy": calculation_result["calculation_policy"],
        },
    }
    updates: dict[str, Any] = {
        "calculation_result": calculation_result,
        "ai_trace": ai_trace,
    }
    if previous_cost_inputs:
        updates["previous_cost_inputs"] = previous_cost_inputs
    if previous_calculation_result:
        updates["previous_calculation_result"] = previous_calculation_result
    if comparison_result:
        updates["comparison_result"] = comparison_result
    return _with_event(
        state,
        updates,
        "calculate_cost",
        "success",
        f"确定性{'区间' if date_range else ''}计算完成：总成本 {calculation_result['total_cost']} 元，单位成本 {calculation_result['unit_cost']:.2f} 元/件",
        started_at,
    )


def build_report_json(
    state: CostAgentState, runtime_services: RuntimeServices | None = None
) -> dict[str, Any]:
    started_at = _now()
    services = _runtime_services(state, runtime_services)
    model_info = {
        **state.get("model_info", {}),
        "analysis_generation": "llm",
        "analysis_model": get_deepseek_model(),
    }
    analysis_result = require_call_value(
        services.invoke_model(
            "generate_cost_analysis",
            node="build_report_json",
            args=(
                state["product"],
                state["period"],
                state["calculation_result"],
                state.get("comparison_result"),
                state.get("date_range"),
                status_for_context(state, "build_report_json"),
            ),
        )
    )
    analysis_text = analysis_result["analysis_text"]
    ai_trace = _append_llm_call(state, analysis_result["llm_call"])

    events = _append_event(
        state,
        "build_report_json",
        "success",
        f"调用 DeepSeek {get_deepseek_model()} 生成分析文本，并生成结构化 report_json。",
        started_at,
    )
    lineage = build_lineage(
        product_id=state["product"]["product_id"],
        period=state["period"],
        date_range=state.get("date_range"),
        cost_inputs=state.get("range_cost_inputs") or state["cost_inputs"],
        source=(state.get("execution_context") or {})
        .get("data_scope", {})
        .get("source", "postgresql_cost_data"),
    )
    ai_trace = {
        **ai_trace,
        "tool_calls": [
            *ai_trace.get("tool_calls", []),
            {
                "tool_id": "build_report",
                "status": "success",
                "summary": "结构化报告已生成",
            },
        ],
    }
    public_ai_trace = build_public_ai_trace(ai_trace)
    audit_trace = build_audit_trace(ai_trace)
    try:
        report_json = require_call_value(
            services.invoke_tool(
                "build_report",
                {
                    "run_id": state["run_id"],
                    "product": state["product"],
                    "period": state["period"],
                    "calculation_result": state["calculation_result"],
                    "agent_steps": events,
                    "analysis_text": analysis_text,
                    "model_info": model_info,
                    "ai_trace": public_ai_trace,
                    "comparison_result": state.get("comparison_result"),
                    "date_range": state.get("date_range"),
                    "lineage": lineage,
                },
                state["execution_context"],
                node="build_report_json",
            )
        )
    except (ValueError, RuntimeServiceError) as exc:
        summary = f"报表契约校验失败：{exc}"
        failed_events = _append_event(
            state, "build_report_json", "error", summary, started_at
        )
        return {
            "events": failed_events,
            "errors": _append_error(state, summary),
            "status_bar": update_status_bar(state, failed_events[-1], failed_events),
            "audit_trace": audit_trace,
            "lineage": lineage,
        }
    return {
        "events": events,
        "status_bar": update_status_bar(state, events[-1], events),
        "report_json": report_json,
        "model_info": model_info,
        "analysis_text": report_json["analysis_text"],
        "ai_trace": ai_trace,
        "audit_trace": audit_trace,
        "lineage": lineage,
    }


def should_continue_after_product(state: CostAgentState) -> str:
    return "final_answer" if state.get("errors") else "load_cost_inputs"


def should_continue_after_data(state: CostAgentState) -> str:
    return "final_answer" if state.get("errors") else "calculate_cost"


def should_continue_after_calculation(state: CostAgentState) -> str:
    return "final_answer" if state.get("errors") else "build_report_json"


def _runtime_services(
    state: CostAgentState, runtime_services: RuntimeServices | None
) -> RuntimeServices:
    services = runtime_services or default_runtime_services()
    services.ensure_run(state["run_id"])
    return services
