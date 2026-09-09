from typing import Any

from app.domain.authorization import execution_context_from_state
from app.domain.cost import ProductRecord, cost_input_to_state
from app.repositories.cost_repository import get_cost_repository
from app.services.cost_calculation_service import (
    calculate_product_cost,
    calculate_product_cost_for_date_range,
    compare_cost_results,
)
from app.services.report_service import build_report_json


class _CostRepositoryProxy:
    """Resolve the configured adapter at call time while retaining test injection."""

    def __getattr__(self, name: str):
        return getattr(get_cost_repository(), name)


cost_repository = _CostRepositoryProxy()


def resolve_product_tool(
    product_text: str, execution_context: object
) -> dict[str, Any] | None:
    candidates = cost_repository.find_products(
        product_text, execution_context_from_state(execution_context)
    )
    return (
        ProductRecord.model_validate(candidates[0]).model_dump(mode="json")
        if len(candidates) == 1
        else None
    )


def resolve_product_candidates_tool(
    product_text: str, execution_context: object
) -> list[dict[str, Any]]:
    return [
        ProductRecord.model_validate(item).model_dump(mode="json")
        for item in cost_repository.find_products(
            product_text, execution_context_from_state(execution_context)
        )
    ]


def list_products_tool(execution_context: object) -> list[dict[str, Any]]:
    return [
        ProductRecord.model_validate(item).model_dump(mode="json")
        for item in cost_repository.list_products(
            execution_context_from_state(execution_context)
        )
    ]


def load_cost_inputs_tool(
    product_id: str, period: str, execution_context: object
) -> dict[str, Any] | None:
    result = cost_repository.load_cost_inputs(
        product_id, period, execution_context_from_state(execution_context)
    )
    return cost_input_to_state(result) if result is not None else None


def load_previous_cost_inputs_tool(
    product_id: str, period: str, execution_context: object
) -> dict[str, Any] | None:
    result = cost_repository.load_previous_cost_inputs(
        product_id, period, execution_context_from_state(execution_context)
    )
    return cost_input_to_state(result) if result is not None else None


def load_cost_inputs_in_period_range_tool(
    product_id: str,
    start_period: str,
    end_period: str,
    execution_context: object,
) -> list[dict[str, Any]]:
    results = cost_repository.load_cost_inputs_in_period_range(
        product_id,
        start_period,
        end_period,
        execution_context_from_state(execution_context),
    )
    return [cost_input_to_state(item) for item in results]


def calculate_product_cost_tool(cost_inputs: dict[str, Any]) -> dict[str, Any]:
    return calculate_product_cost(cost_inputs)


def calculate_product_cost_for_date_range_tool(
    cost_input_records: list[dict[str, Any]],
    start_date: str,
    end_date: str,
) -> dict[str, Any]:
    return calculate_product_cost_for_date_range(
        cost_input_records, start_date, end_date
    )


def compare_cost_results_tool(
    current_result: dict[str, Any],
    previous_result: dict[str, Any],
    previous_period: str,
) -> dict[str, Any]:
    return compare_cost_results(current_result, previous_result, previous_period)


def build_report_tool(
    run_id: str,
    product: dict[str, Any],
    period: str,
    calculation_result: dict[str, Any],
    agent_steps: list[dict[str, Any]],
    analysis_text: str,
    model_info: dict[str, Any] | None = None,
    ai_trace: dict[str, Any] | None = None,
    comparison_result: dict[str, Any] | None = None,
    date_range: dict[str, str] | None = None,
    lineage: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return build_report_json(
        run_id,
        product,
        period,
        calculation_result,
        agent_steps,
        analysis_text,
        model_info,
        ai_trace,
        comparison_result,
        date_range,
        lineage,
    )
