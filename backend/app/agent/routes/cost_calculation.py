from __future__ import annotations

from typing import Any

from app.agent.routes.cost_nodes import (
    build_report_json,
    calculate_cost,
    load_finished_batches,
    resolve_part,
    should_continue_after_calculation,
    should_continue_after_data,
    should_continue_after_product,
)
from app.agent.runtime_services import RuntimeServices

COST_ROUTE_NODES = (
    "resolve_part",
    "load_finished_batches",
    "calculate_cost",
    "build_report_json",
)


def register_cost_calculation_route(
    graph: Any, runtime_services: RuntimeServices
) -> None:
    graph.add_node("resolve_part", lambda state: resolve_part(state, runtime_services))
    graph.add_node(
        "load_finished_batches",
        lambda state: load_finished_batches(state, runtime_services),
    )
    graph.add_node(
        "calculate_cost", lambda state: calculate_cost(state, runtime_services)
    )
    graph.add_node(
        "build_report_json", lambda state: build_report_json(state, runtime_services)
    )
    graph.add_conditional_edges(
        "resolve_part",
        should_continue_after_product,
        {
            "load_finished_batches": "load_finished_batches",
            "final_answer": "final_answer",
        },
    )
    graph.add_conditional_edges(
        "load_finished_batches",
        should_continue_after_data,
        {"calculate_cost": "calculate_cost", "final_answer": "final_answer"},
    )
    graph.add_conditional_edges(
        "calculate_cost",
        should_continue_after_calculation,
        {"build_report_json": "build_report_json", "final_answer": "final_answer"},
    )
    graph.add_edge("build_report_json", "final_answer")
