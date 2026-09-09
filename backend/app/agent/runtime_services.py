"""Per-run control-plane services injected into LangGraph nodes."""

from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeoutError
from dataclasses import dataclass, field
from datetime import UTC, datetime
from threading import Lock
from time import monotonic
from typing import Any, Literal, Protocol

from app.agent.harness import AgentHarness, ToolAccessError, ToolContractError
from app.agent.providers import (
    MODEL_OPERATION_METHODS,
    MODEL_OPERATION_SPECS,
    ModelContractError,
    validate_model_operation_result,
)
from app.agent.runtime_contracts import (
    HarnessCallOutcome,
    RuntimeBudgetLimits,
    RuntimeBudgetReservation,
    RuntimeBudgetUsage,
    RuntimeErrorCode,
    RuntimeEvent,
)
from app.domain.authorization import ExecutionContext
from app.services.llm_service import LLMUnavailableError

CallKind = Literal["model", "tool"]


class RuntimeEventSink(Protocol):
    def emit(self, event: RuntimeEvent) -> None: ...


class RuntimeBudgetStore(Protocol):
    def initialize(self, run_id: str, limits: RuntimeBudgetLimits) -> None: ...

    def reserve(
        self, run_id: str, kind: CallKind, *, event: RuntimeEvent
    ) -> RuntimeBudgetReservation | None: ...

    def add_tokens(
        self, run_id: str, prompt: int, completion: int, total: int
    ) -> RuntimeBudgetUsage: ...

    def get_usage(self, run_id: str) -> RuntimeBudgetUsage: ...


@dataclass
class MemoryEventSink:
    events: list[RuntimeEvent] = field(default_factory=list)

    def emit(self, event: RuntimeEvent) -> None:
        event.sequence = len(self.events) + 1
        self.events.append(event)


@dataclass
class MemoryBudgetStore:
    """Thread-safe budget implementation used by inline runs and tests."""

    _limits: dict[str, RuntimeBudgetLimits] = field(default_factory=dict)
    _usage: dict[str, RuntimeBudgetUsage] = field(default_factory=dict)
    _lock: Lock = field(default_factory=Lock)

    def initialize(self, run_id: str, limits: RuntimeBudgetLimits) -> None:
        with self._lock:
            self._limits.setdefault(run_id, limits.model_copy(deep=True))
            self._usage.setdefault(run_id, RuntimeBudgetUsage())

    def reserve(
        self, run_id: str, kind: CallKind, *, event: RuntimeEvent
    ) -> RuntimeBudgetReservation | None:
        with self._lock:
            limits = self._limits[run_id]
            usage = self._usage[run_id]
            field_name = "model_calls" if kind == "model" else "tool_calls"
            maximum = (
                limits.max_model_calls if kind == "model" else limits.max_tool_calls
            )
            if getattr(usage, field_name) >= maximum:
                return None
            usage = usage.model_copy(
                update={field_name: getattr(usage, field_name) + 1}
            )
            self._usage[run_id] = usage
            return RuntimeBudgetReservation(
                usage=usage.model_copy(deep=True),
                start_event_persisted=False,
            )

    def add_tokens(
        self, run_id: str, prompt: int, completion: int, total: int
    ) -> RuntimeBudgetUsage:
        with self._lock:
            usage = self._usage[run_id]
            usage = usage.model_copy(
                update={
                    "prompt_tokens": usage.prompt_tokens + prompt,
                    "completion_tokens": usage.completion_tokens + completion,
                    "total_tokens": usage.total_tokens + total,
                }
            )
            self._usage[run_id] = usage
            return usage.model_copy(deep=True)

    def get_usage(self, run_id: str) -> RuntimeBudgetUsage:
        with self._lock:
            return self._usage[run_id].model_copy(deep=True)


@dataclass
class PostgresEventSink:
    repository: Any

    def emit(self, event: RuntimeEvent) -> None:
        event_type = "node" if event.event_type == "model" else event.event_type
        self.repository.append_runtime_event(
            self._run_id(event),
            event_key=event.event_key,
            event_type=event_type,
            name=event.name or event.node or event.tool_name,
            status=event.status,
            summary=event.summary,
            payload={
                "budget": event.budget.model_dump(mode="json")
                if event.budget
                else None,
                "error_code": event.error_code,
                **event.details,
            },
            started_at=event.started_at,
            finished_at=event.finished_at,
        )

    @staticmethod
    def _run_id(event: RuntimeEvent) -> str:
        if not event.event_key:
            raise RuntimeError("持久化 Runtime 事件必须包含 event_key。")
        return event.event_key.split(":", 1)[0]


@dataclass
class PostgresBudgetStore:
    repository: Any

    def initialize(self, run_id: str, limits: RuntimeBudgetLimits) -> None:
        # Durable runs initialize limits and usage in create_run's transaction.
        return None

    def reserve(
        self, run_id: str, kind: CallKind, *, event: RuntimeEvent
    ) -> RuntimeBudgetReservation | None:
        return self.repository.reserve_runtime_call(
            run_id,
            kind,
            event=event.model_dump(mode="json"),
        )

    def add_tokens(
        self, run_id: str, prompt: int, completion: int, total: int
    ) -> RuntimeBudgetUsage:
        return self.repository.add_runtime_tokens(run_id, prompt, completion, total)

    def get_usage(self, run_id: str) -> RuntimeBudgetUsage:
        return self.repository.get_runtime_usage(run_id)


@dataclass
class RuntimeServices:
    """Non-serializable control-plane dependencies for one graph execution."""

    harness: AgentHarness
    model_provider: Any
    limits: RuntimeBudgetLimits
    event_sink: RuntimeEventSink = field(default_factory=MemoryEventSink)
    budget_store: RuntimeBudgetStore = field(default_factory=MemoryBudgetStore)
    cancel_check: Callable[[], bool] = lambda: False
    lease_valid_check: Callable[[], bool] = lambda: True
    clock: Callable[[], float] = monotonic
    now: Callable[[], str] = lambda: datetime.now(UTC).isoformat()
    run_id: str | None = None
    deadline: float | None = None
    attempt_count: int = 1
    call_ordinals: dict[str, int] = field(default_factory=dict)
    current_usage: RuntimeBudgetUsage = field(default_factory=RuntimeBudgetUsage)

    @property
    def usage(self) -> RuntimeBudgetUsage:
        return self.current_usage.model_copy(deep=True)

    def start_run(
        self,
        run_id: str,
        *,
        attempt_count: int = 1,
        elapsed_seconds: float = 0,
        initial_usage: RuntimeBudgetUsage | None = None,
    ) -> None:
        self.run_id = run_id
        self.attempt_count = attempt_count
        self.call_ordinals = {}
        self.budget_store.initialize(run_id, self.limits)
        self.current_usage = (
            initial_usage.model_copy(deep=True)
            if initial_usage is not None
            else self.budget_store.get_usage(run_id)
        )
        remaining = max(0.0, self.limits.run_timeout_seconds - elapsed_seconds)
        self.deadline = self.clock() + remaining

    def ensure_run(self, run_id: str) -> None:
        if self.run_id != run_id:
            self.start_run(run_id)

    def remaining_seconds(self) -> float:
        if self.deadline is None:
            return self.limits.run_timeout_seconds
        return max(0.0, self.deadline - self.clock())

    def check_control(self) -> None:
        if not self.lease_valid_check():
            raise RuntimeServiceError(
                "repository_unavailable", "运行租约已失效。", retryable=True
            )
        if self.cancel_check():
            raise RuntimeServiceError("run_cancelled", "运行已取消。")
        if self.remaining_seconds() <= 0:
            raise RuntimeServiceError("run_timeout", "运行已超过服务端时间预算。")

    def invoke_tool(
        self,
        tool_id: str,
        arguments: dict[str, Any],
        execution_context: ExecutionContext | dict[str, Any],
        *,
        node: str,
    ) -> HarnessCallOutcome:
        self._ensure_started()
        self.check_control()
        call_id = self._call_id("tool", node, tool_id)
        started_at = self.now()
        try:
            tool_spec = self.harness.preflight_tool(
                tool_id, arguments, execution_context
            )
        except ToolAccessError:
            return self._finish(
                self._denied(
                    call_id,
                    "tool",
                    tool_id,
                    "tool_access_denied",
                    "工具访问被拒绝。",
                ),
                node,
            )
        except (KeyError, ToolContractError):
            return self._finish(
                self._failed(
                    call_id,
                    "tool",
                    tool_id,
                    "tool_validation_error",
                    "工具参数不符合契约。",
                    started_at,
                ),
                node,
            )
        start_event = self._event(
            "tool",
            event_key=f"{call_id}:start",
            node=node,
            name=tool_id,
            tool_name=tool_id,
            status="running",
            summary="工具调用已开始。",
            started_at=started_at,
            details={"tool_version": tool_spec.version},
        )
        reservation = self.budget_store.reserve(self.run_id, "tool", event=start_event)
        if reservation is None:
            return self._finish(
                self._denied(
                    call_id,
                    "tool",
                    tool_id,
                    "budget_exceeded",
                    "工具调用预算已用尽。",
                ),
                node,
            )
        self.current_usage = reservation.usage
        start_event.budget = reservation.usage
        if not reservation.start_event_persisted:
            self.event_sink.emit(start_event)
        execution_started = self.clock()
        execution_duration_ms: float | None = None
        try:
            result = _run_with_timeout(
                lambda: self.harness.invoke_tool(
                    tool_id, arguments, execution_context, call_id=call_id
                ),
                min(
                    self.limits.tool_timeout_seconds,
                    tool_spec.timeout_ms / 1000,
                    self.remaining_seconds(),
                ),
            )
            execution_duration_ms = round(
                max(0.0, self.clock() - execution_started) * 1000, 3
            )
            self.check_control()
            _, tool_result = result
            status = (
                "success" if tool_result.status == "success" else tool_result.status
            )
            outcome = HarnessCallOutcome(
                call_id=call_id,
                kind="tool",
                name=tool_id,
                status=status,
                value=tool_result.value,
                summary={"tool_id": tool_id, "result": tool_result.result_summary},
                error=tool_result.error,
                error_code=_tool_error_code(tool_result.error_category, status),
                retryable=tool_result.retryable,
                budget=self.usage,
                started_at=started_at,
                finished_at=self.now(),
                execution_duration_ms=execution_duration_ms,
            )
        except FutureTimeoutError:
            execution_duration_ms = round(
                max(0.0, self.clock() - execution_started) * 1000, 3
            )
            code: RuntimeErrorCode = (
                "run_timeout" if self.remaining_seconds() <= 0 else "tool_timeout"
            )
            outcome = self._failed(
                call_id,
                "tool",
                tool_id,
                code,
                "运行已超时。" if code == "run_timeout" else "工具执行超时。",
                started_at,
                retryable=(
                    code == "tool_timeout" and tool_spec.retry_policy == "transient"
                ),
            )
        except RuntimeServiceError as exc:
            outcome = self._failed(
                call_id,
                "tool",
                tool_id,
                exc.code,
                str(exc),
                started_at,
                retryable=exc.retryable,
            )
        except Exception:  # noqa: BLE001 - stable runtime boundary
            outcome = self._failed(
                call_id,
                "tool",
                tool_id,
                "runtime_internal_error",
                "工具执行失败。",
                started_at,
            )
        outcome.execution_duration_ms = (
            execution_duration_ms
            if execution_duration_ms is not None
            else round(max(0.0, self.clock() - execution_started) * 1000, 3)
        )
        return self._finish(outcome, node)

    def invoke_model(
        self,
        operation: str,
        *,
        node: str,
        args: tuple[Any, ...] = (),
        kwargs: dict[str, Any] | None = None,
    ) -> HarnessCallOutcome:
        self._ensure_started()
        self.check_control()
        call_id = self._call_id("model", node, operation)
        started_at = self.now()
        try:
            method = _resolve_model_operation(self.model_provider, operation)
            model_spec = MODEL_OPERATION_SPECS[operation]
        except RuntimeServiceError as exc:
            return self._finish(
                self._failed(
                    call_id, "model", operation, exc.code, str(exc), started_at
                ),
                node,
            )
        start_event = self._event(
            "model",
            event_key=f"{call_id}:start",
            node=node,
            name=operation,
            status="running",
            summary="模型调用已开始。",
            started_at=started_at,
            details={
                "provider": getattr(self.model_provider, "provider_id", None),
                "provider_version": getattr(
                    self.model_provider, "provider_version", None
                ),
                "model": getattr(self.model_provider, "model_id", None),
            },
        )
        reservation = self.budget_store.reserve(self.run_id, "model", event=start_event)
        if reservation is None:
            return self._finish(
                self._denied(
                    call_id,
                    "model",
                    operation,
                    "budget_exceeded",
                    "模型调用预算已用尽。",
                ),
                node,
            )
        self.current_usage = reservation.usage
        start_event.budget = reservation.usage
        if not reservation.start_event_persisted:
            self.event_sink.emit(start_event)
        execution_started = self.clock()
        execution_duration_ms: float | None = None
        try:
            value = _run_with_timeout(
                lambda: method(*args, **(kwargs or {})),
                min(self.limits.model_timeout_seconds, self.remaining_seconds()),
            )
            execution_duration_ms = round(
                max(0.0, self.clock() - execution_started) * 1000, 3
            )
            self.check_control()
            validate_model_operation_result(operation, value)
            self._record_usage(value)
            outcome = HarnessCallOutcome(
                call_id=call_id,
                kind="model",
                name=operation,
                status="success",
                value=value,
                summary={
                    "provider": _provider_from(value, self.model_provider),
                    "model": getattr(self.model_provider, "model_id", None),
                    "provider_version": getattr(
                        self.model_provider, "provider_version", None
                    ),
                },
                budget=self.usage,
                started_at=started_at,
                finished_at=self.now(),
                execution_duration_ms=execution_duration_ms,
            )
        except FutureTimeoutError:
            execution_duration_ms = round(
                max(0.0, self.clock() - execution_started) * 1000, 3
            )
            code = (
                "run_timeout" if self.remaining_seconds() <= 0 else "model_unavailable"
            )
            outcome = self._failed(
                call_id,
                "model",
                operation,
                code,
                "运行已超时。" if code == "run_timeout" else "模型调用超时。",
                started_at,
                retryable=(
                    code == "model_unavailable"
                    and model_spec.retry_policy == "transient"
                ),
            )
        except LLMUnavailableError as exc:
            outcome = self._failed(
                call_id,
                "model",
                operation,
                "model_unavailable",
                "模型服务暂时不可用。",
                started_at,
                retryable=(
                    model_spec.retry_policy == "transient" and _llm_retryable(exc)
                ),
            )
        except ModelContractError:
            outcome = self._failed(
                call_id,
                "model",
                operation,
                "model_unavailable",
                "模型结果不符合结构化契约。",
                started_at,
            )
        except RuntimeServiceError as exc:
            outcome = self._failed(
                call_id,
                "model",
                operation,
                exc.code,
                str(exc),
                started_at,
                retryable=exc.retryable,
            )
        except Exception:  # noqa: BLE001 - stable runtime boundary
            outcome = self._failed(
                call_id,
                "model",
                operation,
                "runtime_internal_error",
                "模型调用失败。",
                started_at,
            )
        outcome.execution_duration_ms = (
            execution_duration_ms
            if execution_duration_ms is not None
            else round(max(0.0, self.clock() - execution_started) * 1000, 3)
        )
        return self._finish(outcome, node)

    def _ensure_started(self) -> None:
        if self.run_id is None:
            raise RuntimeError(
                "RuntimeServices.start_run() must be called before execution"
            )

    def _record_usage(self, value: Any) -> None:
        trace = value.get("llm_call", {}) if isinstance(value, dict) else {}
        usage = (trace.get("response") or {}).get("usage") or {}
        prompt = int(usage.get("prompt_tokens") or 0)
        completion = int(usage.get("completion_tokens") or 0)
        total = int(usage.get("total_tokens") or prompt + completion)
        self.current_usage = self.budget_store.add_tokens(
            self.run_id, prompt, completion, total
        )

    def _finish(self, outcome: HarnessCallOutcome, node: str) -> HarnessCallOutcome:
        event_type = "tool" if outcome.kind == "tool" else "model"
        self._emit(
            event_type,
            event_key=f"{outcome.call_id}:finish",
            node=node,
            name=outcome.name,
            tool_name=outcome.name if outcome.kind == "tool" else None,
            status=outcome.status,
            summary=outcome.error or f"{outcome.name} 执行完成。",
            error=outcome.error,
            error_code=outcome.error_code,
            started_at=outcome.started_at,
            finished_at=outcome.finished_at,
            budget=outcome.budget,
            details=(
                {
                    "call_id": outcome.call_id,
                    "tool_version": _tool_version(self.harness, outcome.name),
                    "duration_ms": _duration_ms(
                        outcome.started_at, outcome.finished_at
                    ),
                    "execution_duration_ms": outcome.execution_duration_ms,
                    "result_shape": _result_shape(outcome.value),
                }
                if outcome.kind == "tool"
                else {
                    "call_id": outcome.call_id,
                    "duration_ms": _duration_ms(
                        outcome.started_at, outcome.finished_at
                    ),
                    "execution_duration_ms": outcome.execution_duration_ms,
                    **outcome.summary,
                }
            ),
        )
        return outcome

    def _event(
        self,
        event_type: str,
        *,
        event_key: str,
        status: str,
        summary: str,
        node: str | None = None,
        name: str | None = None,
        tool_name: str | None = None,
        error: str | None = None,
        error_code: RuntimeErrorCode | None = None,
        started_at: str | None = None,
        finished_at: str | None = None,
        budget: RuntimeBudgetUsage | None = None,
        details: dict[str, Any] | None = None,
    ) -> RuntimeEvent:
        return RuntimeEvent(
            event_type=event_type,
            event_key=event_key,
            node=node,
            name=name,
            tool_name=tool_name,
            status=status,
            summary=summary,
            error=error,
            error_code=error_code,
            started_at=started_at,
            finished_at=finished_at,
            budget=budget,
            details=details or {},
        )

    def _emit(self, event_type: str, **kwargs: Any) -> None:
        self.event_sink.emit(self._event(event_type, **kwargs))

    def _call_id(self, kind: CallKind, node: str, name: str) -> str:
        key = f"{node}:{kind}"
        ordinal = self.call_ordinals.get(key, 0) + 1
        self.call_ordinals[key] = ordinal
        return (
            f"{self.run_id}:attempt-{self.attempt_count}:{node}:{kind}:{name}:{ordinal}"
        )

    def _denied(
        self,
        call_id: str,
        kind: CallKind,
        name: str,
        code: RuntimeErrorCode,
        message: str,
    ) -> HarnessCallOutcome:
        return HarnessCallOutcome(
            call_id=call_id,
            kind=kind,
            name=name,
            status="denied",
            error=message,
            error_code=code,
            budget=self.usage,
            finished_at=self.now(),
        )

    def _failed(
        self,
        call_id: str,
        kind: CallKind,
        name: str,
        code: RuntimeErrorCode,
        message: str,
        started_at: str,
        *,
        retryable: bool = False,
    ) -> HarnessCallOutcome:
        return HarnessCallOutcome(
            call_id=call_id,
            kind=kind,
            name=name,
            status="error",
            error=message,
            error_code=code,
            retryable=retryable,
            budget=self.usage,
            started_at=started_at,
            finished_at=self.now(),
        )


class RuntimeServiceError(RuntimeError):
    def __init__(
        self, code: RuntimeErrorCode, message: str, *, retryable: bool = False
    ) -> None:
        super().__init__(message)
        self.code = code
        self.retryable = retryable


def require_call_value(outcome: HarnessCallOutcome) -> Any:
    if outcome.status == "success":
        return outcome.value
    raise RuntimeServiceError(
        outcome.error_code or "runtime_internal_error",
        outcome.error or "Runtime 调用失败。",
        retryable=outcome.retryable,
    )


def _run_with_timeout(function: Callable[[], Any], timeout: float) -> Any:
    executor = ThreadPoolExecutor(max_workers=1)
    future = executor.submit(function)
    try:
        return future.result(timeout=max(0.001, timeout))
    finally:
        executor.shutdown(wait=False, cancel_futures=True)


def _provider_from(value: Any, provider: Any) -> str | None:
    if isinstance(value, dict):
        configured = value.get("llm_call", {}).get("provider")
        if configured:
            return str(configured)
    return getattr(provider, "provider_id", None)


def _resolve_model_operation(provider: Any, operation: str) -> Callable[..., Any]:
    """Resolve only registered provider operations; never execute arbitrary names."""

    if operation not in MODEL_OPERATION_METHODS:
        raise RuntimeServiceError("runtime_internal_error", "模型操作未注册。")
    invoker = getattr(provider, "invoke_operation", None)
    if callable(invoker):
        return lambda *args, **kwargs: invoker(operation, *args, **kwargs)
    method_name = MODEL_OPERATION_METHODS[operation]
    method = getattr(provider, method_name, None)
    if not callable(method):
        raise RuntimeServiceError("model_unavailable", "模型 Provider 不支持该操作。")
    return method


def _duration_ms(started_at: Any, finished_at: Any) -> float | None:
    if not started_at or not finished_at:
        return None
    try:
        start = datetime.fromisoformat(str(started_at))
        finish = datetime.fromisoformat(str(finished_at))
        return round(max(0.0, (finish - start).total_seconds() * 1000), 3)
    except ValueError:
        return None


def _result_shape(value: Any) -> dict[str, Any]:
    if isinstance(value, list):
        return {"type": "array", "count": len(value)}
    if isinstance(value, dict):
        return {"type": "object", "field_count": len(value)}
    if value is None:
        return {"type": "null"}
    return {"type": type(value).__name__}


def _tool_version(harness: AgentHarness, tool_id: str) -> str | None:
    try:
        return harness.registry.get(tool_id).spec.version
    except KeyError:
        return None


def _tool_error_code(category: str | None, status: str) -> RuntimeErrorCode | None:
    if status == "success":
        return None
    return {
        "authorization": "tool_access_denied",
        "validation": "tool_validation_error",
        "not_found": "tool_validation_error",
        "timeout": "tool_timeout",
        "repository": "repository_unavailable",
        "internal": "runtime_internal_error",
    }.get(category, "runtime_internal_error")


def _llm_retryable(exc: LLMUnavailableError) -> bool:
    error_code = str((exc.trace or {}).get("error_code") or "")
    if error_code == "llm_transport_error":
        return True
    if error_code.startswith("llm_http_"):
        try:
            status_code = int(error_code.removeprefix("llm_http_"))
        except ValueError:
            return False
        return status_code == 429 or status_code >= 500
    return False


def default_runtime_services() -> RuntimeServices:
    from app.agent.providers import get_model_provider
    from app.settings import get_settings

    settings = get_settings()
    return RuntimeServices(
        harness=AgentHarness(),
        model_provider=get_model_provider(),
        limits=RuntimeBudgetLimits(
            max_model_calls=settings.agent_max_model_calls,
            max_tool_calls=settings.agent_max_tool_calls,
            tool_timeout_seconds=settings.agent_tool_timeout_seconds,
            model_timeout_seconds=settings.deepseek_timeout_seconds,
            run_timeout_seconds=settings.agent_run_timeout_seconds,
        ),
    )
