from __future__ import annotations

from typing import Any

from app.agent.routes.cost_nodes import (
    build_report_json,
    calculate_cost,
    load_cost_inputs,
    resolve_product,
    should_continue_after_calculation,
    should_continue_after_data,
    should_continue_after_product,
)
from app.agent.runtime_services import RuntimeServices

COST_ROUTE_NODES = (
    "resolve_product",
    "load_cost_inputs",
    "calculate_cost",
    "build_report_json",
)


def register_cost_calculation_route(
    graph: Any, runtime_services: RuntimeServices
) -> None:
    """Attach the deterministic cost capability route to the top-level graph."""

    graph.add_node(
        "resolve_product", lambda state: resolve_product(state, runtime_services)
    )
    graph.add_node(
        "load_cost_inputs",
        lambda state: load_cost_inputs(state, runtime_services),
    )
    graph.add_node(
        "calculate_cost", lambda state: calculate_cost(state, runtime_services)
    )
    graph.add_node(
        "build_report_json", lambda state: build_report_json(state, runtime_services)
    )
    graph.add_conditional_edges(
        "resolve_product",
        should_continue_after_product,
        {
            "load_cost_inputs": "load_cost_inputs",
            "final_answer": "final_answer",
        },
    )
    graph.add_conditional_edges(
        "load_cost_inputs",
        should_continue_after_data,
        {
            "calculate_cost": "calculate_cost",
            "final_answer": "final_answer",
        },
    )
    graph.add_conditional_edges(
        "calculate_cost",
        should_continue_after_calculation,
        {
            "build_report_json": "build_report_json",
            "final_answer": "final_answer",
        },
    )
    graph.add_edge("build_report_json", "final_answer")
