from __future__ import annotations

from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from typing import Any

from app.agent.runtime_contracts import (
    RUNTIME_VERSION,
    WORKFLOW_VERSION,
    RuntimeRunRequest,
)
from app.agent.runtime_services import RuntimeServices
from app.agent.state import CostAgentState
from app.agent.status import update_status_bar
from app.services.llm_service import get_deepseek_model


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _event(node: str, status: str, summary: str, started_at: str) -> dict[str, Any]:
    return {
        "node": node,
        "status": status,
        "summary": summary,
        "started_at": started_at,
        "finished_at": _now(),
    }


def _append_event(
    state: CostAgentState, node: str, status: str, summary: str, started_at: str
) -> list[dict[str, Any]]:
    return [*state.get("events", []), _event(node, status, summary, started_at)]


def _with_event(
    state: CostAgentState,
    updates: dict[str, Any],
    node: str,
    status: str,
    summary: str,
    started_at: str,
) -> dict[str, Any]:
    events = _append_event(state, node, status, summary, started_at)
    event = events[-1]
    tool_id = {
        "resolve_product": "resolve_product",
        "clarification_gate": "resolve_product_candidates",
        "load_cost_inputs": "load_cost_inputs",
        "calculate_cost": "calculate_product_cost",
        "build_report_json": "build_report",
    }.get(node)
    if tool_id:
        trace = {**(updates.get("ai_trace") or state.get("ai_trace", _new_ai_trace()))}
        trace["tool_calls"] = [
            *trace.get("tool_calls", []),
            {
                "tool_id": tool_id,
                "status": "success" if status == "success" else status,
                "summary": summary,
            },
        ]
        updates = {**updates, "ai_trace": trace}
    status_state = {**state, **updates}
    return {
        **updates,
        "events": events,
        "status_bar": update_status_bar(status_state, event, events),
    }


def _append_error(state: CostAgentState, message: str) -> list[str]:
    return [*state.get("errors", []), message]


def _periods_between(start_period: str, end_period: str) -> list[str]:
    start_year, start_month = [int(part) for part in start_period.split("-")]
    end_year, end_month = [int(part) for part in end_period.split("-")]
    periods: list[str] = []
    year = start_year
    month = start_month
    while (year, month) <= (end_year, end_month):
        periods.append(f"{year}-{month:02d}")
        month += 1
        if month > 12:
            year += 1
            month = 1
    return periods


def _new_ai_trace(metadata: dict[str, Any] | None = None) -> dict[str, Any]:
    trace = {
        "runtime_version": RUNTIME_VERSION,
        "workflow_version": WORKFLOW_VERSION,
        "provider": "deepseek",
        "configured_model": get_deepseek_model(),
        "calls": [],
        "tool_calls": [],
        "guardrails": [
            "AI 只负责理解问题和生成解释文本。",
            "成本金额只读取 PostgreSQL 中已发布且在授权范围内的数据。",
            "前端只从 report_json 结构化字段渲染金额，不解析自由文本。",
        ],
        "deterministic_calculation": {},
    }
    if metadata:
        trace.update(metadata)
    return trace


def _append_llm_call(state: CostAgentState, call: dict[str, Any]) -> dict[str, Any]:
    trace = {**state.get("ai_trace", _new_ai_trace())}
    trace["calls"] = [*trace.get("calls", []), call]
    return trace


class LangGraphRuntimeAdapter:
    """Execute the single LangGraph workflow behind the internal runtime API."""

    def __init__(
        self,
        graph: Any,
        state_factory: Callable[[RuntimeRunRequest], CostAgentState],
        result_factory: Callable[..., dict[str, Any]],
        runtime_services: RuntimeServices | None = None,
    ) -> None:
        self.graph = graph
        self.state_factory = state_factory
        self.result_factory = result_factory
        self.runtime_services = runtime_services

    def run(self, request: RuntimeRunRequest) -> dict[str, Any]:
        initial_state = self.state_factory(request)
        if self.runtime_services:
            self.runtime_services.ensure_run(initial_state["run_id"])
        try:
            state = self.graph.invoke(initial_state)
        except Exception as exc:  # noqa: BLE001 - stable runtime failure contract
            state = self._failure_state(initial_state, exc)
        return self.result_factory(state, include_internal=request.include_internal)

    def stream(self, request: RuntimeRunRequest) -> Iterator[dict[str, Any]]:
        initial_state = self.state_factory(request)
        if self.runtime_services:
            self.runtime_services.ensure_run(initial_state["run_id"])
        yield {
            "type": "status",
            "run_id": initial_state["run_id"],
            "status_bar": initial_state["status_bar"],
        }
        latest_state: dict[str, Any] | None = None
        emitted_events = 0
        try:
            for snapshot in self.graph.stream(initial_state, stream_mode="values"):
                latest_state = snapshot
                events = snapshot.get("events", [])
                if len(events) <= emitted_events:
                    continue
                for event in events[emitted_events:]:
                    yield {
                        "type": "event",
                        "run_id": snapshot["run_id"],
                        "event": event,
                        "events": events,
                        "status_bar": snapshot.get("status_bar"),
                    }
                emitted_events = len(events)
        except Exception as exc:  # noqa: BLE001 - stable runtime failure contract
            latest_state = self._failure_state(latest_state or initial_state, exc)
            event = latest_state["events"][-1]
            yield {
                "type": "event",
                "run_id": latest_state["run_id"],
                "event": event,
                "events": latest_state["events"],
                "status_bar": latest_state.get("status_bar"),
            }
        if latest_state is None:
            latest_state = self._failure_state(
                initial_state, RuntimeError("Agent workflow 未产生状态快照。")
            )
        result = self.result_factory(
            latest_state, include_internal=request.include_internal
        )
        if result.get("outcome") == "needs_clarification":
            yield {
                "type": "clarification",
                "run_id": result["run_id"],
                "clarification": result.get("clarification"),
                "status_bar": result.get("status_bar"),
            }
        yield {"type": "result", "result": result}

    @staticmethod
    def _failure_state(
        state: CostAgentState | dict[str, Any], exc: Exception
    ) -> CostAgentState:
        started_at = _now()
        message = "Agent Runtime 执行失败，请稍后重试。"
        internal_error = f"{type(exc).__name__}: {exc}"
        events = [
            *state.get("events", []),
            _event("runtime_adapter", "error", message, started_at),
        ]
        status_bar = {
            **(state.get("status_bar") or {}),
            "run_state": "failed",
            "phase": "failed",
            "current_step": "failed",
            "outcome": "failed",
            "last_event": events[-1],
            "updated_at": _now(),
        }
        return {
            **state,
            "final_message": message,
            "report_json": None,
            "outcome": "failed",
            "errors": [*state.get("errors", []), internal_error],
            "events": events,
            "status_bar": status_bar,
        }
