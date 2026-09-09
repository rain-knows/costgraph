from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import NAMESPACE_URL, uuid4, uuid5

from sqlalchemy import and_, or_, select, text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from app.agent.capabilities import normalize_capabilities
from app.agent.runtime_contracts import (
    RUNTIME_API_VERSION,
    RUNTIME_VERSION,
    WORKFLOW_VERSION,
    RuntimeBudgetLimits,
    RuntimeBudgetReservation,
    RuntimeBudgetUsage,
)
from app.agent.trace import build_audit_trace
from app.db.engine import get_session_factory
from app.db.models import (
    AgentArtifact,
    RuntimeAgentRun,
    RuntimeAgentRunEvent,
    RuntimeAgentWorker,
    RuntimeAuditTrace,
    RuntimeConversation,
    RuntimeMessage,
    RuntimeTurn,
)
from app.domain.authorization import ExecutionPrincipal
from app.domain.errors import (
    AgentDomainError,
    ConversationBusyError,
    ConversationNotFoundError,
)
from app.repositories.artifact_repository import build_artifact_record
from app.services.llm_service import canonical_sha256
from app.settings import AppSettings, get_settings

ACTIVE_RUN_STATUSES = ("queued", "running", "retry_wait", "finalizing")
TERMINAL_RUN_STATUSES = ("succeeded", "failed", "cancelled")
ALLOWED_RUN_TRANSITIONS: dict[str, set[str]] = {
    "queued": {"running", "cancelled", "failed"},
    "running": {"finalizing", "retry_wait", "failed", "cancelled"},
    "retry_wait": {"running", "cancelled", "failed"},
    "finalizing": {"succeeded", "failed"},
    "succeeded": set(),
    "failed": set(),
    "cancelled": set(),
}
RETRY_BACKOFF_SECONDS = (1, 5, 30)
RUNNING_EVENT_SEQUENCE_BASE = 900_000
FINALIZING_EVENT_SEQUENCE = 950_000
RETRY_WAIT_EVENT_SEQUENCE_BASE = 960_000


def utc_now() -> datetime:
    return datetime.now(UTC)


def validate_run_transition(current: str, target: str) -> None:
    if target not in ALLOWED_RUN_TRANSITIONS.get(current, set()):
        raise ValueError(f"非法 Run 状态转换：{current} -> {target}")


class PostgresRunRepository:
    def __init__(
        self, session_factory=None, settings: AppSettings | None = None
    ) -> None:
        self._session_factory = session_factory or get_session_factory()
        self.settings = settings or get_settings()

    def create_run(
        self,
        *,
        conversation_id: str,
        message_id: str,
        question: str,
        routing_mode: str | None,
        enabled_capabilities: list[str] | None,
        reply_to_clarification_id: str | None,
        principal: ExecutionPrincipal,
        request_id: str,
    ) -> dict[str, Any]:
        now = utc_now()
        session: Session = self._session_factory()
        try:
            conversation = session.scalar(
                select(RuntimeConversation)
                .where(
                    RuntimeConversation.conversation_id == conversation_id,
                    RuntimeConversation.tenant_id == principal.tenant_id,
                    RuntimeConversation.principal_id == principal.principal_id,
                )
                .with_for_update()
            )
            if conversation is None:
                raise ConversationNotFoundError(conversation_id)

            pending = (conversation.context_json or {}).get("pending_clarification")
            if reply_to_clarification_id and (
                not pending or pending.get("id") != reply_to_clarification_id
            ):
                raise AgentDomainError(
                    "clarification_mismatch",
                    "当前补充信息与待处理的澄清问题不匹配。",
                    details={"reply_to_clarification_id": reply_to_clarification_id},
                )

            effective_routing_mode = routing_mode or conversation.routing_mode
            if enabled_capabilities is None and routing_mode is None:
                effective_capabilities = list(conversation.enabled_capabilities_json)
            else:
                effective_capabilities = normalize_capabilities(
                    effective_routing_mode,
                    enabled_capabilities=enabled_capabilities,
                )
            recent_rows = session.scalars(
                select(RuntimeMessage)
                .where(RuntimeMessage.conversation_id == conversation_id)
                .order_by(RuntimeMessage.created_at.desc())
                .limit(6)
            ).all()
            recent_messages = [
                {"role": row.role, "content": row.content}
                for row in reversed(recent_rows)
            ]
            request_snapshot = {
                "api_version": RUNTIME_API_VERSION,
                "question": question,
                "routing_mode": effective_routing_mode,
                "enabled_capabilities": effective_capabilities,
                "reply_to_clarification_id": reply_to_clarification_id,
                "conversation_context": conversation.context_json,
                "recent_messages": recent_messages,
                "principal": principal.model_dump(mode="json"),
            }
            request_hash = canonical_sha256(
                {
                    "conversation_id": conversation_id,
                    "message_id": message_id,
                    **request_snapshot,
                }
            )
            existing = session.scalar(
                select(RuntimeAgentRun).where(
                    RuntimeAgentRun.tenant_id == principal.tenant_id,
                    RuntimeAgentRun.message_id == message_id,
                )
            )
            if existing is not None:
                if (
                    existing.principal_id != principal.principal_id
                    or existing.conversation_id != conversation_id
                    or existing.request_sha256 != request_hash
                ):
                    raise AgentDomainError(
                        "message_id_conflict",
                        "message_id 已绑定到其他请求。",
                        details={"message_id": message_id},
                    )
                session.rollback()
                return self._run_dict(existing)

            active = session.scalar(
                select(RuntimeAgentRun).where(
                    RuntimeAgentRun.conversation_id == conversation_id,
                    RuntimeAgentRun.status.in_(ACTIVE_RUN_STATUSES),
                )
            )
            if active is not None:
                raise ConversationBusyError(conversation_id)

            run_id = f"run_{uuid4().hex}"
            turn_id = f"turn_{uuid4().hex}"
            run = RuntimeAgentRun(
                run_id=run_id,
                conversation_id=conversation_id,
                turn_id=turn_id,
                message_id=message_id,
                tenant_id=principal.tenant_id,
                principal_id=principal.principal_id,
                request_id=request_id,
                request_json=request_snapshot,
                request_sha256=request_hash,
                api_version=RUNTIME_API_VERSION,
                runtime_version=RUNTIME_VERSION,
                workflow_version=WORKFLOW_VERSION,
                budget_limits_json=RuntimeBudgetLimits(
                    max_model_calls=self.settings.agent_max_model_calls,
                    max_tool_calls=self.settings.agent_max_tool_calls,
                    tool_timeout_seconds=self.settings.agent_tool_timeout_seconds,
                    model_timeout_seconds=self.settings.deepseek_timeout_seconds,
                    run_timeout_seconds=self.settings.agent_run_timeout_seconds,
                ).model_dump(mode="json"),
                budget_usage_json=RuntimeBudgetUsage().model_dump(mode="json"),
                next_event_sequence=1,
                status="queued",
                attempt_count=0,
                available_at=now,
                created_at=now,
                updated_at=now,
            )
            session.add(run)
            session.flush()
            event = self.append_runtime_event(
                run_id,
                event_key=f"{run_id}:create:status",
                event_type="status",
                name="run",
                status="queued",
                summary="运行已进入队列。",
                session=session,
                locked_run=run,
                event_absent=True,
            )
            run.last_event_id = event["event_id"]
            session.commit()
            session.refresh(run)
            return self._run_dict(run)
        except IntegrityError as exc:
            session.rollback()
            existing = session.scalar(
                select(RuntimeAgentRun).where(
                    RuntimeAgentRun.tenant_id == principal.tenant_id,
                    RuntimeAgentRun.message_id == message_id,
                )
            )
            if existing is not None and existing.request_sha256 == request_hash:
                return self._run_dict(existing)
            raise ConversationBusyError(conversation_id) from exc
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def get_run(
        self,
        conversation_id: str,
        run_id: str,
        principal: ExecutionPrincipal,
    ) -> dict[str, Any]:
        session: Session = self._session_factory()
        try:
            row = self._owned_run(
                session,
                conversation_id,
                run_id,
                principal,
            )
            if row is None:
                raise AgentDomainError(
                    "run_not_found",
                    "未找到当前会话中的 Run。",
                    details={"conversation_id": conversation_id, "run_id": run_id},
                )
            return self._run_dict(row)
        finally:
            session.close()

    def get_active_run(
        self,
        conversation_id: str,
        principal: ExecutionPrincipal,
    ) -> dict[str, Any] | None:
        session: Session = self._session_factory()
        try:
            conditions = [
                RuntimeAgentRun.conversation_id == conversation_id,
                RuntimeAgentRun.tenant_id == principal.tenant_id,
                RuntimeAgentRun.principal_id == principal.principal_id,
                RuntimeAgentRun.status.in_(ACTIVE_RUN_STATUSES),
            ]
            row = session.scalar(
                select(RuntimeAgentRun)
                .where(*conditions)
                .order_by(RuntimeAgentRun.created_at.desc())
            )
            return self._run_dict(row) if row is not None else None
        finally:
            session.close()

    def list_events(
        self,
        conversation_id: str,
        run_id: str,
        principal: ExecutionPrincipal,
        *,
        after_event_id: int = 0,
    ) -> list[dict[str, Any]]:
        session: Session = self._session_factory()
        try:
            if (
                self._owned_run(
                    session,
                    conversation_id,
                    run_id,
                    principal,
                )
                is None
            ):
                raise AgentDomainError("run_not_found", "未找到当前会话中的 Run。")
            rows = session.scalars(
                select(RuntimeAgentRunEvent)
                .where(
                    RuntimeAgentRunEvent.run_id == run_id,
                    RuntimeAgentRunEvent.event_id > after_event_id,
                )
                .order_by(RuntimeAgentRunEvent.event_id)
            ).all()
            return [self._event_dict(row) for row in rows]
        finally:
            session.close()

    def list_runtime_event_keys(self, run_id: str, *, prefix: str) -> set[str]:
        """Read persisted event keys once when a Worker resumes a Run."""

        with self._session_factory() as session:
            values = session.scalars(
                select(RuntimeAgentRunEvent.event_key).where(
                    RuntimeAgentRunEvent.run_id == run_id,
                    RuntimeAgentRunEvent.event_key.startswith(prefix),
                )
            ).all()
            return set(values)

    def append_event(
        self,
        run_id: str,
        *,
        graph_sequence: int,
        event_type: str,
        payload: dict[str, Any],
        session: Session | None = None,
        locked_run: RuntimeAgentRun | None = None,
        event_absent: bool = False,
    ) -> dict[str, Any]:
        semantic = _semantic_event(event_type, payload)
        return self.append_runtime_event(
            run_id,
            event_key=f"{run_id}:graph:{graph_sequence}:{event_type}",
            event_type=semantic["event_type"],
            name=semantic["name"],
            status=semantic["status"],
            summary=semantic["summary"],
            payload=semantic["payload"],
            started_at=semantic.get("started_at"),
            finished_at=semantic.get("finished_at"),
            session=session,
            locked_run=locked_run,
            event_absent=event_absent,
        )

    def append_runtime_event(
        self,
        run_id: str,
        *,
        event_key: str,
        event_type: str,
        name: str | None,
        status: str,
        summary: str,
        payload: dict[str, Any] | None = None,
        started_at: str | datetime | None = None,
        finished_at: str | datetime | None = None,
        session: Session | None = None,
        locked_run: RuntimeAgentRun | None = None,
        event_absent: bool = False,
    ) -> dict[str, Any]:
        """Append one semantic event with an atomically allocated run-local sequence."""

        owns_session = session is None
        current = session or self._session_factory()
        try:
            run = locked_run
            existing = None
            if run is None:
                locked = self._lock_run_and_event(current, run_id, event_key)
                if locked is None:
                    raise RuntimeError("Run 不存在。")
                run, existing = locked
            elif not event_absent:
                existing = current.scalar(
                    select(RuntimeAgentRunEvent).where(
                        RuntimeAgentRunEvent.event_key == event_key
                    )
                )
            if existing is not None:
                if owns_session:
                    current.rollback()
                return self._event_dict(existing)
            sequence = run.next_event_sequence
            run.next_event_sequence += 1
            now = utc_now()
            public_payload = {
                "schema_version": "2.0",
                "sequence": sequence,
                "kind": event_type,
                "name": name,
                "status": status,
                "summary": summary,
                "started_at": _datetime_json(started_at),
                "finished_at": _datetime_json(finished_at),
                **(payload or {}),
            }
            row = RuntimeAgentRunEvent(
                run_id=run_id,
                graph_sequence=sequence,
                schema_version="2.0",
                event_key=event_key,
                event_type=event_type,
                name=name,
                status=status,
                started_at=_as_datetime(started_at),
                finished_at=_as_datetime(finished_at),
                payload_json=public_payload,
                created_at=now,
            )
            current.add(row)
            current.flush()
            run.last_event_id = row.event_id
            run.updated_at = now
            if owns_session:
                current.commit()
            return self._event_dict(row)
        except Exception:
            if owns_session:
                current.rollback()
            raise
        finally:
            if owns_session:
                current.close()

    def initialize_runtime_budget(
        self, run_id: str, limits: RuntimeBudgetLimits
    ) -> None:
        session: Session = self._session_factory()
        try:
            row = session.scalar(
                select(RuntimeAgentRun)
                .where(RuntimeAgentRun.run_id == run_id)
                .with_for_update()
            )
            if row is None:
                raise RuntimeError("Run 不存在。")
            if not row.budget_limits_json:
                row.budget_limits_json = limits.model_dump(mode="json")
            if not row.budget_usage_json:
                row.budget_usage_json = RuntimeBudgetUsage().model_dump(mode="json")
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def reserve_runtime_call(
        self,
        run_id: str,
        kind: str,
        *,
        event: dict[str, Any],
    ) -> RuntimeBudgetReservation | None:
        """Reserve a call and persist its start event in the same transaction."""

        session: Session = self._session_factory()
        try:
            locked = self._lock_run_and_event(session, run_id, event["event_key"])
            if locked is None:
                raise RuntimeError("Run 不存在。")
            row, existing = locked
            limits = RuntimeBudgetLimits.model_validate(row.budget_limits_json)
            usage = RuntimeBudgetUsage.model_validate(row.budget_usage_json)
            if existing is not None:
                session.rollback()
                return RuntimeBudgetReservation(
                    usage=usage,
                    start_event_persisted=True,
                )
            field_name = "model_calls" if kind == "model" else "tool_calls"
            maximum = (
                limits.max_model_calls if kind == "model" else limits.max_tool_calls
            )
            if getattr(usage, field_name) >= maximum:
                session.rollback()
                return None
            usage = usage.model_copy(
                update={field_name: getattr(usage, field_name) + 1}
            )
            row.budget_usage_json = usage.model_dump(mode="json")
            public_type = "node" if kind == "model" else "tool"
            self.append_runtime_event(
                run_id,
                event_key=event["event_key"],
                event_type=public_type,
                name=event.get("name"),
                status="running",
                summary=event.get("summary") or "调用已开始。",
                payload={"budget": usage.model_dump(mode="json")},
                started_at=event.get("started_at"),
                session=session,
                locked_run=row,
                event_absent=True,
            )
            session.commit()
            return RuntimeBudgetReservation(
                usage=usage,
                start_event_persisted=True,
            )
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def add_runtime_tokens(
        self, run_id: str, prompt: int, completion: int, total: int
    ) -> RuntimeBudgetUsage:
        session: Session = self._session_factory()
        try:
            row = session.scalar(
                select(RuntimeAgentRun)
                .where(RuntimeAgentRun.run_id == run_id)
                .with_for_update()
            )
            if row is None:
                raise RuntimeError("Run 不存在。")
            usage = RuntimeBudgetUsage.model_validate(row.budget_usage_json)
            usage = usage.model_copy(
                update={
                    "prompt_tokens": usage.prompt_tokens + prompt,
                    "completion_tokens": usage.completion_tokens + completion,
                    "total_tokens": usage.total_tokens + total,
                }
            )
            row.budget_usage_json = usage.model_dump(mode="json")
            session.commit()
            return usage
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def get_runtime_usage(self, run_id: str) -> RuntimeBudgetUsage:
        with self._session_factory() as session:
            value = session.scalar(
                select(RuntimeAgentRun.budget_usage_json).where(
                    RuntimeAgentRun.run_id == run_id
                )
            )
            if value is None:
                raise RuntimeError("Run 不存在。")
            return RuntimeBudgetUsage.model_validate(value)

    def claim_next(self, worker_id: str) -> dict[str, Any] | None:
        session: Session = self._session_factory()
        try:
            now = utc_now()
            lease_expires_at = now + timedelta(
                seconds=self.settings.agent_run_lease_seconds
            )
            statement = (
                select(RuntimeAgentRun)
                .where(
                    RuntimeAgentRun.available_at <= now,
                    or_(
                        RuntimeAgentRun.status.in_(("queued", "retry_wait")),
                        (RuntimeAgentRun.status == "running")
                        & (RuntimeAgentRun.lease_expires_at < now),
                        (RuntimeAgentRun.status == "finalizing")
                        & or_(
                            RuntimeAgentRun.lease_expires_at.is_(None),
                            RuntimeAgentRun.lease_expires_at < now,
                        ),
                    ),
                )
                .order_by(RuntimeAgentRun.created_at)
                .with_for_update(skip_locked=True)
                .limit(1)
            )
            row = session.scalar(statement)
            if row is None:
                session.rollback()
                return None
            if row.cancel_requested_at and row.status != "finalizing":
                validate_run_transition(row.status, "cancelled")
                row.status = "cancelled"
                row.finished_at = now
                row.updated_at = now
                row.lease_owner = None
                row.lease_expires_at = None
                row.checkpoint_expires_at = now + timedelta(
                    hours=self.settings.agent_checkpoint_retention_hours
                )
                self._persist_cancelled_turn(session, row, now)
                session.commit()
                return None
            previous_status = row.status
            if row.status == "finalizing":
                if row.attempt_count >= self.settings.agent_run_max_attempts:
                    validate_run_transition(row.status, "failed")
                    row.status = "failed"
                    row.public_error_json = self._public_error(
                        row, "run_attempts_exhausted", "运行重试次数已用尽。"
                    )
                    row.finished_at = now
                    row.updated_at = now
                    row.lease_owner = None
                    row.lease_expires_at = None
                    row.checkpoint_expires_at = now + timedelta(
                        hours=self.settings.agent_checkpoint_retention_hours
                    )
                    self.append_event(
                        row.run_id,
                        graph_sequence=1_000_000 + row.attempt_count,
                        event_type="error",
                        payload=row.public_error_json,
                        session=session,
                        locked_run=row,
                    )
                    session.commit()
                    return None
                row.attempt_count += 1
            else:
                if row.attempt_count >= self.settings.agent_run_max_attempts:
                    validate_run_transition(row.status, "failed")
                    row.status = "failed"
                    row.public_error_json = self._public_error(
                        row, "run_attempts_exhausted", "运行重试次数已用尽。"
                    )
                    row.finished_at = now
                    row.updated_at = now
                    row.lease_owner = None
                    row.lease_expires_at = None
                    row.checkpoint_expires_at = now + timedelta(
                        hours=self.settings.agent_checkpoint_retention_hours
                    )
                    self.append_event(
                        row.run_id,
                        graph_sequence=1_000_000 + row.attempt_count,
                        event_type="error",
                        payload=row.public_error_json,
                        session=session,
                        locked_run=row,
                    )
                    session.commit()
                    return None
                if row.status != "running":
                    validate_run_transition(row.status, "running")
                row.status = "running"
                row.attempt_count += 1
                row.started_at = row.started_at or now
            row.lease_owner = worker_id
            row.lease_expires_at = lease_expires_at
            row.updated_at = now
            if previous_status == "finalizing":
                self.append_event(
                    row.run_id,
                    graph_sequence=FINALIZING_EVENT_SEQUENCE + row.attempt_count,
                    event_type="status",
                    payload={
                        "run_id": row.run_id,
                        "status": "finalizing",
                        "attempt_count": row.attempt_count,
                    },
                    session=session,
                    locked_run=row,
                )
            else:
                self.append_event(
                    row.run_id,
                    graph_sequence=RUNNING_EVENT_SEQUENCE_BASE + row.attempt_count,
                    event_type="status",
                    payload={
                        "run_id": row.run_id,
                        "status": "running",
                        "attempt_count": row.attempt_count,
                    },
                    session=session,
                    locked_run=row,
                )
            session.commit()
            session.refresh(row)
            value = self._run_dict(row)
            value["claimed_from"] = previous_status
            return value
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def renew_lease(self, run_id: str, worker_id: str) -> bool:
        session: Session = self._session_factory()
        try:
            now = utc_now()
            result = session.execute(
                update(RuntimeAgentRun)
                .where(
                    RuntimeAgentRun.run_id == run_id,
                    RuntimeAgentRun.lease_owner == worker_id,
                    RuntimeAgentRun.status.in_(("running", "finalizing")),
                )
                .values(
                    lease_expires_at=now
                    + timedelta(seconds=self.settings.agent_run_lease_seconds),
                    updated_at=now,
                )
            )
            session.commit()
            return bool(result.rowcount)
        finally:
            session.close()

    def cancellation_requested(self, run_id: str) -> bool:
        session: Session = self._session_factory()
        try:
            return (
                session.scalar(
                    select(RuntimeAgentRun.cancel_requested_at).where(
                        RuntimeAgentRun.run_id == run_id
                    )
                )
                is not None
            )
        finally:
            session.close()

    def cancel(
        self,
        conversation_id: str,
        run_id: str,
        principal: ExecutionPrincipal,
    ) -> dict[str, Any]:
        session: Session = self._session_factory()
        try:
            row = self._owned_run(
                session,
                conversation_id,
                run_id,
                principal,
                for_update=True,
            )
            if row is None:
                raise AgentDomainError("run_not_found", "未找到当前会话中的 Run。")
            if row.status in TERMINAL_RUN_STATUSES or row.status == "finalizing":
                session.rollback()
                return self._run_dict(row)
            now = utc_now()
            row.cancel_requested_at = row.cancel_requested_at or now
            if row.status in {"queued", "retry_wait"}:
                validate_run_transition(row.status, "cancelled")
                row.status = "cancelled"
                row.finished_at = now
                row.lease_owner = None
                row.lease_expires_at = None
                row.checkpoint_expires_at = now + timedelta(
                    hours=self.settings.agent_checkpoint_retention_hours
                )
                self._persist_cancelled_turn(session, row, now)
            row.updated_at = now
            session.commit()
            session.refresh(row)
            return self._run_dict(row)
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def mark_cancelled(self, run_id: str, worker_id: str) -> None:
        session: Session = self._session_factory()
        try:
            row = session.scalar(
                select(RuntimeAgentRun)
                .where(
                    RuntimeAgentRun.run_id == run_id,
                    RuntimeAgentRun.lease_owner == worker_id,
                )
                .with_for_update()
            )
            if row is None or row.status in TERMINAL_RUN_STATUSES:
                session.rollback()
                return
            validate_run_transition(row.status, "cancelled")
            now = utc_now()
            row.status = "cancelled"
            row.finished_at = now
            row.updated_at = now
            row.lease_owner = None
            row.lease_expires_at = None
            row.checkpoint_expires_at = now + timedelta(
                hours=self.settings.agent_checkpoint_retention_hours
            )
            self._persist_cancelled_turn(session, row, now)
            session.commit()
        finally:
            session.close()

    def mark_finalizing(self, run_id: str, worker_id: str) -> None:
        session: Session = self._session_factory()
        try:
            row = session.scalar(
                select(RuntimeAgentRun)
                .where(
                    RuntimeAgentRun.run_id == run_id,
                    RuntimeAgentRun.lease_owner == worker_id,
                )
                .with_for_update()
            )
            if row is None:
                raise RuntimeError("Run 租约已丢失。")
            if row.status == "running":
                validate_run_transition(row.status, "finalizing")
                row.status = "finalizing"
                row.finalizing_at = utc_now()
                row.updated_at = utc_now()
                self.append_event(
                    run_id,
                    graph_sequence=FINALIZING_EVENT_SEQUENCE,
                    event_type="status",
                    payload={"run_id": run_id, "status": "finalizing"},
                    session=session,
                    locked_run=row,
                )
            session.commit()
        finally:
            session.close()

    def handle_failure(
        self,
        run_id: str,
        worker_id: str,
        *,
        retryable: bool,
        code: str,
        message: str,
    ) -> str:
        session: Session = self._session_factory()
        try:
            row = session.scalar(
                select(RuntimeAgentRun)
                .where(
                    RuntimeAgentRun.run_id == run_id,
                    RuntimeAgentRun.lease_owner == worker_id,
                )
                .with_for_update()
            )
            if row is None:
                return "lease_lost"
            now = utc_now()
            if row.status == "finalizing" and retryable:
                if row.attempt_count < self.settings.agent_run_max_attempts:
                    backoff = RETRY_BACKOFF_SECONDS[
                        min(row.attempt_count - 1, len(RETRY_BACKOFF_SECONDS) - 1)
                    ]
                    row.available_at = now + timedelta(seconds=backoff)
                    row.lease_owner = None
                    row.lease_expires_at = None
                    row.updated_at = now
                    session.commit()
                    return "finalizing"
            elif (
                row.status == "running"
                and retryable
                and row.attempt_count < self.settings.agent_run_max_attempts
            ):
                validate_run_transition(row.status, "retry_wait")
                backoff = RETRY_BACKOFF_SECONDS[
                    min(row.attempt_count - 1, len(RETRY_BACKOFF_SECONDS) - 1)
                ]
                row.status = "retry_wait"
                row.available_at = now + timedelta(seconds=backoff)
                row.lease_owner = None
                row.lease_expires_at = None
                row.updated_at = now
                self.append_event(
                    run_id,
                    graph_sequence=RETRY_WAIT_EVENT_SEQUENCE_BASE + row.attempt_count,
                    event_type="status",
                    payload={
                        "run_id": run_id,
                        "status": "retry_wait",
                        "attempt_count": row.attempt_count,
                    },
                    session=session,
                    locked_run=row,
                )
                session.commit()
                return "retry_wait"

            validate_run_transition(row.status, "failed")
            row.status = "failed"
            row.public_error_json = self._public_error(row, code, message)
            row.finished_at = now
            row.updated_at = now
            row.lease_owner = None
            row.lease_expires_at = None
            row.checkpoint_expires_at = now + timedelta(
                hours=self.settings.agent_checkpoint_retention_hours
            )
            self.append_event(
                run_id,
                graph_sequence=1_000_000 + row.attempt_count,
                event_type="error",
                payload=row.public_error_json,
                session=session,
                locked_run=row,
            )
            session.commit()
            return "failed"
        finally:
            session.close()

    def finalize(
        self,
        run_id: str,
        worker_id: str,
        *,
        result: dict[str, Any],
        context: dict[str, Any],
        audit_trace: dict[str, Any] | None,
    ) -> dict[str, Any]:
        session: Session = self._session_factory()
        try:
            run = session.scalar(
                select(RuntimeAgentRun)
                .where(
                    RuntimeAgentRun.run_id == run_id,
                    RuntimeAgentRun.lease_owner == worker_id,
                )
                .with_for_update()
            )
            if run is None:
                raise RuntimeError("Run 租约已丢失。")
            if run.status == "succeeded":
                return self._run_dict(run)
            if run.status != "finalizing":
                raise RuntimeError("Run 尚未进入 finalizing。")
            conversation = session.scalar(
                select(RuntimeConversation)
                .where(
                    RuntimeConversation.conversation_id == run.conversation_id,
                    RuntimeConversation.tenant_id == run.tenant_id,
                    RuntimeConversation.principal_id == run.principal_id,
                )
                .with_for_update()
            )
            if conversation is None:
                raise ConversationNotFoundError(run.conversation_id)
            now = utc_now()
            existing_turn = session.scalar(
                select(RuntimeTurn).where(RuntimeTurn.turn_id == run.turn_id)
            )
            if existing_turn is None:
                session.add(
                    RuntimeTurn(
                        turn_id=run.turn_id,
                        conversation_id=run.conversation_id,
                        tenant_id=run.tenant_id,
                        principal_id=run.principal_id,
                        message_id=run.message_id,
                        question=run.request_json["question"],
                        result_json=result,
                        created_at=now,
                    )
                )
                session.flush()
                assistant_message_id = f"msg_{uuid5(NAMESPACE_URL, run_id).hex}"
                session.add_all(
                    [
                        RuntimeMessage(
                            message_id=run.message_id,
                            conversation_id=run.conversation_id,
                            turn_id=run.turn_id,
                            role="user",
                            content=run.request_json["question"],
                            run_json=None,
                            created_at=now,
                        ),
                        RuntimeMessage(
                            message_id=assistant_message_id,
                            conversation_id=run.conversation_id,
                            turn_id=run.turn_id,
                            role="assistant",
                            content=result["final_message"],
                            run_json=result,
                            created_at=now,
                        ),
                    ]
                )
            if audit_trace is not None:
                session.execute(
                    pg_insert(RuntimeAuditTrace)
                    .values(
                        run_id=run_id,
                        conversation_id=run.conversation_id,
                        turn_id=run.turn_id,
                        trace_json=audit_trace,
                        created_at=now,
                    )
                    .on_conflict_do_nothing(index_elements=["run_id"])
                )
            if conversation.title == "新建成本 Agent 对话":
                conversation.title = run.request_json["question"][:24]
            if result.get("outcome") == "completed" and result.get("report_json"):
                principal = ExecutionPrincipal(
                    principal_id=run.principal_id,
                    tenant_id=run.tenant_id,
                    roles=tuple(run.request_json["principal"]["roles"]),
                )
                record = build_artifact_record(
                    conversation_id=run.conversation_id,
                    conversation_title=conversation.title,
                    turn_id=run.turn_id,
                    message_id=run.message_id,
                    result=result,
                    created_at=now.isoformat(),
                    principal=principal,
                )
                record["created_at"] = now
                session.execute(
                    pg_insert(AgentArtifact)
                    .values(**record)
                    .on_conflict_do_nothing(index_elements=["tenant_id", "run_id"])
                )
            conversation.context_json = context
            conversation.updated_at = now
            result_event = self.append_event(
                run_id,
                graph_sequence=len(result.get("events") or []) + 2,
                event_type="result",
                payload={"result": self._public_event_result(result)},
                session=session,
                locked_run=run,
            )
            validate_run_transition(run.status, "succeeded")
            run.status = "succeeded"
            run.result_json = result
            run.public_error_json = None
            run.last_event_id = result_event["event_id"]
            run.finished_at = now
            run.updated_at = now
            run.lease_owner = None
            run.lease_expires_at = None
            run.checkpoint_expires_at = now + timedelta(
                hours=self.settings.agent_checkpoint_retention_hours
            )
            session.commit()
            session.refresh(run)
            return self._run_dict(run)
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def register_worker(
        self,
        worker_id: str,
        *,
        hostname: str,
        process_id: int,
        app_version: str,
    ) -> None:
        now = utc_now()
        session: Session = self._session_factory()
        try:
            session.execute(
                pg_insert(RuntimeAgentWorker)
                .values(
                    worker_id=worker_id,
                    status="running",
                    hostname=hostname,
                    process_id=process_id,
                    app_version=app_version,
                    current_run_id=None,
                    started_at=now,
                    last_heartbeat_at=now,
                )
                .on_conflict_do_update(
                    index_elements=["worker_id"],
                    set_={
                        "status": "running",
                        "hostname": hostname,
                        "process_id": process_id,
                        "app_version": app_version,
                        "current_run_id": None,
                        "started_at": now,
                        "last_heartbeat_at": now,
                    },
                )
            )
            session.commit()
        finally:
            session.close()

    def heartbeat_worker(
        self, worker_id: str, current_run_id: str | None = None
    ) -> None:
        session: Session = self._session_factory()
        try:
            session.execute(
                update(RuntimeAgentWorker)
                .where(RuntimeAgentWorker.worker_id == worker_id)
                .values(
                    status="running",
                    current_run_id=current_run_id,
                    last_heartbeat_at=utc_now(),
                )
            )
            session.commit()
        finally:
            session.close()

    def stop_worker(self, worker_id: str) -> None:
        session: Session = self._session_factory()
        try:
            session.execute(
                update(RuntimeAgentWorker)
                .where(RuntimeAgentWorker.worker_id == worker_id)
                .values(
                    status="stopped",
                    current_run_id=None,
                    last_heartbeat_at=utc_now(),
                )
            )
            session.commit()
        finally:
            session.close()

    def cleanup_expired_checkpoints(self) -> int:
        session: Session = self._session_factory()
        try:
            run_ids = session.scalars(
                select(RuntimeAgentRun.run_id).where(
                    RuntimeAgentRun.status.in_(TERMINAL_RUN_STATUSES),
                    RuntimeAgentRun.checkpoint_expires_at.is_not(None),
                    RuntimeAgentRun.checkpoint_expires_at <= utc_now(),
                )
            ).all()
            if not run_ids:
                return 0
            for table_name in (
                "checkpoint_writes",
                "checkpoint_blobs",
                "checkpoints",
            ):
                session.execute(
                    text(
                        f"DELETE FROM agent_checkpoint.{table_name} "
                        "WHERE thread_id = ANY(:run_ids)"
                    ),
                    {"run_ids": list(run_ids)},
                )
            session.execute(
                update(RuntimeAgentRun)
                .where(RuntimeAgentRun.run_id.in_(run_ids))
                .values(checkpoint_expires_at=None)
            )
            session.commit()
            return len(run_ids)
        except SQLAlchemyError:
            session.rollback()
            raise
        finally:
            session.close()

    @staticmethod
    def _lock_run_and_event(
        session: Session, run_id: str, event_key: str
    ) -> tuple[RuntimeAgentRun, RuntimeAgentRunEvent | None] | None:
        statement = (
            select(RuntimeAgentRun, RuntimeAgentRunEvent)
            .outerjoin(
                RuntimeAgentRunEvent,
                and_(
                    RuntimeAgentRunEvent.run_id == RuntimeAgentRun.run_id,
                    RuntimeAgentRunEvent.event_key == event_key,
                ),
            )
            .where(RuntimeAgentRun.run_id == run_id)
            .with_for_update(of=RuntimeAgentRun)
        )
        row = session.execute(statement).one_or_none()
        if row is None:
            return None
        return row[0], row[1]

    @staticmethod
    def _owned_run(
        session: Session,
        conversation_id: str,
        run_id: str,
        principal: ExecutionPrincipal,
        *,
        for_update: bool = False,
    ) -> RuntimeAgentRun | None:
        conditions = [
            RuntimeAgentRun.run_id == run_id,
            RuntimeAgentRun.conversation_id == conversation_id,
            RuntimeAgentRun.tenant_id == principal.tenant_id,
            RuntimeAgentRun.principal_id == principal.principal_id,
        ]
        statement = select(RuntimeAgentRun).where(*conditions)
        if for_update:
            statement = statement.with_for_update()
        return session.scalar(statement)

    @staticmethod
    def _public_error(run: RuntimeAgentRun, code: str, message: str) -> dict[str, Any]:
        return {
            "code": code,
            "message": message,
            "request_id": run.request_id,
            "details": {"run_id": run.run_id},
        }

    @staticmethod
    def _public_event_result(result: dict[str, Any]) -> dict[str, Any]:
        public = dict(result)
        status_bar = public.get("status_bar")
        if isinstance(status_bar, dict):
            public_status = dict(status_bar)
            public_status.pop("goal", None)
            public["status_bar"] = public_status
        return public

    def _persist_cancelled_turn(
        self, session: Session, run: RuntimeAgentRun, now: datetime
    ) -> None:
        existing = session.scalar(
            select(RuntimeTurn.turn_id).where(RuntimeTurn.turn_id == run.turn_id)
        )
        if existing is None:
            event = {
                "node": "cancel",
                "status": "error",
                "summary": "运行已取消。",
                "started_at": now.isoformat(),
                "finished_at": now.isoformat(),
            }
            result = {
                "run_id": run.run_id,
                "conversation_id": run.conversation_id,
                "turn_id": run.turn_id,
                "message_id": run.message_id,
                "final_message": "已取消",
                "report_json": None,
                "events": [event],
                "status_bar": None,
                "outcome": "failed",
                "clarification": None,
                "context_used": {},
            }
            session.add(
                RuntimeTurn(
                    turn_id=run.turn_id,
                    conversation_id=run.conversation_id,
                    tenant_id=run.tenant_id,
                    principal_id=run.principal_id,
                    message_id=run.message_id,
                    question=run.request_json["question"],
                    result_json=result,
                    created_at=now,
                )
            )
            session.flush()
            session.add_all(
                [
                    RuntimeMessage(
                        message_id=run.message_id,
                        conversation_id=run.conversation_id,
                        turn_id=run.turn_id,
                        role="user",
                        content=run.request_json["question"],
                        run_json=None,
                        created_at=now,
                    ),
                    RuntimeMessage(
                        message_id=f"msg_{uuid5(NAMESPACE_URL, run.run_id).hex}",
                        conversation_id=run.conversation_id,
                        turn_id=run.turn_id,
                        role="assistant",
                        content="已取消",
                        run_json=result,
                        created_at=now,
                    ),
                ]
            )
            session.execute(
                pg_insert(RuntimeAuditTrace)
                .values(
                    run_id=run.run_id,
                    conversation_id=run.conversation_id,
                    turn_id=run.turn_id,
                    trace_json=build_audit_trace({}),
                    created_at=now,
                )
                .on_conflict_do_nothing(index_elements=["run_id"])
            )
            conversation = session.get(RuntimeConversation, run.conversation_id)
            if conversation is not None:
                if conversation.title == "新建成本 Agent 对话":
                    conversation.title = run.request_json["question"][:24]
                conversation.updated_at = now
        persisted = self.append_event(
            run.run_id,
            graph_sequence=1_000_000,
            event_type="status",
            payload={"run_id": run.run_id, "status": "cancelled"},
            session=session,
            locked_run=run,
        )
        run.last_event_id = persisted["event_id"]

    @staticmethod
    def _run_dict(row: RuntimeAgentRun) -> dict[str, Any]:
        value = {
            "run_id": row.run_id,
            "conversation_id": row.conversation_id,
            "turn_id": row.turn_id,
            "message_id": row.message_id,
            "status": row.status,
            "attempt_count": row.attempt_count,
            "created_at": row.created_at.isoformat(),
            "updated_at": row.updated_at.isoformat(),
            "started_at": row.started_at.isoformat() if row.started_at else None,
            "finished_at": row.finished_at.isoformat() if row.finished_at else None,
            "cancel_requested_at": row.cancel_requested_at.isoformat()
            if row.cancel_requested_at
            else None,
            "last_event_id": row.last_event_id,
            "request_id": row.request_id,
        }
        if row.status == "succeeded":
            value["result"] = row.result_json
        if row.status == "failed":
            value["error"] = row.public_error_json
        value.update(
            {
                "schema_version": "2.0",
                "api_version": row.api_version,
                "runtime_version": row.runtime_version,
                "workflow_version": row.workflow_version,
                "trace_id": row.run_id,
                "budget": {
                    "limits": row.budget_limits_json,
                    "usage": row.budget_usage_json,
                },
            }
        )
        return value

    @staticmethod
    def _event_dict(row: RuntimeAgentRunEvent) -> dict[str, Any]:
        value = {
            "event_id": row.event_id,
            "run_id": row.run_id,
            "graph_sequence": row.graph_sequence,
            "type": row.event_type,
            "payload": row.payload_json,
            "created_at": row.created_at.isoformat(),
        }
        value.update(
            {
                "schema_version": row.schema_version,
                "sequence": row.graph_sequence,
                "event_key": row.event_key,
                "name": row.name,
                "status": row.status,
                "started_at": row.started_at.isoformat() if row.started_at else None,
                "finished_at": row.finished_at.isoformat() if row.finished_at else None,
            }
        )
        return value


def _as_datetime(value: str | datetime | None) -> datetime | None:
    if value is None or isinstance(value, datetime):
        return value
    return datetime.fromisoformat(value)


def _datetime_json(value: str | datetime | None) -> str | None:
    parsed = _as_datetime(value)
    return parsed.isoformat() if parsed else None


def _semantic_event(event_type: str, payload: dict[str, Any]) -> dict[str, Any]:
    if event_type == "event" and isinstance(payload.get("event"), dict):
        event = payload["event"]
        return {
            "event_type": "node",
            "name": event.get("node") or "workflow",
            "status": event.get("status") or "success",
            "summary": event.get("summary") or "工作流节点已更新。",
            "started_at": event.get("started_at"),
            "finished_at": event.get("finished_at"),
            "payload": {
                "status_bar": payload.get("status_bar"),
            },
        }
    status = str(
        payload.get("status") or ("error" if event_type == "error" else "success")
    )
    summary = str(
        payload.get("message")
        or {
            "queued": "运行已进入队列。",
            "running": "运行已开始。",
            "finalizing": "运行正在提交结果。",
            "retry_wait": "运行等待重试。",
            "succeeded": "运行已完成。",
            "cancelled": "运行已取消。",
        }.get(status, "运行状态已更新。")
    )
    public_payload = {
        key: value
        for key, value in payload.items()
        if key
        in {
            "attempt_count",
            "budget",
            "clarification",
            "error_code",
            "result",
            "status_bar",
        }
    }
    if event_type == "error":
        public_payload.update(
            {
                "error_code": payload.get("error_code") or payload.get("code"),
                "request_id": payload.get("request_id"),
            }
        )
    return {
        "event_type": event_type,
        "name": "run" if event_type == "status" else event_type,
        "status": status,
        "summary": summary,
        "payload": public_payload,
    }
