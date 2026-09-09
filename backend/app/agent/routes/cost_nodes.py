from __future__ import annotations

from typing import Any

from app.agent.runtime import (
    _append_error,
    _append_event,
    _append_llm_call,
    _new_ai_trace,
    _now,
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
from app.services.llm_service import canonical_sha256


def resolve_part(
    state: CostAgentState, runtime_services: RuntimeServices | None = None
) -> dict[str, Any]:
    started_at = _now()
    services = _runtime_services(state, runtime_services)
    try:
        part = state.get("part") or require_call_value(
            services.invoke_tool(
                "resolve_part",
                {"part_text": state.get("part_text", "")},
                state["execution_context"],
                node="resolve_part",
            )
        )
    except (PermissionError, ValueError, RuntimeServiceError) as exc:
        summary = f"零件数据边界校验失败：{exc}"
        return _with_event(
            state,
            {"errors": _append_error(state, summary)},
            "resolve_part",
            "error",
            summary,
            started_at,
        )
    if part is None:
        summary = f"未找到零件：{state.get('part_text', '') or '空'}。"
        return _with_event(
            state,
            {"errors": _append_error(state, summary)},
            "resolve_part",
            "error",
            summary,
            started_at,
        )
    return _with_event(
        state,
        {"part": part},
        "resolve_part",
        "success",
        f"匹配到零件 {part['part_number']}（{part['part_id']}）",
        started_at,
    )


def load_finished_batches(
    state: CostAgentState, runtime_services: RuntimeServices | None = None
) -> dict[str, Any]:
    started_at = _now()
    services = _runtime_services(state, runtime_services)
    period = state["period"]
    try:
        batches = require_call_value(
            services.invoke_tool(
                "load_finished_batches",
                {"period": period},
                state["execution_context"],
                node="load_finished_batches",
            )
        )
    except (PermissionError, ValueError, RuntimeServiceError) as exc:
        summary = f"成本批次读取失败：{exc}"
        return _with_event(
            state,
            {"errors": _append_error(state, summary)},
            "load_finished_batches",
            "error",
            summary,
            started_at,
        )
    part_id = (state.get("part") or {}).get("part_id")
    if part_id:
        batches = [
            item for item in batches if item.get("part", {}).get("part_id") == part_id
        ]
    if not batches:
        summary = f"缺少 {part_id or '指定零件'} {period} 的已发布产成品批次。"
        return _with_event(
            state,
            {"errors": _append_error(state, summary)},
            "load_finished_batches",
            "error",
            summary,
            started_at,
        )
    return _with_event(
        state,
        {"batch_sources": batches},
        "load_finished_batches",
        "success",
        f"读取到 {period} 的 {len(batches)} 个产成品批次。",
        started_at,
    )


def calculate_cost(
    state: CostAgentState, runtime_services: RuntimeServices | None = None
) -> dict[str, Any]:
    started_at = _now()
    batches = state.get("batch_sources", [])
    if not batches:
        summary = "没有可计算的产成品批次。"
        return _with_event(
            state,
            {"errors": _append_error(state, summary)},
            "calculate_cost",
            "error",
            summary,
            started_at,
        )
    # Keep batch-period aggregation in the same deterministic service used by
    # the HTTP report path.  The Agent must not maintain a second set of
    # summation/rounding formulas (or average per-batch unit costs).
    from app.services.cost_calculation_service import aggregate_part_period_costs

    result = aggregate_part_period_costs(batches)
    trace = {
        **state.get("ai_trace", _new_ai_trace()),
        "deterministic_calculation": {
            "input_sha256": canonical_sha256(batches),
            "result": result,
            "formula": result["calculation_policy"],
        },
    }
    return _with_event(
        state,
        {"calculation_result": result, "ai_trace": trace},
        "calculate_cost",
        "success",
        f"完成 {len(batches)} 个批次的确定性成本卷积。",
        started_at,
    )


def build_report_json(
    state: CostAgentState, runtime_services: RuntimeServices | None = None
) -> dict[str, Any]:
    started_at = _now()
    services = _runtime_services(state, runtime_services)
    result = state["calculation_result"]
    analysis_result = require_call_value(
        services.invoke_model(
            "generate_cost_analysis",
            node="build_report_json",
            args=(
                state.get("part") or result["part"],
                state["period"],
                result,
                None,
                state.get("date_range"),
                status_for_context(state, "build_report_json"),
            ),
        )
    )
    analysis_text = analysis_result["analysis_text"]
    ai_trace = _append_llm_call(state, analysis_result["llm_call"])
    events = _append_event(
        state, "build_report_json", "success", "生成成本 v2 结构化报告。", started_at
    )
    lineage = build_lineage(
        part_id=result["part"]["part_id"],
        period=state["period"],
        batch_sources=state.get("batch_sources", []),
        source=(state.get("execution_context") or {})
        .get("data_scope", {})
        .get("source", "postgresql_cost_data"),
    )
    try:
        report_json = require_call_value(
            services.invoke_tool(
                "build_report",
                {
                    "run_id": state["run_id"],
                    "part": result["part"],
                    "period": state["period"],
                    "calculation_result": result,
                    "agent_steps": events,
                    "analysis_text": analysis_text,
                    "model_info": state.get("model_info", {}),
                    "ai_trace": build_public_ai_trace(ai_trace),
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
            "lineage": lineage,
        }
    return {
        "events": events,
        "status_bar": update_status_bar(state, events[-1], events),
        "report_json": report_json,
        "model_info": state.get("model_info", {}),
        "analysis_text": analysis_text,
        "ai_trace": ai_trace,
        "audit_trace": build_audit_trace(ai_trace),
        "lineage": lineage,
    }


def should_continue_after_product(state: CostAgentState) -> str:
    return "final_answer" if state.get("errors") else "load_finished_batches"


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
