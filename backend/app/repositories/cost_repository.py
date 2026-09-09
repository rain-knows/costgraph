from __future__ import annotations

from typing import Any, Protocol

from app.domain.authorization import ExecutionContext

CostSource = dict[str, Any]


class CostRepository(Protocol):
    def list_parts(
        self, execution_context: ExecutionContext
    ) -> list[dict[str, Any]]: ...

    def find_parts(
        self, part_text: str, execution_context: ExecutionContext
    ) -> list[dict[str, Any]]: ...

    def load_cost_snapshot(
        self, execution_context: ExecutionContext
    ) -> CostSource | None: ...

    def list_finished_batch_sources(
        self, period: str, execution_context: ExecutionContext
    ) -> list[CostSource]: ...

    def load_finished_batch_source(
        self, finished_batch_id: str, execution_context: ExecutionContext
    ) -> CostSource | None: ...

    def list_part_period_sources(
        self,
        part_id: str,
        period: str,
        execution_context: ExecutionContext,
    ) -> list[CostSource]: ...


def get_cost_repository() -> CostRepository:
    from app.repositories.postgres_cost_repository import PostgresCostRepository

    return PostgresCostRepository()
