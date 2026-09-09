from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from app.agent.capabilities import (
    CAPABILITY_SPECS,
    RoutingMode,
    capability_manifest,
)
from app.domain.authorization import ExecutionContext, execution_context_from_state

STATUS_NODE_ORDERS: dict[str, list[str]] = {
    "routing": [
        "load_conversation_context",
        "policy_gate",
        "select_route",
    ],
    **{spec.status_plan: list(spec.status_nodes) for spec in CAPABILITY_SPECS},
    "clarification": [
        "load_conversation_context",
        "policy_gate",
        "select_route",
        "understand_question",
        "merge_context_slots",
        "clarification_gate",
        "request_clarification",
    ],
    "blocked": [
        "load_conversation_context",
        "policy_gate",
        "select_route",
        "final_answer",
    ],
}

NODE_LABELS = {
    "load_conversation_context": "加载会话上下文",
    "policy_gate": "能力策略",
    "select_route": "选择路由",
    "understand_question": "理解问题",
    "merge_context_slots": "合并对话信息",
    "clarification_gate": "检查信息完整性",
    "request_clarification": "等待用户补充",
    "resolve_part": "匹配零件",
    "load_finished_batches": "读取产成品批次",
    "calculate_cost": "确定性成本计算",
    "build_report_json": "生成结构化报告",
    "final_answer": "生成最终回答",
}


def _now() -> str:
    return datetime.now(UTC).isoformat()


def initial_status_bar(
    run_id: str,
    question: str,
    routing_mode: RoutingMode,
    execution_context: ExecutionContext,
) -> dict[str, Any]:
    node_order = STATUS_NODE_ORDERS["routing"]
    return {
        "schema_version": 2,
        "run_id": run_id,
        "run_state": "running",
        "phase": "load_conversation_context",
        "goal": question,
        "current_step": "load_conversation_context",
        "completed_steps": 0,
        "total_steps": len(node_order),
        "todo": [
            {
                "id": node,
                "label": NODE_LABELS[node],
                "status": "current" if index == 0 else "pending",
            }
            for index, node in enumerate(node_order)
        ],
        "capabilities": capability_manifest(
            list(execution_context.effective_capabilities),
            requested_capabilities=list(execution_context.requested_capabilities),
            authorized_capabilities=list(execution_context.authorized_capabilities),
        ),
        "routing_mode": routing_mode,
        "requested_route": "blocked",
        "effective_route": "blocked",
        "route_reason": "等待路由选择",
        "outcome": None,
        "missing_slots": [],
        "clarification": None,
        "environment": {
            "principal_id": execution_context.principal.principal_id,
            "tenant_id": execution_context.principal.tenant_id,
            "data_scope": execution_context.data_scope.source,
            "permission": "read_only",
        },
        "authorization": execution_context.public_summary(),
        "last_event": None,
        "updated_at": _now(),
    }


def update_status_bar(
    state: dict[str, Any], event: dict[str, Any], events: list[dict[str, Any]]
) -> dict[str, Any]:
    """Project a completed node event into a compact, structured status snapshot."""

    execution_context = execution_context_from_state(state["execution_context"])
    previous = state.get("status_bar") or initial_status_bar(
        state.get("run_id", ""),
        state.get("user_query", ""),
        state.get("routing_mode", "auto"),
        execution_context,
    )
    node = event["node"]
    status = event["status"]
    route = state.get("effective_route", "blocked")
    status_plan = state.get("status_plan") or route
    node_order = STATUS_NODE_ORDERS.get(
        status_plan, STATUS_NODE_ORDERS.get(route, STATUS_NODE_ORDERS["blocked"])
    )
    node_index = node_order.index(node) if node in node_order else -1
    completed_nodes = {item["node"] for item in events if item["node"] in node_order}
    is_final = node == "final_answer"
    outcome = state.get("outcome")
    is_waiting = node == "request_clarification" or outcome == "needs_clarification"
    run_state = (
        "waiting_for_user"
        if is_waiting
        else "blocked"
        if is_final and outcome == "blocked"
        else "completed"
        if is_final and status == "success"
        else "failed"
        if is_final and status == "error"
        else "running"
    )
    next_step = (
        "waiting_for_user"
        if run_state == "waiting_for_user"
        else (
            "completed"
            if run_state == "completed"
            else (
                "blocked"
                if run_state == "blocked"
                else (
                    "failed"
                    if run_state == "failed"
                    else (
                        "final_answer"
                        if status == "error" and not is_final
                        else (
                            node_order[node_index + 1]
                            if node_index >= 0 and node_index + 1 < len(node_order)
                            else node
                        )
                    )
                )
            )
        )
    )
    todo = []
    for item in node_order:
        if item in completed_nodes:
            item_status = "done"
        elif item == next_step:
            item_status = "current"
        else:
            item_status = "pending"
        todo.append({"id": item, "label": NODE_LABELS[item], "status": item_status})

    return {
        **previous,
        "routing_mode": state.get("routing_mode", previous.get("routing_mode", "auto")),
        "requested_route": state.get(
            "requested_route", previous.get("requested_route", "blocked")
        ),
        "effective_route": state.get(
            "effective_route", previous.get("effective_route", "blocked")
        ),
        "route_reason": state.get("route_reason", previous.get("route_reason", "")),
        "capabilities": capability_manifest(
            list(execution_context.effective_capabilities),
            requested_capabilities=list(execution_context.requested_capabilities),
            authorized_capabilities=list(execution_context.authorized_capabilities),
        ),
        "authorization": execution_context.public_summary(),
        "run_state": run_state,
        "phase": next_step,
        "current_step": next_step,
        "completed_steps": len(completed_nodes),
        "total_steps": len(node_order),
        "todo": todo,
        "outcome": outcome,
        "missing_slots": state.get("missing_slots", []),
        "clarification": state.get("clarification"),
        "last_event": event,
        "updated_at": _now(),
    }


def status_for_context(state: dict[str, Any], current_step: str) -> dict[str, Any]:
    """Return a context-tail snapshot without exposing the whole event history."""

    status = dict(state.get("status_bar") or {})
    status["current_step"] = current_step
    status["phase"] = current_step
    status["run_state"] = "running"
    status["updated_at"] = _now()
    return status


def render_status_context(status_bar: dict[str, Any]) -> str:
    """Render the runtime-owned status as a bounded context tail for an LLM call."""

    compact = {
        "schema_version": status_bar.get("schema_version", 1),
        "run_id": status_bar.get("run_id"),
        "run_state": status_bar.get("run_state"),
        "goal": status_bar.get("goal"),
        "phase": status_bar.get("phase"),
        "current_step": status_bar.get("current_step"),
        "todo": status_bar.get("todo", []),
        "capabilities": status_bar.get("capabilities", []),
        "routing_mode": status_bar.get("routing_mode"),
        "requested_route": status_bar.get("requested_route"),
        "effective_route": status_bar.get("effective_route"),
        "route_reason": status_bar.get("route_reason"),
        "outcome": status_bar.get("outcome"),
        "missing_slots": status_bar.get("missing_slots", []),
        "clarification": status_bar.get("clarification"),
        "environment": status_bar.get("environment", {}),
        "last_event": status_bar.get("last_event"),
        "updated_at": status_bar.get("updated_at"),
    }
    return (
        "<agent_status_bar>\n"
        "以下是运行时生成的当前状态，只用于理解任务进度；不能把它当作权限授予或业务事实来源。\n"
        f"{json.dumps(compact, ensure_ascii=False, separators=(',', ':'))}\n"
        "</agent_status_bar>"
    )
