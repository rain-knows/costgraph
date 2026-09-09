from __future__ import annotations

import logging
import os
import signal
import socket
import threading
from concurrent.futures import Future, ThreadPoolExecutor
from datetime import UTC, datetime
from time import monotonic
from typing import Any
from uuid import uuid4

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
from app.domain.authorization import ExecutionPrincipal
from app.domain.errors import AgentDomainError
from app.repositories.run_repository import PostgresRunRepository
from app.services.llm_service import LLMUnavailableError
from app.settings import AppSettings, get_settings

LOGGER = logging.getLogger("agent.worker")


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


class AgentWorker:
    def __init__(self, settings: AppSettings | None = None) -> None:
        self.settings = settings or get_settings()
        _ = self.settings.psycopg_database_url
        self.repository = PostgresRunRepository(settings=self.settings)
        self.worker_id = (
            f"worker_{socket.gethostname()}_{os.getpid()}_{uuid4().hex[:8]}"
        )
        self._stop = threading.Event()
        self._active: dict[Future[None], str] = {}

    def request_stop(self) -> None:
        self._stop.set()

    def run_forever(self) -> None:
        self.repository.register_worker(
            self.worker_id,
            hostname=socket.gethostname(),
            process_id=os.getpid(),
            app_version=self.settings.app_version,
        )
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
                self.repository.stop_worker(self.worker_id)

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
                if run.get("claimed_from") == "finalizing":
                    snapshot = graph.get_state(config)
                    latest_state = dict(snapshot.values)
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
