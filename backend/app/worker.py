from __future__ import annotations

import json
import logging
import os
import signal
import socket
import threading
from concurrent.futures import Future, ThreadPoolExecutor
from datetime import UTC, datetime
from time import monotonic
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy.exc import SQLAlchemyError

from app.agent.checkpoint import open_postgres_checkpointer
from app.agent.graph import (
    _new_run_state_from_request,
    _result_from_state,
    create_cost_agent_graph,
)
from app.agent.harness import AgentHarness
from app.agent.providers import get_model_provider
from app.agent.runtime_contracts import (
    RuntimeBudgetLimits,
    RuntimeBudgetUsage,
    RuntimeRunRequest,
)
from app.agent.runtime_services import (
    PostgresBudgetStore,
    PostgresEventSink,
    RuntimeServiceError,
    RuntimeServices,
)
from app.domain.authorization import ExecutionPrincipal, execution_context_from_state
from app.domain.errors import AgentDomainError
from app.repositories.import_job_repository import (
    ImportJobLeaseLostError,
    get_import_job_repository,
)
from app.repositories.run_repository import PostgresRunRepository
from app.services.cost_data_import_service import CostDataImporter, ValidationOutcome
from app.services.import_object_store import (
    ImportObjectStoreError,
    LocalImportObjectStore,
)
from app.services.llm_service import LLMUnavailableError
from app.settings import AppSettings, get_settings

LOGGER = logging.getLogger("agent.worker")

#: 导入任务领取循环的轮询间隔（秒）。
IMPORT_JOB_POLL_SECONDS = 0.5
#: 导入载荷允许的四类记录集合（规格第 4 节请求体）。
IMPORT_PAYLOAD_TABLES: tuple[str, ...] = (
    "parts",
    "cost_events",
    "cost_event_inputs",
    "cost_records",
)
#: 审计 ``detail`` 里错误码聚合字符串的长度上限，避免写入无界文本。
AUDIT_ERROR_SUMMARY_LIMIT = 200
#: 关闭时等待导入任务收尾的最长时间（秒），与 agent run 超时保持同一量级。
IMPORT_DISPATCH_JOIN_SECONDS = 180


def classify_run_exception(exc: Exception) -> tuple[bool, str, str]:
    if isinstance(exc, RuntimeServiceError):
        return (
            exc.retryable,
            exc.code,
            {
                "budget_exceeded": "运行调用预算已用尽。",
                "tool_timeout": "工具执行超时。",
                "tool_access_denied": "工具访问被拒绝。",
                "tool_validation_error": "工具参数无效。",
                "repository_unavailable": "数据服务暂时不可用。",
                "model_unavailable": "模型服务暂时不可用。",
                "run_timeout": "运行已超过时间预算。",
                "run_cancelled": "运行已取消。",
            }.get(exc.code, "运行暂时失败，请稍后重试。"),
        )
    if isinstance(exc, TimeoutError):
        return False, "run_timeout", "运行超时，已按策略处理。"
    if isinstance(exc, SQLAlchemyError):
        return True, "database_unavailable", "运行所需的数据服务暂时不可用。"
    if isinstance(exc, LLMUnavailableError):
        error_code = str((exc.trace or {}).get("error_code") or "")
        if error_code == "llm_transport_error":
            return True, "model_unavailable", "模型服务暂时不可用。"
        if error_code.startswith("llm_http_"):
            try:
                status_code = int(error_code.removeprefix("llm_http_"))
            except ValueError:
                status_code = 0
            if status_code == 429 or status_code >= 500:
                return True, "model_unavailable", "模型服务暂时不可用。"
        return False, "run_invalid", "运行请求无效。"
    if isinstance(exc, (AgentDomainError, PermissionError, ValueError)):
        return False, "run_invalid", "运行请求无效。"
    return True, "run_infrastructure_error", "运行暂时失败，请稍后重试。"


def classify_import_job_exception(exc: Exception) -> tuple[bool, str]:
    """导入任务失败的确定性分类：``(retryable, error_code)``。

    可重试 = 数据库/对象存储的瞬时故障，由 ``attempt_count`` + ``available_at``
    退避重试；载荷、完整性与 ``object_key`` 语义错误不可重试，直接终态。
    """
    if isinstance(exc, ImportObjectStoreError):
        return False, "import_object_unavailable"
    if isinstance(exc, SQLAlchemyError):
        return True, "database_unavailable"
    if isinstance(exc, OSError):
        return True, "import_object_store_unavailable"
    if isinstance(exc, (ValueError, KeyError, TypeError, UnicodeDecodeError)):
        return False, "import_payload_invalid"
    return True, "import_infrastructure_error"


def decode_import_payload(raw: bytes) -> dict[str, list[dict[str, Any]]]:
    """把对象存储里的载荷解码为四类记录集合；结构非法一律拒绝。"""
    value = json.loads(raw.decode("utf-8"))
    if not isinstance(value, dict):
        raise TypeError("导入载荷必须是包含四类记录的 JSON 对象。")
    unknown = sorted(set(value) - set(IMPORT_PAYLOAD_TABLES))
    if unknown:
        raise ValueError(f"导入载荷包含未知记录集合：{', '.join(unknown)}")
    payload: dict[str, list[dict[str, Any]]] = {}
    for name in IMPORT_PAYLOAD_TABLES:
        rows = value.get(name)
        if rows is None:
            rows = []
        if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
            raise ValueError(f"导入载荷 {name} 必须是对象数组。")
        payload[name] = [dict(row) for row in rows]
    return payload


def assert_state_principal(
    state: dict[str, Any], authoritative: ExecutionPrincipal
) -> None:
    """Bind a persisted checkpoint to the principal frozen in its run row.

    Rejects a checkpoint whose principal differs from the run snapshot, so a
    forged or cross-tenant checkpoint cannot resume under another identity.
    """
    value = state.get("execution_context")
    if value is None:
        raise PermissionError("运行状态缺少主体上下文")
    execution_context_from_state(value, authoritative_principal=authoritative)


class AgentWorker:
    def __init__(self, settings: AppSettings | None = None) -> None:
        self.settings = settings or get_settings()
        _ = self.settings.psycopg_database_url
        self.repository = PostgresRunRepository(settings=self.settings)
        self.worker_id = (
            f"worker_{socket.gethostname()}_{os.getpid()}_{uuid4().hex[:8]}"
        )
        self.import_job_repository = get_import_job_repository()
        self.importer = CostDataImporter()
        self.object_store = LocalImportObjectStore()
        self._stop = threading.Event()
        self._active: dict[Future[None], str] = {}
        self._import_active: dict[Future[None], str] = {}
        self._import_thread: threading.Thread | None = None

    def request_stop(self) -> None:
        self._stop.set()

    def run_forever(self) -> None:
        self.repository.register_worker(
            self.worker_id,
            hostname=socket.gethostname(),
            process_id=os.getpid(),
            app_version=self.settings.app_version,
        )
        self._import_thread = threading.Thread(
            target=self.run_import_jobs,
            name="import-job-dispatch",
            daemon=True,
        )
        self._import_thread.start()
        last_cleanup = monotonic()
        with ThreadPoolExecutor(
            max_workers=self.settings.agent_worker_concurrency,
            thread_name_prefix="agent-run",
        ) as executor:
            try:
                while not self._stop.is_set() or self._active:
                    self._collect_finished()
                    active_run = next(iter(self._active.values()), None)
                    self.repository.heartbeat_worker(self.worker_id, active_run)
                    if not self._stop.is_set():
                        while (
                            len(self._active) < self.settings.agent_worker_concurrency
                        ):
                            claimed = self.repository.claim_next(self.worker_id)
                            if claimed is None:
                                break
                            future = executor.submit(self._execute_run, claimed)
                            self._active[future] = claimed["run_id"]
                    if monotonic() - last_cleanup >= 60:
                        self.repository.cleanup_expired_checkpoints()
                        last_cleanup = monotonic()
                    self._stop.wait(0.5)
            finally:
                self._stop.set()
                self._join_import_dispatcher()
                self.repository.stop_worker(self.worker_id)

    def _join_import_dispatcher(self) -> None:
        thread = self._import_thread
        if thread is None:
            return
        thread.join(timeout=IMPORT_DISPATCH_JOIN_SECONDS)
        if thread.is_alive():  # pragma: no cover - defensive shutdown boundary
            LOGGER.warning("import job dispatcher did not stop in time")

    def _collect_finished(self) -> None:
        for future, run_id in list(self._active.items()):
            if not future.done():
                continue
            self._active.pop(future, None)
            try:
                future.result()
            except Exception:  # pragma: no cover - defensive worker boundary
                LOGGER.exception(
                    "worker task escaped run boundary", extra={"run_id": run_id}
                )

    # -------------------------------------------------- import job scheduling

    def run_import_jobs(self) -> None:
        """领取导入任务的循环，与 agent run 循环共享 stop event 与 worker_id。

        池内每个任务走 ``_execute_import_job``（自带租约心跳），直到 stop event
        置位且没有在飞的导入任务才退出。
        """
        with ThreadPoolExecutor(
            max_workers=self.settings.agent_worker_concurrency,
            thread_name_prefix="import-job",
        ) as executor:
            while not self._stop.is_set() or self._import_active:
                self._collect_finished_imports()
                if not self._stop.is_set():
                    while (
                        len(self._import_active)
                        < self.settings.agent_worker_concurrency
                    ):
                        try:
                            claimed = self.import_job_repository.claim_next_job(
                                self.worker_id
                            )
                        except Exception:  # keep the loop alive on transient failures
                            LOGGER.exception("import job claim failed")
                            break
                        if claimed is None:
                            break
                        future = executor.submit(self._execute_import_job, claimed)
                        self._import_active[future] = str(claimed["job_id"])
                self._stop.wait(IMPORT_JOB_POLL_SECONDS)

    def process_next_import_job(self) -> bool:
        """同步领取并推进一个导入任务；返回是否领到任务。

        供 CLI 与端到端验证确定性地驱动完整状态机；与并发循环共用
        ``_execute_import_job``，因此租约心跳与失败分类完全一致。
        """
        claimed = self.import_job_repository.claim_next_job(self.worker_id)
        if claimed is None:
            return False
        self._execute_import_job(claimed)
        return True

    def _collect_finished_imports(self) -> None:
        for future, job_id in list(self._import_active.items()):
            if not future.done():
                continue
            self._import_active.pop(future, None)
            try:
                future.result()
            except Exception:  # pragma: no cover - defensive worker boundary
                LOGGER.exception(
                    "import job task escaped run boundary", extra={"job_id": job_id}
                )

    def _execute_import_job(self, job: dict[str, Any]) -> None:
        job_id: UUID = job["job_id"]
        status = str(job["status"])
        heartbeat_stop = threading.Event()
        heartbeat = threading.Thread(
            target=self._import_job_heartbeat,
            args=(job_id, heartbeat_stop),
            daemon=True,
        )
        heartbeat.start()
        try:
            if status == "validating":
                self._validate_import_job(job)
            elif status == "publishing":
                self._publish_import_job(job)
            else:
                LOGGER.warning(
                    "import job claimed in unexpected status",
                    extra={"job_id": str(job_id), "status": status},
                )
        except ImportJobLeaseLostError:
            LOGGER.warning(
                "import job lease lost", extra={"job_id": str(job_id), "status": status}
            )
        except Exception as exc:  # noqa: BLE001 - classify at durable boundary
            retryable, error_code = classify_import_job_exception(exc)
            self._fail_import_job(job, retryable=retryable, error_code=error_code)
        finally:
            heartbeat_stop.set()
            heartbeat.join(timeout=1)

    def _validate_import_job(self, job: dict[str, Any]) -> None:
        job_id: UUID = job["job_id"]
        payload = decode_import_payload(self.object_store.get(job["source_object_key"]))
        outcome = self.importer.validate_payload(
            tenant_id=job["tenant_id"],
            source_system=job["source_system"],
            parts=payload["parts"],
            events=payload["cost_events"],
            inputs=payload["cost_event_inputs"],
            records=payload["cost_records"],
        )
        if outcome.snapshot_hash != job["snapshot_hash"]:
            raise ValueError("载荷哈希与任务快照哈希不一致。")
        new_status = self.import_job_repository.complete_validation(
            job_id, self.worker_id, outcome=outcome
        )
        action = (
            "validated" if new_status == "approval_pending" else "validation_failed"
        )
        self._audit_job(
            job,
            action=action,
            outcome="success" if new_status == "approval_pending" else "failure",
            detail=self._validation_detail(outcome, new_status),
        )

    def _publish_import_job(self, job: dict[str, Any]) -> None:
        job_id: UUID = job["job_id"]
        payload = decode_import_payload(self.object_store.get(job["source_object_key"]))
        # 每次发布尝试开始即留痕；失败重试会再追加一条，published/publish_failed
        # 各自记结果。
        self._audit_job(job, action="publishing", outcome="success", detail=None)
        published = self.importer.publish_validated_snapshot(
            tenant_id=job["tenant_id"],
            source_system=job["source_system"],
            batch_id=job_id,
            source_file=job["source_file_name"],
            source_snapshot_hash=job["snapshot_hash"],
            calculation_rule_version=job["calculation_rule_version"],
            parts=payload["parts"],
            events=payload["cost_events"],
            inputs=payload["cost_event_inputs"],
            records=payload["cost_records"],
        )
        batch_id = UUID(str(published["batch_id"]))
        self.import_job_repository.complete_publish(
            job_id, self.worker_id, published_batch_id=batch_id
        )
        self.import_job_repository.append_audit_event(
            tenant_id=job["tenant_id"],
            actor_principal_id=self._import_actor(),
            action="published",
            target_type="snapshot",
            target_id=str(batch_id),
            outcome="success",
            job_id=job_id,
            batch_id=batch_id,
            detail={"batch_id": str(batch_id), "status": "published"},
        )

    def _fail_import_job(
        self, job: dict[str, Any], *, retryable: bool, error_code: str
    ) -> None:
        job_id: UUID = job["job_id"]
        status = str(job["status"])
        try:
            new_status = self.import_job_repository.fail_job(
                job_id, self.worker_id, retryable=retryable, error_code=error_code
            )
        except ImportJobLeaseLostError:
            LOGGER.warning(
                "import job lease lost while failing",
                extra={"job_id": str(job_id), "status": status},
            )
            return
        LOGGER.warning(
            "import job execution failed",
            extra={
                "job_id": str(job_id),
                "status": new_status,
                "error_code": error_code,
                "retryable": retryable,
            },
        )
        if retryable:
            # 任务仍在工作中状态等待退避重试，审计只在终态失败时追加。
            return
        self._audit_job(
            job,
            action=(
                "validation_failed" if status == "validating" else "publish_failed"
            ),
            outcome="failure",
            detail={
                "error_code": error_code,
                "retryable": False,
                "previous_status": status,
                "status": new_status,
                "attempt_count": job.get("attempt_count"),
            },
        )

    def _import_job_heartbeat(self, job_id: UUID, stop: threading.Event) -> None:
        while not stop.wait(self.settings.agent_run_heartbeat_seconds):
            try:
                renewed = self.import_job_repository.renew_job_lease(
                    job_id, self.worker_id
                )
            except Exception:
                LOGGER.exception(
                    "import job lease renewal failed", extra={"job_id": str(job_id)}
                )
                return
            if not renewed:
                return

    def _audit_job(
        self,
        job: dict[str, Any],
        *,
        action: str,
        outcome: str,
        detail: dict[str, Any] | None,
    ) -> None:
        self.import_job_repository.append_audit_event(
            tenant_id=job["tenant_id"],
            actor_principal_id=self._import_actor(),
            action=action,
            target_type="import_job",
            target_id=str(job["job_id"]),
            outcome=outcome,
            job_id=job["job_id"],
            detail=detail,
        )

    def _import_actor(self) -> str:
        return f"system:{self.worker_id}"[:128]

    @staticmethod
    def _validation_detail(outcome: ValidationOutcome, status: str) -> dict[str, Any]:
        summary: dict[str, int] = {}
        for item in outcome.errors:
            summary[item.error_code] = summary.get(item.error_code, 0) + 1
        codes = ", ".join(f"{code}:{count}" for code, count in sorted(summary.items()))
        return {
            "status": status,
            "total_rows": outcome.total_rows,
            "valid_rows": outcome.valid_rows,
            "error_rows": outcome.error_rows,
            "error_codes": codes[:AUDIT_ERROR_SUMMARY_LIMIT],
        }

    def _execute_run(self, run: dict[str, Any]) -> None:
        run_id = run["run_id"]
        heartbeat_stop = threading.Event()
        lease_lost = threading.Event()
        heartbeat = threading.Thread(
            target=self._lease_heartbeat,
            args=(run_id, heartbeat_stop, lease_lost),
            daemon=True,
        )
        heartbeat.start()
        try:
            with open_postgres_checkpointer(self.settings) as checkpointer:
                runtime_services = self._runtime_services(run, lease_lost=lease_lost)
                graph = create_cost_agent_graph(
                    checkpointer=checkpointer, runtime_services=runtime_services
                )
                config = {"configurable": {"thread_id": run_id}}
                authoritative = self._run_principal(run_id)
                if run.get("claimed_from") == "finalizing":
                    snapshot = graph.get_state(config)
                    latest_state = dict(snapshot.values)
                    assert_state_principal(latest_state, authoritative)
                else:
                    checkpoint = checkpointer.get_tuple(config)
                    persisted_graph_event_keys = (
                        self.repository.list_runtime_event_keys(
                            run_id, prefix=f"{run_id}:graph:"
                        )
                    )
                    initial_state = (
                        None
                        if checkpoint is not None
                        else self._initial_state(run, run_id)
                    )
                    if initial_state is not None:
                        assert_state_principal(initial_state, authoritative)
                    elif checkpoint is not None:
                        assert_state_principal(
                            dict(checkpoint.checkpoint["channel_values"]), authoritative
                        )
                    latest_state: dict[str, Any] | None = None
                    started_at = datetime.fromisoformat(
                        str(run["started_at"] or run["created_at"])
                    )
                    for snapshot in graph.stream(
                        initial_state,
                        config=config,
                        stream_mode="values",
                    ):
                        latest_state = dict(snapshot)
                        self._persist_graph_events(
                            run_id, latest_state, persisted_graph_event_keys
                        )
                        if lease_lost.is_set():
                            raise RuntimeServiceError(
                                "repository_unavailable",
                                "运行租约已失效。",
                                retryable=True,
                            )
                        if self.repository.cancellation_requested(run_id):
                            self.repository.mark_cancelled(run_id, self.worker_id)
                            return
                        elapsed = (datetime.now(UTC) - started_at).total_seconds()
                        if elapsed > self.settings.agent_run_timeout_seconds:
                            raise TimeoutError("run timeout")
                    if latest_state is None:
                        latest_state = dict(graph.get_state(config).values)

                if not latest_state:
                    raise RuntimeError("Agent workflow 未产生终态 checkpoint。")
                if lease_lost.is_set():
                    raise RuntimeServiceError(
                        "repository_unavailable",
                        "运行租约已失效。",
                        retryable=True,
                    )
                if self.repository.cancellation_requested(run_id):
                    self.repository.mark_cancelled(run_id, self.worker_id)
                    return
                result = _result_from_state(latest_state, include_internal=True)
                if result.get("outcome") == "needs_clarification":
                    self.repository.append_event(
                        run_id,
                        graph_sequence=len(result.get("events") or []) + 1,
                        event_type="clarification",
                        payload={
                            "run_id": run_id,
                            "clarification": result.get("clarification"),
                            "status_bar": self._public_status_bar(
                                result.get("status_bar")
                            ),
                        },
                    )
                context = result.pop("_conversation_context")
                audit_trace = result.pop("_audit_trace")
                audit_trace = {
                    **audit_trace,
                    "schema_version": "3.0",
                    "runtime_version": run["runtime_version"],
                    "workflow_version": run["workflow_version"],
                    "budget": {
                        "limits": runtime_services.limits.model_dump(mode="json"),
                        "usage": runtime_services.usage.model_dump(mode="json"),
                    },
                    "capability_snapshot": latest_state.get("runtime_metadata", {}).get(
                        "capabilities", []
                    ),
                    "policy_decisions": latest_state.get("runtime_metadata", {}).get(
                        "policy_decisions", []
                    ),
                }
                self.repository.mark_finalizing(run_id, self.worker_id)
                self.repository.finalize(
                    run_id,
                    self.worker_id,
                    result=result,
                    context=context,
                    audit_trace=audit_trace,
                )
        except Exception as exc:  # noqa: BLE001 - classify at durable boundary
            retryable, code, message = classify_run_exception(exc)
            status = self.repository.handle_failure(
                run_id,
                self.worker_id,
                retryable=retryable,
                code=code,
                message=message,
            )
            LOGGER.warning(
                "run execution failed",
                extra={"run_id": run_id, "status": status, "error_code": code},
            )
        finally:
            heartbeat_stop.set()
            heartbeat.join(timeout=1)

    def _runtime_services(
        self, run: dict[str, Any], *, lease_lost: threading.Event | None = None
    ) -> RuntimeServices:
        lease_lost = lease_lost or threading.Event()
        services = RuntimeServices(
            harness=AgentHarness(),
            model_provider=get_model_provider(),
            limits=RuntimeBudgetLimits.model_validate(run["budget"]["limits"]),
            event_sink=PostgresEventSink(self.repository),
            budget_store=PostgresBudgetStore(self.repository),
            cancel_check=lambda: self.repository.cancellation_requested(run["run_id"]),
            lease_valid_check=lambda: not lease_lost.is_set(),
        )
        started_at = datetime.fromisoformat(str(run["started_at"] or run["created_at"]))
        elapsed = max(0.0, (datetime.now(UTC) - started_at).total_seconds())
        services.start_run(
            run["run_id"],
            attempt_count=run["attempt_count"],
            elapsed_seconds=elapsed,
            initial_usage=RuntimeBudgetUsage.model_validate(run["budget"]["usage"]),
        )
        return services

    def _initial_state(self, run: dict[str, Any], run_id: str) -> dict[str, Any]:
        request = self._load_request(run_id)
        principal = ExecutionPrincipal.model_validate(request["principal"])
        return _new_run_state_from_request(
            RuntimeRunRequest(
                question=request["question"],
                run_id=run_id,
                conversation_id=run["conversation_id"],
                turn_id=run["turn_id"],
                message_id=run["message_id"],
                principal=principal,
                routing_mode=request["routing_mode"],
                requested_capabilities=request["enabled_capabilities"],
                conversation_context=request["conversation_context"],
                recent_messages=request["recent_messages"],
            )
        )

    def _run_principal(self, run_id: str) -> ExecutionPrincipal:
        """The principal frozen into the run row when the run was submitted."""
        with self.repository._session_factory() as session:
            from app.db.models import RuntimeAgentRun

            row = session.get(RuntimeAgentRun, run_id)
            if row is None:
                raise RuntimeError("Run 不存在。")
            request = dict(row.request_json)
        principal = ExecutionPrincipal.model_validate(request["principal"])
        if (principal.principal_id, principal.tenant_id) != (
            row.principal_id,
            row.tenant_id,
        ):
            raise PermissionError("主体与运行快照不一致")
        return principal

    def _load_request(self, run_id: str) -> dict[str, Any]:
        with self.repository._session_factory() as session:
            from app.db.models import RuntimeAgentRun

            row = session.get(RuntimeAgentRun, run_id)
            if row is None:
                raise RuntimeError("Run 不存在。")
            return dict(row.request_json)

    def _persist_graph_events(
        self,
        run_id: str,
        state: dict[str, Any],
        persisted_event_keys: set[str],
    ) -> None:
        events = state.get("events") or []
        for index, event in enumerate(events, start=1):
            event_key = f"{run_id}:graph:{index}:event"
            if event_key in persisted_event_keys:
                continue
            self.repository.append_event(
                run_id,
                graph_sequence=index,
                event_type="event",
                payload={
                    "run_id": run_id,
                    "event": event,
                    "events": events,
                    "status_bar": self._public_status_bar(state.get("status_bar")),
                },
            )
            persisted_event_keys.add(event_key)

    @staticmethod
    def _public_status_bar(value: Any) -> dict[str, Any] | None:
        if not isinstance(value, dict):
            return None
        public = dict(value)
        public.pop("goal", None)
        return public

    def _lease_heartbeat(
        self, run_id: str, stop: threading.Event, lease_lost: threading.Event
    ) -> None:
        while not stop.wait(self.settings.agent_run_heartbeat_seconds):
            try:
                renewed = self.repository.renew_lease(run_id, self.worker_id)
            except Exception:
                LOGGER.exception(
                    "worker lease renewal failed", extra={"run_id": run_id}
                )
                lease_lost.set()
                return
            if not renewed:
                lease_lost.set()
                return


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    worker = AgentWorker()

    def stop_handler(_signum, _frame) -> None:
        worker.request_stop()

    signal.signal(signal.SIGINT, stop_handler)
    signal.signal(signal.SIGTERM, stop_handler)
    worker.run_forever()


if __name__ == "__main__":
    main()
