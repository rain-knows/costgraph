"""导入任务服务（规格第 4、5 节）。

职责边界：

- HTTP 层只创建任务或改变任务状态；校验、投影生成与发布全部由 Worker 完成。
- 载荷写入对象存储，数据库只保留 ``object_key`` / ``size`` / ``sha256``；
  ``payload`` 与对象内容永不出现在响应里。
- 权限来自服务端固定请求的权限（客户端不能提交权限来授权自己），沿用
  ``data_admin_service`` 的 ``_context`` + ``require_permission`` 模式。
- 状态机非法迁移由持久层确定性校验并抛
  :class:`ImportJobStateConflictError`（``import_job_state_conflict``），
  不存在或跨租户统一 :class:`ImportJobNotFoundError`（``import_job_not_found``）。
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any
from uuid import UUID

from app.domain.authorization import (
    ExecutionContext,
    ExecutionPrincipal,
    PermissionId,
    build_execution_context,
)
from app.domain.errors import AgentDomainError
from app.repositories.import_job_repository import (
    ImportJobNotFoundError,
    ImportJobRepository,
    get_import_job_repository,
)
from app.schemas.data_admin import (
    DataAdminErrorList,
    ImportDecisionRequest,
    ImportJobCreateRequest,
    ImportJobDetail,
    ImportJobList,
    ImportJobSubmission,
    import_job_detail_response,
    import_job_response,
    import_job_submission_response,
)
from app.services.cost_data_import_service import _canonical_hash
from app.services.import_object_store import LocalImportObjectStore

#: 导入载荷固定包含的四类记录集合（与 Worker 的 ``decode_import_payload`` 一致）。
IMPORT_PAYLOAD_TABLES: tuple[str, ...] = (
    "parts",
    "cost_events",
    "cost_event_inputs",
    "cost_records",
)

#: ``Idempotency-Key`` 请求头长度上限，与 ``idempotency_key`` 列宽一致。
IDEMPOTENCY_KEY_MAX_LENGTH = 128

#: 权限 -> 无权限时的公开错误码（规格第 5 节）。
ACCESS_DENIED_CODES: dict[str, str] = {
    "data_management.read": "data_management_access_denied",
    "data_import.submit": "data_import_submit_access_denied",
    "data_import.approve": "data_import_approve_access_denied",
}


class ImportAccessDeniedError(PermissionError):
    """主体缺少指定权限；HTTP 层按权限映射为 403 与对应错误码。"""

    def __init__(self, permission: str) -> None:
        self.code = ACCESS_DENIED_CODES.get(permission, "data_management_access_denied")
        self.message = "当前执行主体无权执行该数据导入操作。"
        super().__init__(self.message)


class ImportPayloadInvalidError(AgentDomainError):
    """载荷结构非法（含未知记录集合）；HTTP 层映射为 422。"""

    def __init__(self, unknown_tables: list[str]) -> None:
        super().__init__(
            "import_payload_invalid",
            "导入载荷必须只包含 parts、cost_events、cost_event_inputs、cost_records。",
            details={"unknown_tables": unknown_tables},
        )


def normalize_import_payload(
    payload: Mapping[str, list[dict[str, Any]]],
) -> dict[str, list[dict[str, Any]]]:
    """把请求载荷归一化为固定四类记录集合。

    未知记录集合直接拒绝，避免写入 Worker 永远无法解码的对象，也保证对象内容
    与 ``snapshot_hash`` 的定义完全一致。
    """
    unknown = sorted(set(payload) - set(IMPORT_PAYLOAD_TABLES))
    if unknown:
        raise ImportPayloadInvalidError(unknown)
    return {
        name: [dict(row) for row in (payload.get(name) or [])]
        for name in IMPORT_PAYLOAD_TABLES
    }


def encode_import_payload(payload: Mapping[str, list[dict[str, Any]]]) -> bytes:
    """对象存储中的载荷字节；与 :func:`_canonical_hash` 的输入一一对应。"""
    return json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


class ImportJobService:
    def __init__(
        self,
        repository: ImportJobRepository | None = None,
        object_store: LocalImportObjectStore | None = None,
    ) -> None:
        self._repository = repository
        self._object_store = object_store

    @property
    def repository(self) -> ImportJobRepository:
        if self._repository is None:
            self._repository = get_import_job_repository()
        return self._repository

    @property
    def object_store(self) -> LocalImportObjectStore:
        if self._object_store is None:
            self._object_store = LocalImportObjectStore()
        return self._object_store

    # ------------------------------------------------------------- 授权上下文

    def _context(
        self, principal: ExecutionPrincipal, permission: PermissionId
    ) -> ExecutionContext:
        context = build_execution_context(
            "manual",
            ["system_help"],
            requested_permissions=[permission],
            principal=principal,
        )
        try:
            context.require_permission(permission)
        except PermissionError as exc:
            raise ImportAccessDeniedError(permission) from exc
        return context

    # ------------------------------------------------------------------- 提交

    def submit(
        self,
        principal: ExecutionPrincipal,
        idempotency_key: str,
        request: ImportJobCreateRequest,
        request_id: str | None = None,
    ) -> ImportJobSubmission:
        self._context(principal, "data_import.submit")
        payload = normalize_import_payload(request.payload)
        snapshot_hash = _canonical_hash(payload)
        total_rows = sum(len(rows) for rows in payload.values())
        reference = self.object_store.put(encode_import_payload(payload))
        record = self.repository.create_job(
            tenant_id=principal.tenant_id,
            principal_id=principal.principal_id,
            principal_roles=list(principal.roles),
            source_system=request.source_system,
            source_object_key=reference.object_key,
            source_object_size=reference.size,
            source_object_sha256=reference.sha256,
            snapshot_hash=snapshot_hash,
            calculation_rule_version=request.calculation_rule_version,
            idempotency_key=idempotency_key,
            total_rows=total_rows,
        )
        if not record["duplicate"]:
            self._audit(
                principal,
                action="submitted",
                target_type="import_job",
                target_id=str(record["job_id"]),
                outcome="success",
                request_id=request_id,
                job_id=record["job_id"],
                detail={"status": "submitted", "total_rows": total_rows},
            )
        return ImportJobSubmission.model_validate(
            import_job_submission_response(record)
        )

    # ------------------------------------------------------------------- 查询

    def list_jobs(
        self,
        principal: ExecutionPrincipal,
        *,
        status: str | None,
        page: int,
        page_size: int,
    ) -> ImportJobList:
        context = self._context(principal, "data_management.read")
        items, total = self.repository.list_jobs(
            context, status=status, page=page, page_size=page_size
        )
        return ImportJobList(
            items=[import_job_response(item) for item in items],
            page=page,
            page_size=page_size,
            total=total,
        )

    def get_job(self, principal: ExecutionPrincipal, job_id: UUID) -> ImportJobDetail:
        context = self._context(principal, "data_management.read")
        item = self.repository.get_job(context, job_id)
        if item is None:
            raise ImportJobNotFoundError(job_id)
        return ImportJobDetail.model_validate(import_job_detail_response(item))

    def list_errors(
        self,
        principal: ExecutionPrincipal,
        job_id: UUID,
        *,
        page: int,
        page_size: int,
    ) -> DataAdminErrorList:
        context = self._context(principal, "data_management.read")
        result = self.repository.list_job_errors(
            context, job_id, page=page, page_size=page_size
        )
        if result is None:
            raise ImportJobNotFoundError(job_id)
        items, total = result
        return DataAdminErrorList(
            items=items, page=page, page_size=page_size, total=total
        )

    # --------------------------------------------------------------- 状态变更

    def decide(
        self,
        principal: ExecutionPrincipal,
        job_id: UUID,
        decision: ImportDecisionRequest,
        request_id: str | None = None,
    ) -> ImportJobDetail:
        context = self._context(principal, "data_import.approve")
        record = self.repository.decide(
            context,
            job_id,
            decision=decision.decision,
            reason=decision.reason,
            actor_principal_id=principal.principal_id,
        )
        self._audit(
            principal,
            action=str(record["decision"]),
            target_type="import_job",
            target_id=str(job_id),
            outcome="success",
            request_id=request_id,
            job_id=job_id,
            detail={"status": record["status"], "decision": record["decision"]},
        )
        return ImportJobDetail.model_validate(import_job_detail_response(record))

    def cancel(
        self,
        principal: ExecutionPrincipal,
        job_id: UUID,
        request_id: str | None = None,
    ) -> ImportJobDetail:
        context = self._context(principal, "data_import.submit")
        record = self.repository.cancel(
            context, job_id, actor_principal_id=principal.principal_id
        )
        self._audit(
            principal,
            action="cancelled",
            target_type="import_job",
            target_id=str(job_id),
            outcome="success",
            request_id=request_id,
            job_id=job_id,
            detail={"status": "cancelled"},
        )
        return ImportJobDetail.model_validate(import_job_detail_response(record))

    # ------------------------------------------------------------------- 审计

    def _audit(
        self,
        principal: ExecutionPrincipal,
        *,
        action: str,
        target_type: str,
        target_id: str,
        outcome: str,
        request_id: str | None,
        job_id: UUID,
        detail: dict[str, Any],
    ) -> None:
        self.repository.append_audit_event(
            tenant_id=principal.tenant_id,
            actor_principal_id=principal.principal_id,
            action=action,
            target_type=target_type,
            target_id=target_id,
            outcome=outcome,
            job_id=job_id,
            request_id=request_id,
            detail=detail,
        )


def get_import_job_service() -> ImportJobService:
    return ImportJobService()
