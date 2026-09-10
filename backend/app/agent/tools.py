from __future__ import annotations

from typing import Any

from app.domain.authorization import execution_context_from_state
from app.repositories.cost_repository import get_cost_repository
from app.services.cost_calculation_service import calculate_finished_batch_cost
from app.services.report_service import build_report_json


class _CostRepositoryProxy:
    def __getattr__(self, name: str):
        return getattr(get_cost_repository(), name)


cost_repository = _CostRepositoryProxy()


def resolve_part_tool(
    part_text: str, execution_context: object
) -> dict[str, Any] | None:
    candidates = cost_repository.find_parts(
        part_text, execution_context_from_state(execution_context)
    )
    return dict(candidates[0]) if len(candidates) == 1 else None


def resolve_part_candidates_tool(
    part_text: str, execution_context: object
) -> list[dict[str, Any]]:
    return [
        dict(item)
        for item in cost_repository.find_parts(
            part_text, execution_context_from_state(execution_context)
        )
    ]


def list_parts_tool(execution_context: object) -> list[dict[str, Any]]:
    return [
        dict(item)
        for item in cost_repository.list_parts(
            execution_context_from_state(execution_context)
        )
    ]


def load_finished_batches_tool(
    period: str, execution_context: object
) -> list[dict[str, Any]]:
    return [
        calculate_finished_batch_cost(source)
        for source in cost_repository.list_finished_batch_sources(
            period, execution_context_from_state(execution_context)
        )
    ]


def calculate_finished_batch_cost_tool(source: dict[str, Any]) -> dict[str, Any]:
    return calculate_finished_batch_cost(source)


def build_report_tool(
    run_id: str,
    part: dict[str, Any],
    period: str,
    calculation_result: dict[str, Any],
    agent_steps: list[dict[str, Any]],
    analysis_text: str,
    model_info: dict[str, Any] | None = None,
    ai_trace: dict[str, Any] | None = None,
    lineage: dict[str, Any] | None = None,
    report_style: str = "presentation",
    comparison_period: str | None = None,
    comparison_calculation_result: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return build_report_json(
        run_id,
        part,
        period,
        calculation_result,
        agent_steps,
        analysis_text,
        model_info,
        ai_trace,
        lineage,
        report_style,
        comparison_period,
        comparison_calculation_result,
    )
