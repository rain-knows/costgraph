"""导入任务持久层契约（规格 3.2、3.4、第 4 节）。

冻结接口名，Worker 与 API 层依赖本模块：

- ``create_job`` 幂等创建（同租户 + ``idempotency_key``，或同租户 +
  ``source_system`` + ``snapshot_hash`` + ``calculation_rule_version``），
  返回的任务 dict 附带 ``duplicate: bool``。
- ``list_jobs`` / ``get_job`` / ``list_job_errors`` 强制 ``tenant_id`` 边界，
  未找到统一返回 ``None``（HTTP 层转 404）。
- ``decide`` / ``cancel`` 不是查询：目标任务不存在时抛
  :class:`ImportJobNotFoundError`（错误码 ``import_job_not_found``），
  状态机非法迁移抛 :class:`ImportJobStateConflictError`
  （错误码 ``import_job_state_conflict``）。
- ``claim_next_job`` / ``renew_job_lease`` / ``complete_validation`` /
  ``fail_job`` 是 Worker 租约路径；发布状态只能与快照共用事务推进。

状态机合法迁移在 repository 层确定性校验；``claim_next_job`` 额外回收租约
已过期的 ``validating`` / ``publishing`` 行，使可重试失败无需非法状态迁移。
"""

from __future__ import annotations

from typing import Any, Protocol
from uuid import UUID

from app.domain.authorization import ExecutionContext
from app.domain.errors import AgentDomainError

IMPORT_JOB_STATUSES: tuple[str, ...] = (
    "submitted",
    "validating",
    "validation_failed",
    "approval_pending",
    "approved",
    "rejected",
    "publishing",
    "published",
    "publish_failed",
    "cancelled",
)

#: 只有这些状态允许被 Worker 领取并推进（规格第 4 节状态机）。
#: ``publish_failed`` 是唯一的重试回边：已批准任务发布失败后保留租约语义，
#: 可由下一次领取重新进入 ``publishing``；``validation_failed`` 不可重试。
CLAIMABLE_JOB_STATUSES: tuple[str, ...] = (
    "submitted",
    "approved",
    "publish_failed",
)

#: 领取后需要进入的工作中状态；租约过期时可在同一状态内重试。
WORKING_JOB_STATUSES: tuple[str, ...] = ("validating", "publishing")

CLAIM_TARGET_STATUS: dict[str, str] = {
    "submitted": "validating",
    "approved": "publishing",
    "publish_failed": "publishing",
}

ALLOWED_JOB_TRANSITIONS: dict[str, frozenset[str]] = {
    "submitted": frozenset({"validating", "cancelled"}),
    "validating": frozenset({"validation_failed", "approval_pending", "cancelled"}),
    "validation_failed": frozenset(),
    "approval_pending": frozenset({"approved", "rejected", "cancelled"}),
    "approved": frozenset({"publishing"}),
    "rejected": frozenset(),
    "publishing": frozenset({"published", "publish_failed"}),
    "publish_failed": frozenset({"publishing"}),
    "published": frozenset(),
    "cancelled": frozenset(),
}

#: 只有这些状态可以取消（规格第 4 节）。
CANCELLABLE_JOB_STATUSES: tuple[str, ...] = (
    "submitted",
    "validating",
    "approval_pending",
)

DECISION_TO_STATUS: dict[str, str] = {"approve": "approved", "reject": "rejected"}


class ImportJobNotFoundError(AgentDomainError):
    """任务不存在或不属于当前租户；HTTP 层必须映射为 404。"""

    def __init__(self, job_id: UUID) -> None:
        super().__init__(
            "import_job_not_found",
            "未找到指定的数据导入任务。",
            details={"job_id": str(job_id)},
        )


class ImportJobStateConflictError(AgentDomainError):
    """状态机非法迁移；HTTP 层必须映射为 409。"""

    def __init__(self, current: str, target: str) -> None:
        super().__init__(
            "import_job_state_conflict",
            "导入任务当前状态不允许该操作。",
            details={"current_status": current, "target_status": target},
        )


class ImportJobLeaseLostError(AgentDomainError):
    """Worker 不再持有任务租约，或任务状态与预期不一致；Worker 应放弃本任务。"""

    def __init__(self, job_id: UUID, *, status: str | None = None) -> None:
        super().__init__(
            "import_job_lease_lost",
            "导入任务租约已失效。",
            details={"job_id": str(job_id), "status": status},
        )


def validate_job_transition(current: str, target: str) -> None:
    if target not in ALLOWED_JOB_TRANSITIONS.get(current, frozenset()):
        raise ImportJobStateConflictError(current, target)


class ImportJobRepository(Protocol):
    def create_job(
        self,
        *,
        tenant_id: str,
        principal_id: str,
        principal_roles: list[str],
        source_system: str,
        source_object_key: str,
        source_object_size: int,
        source_object_sha256: str,
        snapshot_hash: str,
        calculation_rule_version: str,
        idempotency_key: str,
        total_rows: int,
    ) -> dict[str, Any]:
        """幂等创建导入任务；重复提交返回已有任务与 ``duplicate: True``。"""
        ...

    def list_jobs(
        self,
        context: ExecutionContext,
        *,
        status: str | None,
        page: int,
        page_size: int,
    ) -> tuple[list[dict[str, Any]], int]: ...

    def get_job(self, context: ExecutionContext, job_id: UUID) -> dict[str, Any] | None:
        """返回任务详情（含 ``error_codes``）；不存在或跨租户返回 ``None``。"""
        ...

    def list_job_errors(
        self, context: ExecutionContext, job_id: UUID, *, page: int, page_size: int
    ) -> tuple[list[dict[str, Any]], int] | None: ...

    def decide(
        self,
        context: ExecutionContext,
        job_id: UUID,
        *,
        decision: str,
        reason: str | None,
        actor_principal_id: str,
    ) -> dict[str, Any]:
        """``decision`` 取 ``approve`` / ``reject``；仅 ``approval_pending`` 可决策。"""
        ...

    def cancel(
        self, context: ExecutionContext, job_id: UUID, *, actor_principal_id: str
    ) -> dict[str, Any]:
        """仅 ``submitted`` / ``validating`` / ``approval_pending`` 可取消。"""
        ...

    def claim_next_job(self, worker_id: str) -> dict[str, Any] | None:
        """领取新提交、已批准或发布失败待重试的任务。"""
        ...

    def renew_job_lease(self, job_id: UUID, worker_id: str) -> bool: ...

    def complete_validation(self, job_id: UUID, worker_id: str, *, outcome: Any) -> str:
        """写入校验计数与错误摘要；返回新状态（``approval_pending`` 或 ``validation_failed``）。

        ``outcome`` 接受 ``Mapping`` 或带 ``as_dict()`` 的校验结果对象，字段为
        ``status`` (``valid`` / ``invalid``) 或 ``valid`` (bool)，
        以及 ``total_rows`` / ``valid_rows`` / ``error_rows`` / ``errors``；
        ``errors`` 元素只读取 ``source_table`` / ``source_record_id`` / ``error_code``，
        原始错误文本与载荷一律不落库。
        """
        ...

    def fail_job(
        self, job_id: UUID, worker_id: str, *, retryable: bool, error_code: str
    ) -> str:
        """可重试走 ``available_at`` 退避；不可重试转终态失败并返回新状态。"""
        ...

    def append_audit_event(
        self,
        *,
        tenant_id: str,
        actor_principal_id: str,
        action: str,
        target_type: str,
        target_id: str,
        outcome: str,
        job_id: UUID | None = None,
        batch_id: UUID | None = None,
        request_id: str | None = None,
        detail: dict[str, Any] | None = None,
    ) -> None:
        """只追加治理审计事件；``detail`` 只允许计数/错误码/状态等非敏感字段。"""
        ...


def get_import_job_repository() -> ImportJobRepository:
    from app.repositories.postgres_import_job_repository import (
        PostgresImportJobRepository,
    )

    return PostgresImportJobRepository()
