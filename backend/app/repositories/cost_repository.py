from __future__ import annotations

from typing import Any, Protocol

from app.domain.authorization import ExecutionContext


class CostRepository(Protocol):
    def find_products(
        self, product_text: str, execution_context: ExecutionContext
    ) -> list[dict[str, Any]]: ...

    def list_products(
        self, execution_context: ExecutionContext
    ) -> list[dict[str, Any]]: ...

    def load_cost_inputs(
        self, product_id: str, period: str, execution_context: ExecutionContext
    ) -> dict[str, Any] | None: ...

    def load_previous_cost_inputs(
        self, product_id: str, period: str, execution_context: ExecutionContext
    ) -> dict[str, Any] | None: ...

    def load_cost_inputs_in_period_range(
        self,
        product_id: str,
        start_period: str,
        end_period: str,
        execution_context: ExecutionContext,
    ) -> list[dict[str, Any]]: ...

    def load_product_period_source(
        self, product_id: str, period: str, execution_context: ExecutionContext
    ) -> dict[str, Any] | None: ...


def get_cost_repository() -> CostRepository:
    from app.repositories.postgres_cost_repository import PostgresCostRepository

    return PostgresCostRepository()
