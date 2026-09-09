from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from langgraph.graph import END, START, StateGraph

from app.agent.harness import AgentHarness
from app.agent.nodes import (
    clarification_gate,
    final_answer,
    load_conversation_context,
    merge_context_slots,
    policy_gate,
    request_clarification,
    route_after_clarification_gate,
    route_after_select_route,
    select_route,
    understand_question,
)
from app.agent.routes.cost_calculation import register_cost_calculation_route
from app.agent.runtime import LangGraphRuntimeAdapter, _new_ai_trace
from app.agent.runtime_contracts import (
    RuntimeEvent,
    RuntimeRunRequest,
    RuntimeRunResult,
)
from app.agent.runtime_services import RuntimeServices, default_runtime_services
from app.agent.state import CostAgentState
from app.agent.status import initial_status_bar
from app.agent.trace import build_audit_trace
from app.domain.conversation import empty_conversation_context


def create_cost_agent_graph(
    checkpointer=None, runtime_services: RuntimeServices | None = None
):
    services = runtime_services or default_runtime_services()
    graph = StateGraph(CostAgentState)
    graph.add_node("normalize_request", lambda state: _start_services(state, services))
    graph.add_node("load_conversation_context", load_conversation_context)
    graph.add_node("policy_gate", policy_gate)
    graph.add_node("select_route", lambda state: select_route(state, services))
    graph.add_node(
        "understand_question", lambda state: understand_question(state, services)
    )
    graph.add_node("merge_context_slots", merge_context_slots)
    graph.add_node(
        "clarification_gate", lambda state: clarification_gate(state, services)
    )
    graph.add_node("request_clarification", request_clarification)
    graph.add_node("final_answer", final_answer)
    register_cost_calculation_route(graph, services)

    graph.add_edge(START, "normalize_request")
    graph.add_edge("normalize_request", "load_conversation_context")
    graph.add_edge("load_conversation_context", "policy_gate")
    graph.add_edge("policy_gate", "select_route")
    graph.add_conditional_edges(
        "select_route",
        route_after_select_route,
        {
            "understand_question": "understand_question",
            "final_answer": "final_answer",
        },
    )
    graph.add_edge("understand_question", "merge_context_slots")
    graph.add_edge("merge_context_slots", "clarification_gate")
    graph.add_conditional_edges(
        "clarification_gate",
        route_after_clarification_gate,
        {
            "request_clarification": "request_clarification",
            "resolve_product": "resolve_product",
        },
    )
    graph.add_edge("request_clarification", END)
    graph.add_edge("final_answer", END)
    return graph.compile(checkpointer=checkpointer)


def _new_run_state_from_request(request: RuntimeRunRequest) -> CostAgentState:
    resolved_run_id = request.run_id or (
        f"run_{datetime.now(UTC).strftime('%Y%m%d_%H%M%S')}_{uuid4().hex[:8]}"
    )
    preparation = AgentHarness().prepare(request)
    execution_context = preparation.execution_context
    capabilities = list(execution_context.effective_capabilities)
    resolved_turn_id = request.turn_id or f"turn_{uuid4().hex}"
    resolved_message_id = request.message_id or f"msg_{uuid4().hex}"
    status_bar = initial_status_bar(
        resolved_run_id, request.question, request.routing_mode, execution_context
    )
    status_bar.update(
        {
            "conversation_id": request.conversation_id,
            "turn_id": resolved_turn_id,
            "message_id": resolved_message_id,
        }
    )
    return {
        "run_id": resolved_run_id,
        "conversation_id": request.conversation_id or "",
        "turn_id": resolved_turn_id,
        "message_id": resolved_message_id,
        "user_query": request.question,
        "routing_mode": request.routing_mode,
        "requested_capabilities": list(execution_context.requested_capabilities),
        "authorized_capabilities": list(execution_context.authorized_capabilities),
        "server_allowed_capabilities": list(
            execution_context.server_allowed_capabilities
        ),
        "enabled_capabilities": capabilities,
        "execution_context": execution_context.model_dump(mode="json"),
        "runtime_context": preparation.runtime_context.model_dump(mode="json"),
        "runtime_metadata": preparation.trace_metadata,
        "requested_route": "blocked",
        "effective_route": "blocked",
        "conversation_context": request.conversation_context
        or empty_conversation_context(),
        "recent_messages": request.recent_messages[-6:],
        "status_plan": "routing",
        "status_bar": status_bar,
        "events": [],
        "errors": [],
        "ai_trace": _new_ai_trace(preparation.trace_metadata),
    }


def _result_from_state(
    result: dict[str, Any], *, include_internal: bool = False
) -> dict[str, Any]:
    runtime_result = RuntimeRunResult(
        run_id=result["run_id"],
        conversation_id=result.get("conversation_id") or None,
        turn_id=result.get("turn_id"),
        message_id=result.get("message_id"),
        final_message=result.get("final_message", ""),
        report_json=result.get("report_json"),
        events=[
            RuntimeEvent.model_validate(event) for event in result.get("events", [])
        ],
        status_bar=result.get("status_bar"),
        outcome=result.get(
            "outcome", "failed" if result.get("errors") else "completed"
        ),
        clarification=result.get("clarification"),
        context_summary=result.get("context_used", {}),
        internal_trace=result.get("ai_trace"),
    )
    response = runtime_result.to_public_dict()
    if include_internal:
        response["_conversation_context"] = result.get(
            "conversation_context", empty_conversation_context()
        )
        response["_audit_trace"] = result.get("audit_trace") or build_audit_trace(
            runtime_result.internal_trace or {}
        )
    return response


def run_runtime(
    request: RuntimeRunRequest, *, runtime_services: RuntimeServices | None = None
) -> dict[str, Any]:
    services = runtime_services or default_runtime_services()
    return LangGraphRuntimeAdapter(
        create_cost_agent_graph(runtime_services=services),
        _new_run_state_from_request,
        _result_from_state,
        services,
    ).run(request)


def stream_runtime(
    request: RuntimeRunRequest, *, runtime_services: RuntimeServices | None = None
):
    services = runtime_services or default_runtime_services()
    yield from LangGraphRuntimeAdapter(
        create_cost_agent_graph(runtime_services=services),
        _new_run_state_from_request,
        _result_from_state,
        services,
    ).stream(request)


def _start_services(
    state: CostAgentState, runtime_services: RuntimeServices
) -> dict[str, Any]:
    runtime_services.ensure_run(state["run_id"])
    provider = runtime_services.model_provider
    provider_metadata = {
        "provider": getattr(provider, "provider_id", None),
        "provider_version": getattr(provider, "provider_version", None),
        "configured_model": getattr(provider, "model_id", None),
    }
    return {
        "runtime_metadata": {
            **state.get("runtime_metadata", {}),
            **provider_metadata,
        },
        "ai_trace": {
            **state.get("ai_trace", _new_ai_trace()),
            **provider_metadata,
        },
    }
