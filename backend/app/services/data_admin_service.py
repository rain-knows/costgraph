from __future__ import annotations

from uuid import UUID

from app.domain.authorization import (
    ExecutionContext,
    ExecutionPrincipal,
    build_execution_context,
)
from app.domain.errors import AgentDomainError
from app.repositories.data_admin_repository import (
    DataAdminRepository,
    get_data_admin_repository,
)
from app.schemas.data_admin import (
    DataAdminBatchDetail,
    DataAdminBatchList,
    DataAdminErrorList,
    DataQualitySummary,
    PublishedSnapshot,
)


class DataAdminNotFoundError(AgentDomainError):
    def __init__(self, batch_id: UUID) -> None:
        super().__init__(
            "data_admin_batch_not_found",
            "未找到指定的数据导入批次。",
            details={"batch_id": str(batch_id)},
        )


class DataAdminService:
    def __init__(self, repository: DataAdminRepository | None = None) -> None:
        self._repository = repository

    @property
    def repository(self) -> DataAdminRepository:
        if self._repository is None:
            self._repository = get_data_admin_repository()
        return self._repository

    def _context(self, principal: ExecutionPrincipal) -> ExecutionContext:
        context = build_execution_context(
            "manual",
            ["system_help"],
            requested_permissions=["data_management.read"],
            principal=principal,
        )
        context.require_permission("data_management.read")
        return context

    def list_batches(
        self,
        principal: ExecutionPrincipal,
        *,
        status: str | None,
        page: int,
        page_size: int,
    ) -> DataAdminBatchList:
        context = self._context(principal)
        items, total = self.repository.list_batches(
            context, status=status, page=page, page_size=page_size
        )
        return DataAdminBatchList(
            items=items, page=page, page_size=page_size, total=total
        )

    def get_batch(
        self, principal: ExecutionPrincipal, batch_id: UUID
    ) -> DataAdminBatchDetail:
        context = self._context(principal)
        item = self.repository.get_batch(context, batch_id)
        if item is None:
            raise DataAdminNotFoundError(batch_id)
        return DataAdminBatchDetail.model_validate(item)

    def list_errors(
        self,
        principal: ExecutionPrincipal,
        batch_id: UUID,
        *,
        page: int,
        page_size: int,
    ) -> DataAdminErrorList:
        context = self._context(principal)
        result = self.repository.list_batch_errors(
            context, batch_id, page=page, page_size=page_size
        )
        if result is None:
            raise DataAdminNotFoundError(batch_id)
        items, total = result
        return DataAdminErrorList(
            items=items, page=page, page_size=page_size, total=total
        )

    def published(self, principal: ExecutionPrincipal) -> PublishedSnapshot:
        context = self._context(principal)
        return PublishedSnapshot.model_validate(
            self.repository.get_published_snapshot(context)
        )

    def quality(self, principal: ExecutionPrincipal) -> DataQualitySummary:
        snapshot = self.published(principal)
        return DataQualitySummary(
            published=snapshot.batch is not None,
            error_rows=snapshot.batch.error_rows if snapshot.batch else 0,
            fact_rows=snapshot.fact_rows,
            projection_rows=snapshot.projection_rows,
            part_count=snapshot.part_count,
            period_start=snapshot.period_start,
            period_end=snapshot.period_end,
            rule_version_matches=snapshot.rule_version_matches,
        )


def get_data_admin_service() -> DataAdminService:
    return DataAdminService()
