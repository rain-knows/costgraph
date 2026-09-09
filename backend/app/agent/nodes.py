import re
from typing import Any
from uuid import uuid4

from app.agent.capabilities import (
    CAPABILITY_SPECS,
    RouteId,
    routes_for_capabilities,
)
from app.agent.runtime import (
    _append_event,
    _append_llm_call,
    _new_ai_trace,
    _now,
    _with_event,
)
from app.agent.runtime_services import (
    RuntimeServices,
    default_runtime_services,
    require_call_value,
)
from app.agent.state import CostAgentState
from app.agent.status import status_for_context, update_status_bar
from app.domain.authorization import execution_context_from_state
from app.domain.conversation import empty_conversation_context
from app.services.llm_service import (
    extract_date_range_from_question,
    extract_latest_period_from_question,
    get_deepseek_model,
)

COST_KEYWORDS = (
    "成本",
    "单位成本",
    "构成",
    "核算",
    "报表",
    "产品",
    "零件",
    "工序",
    "产量",
)
AMBIGUOUS_ROUTE_KEYWORDS = ("查询", "分析", "数据", "检查", "看一下")
FOLLOWUP_COST_KEYWORDS = (
    "上个月",
    "下个月",
    "这个月",
    "那个月",
    "再算",
    "重新算",
    "对比一下",
    "它呢",
    "那它呢",
)
CHINESE_MONTHS = {
    "一": 1,
    "二": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "七": 7,
    "八": 8,
    "九": 9,
    "十": 10,
    "十一": 11,
    "十二": 12,
}


def load_conversation_context(state: CostAgentState) -> dict[str, Any]:
    started_at = _now()
    context = {
        **empty_conversation_context(),
        **state.get("conversation_context", {}),
    }
    recent_messages = state.get("recent_messages", [])[-6:]
    summary = (
        f"已加载会话上下文，使用最近 {len(recent_messages)} 条消息和结构化槽位。"
        if state.get("conversation_id")
        else "本次为临时单轮会话，不继承历史业务槽位。"
    )
    return _with_event(
        state,
        {
            "conversation_context": context,
            "recent_messages": recent_messages,
            "context_used": {"recent_message_count": len(recent_messages)},
        },
        "load_conversation_context",
        "success",
        summary,
        started_at,
    )


def policy_gate(state: CostAgentState) -> dict[str, Any]:
    started_at = _now()
    execution_context = execution_context_from_state(state["execution_context"])
    capabilities = list(execution_context.effective_capabilities)
    denied = [
        capability
        for capability in execution_context.requested_capabilities
        if capability not in execution_context.effective_capabilities
    ]
    summary = f"授权策略已生效，有效能力：{', '.join(capabilities) or '无'}。"
    if denied:
        summary += f"未获授权：{', '.join(denied)}。"
    return _with_event(
        state,
        {
            "requested_capabilities": list(execution_context.requested_capabilities),
            "authorized_capabilities": list(execution_context.authorized_capabilities),
            "server_allowed_capabilities": list(
                execution_context.server_allowed_capabilities
            ),
            "enabled_capabilities": capabilities,
        },
        "policy_gate",
        "success",
        summary,
        started_at,
    )


def _deterministic_route(
    question: str, context: dict[str, Any] | None = None
) -> RouteId | None:
    context = context or {}
    if context.get("pending_clarification"):
        return "cost_calculation"
    if any(keyword in question for keyword in COST_KEYWORDS):
        return "cost_calculation"
    has_month_followup = bool(
        re.search(r"(?:[0-9一二三四五六七八九十]{1,3})月", question)
    )
    if context.get("current_part_text") and (
        has_month_followup
        or any(keyword in question for keyword in FOLLOWUP_COST_KEYWORDS)
    ):
        return "cost_calculation"
    if not any(keyword in question for keyword in AMBIGUOUS_ROUTE_KEYWORDS):
        return "system_help"
    return None


def select_route(
    state: CostAgentState, runtime_services: RuntimeServices | None = None
) -> dict[str, Any]:
    started_at = _now()
    query = state["user_query"]
    capabilities = state.get("enabled_capabilities", [])
    allowed_routes = routes_for_capabilities(capabilities)
    candidate_routes = [spec.route for spec in CAPABILITY_SPECS]

    route = _deterministic_route(query, state.get("conversation_context"))
    route_source = "deterministic"
    ai_trace = state.get("ai_trace", _new_ai_trace())
    if route is None:
        services = _runtime_services(state, runtime_services)
        outcome = services.invoke_model(
            "classify_route",
            node="select_route",
            args=(
                query,
                candidate_routes,
                status_for_context(state, "select_route"),
                state.get("conversation_context"),
                state.get("recent_messages"),
            ),
        )
        classified = require_call_value(outcome)
        route = classified["route"]
        route_source = "llm"
        ai_trace = _append_llm_call(state, classified["llm_call"])
        route_reason = classified["reason"]
    else:
        route_reason = (
            "识别到成本查询意图。"
            if route == "cost_calculation"
            else "未识别到成本核算意图，进入系统说明。"
        )

    requested_route = route
    effective_route: RouteId = route
    if route not in allowed_routes:
        effective_route = "blocked"
        route_reason = f"识别到 {route} 路由，但当前执行主体未获得该能力。"

    readonly_answer = ""
    status = "success"
    if effective_route == "blocked":
        status = "error"
        readonly_answer = (
            "当前未开启成本核算能力。请切换到自动路由或在手动模式中开启“成本核算”，"
            "系统不会读取成本数据、执行计算或生成金额。"
        )
    elif effective_route == "system_help":
        readonly_answer = (
            "当前系统采用服务端授权的能力工作流。系统说明路由只解释能力和流程，"
            "不读取成本事实、不执行成本计算，也不生成成本金额。"
        )

    route_selection = {
        "requested_route": requested_route,
        "effective_route": effective_route,
        "routing_mode": state.get("routing_mode", "auto"),
        "source": route_source,
        "reason": route_reason,
        "allowed_routes": allowed_routes,
    }
    ai_trace = {**ai_trace, "route_selection": route_selection}
    summary = f"路由确定为 {effective_route}：{route_reason}"
    return _with_event(
        state,
        {
            "workflow_mode": effective_route,
            "requested_route": requested_route,
            "effective_route": effective_route,
            "route_reason": route_reason,
            "status_plan": (
                "blocked" if effective_route == "blocked" else effective_route
            ),
            "outcome": "blocked"
            if effective_route == "blocked"
            else state.get("outcome"),
            "readonly_answer": readonly_answer,
            "ai_trace": ai_trace,
        },
        "select_route",
        status,
        summary,
        started_at,
    )


def understand_question(
    state: CostAgentState, runtime_services: RuntimeServices | None = None
) -> dict[str, Any]:
    started_at = _now()
    query = state["user_query"]
    services = _runtime_services(state, runtime_services)
    outcome = services.invoke_model(
        "parse_cost_question",
        node="understand_question",
        args=(
            query,
            status_for_context(state, "understand_question"),
            state.get("conversation_context"),
            state.get("recent_messages"),
        ),
    )
    parsed = require_call_value(outcome)

    part_text = parsed.get("part_text", "")
    period = parsed["period"]
    date_range = parsed.get("date_range")
    intent = parsed["intent"]
    status = "success"
    period_text = (
        f"{date_range['start_date']} 至 {date_range['end_date']}"
        if date_range
        else period
    )
    if part_text or period_text:
        summary = (
            f"DeepSeek {get_deepseek_model()} 识别到{part_text}和期间{period_text}"
        )
    else:
        summary = "当前消息未提供完整产品或期间，交由澄清节点检查。"

    ai_trace = _append_llm_call(state, parsed["llm_call"])

    updates: dict[str, Any] = {
        "intent": intent,
        "part_text": part_text,
        "period": period,
        "date_range": date_range,
        "explicit_part": _has_explicit_part_mention(query, part_text),
        "explicit_period": bool(extract_latest_period_from_question(query)),
        "explicit_date_range": bool(extract_date_range_from_question(query)),
        "ai_trace": ai_trace,
        "model_info": {
            "provider": "deepseek",
            "model": parsed["model"],
            "understand_question": "llm",
        },
    }
    return _with_event(
        state, updates, "understand_question", status, summary, started_at
    )


def _shift_period(period: str, delta: int) -> str:
    year, month = [int(part) for part in period.split("-")]
    month_index = year * 12 + month - 1 + delta
    return f"{month_index // 12:04d}-{month_index % 12 + 1:02d}"


def _has_explicit_part_mention(question: str, part_text: str) -> bool:
    compact = question.replace(" ", "").upper()
    if re.search(r"产品[A-Z一二三四五六七八九十]+", compact):
        return True
    if re.search(r"\bP\d+\b", compact):
        return True
    return bool(part_text and part_text.replace(" ", "").upper() in compact)


def _infer_followup_period(question: str, base_period: str) -> str:
    if not base_period:
        return ""
    compact = question.replace(" ", "")
    if "上个月" in compact:
        return _shift_period(base_period, -1)
    if "下个月" in compact:
        return _shift_period(base_period, 1)
    if "这个月" in compact or "那个月" in compact:
        return base_period

    numeric_match = re.search(r"(?<!年)(1[0-2]|0?[1-9])月", compact)
    chinese_match = re.search(r"(十二|十一|十|[一二三四五六七八九])月", compact)
    month = (
        int(numeric_match.group(1))
        if numeric_match
        else (CHINESE_MONTHS[chinese_match.group(1)] if chinese_match else 0)
    )
    if not month:
        return ""
    return f"{base_period[:4]}-{month:02d}"


def merge_context_slots(state: CostAgentState) -> dict[str, Any]:
    started_at = _now()
    context = state.get("conversation_context", empty_conversation_context())
    query = state["user_query"]

    explicit_part = state.get("explicit_part", False)
    explicit_period = state.get("explicit_period", False)
    explicit_date_range = state.get("explicit_date_range", False)

    part_text = state.get("part_text", "") if explicit_part else ""
    inherited_part: str | None = None
    if not part_text:
        part_text = context.get("current_part_text", "")
        inherited_part = context.get("current_part_number") or part_text or None

    date_range = state.get("date_range") if explicit_date_range else None
    period = state.get("period", "") if explicit_period else ""
    relative_period = _infer_followup_period(query, context.get("current_period", ""))
    inherited_period: str | None = None
    inherited_date_range: dict[str, str] | None = None
    if relative_period:
        period = relative_period
        date_range = None
    elif not period and not date_range:
        if context.get("current_date_range"):
            date_range = context["current_date_range"]
            inherited_date_range = date_range
        elif context.get("current_period"):
            period = context["current_period"]
            inherited_period = period

    if date_range:
        period = date_range["end_date"][:7]

    context_used = {
        **state.get("context_used", {}),
        "inherited_part": inherited_part,
        "inherited_period": inherited_period,
        "inherited_date_range": inherited_date_range,
    }
    inherited_labels = [
        label
        for value, label in (
            (inherited_part, "零件"),
            (inherited_period or inherited_date_range, "期间"),
        )
        if value
    ]
    summary = (
        f"已从会话继承{'、'.join(inherited_labels)}，显式输入始终优先。"
        if inherited_labels
        else "当前消息未使用历史业务槽位，显式输入保持不变。"
    )
    ai_trace = {
        **state.get("ai_trace", _new_ai_trace()),
        "context_resolution": {
            "context_used": context_used,
            "resolved_slots": {
                "part_text": part_text,
                "period": period,
                "date_range": date_range,
                "intent": state.get("intent", "cost_query"),
            },
            "recent_message_count": len(state.get("recent_messages", [])),
        },
    }
    return _with_event(
        state,
        {
            "part_text": part_text,
            "period": period,
            "date_range": date_range,
            "resolved_slots": {
                "part_text": part_text,
                "period": period,
                "date_range": date_range,
                "intent": state.get("intent", "cost_query"),
            },
            "context_used": context_used,
            "ai_trace": ai_trace,
        },
        "merge_context_slots",
        "success",
        summary,
        started_at,
    )


def clarification_gate(
    state: CostAgentState, runtime_services: RuntimeServices | None = None
) -> dict[str, Any]:
    started_at = _now()
    part_text = state.get("part_text", "")
    period = state.get("period", "")
    date_range = state.get("date_range")
    missing_slots: list[str] = []
    invalid_slots: list[str] = []
    candidates: list[dict[str, Any]] = []
    services = _runtime_services(state, runtime_services)

    if not part_text:
        missing_slots.append("part")
    else:
        candidates = require_call_value(
            services.invoke_tool(
                "resolve_part_candidates",
                {"part_text": part_text},
                state["execution_context"],
                node="clarification_gate",
            )
        )
        if len(candidates) != 1:
            invalid_slots.append("part")

    if not period and not date_range:
        missing_slots.append("period_or_date_range")
    if date_range and date_range["start_date"] > date_range["end_date"]:
        invalid_slots.append("date_range")

    if not missing_slots and not invalid_slots:
        part = candidates[0]
        ai_trace = {
            **state.get("ai_trace", _new_ai_trace()),
            "clarification_decision": {
                "required": False,
                "missing_slots": [],
                "invalid_slots": [],
                "data_access_permitted": True,
            },
        }
        return _with_event(
            state,
            {
                "part": part,
                "missing_slots": [],
                "invalid_slots": [],
                "clarification": None,
                "status_plan": "cost_calculation",
                "ai_trace": ai_trace,
            },
            "clarification_gate",
            "success",
            "零件和核算期间信息完整，可以进入确定性成本链路。",
            started_at,
        )

    options = (
        [
            {"label": item["part_number"], "value": item["part_number"]}
            for item in candidates
        ]
        if "part" in invalid_slots
        else []
    )

    if "date_range" in invalid_slots:
        question = "开始日期不能晚于结束日期，请重新提供核算日期范围。"
    elif "part" in invalid_slots:
        question = f"没有唯一匹配到“{part_text}”，请选择要核算的零件。"
    elif set(missing_slots) == {"part", "period_or_date_range"}:
        question = "需要补充零件和核算期间，例如“FG-001 2026年6月”。"
    elif "part" in missing_slots:
        question = "要核算哪个零件？"
    else:
        display_name = candidates[0]["part_number"] if candidates else part_text
        question = f"要核算{display_name}的哪个期间？例如“2026年6月”。"

    clarification = {
        "id": f"clar_{uuid4().hex[:12]}",
        "question": question,
        "missing_slots": missing_slots,
        "invalid_slots": invalid_slots,
        "options": options,
    }
    ai_trace = {
        **state.get("ai_trace", _new_ai_trace()),
        "clarification_decision": {
            "required": True,
            "missing_slots": missing_slots,
            "invalid_slots": invalid_slots,
            "data_access_permitted": False,
            "clarification_id": clarification["id"],
        },
    }
    return _with_event(
        state,
        {
            "missing_slots": missing_slots,
            "invalid_slots": invalid_slots,
            "clarification": clarification,
            "outcome": "needs_clarification",
            "status_plan": "clarification",
            "ai_trace": ai_trace,
        },
        "clarification_gate",
        "waiting",
        question,
        started_at,
    )


def request_clarification(state: CostAgentState) -> dict[str, Any]:
    started_at = _now()
    clarification = state["clarification"]
    context = {
        **empty_conversation_context(),
        **state.get("conversation_context", {}),
        "current_part_text": state.get("part_text", ""),
        "current_period": state.get("period", ""),
        "current_date_range": state.get("date_range"),
        "last_intent": state.get("intent", "cost_query"),
        "last_effective_route": "cost_calculation",
        "pending_clarification": clarification,
        "partial_slots": state.get("resolved_slots", {}),
    }
    events = _append_event(
        state,
        "request_clarification",
        "waiting",
        clarification["question"],
        started_at,
    )
    status_state = {
        **state,
        "outcome": "needs_clarification",
        "status_plan": "clarification",
        "conversation_context": context,
    }
    return {
        "final_message": clarification["question"],
        "report_json": None,
        "outcome": "needs_clarification",
        "conversation_context": context,
        "events": events,
        "status_bar": update_status_bar(status_state, events[-1], events),
    }


def final_answer(state: CostAgentState) -> dict[str, Any]:
    started_at = _now()
    errors = state.get("errors", [])
    if state.get("workflow_mode") in {"system_help", "blocked"}:
        final_message = state.get("readonly_answer", "默认只读工作流已结束。")
        status = "error" if state.get("workflow_mode") == "blocked" else "success"
        summary = "未进入成本核算链路。"
        outcome = "blocked" if state.get("workflow_mode") == "blocked" else "completed"
    elif errors:
        final_message = errors[-1]
        status = "error"
        summary = "Agent 已停止，未返回编造金额。"
        outcome = "failed"
    else:
        part = state["part"]
        date_range = state.get("date_range")
        period_text = (
            f"{date_range['start_date']} 至 {date_range['end_date']}"
            if date_range
            else state["period"]
        )
        final_message = f"已完成{part['part_number']} {period_text} 成本分析。"
        status = "success"
        summary = final_message
        outcome = "completed"

    context = {
        **empty_conversation_context(),
        **state.get("conversation_context", {}),
        "last_effective_route": state.get("effective_route", "blocked"),
        "pending_clarification": None,
        "partial_slots": {},
    }
    if outcome == "completed" and state.get("effective_route") == "cost_calculation":
        part = state["part"]
        context.update(
            {
                "current_part_text": part["part_number"],
                "current_part_id": part["part_id"],
                "current_part_number": part["part_number"],
                "current_period": state.get("period", ""),
                "current_date_range": state.get("date_range"),
                "last_intent": state.get("intent", "cost_query"),
                "last_report_run_id": state["run_id"],
            }
        )

    status_state = {
        **state,
        "outcome": outcome,
        "conversation_context": context,
    }
    events = _append_event(status_state, "final_answer", status, summary, started_at)
    report_json = state.get("report_json")
    if report_json:
        report_json = {**report_json, "agent_steps": events}

    return {
        "final_message": final_message,
        "events": events,
        "status_bar": update_status_bar(status_state, events[-1], events),
        "report_json": report_json,
        "outcome": outcome,
        "conversation_context": context,
    }


def route_after_clarification_gate(state: CostAgentState) -> str:
    if state.get("outcome") == "needs_clarification":
        return "request_clarification"
    return "resolve_part"


def route_after_select_route(state: CostAgentState) -> str:
    if state.get("workflow_mode") == "cost_calculation":
        return "understand_question"
    return "final_answer"


def _runtime_services(
    state: CostAgentState, runtime_services: RuntimeServices | None
) -> RuntimeServices:
    services = runtime_services or default_runtime_services()
    services.ensure_run(state["run_id"])
    return services
