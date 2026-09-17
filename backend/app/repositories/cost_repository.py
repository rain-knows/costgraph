from __future__ import annotations

from typing import Any, Protocol

from app.domain.authorization import ExecutionContext


class CostRepository(Protocol):
    def list_parts(
        self, execution_context: ExecutionContext
    ) -> list[dict[str, Any]]: ...

    def find_parts(
        self, part_text: str, execution_context: ExecutionContext
    ) -> list[dict[str, Any]]: ...

    def overview_projection(
        self, period: str, execution_context: ExecutionContext
    ) -> dict[str, Any]: ...

    def list_finished_batch_projections(
        self,
        *,
        period: str,
        query: str | None,
        cost_center_code: str | None,
        sort: str,
        page: int,
        page_size: int,
        execution_context: ExecutionContext,
    ) -> tuple[list[dict[str, Any]], int]: ...

    def load_finished_batch_projection(
        self, finished_batch_id: str, execution_context: ExecutionContext
    ) -> dict[str, Any] | None: ...

    def list_part_period_projections(
        self,
        part_id: str,
        period: str,
        execution_context: ExecutionContext,
    ) -> list[dict[str, Any]]: ...


def get_cost_repository() -> CostRepository:
    from app.repositories.postgres_cost_repository import PostgresCostRepository

    return PostgresCostRepository()
