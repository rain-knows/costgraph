from __future__ import annotations

from typing import Any, Protocol
from uuid import UUID

from app.domain.authorization import ExecutionContext


class DataAdminRepository(Protocol):
    def list_batches(
        self,
        context: ExecutionContext,
        *,
        status: str | None,
        page: int,
        page_size: int,
    ) -> tuple[list[dict[str, Any]], int]: ...
    def get_batch(
        self, context: ExecutionContext, batch_id: UUID
    ) -> dict[str, Any] | None: ...
    def list_batch_errors(
        self, context: ExecutionContext, batch_id: UUID, *, page: int, page_size: int
    ) -> tuple[list[dict[str, Any]], int] | None: ...
    def get_published_snapshot(self, context: ExecutionContext) -> dict[str, Any]: ...


def get_data_admin_repository() -> DataAdminRepository:
    from app.repositories.postgres_data_admin_repository import (
        PostgresDataAdminRepository,
    )

    return PostgresDataAdminRepository()
