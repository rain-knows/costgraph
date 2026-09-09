from __future__ import annotations

from typing import Any


class AgentDomainError(RuntimeError):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details or {}


class ConversationNotFoundError(AgentDomainError):
    def __init__(self, conversation_id: str) -> None:
        super().__init__(
            "conversation_not_found",
            f"未找到会话：{conversation_id}",
            details={"conversation_id": conversation_id},
        )


class ConversationBusyError(AgentDomainError):
    def __init__(self, conversation_id: str) -> None:
        super().__init__(
            "conversation_busy",
            "当前会话已有一个 Turn 正在执行，请等待完成后再管理会话。",
            details={"conversation_id": conversation_id},
        )


class ConversationMessageCursorNotFoundError(AgentDomainError):
    def __init__(self, conversation_id: str, message_id: str) -> None:
        super().__init__(
            "conversation_message_cursor_not_found",
            "消息分页游标不存在或不属于当前会话。",
            details={
                "conversation_id": conversation_id,
                "message_id": message_id,
            },
        )


class ConversationTurnNotFoundError(AgentDomainError):
    def __init__(self, conversation_id: str, turn_id: str) -> None:
        super().__init__(
            "conversation_turn_not_found",
            "未找到当前会话中的运行记录。",
            details={"conversation_id": conversation_id, "turn_id": turn_id},
        )


class ArtifactNotFoundError(AgentDomainError):
    def __init__(self, artifact_id: str) -> None:
        super().__init__(
            "artifact_not_found",
            f"未找到产出：{artifact_id}",
            details={"artifact_id": artifact_id},
        )


class ArtifactConflictError(AgentDomainError):
    def __init__(self, run_id: str) -> None:
        super().__init__(
            "artifact_idempotency_conflict",
            "相同运行或消息标识已绑定到不同产出。",
            details={"run_id": run_id},
        )


class ArtifactPersistenceError(AgentDomainError):
    def __init__(self) -> None:
        super().__init__(
            "artifact_persistence_failed",
            "产出持久化失败，请使用相同 message_id 重试。",
        )


class CostDataNotFoundError(AgentDomainError):
    def __init__(self, product_id: str, period: str) -> None:
        super().__init__(
            "cost_data_not_found",
            "未找到该产品在指定期间的已发布成本数据。",
            details={"product_id": product_id, "period": period},
        )
