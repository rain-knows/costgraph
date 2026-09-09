"""Deterministic model provider used only by tests and offline evaluation."""

from __future__ import annotations

import re
from copy import deepcopy
from typing import Any

from app.services.llm_service import (
    extract_date_range_from_question,
    extract_latest_period_from_question,
)


class FixtureModelProvider:
    """Provide explicit, local model responses without contacting DeepSeek.

    ``responses`` can override any operation with a value or a list of values.
    The built-in responses are intentionally limited to the offline evaluation
    contract and are never selected by the production runtime.
    """

    provider_id = "fixture"
    provider_version = "fixture-provider-v1"
    model_id = "fixture-model"

    def __init__(self, responses: dict[str, Any] | None = None) -> None:
        self.responses = {
            key: list(value) if isinstance(value, list) else [value]
            for key, value in (responses or {}).items()
        }

    def _take(self, operation: str, default: dict[str, Any]) -> dict[str, Any]:
        values = self.responses.get(operation)
        value = deepcopy(values.pop(0)) if values else deepcopy(default)
        if not isinstance(value, dict):
            raise TypeError(f"Fixture 模型响应必须是对象：{operation}")
        if operation == "parse_cost_question":
            value.setdefault("model", self.model_id)
        if operation == "classify_route":
            value.setdefault("reason", "fixture")
        value.setdefault(
            "llm_call",
            {
                "node": operation,
                "purpose": "离线 fixture 响应",
                "provider": self.provider_id,
                "model": self.model_id,
                "status": "success",
                "response": {"usage": {}},
            },
        )
        return value

    def classify_route(
        self, question: str, candidate_routes: list[str] | None = None, *_args: Any
    ) -> dict[str, Any]:
        route = (
            "cost_calculation"
            if any(word in question for word in ("成本", "核算", "产品"))
            else "system_help"
        )
        if candidate_routes and route not in candidate_routes:
            route = candidate_routes[0]
        return self._take(
            "classify_route",
            {
                "route": route,
                "reason": "fixture",
            },
        )

    def parse_cost_question(self, question: str, *_args: Any) -> dict[str, Any]:
        compact_query = question.replace(" ", "")
        product_match = re.search(r"产品[A-Za-z一二三四五六七八九十]+", compact_query)
        product_text = product_match.group(0) if product_match else ""
        date_range = extract_date_range_from_question(question)
        period = extract_latest_period_from_question(question)
        if date_range:
            period = date_range["end_date"][:7]
        if any(
            keyword in compact_query for keyword in ("对比", "变化", "环比", "原因")
        ):
            intent = "variance_analysis"
        elif "构成" in compact_query or "最高" in compact_query:
            intent = "cost_breakdown"
        else:
            intent = "cost_query"
        return self._take(
            "parse_cost_question",
            {
                "intent": intent,
                "product_text": product_text,
                "period": period,
                "date_range": date_range,
                "model": self.model_id,
            },
        )

    def generate_cost_analysis(self, *_args: Any) -> dict[str, Any]:
        return self._take(
            "generate_cost_analysis",
            {"analysis_text": "离线评测分析。"},
        )


def build_fixture_runtime_services() -> Any:
    """Build an inline RuntimeServices instance for offline callers."""

    from app.agent.harness import AgentHarness
    from app.agent.runtime_contracts import RuntimeBudgetLimits
    from app.agent.runtime_services import RuntimeServices

    return RuntimeServices(
        harness=AgentHarness(),
        model_provider=FixtureModelProvider(),
        limits=RuntimeBudgetLimits(),
    )
