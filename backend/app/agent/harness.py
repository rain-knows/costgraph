"""Runtime control layer for capability, context and tool boundaries."""

from __future__ import annotations

import inspect
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, get_type_hints
from uuid import uuid4

from jsonschema import Draft202012Validator, SchemaError
from jsonschema import ValidationError as JSONSchemaValidationError
from pydantic import TypeAdapter
from sqlalchemy.exc import SQLAlchemyError

from app.agent.capabilities import CapabilityId
from app.agent.context import RuntimeContext, build_runtime_context
from app.agent.runtime_contracts import (
    RUNTIME_VERSION,
    WORKFLOW_VERSION,
    RuntimeRunRequest,
    ToolCall,
    ToolResult,
    ToolSpec,
)
from app.domain.authorization import ExecutionContext, build_execution_context

ToolFunction = Callable[..., Any]


@dataclass(frozen=True)
class RegisteredTool:
    spec: ToolSpec
    function: ToolFunction


@dataclass(frozen=True)
class HarnessPreparation:
    request: RuntimeRunRequest
    execution_context: ExecutionContext
    runtime_context: RuntimeContext
    tools: tuple[ToolSpec, ...]
    trace_metadata: dict[str, Any]


class ToolAccessError(PermissionError):
    pass


class ToolContractError(ValueError):
    pass


class ToolRegistry:
    """Small in-process registry; registration is explicit and deterministic."""

    def __init__(self) -> None:
        self._tools: dict[str, RegisteredTool] = {}

    def register(self, spec: ToolSpec, function: ToolFunction) -> None:
        if spec.tool_id in self._tools:
            raise ValueError(f"工具已注册：{spec.tool_id}")
        if not spec.input_schema:
            spec = spec.model_copy(
                update={"input_schema": _callable_input_schema(function)}
            )
        for schema_name, schema in (
            ("input_schema", spec.input_schema),
            ("output_schema", spec.output_schema),
        ):
            if schema:
                try:
                    Draft202012Validator.check_schema(schema)
                except SchemaError as exc:
                    raise ValueError(f"{spec.tool_id} 的 {schema_name} 无效") from exc
        self._tools[spec.tool_id] = RegisteredTool(spec=spec, function=function)

    def get(self, tool_id: str) -> RegisteredTool:
        try:
            return self._tools[tool_id]
        except KeyError as exc:
            raise KeyError(f"未知工具：{tool_id}") from exc

    def specs(self) -> list[ToolSpec]:
        return [item.spec for item in self._tools.values()]

    def available_for(
        self, execution_context: ExecutionContext | dict[str, Any]
    ) -> list[ToolSpec]:
        context = _context(execution_context)
        return [
            item.spec
            for item in self._tools.values()
            if item.spec.enabled
            if all(
                context.has_capability(capability)
                for capability in item.spec.required_capabilities
            )
        ]

    def preflight(
        self,
        tool_id: str,
        arguments: dict[str, Any],
        execution_context: ExecutionContext | dict[str, Any],
    ) -> ToolSpec:
        binding = self.get(tool_id)
        if not binding.spec.enabled:
            raise ToolAccessError("工具当前未启用。")
        context = _context(execution_context)
        try:
            for capability in binding.spec.required_capabilities:
                context.require_capability(capability)
            _validate_data_scope(binding.spec, context, arguments)
        except PermissionError as exc:
            raise ToolAccessError("工具访问被拒绝。") from exc
        if "execution_context" in arguments:
            raise ToolContractError("execution_context 由 Runtime 注入。")
        try:
            _validate_schema(binding.spec.input_schema, arguments)
        except JSONSchemaValidationError as exc:
            raise ToolContractError("工具参数不符合契约。") from exc
        return binding.spec

    def invoke(
        self,
        tool_id: str,
        arguments: dict[str, Any],
        execution_context: ExecutionContext | dict[str, Any],
        *,
        call_id: str | None = None,
    ) -> tuple[ToolCall, ToolResult]:
        resolved_call_id = call_id or f"tool_{uuid4().hex}"
        started_at = datetime.now(UTC).isoformat()
        call = ToolCall(
            call_id=resolved_call_id,
            tool_id=tool_id,
            arguments_summary=summarize_tool_arguments(arguments),
            status="running",
            started_at=started_at,
        )
        try:
            self.preflight(tool_id, arguments, execution_context)
            binding = self.get(tool_id)
            context = _context(execution_context)
            kwargs = dict(arguments)
            if _accepts_execution_context(binding.function):
                kwargs["execution_context"] = context
            result = binding.function(**kwargs)
            _validate_schema(binding.spec.output_schema, result)
        except KeyError:
            finished_at = datetime.now(UTC).isoformat()
            return (
                call.model_copy(update={"status": "error", "finished_at": finished_at}),
                ToolResult(
                    call_id=resolved_call_id,
                    tool_id=tool_id,
                    status="error",
                    error="请求的数据不存在。",
                    error_category="not_found",
                    started_at=started_at,
                    finished_at=finished_at,
                ),
            )
        except (PermissionError, ToolAccessError):
            finished_at = datetime.now(UTC).isoformat()
            return (
                call.model_copy(
                    update={"status": "denied", "finished_at": finished_at}
                ),
                ToolResult(
                    call_id=resolved_call_id,
                    tool_id=tool_id,
                    status="denied",
                    error="工具访问被拒绝。",
                    error_category="authorization",
                    started_at=started_at,
                    finished_at=finished_at,
                ),
            )
        except TimeoutError:
            finished_at = datetime.now(UTC).isoformat()
            return (
                call.model_copy(update={"status": "error", "finished_at": finished_at}),
                ToolResult(
                    call_id=resolved_call_id,
                    tool_id=tool_id,
                    status="error",
                    error="工具执行超时。",
                    error_category="timeout",
                    retryable=binding.spec.retry_policy == "transient",
                    started_at=started_at,
                    finished_at=finished_at,
                ),
            )
        except (TypeError, ValueError, ToolContractError):
            finished_at = datetime.now(UTC).isoformat()
            return (
                call.model_copy(update={"status": "error", "finished_at": finished_at}),
                ToolResult(
                    call_id=resolved_call_id,
                    tool_id=tool_id,
                    status="error",
                    error="工具参数不符合契约。",
                    error_category="validation",
                    started_at=started_at,
                    finished_at=finished_at,
                ),
            )
        except SQLAlchemyError:
            finished_at = datetime.now(UTC).isoformat()
            return (
                call.model_copy(update={"status": "error", "finished_at": finished_at}),
                ToolResult(
                    call_id=resolved_call_id,
                    tool_id=tool_id,
                    status="error",
                    error="数据服务暂时不可用。",
                    error_category="repository",
                    retryable=binding.spec.retry_policy == "transient",
                    started_at=started_at,
                    finished_at=finished_at,
                ),
            )
        except JSONSchemaValidationError:
            finished_at = datetime.now(UTC).isoformat()
            return (
                call.model_copy(update={"status": "error", "finished_at": finished_at}),
                ToolResult(
                    call_id=resolved_call_id,
                    tool_id=tool_id,
                    status="error",
                    error="工具输入或输出不符合契约。",
                    error_category="validation",
                    started_at=started_at,
                    finished_at=finished_at,
                ),
            )
        except Exception:  # noqa: BLE001 - stable tool boundary
            finished_at = datetime.now(UTC).isoformat()
            return (
                call.model_copy(update={"status": "error", "finished_at": finished_at}),
                ToolResult(
                    call_id=resolved_call_id,
                    tool_id=tool_id,
                    status="error",
                    error="工具执行失败。",
                    error_category="internal",
                    started_at=started_at,
                    finished_at=finished_at,
                ),
            )
        finished_at = datetime.now(UTC).isoformat()
        return (
            call.model_copy(update={"status": "success", "finished_at": finished_at}),
            ToolResult(
                call_id=resolved_call_id,
                tool_id=tool_id,
                status="success",
                value=result,
                result_summary=_summarize_result(result),
                started_at=started_at,
                finished_at=finished_at,
            ),
        )


def _context(value: ExecutionContext | dict[str, Any]) -> ExecutionContext:
    if isinstance(value, ExecutionContext):
        return value
    return ExecutionContext.model_validate(value)


def _validate_schema(schema: dict[str, Any], value: Any) -> None:
    if schema:
        Draft202012Validator(schema).validate(value)


def _validate_data_scope(
    spec: ToolSpec, context: ExecutionContext, arguments: dict[str, Any]
) -> None:
    if spec.data_scope == "published_cost_data":
        if context.data_scope.source != "postgresql_cost_data":
            raise PermissionError("成本数据范围无效。")
        part_id = arguments.get("part_id")
        periods = [
            arguments.get(key)
            for key in ("period", "start_period", "end_period")
            if arguments.get(key)
        ]
        if part_id:
            if periods:
                for period in periods:
                    context.require_cost_scope(str(part_id), str(period))
            else:
                context.require_cost_scope(str(part_id))
    elif spec.data_scope != "none":
        raise PermissionError("工具声明了未知数据范围。")


def _accepts_execution_context(function: ToolFunction) -> bool:
    try:
        return "execution_context" in inspect.signature(function).parameters
    except (TypeError, ValueError):
        return False


def _callable_input_schema(function: ToolFunction) -> dict[str, Any]:
    try:
        type_hints = get_type_hints(function)
    except (NameError, TypeError):
        type_hints = {}
    parameters = {
        name: parameter
        for name, parameter in inspect.signature(function).parameters.items()
        if name != "execution_context"
    }
    return {
        "type": "object",
        "properties": {
            name: _annotation_schema(type_hints.get(name, parameter.annotation))
            for name, parameter in parameters.items()
        },
        "required": [
            name
            for name, parameter in parameters.items()
            if parameter.default is inspect.Parameter.empty
        ],
        "additionalProperties": False,
    }


def _annotation_schema(annotation: Any) -> dict[str, Any]:
    if annotation is inspect.Parameter.empty or annotation is Any:
        return {}
    try:
        return TypeAdapter(annotation).json_schema()
    except Exception:  # noqa: BLE001 - metadata generation must not break runtime
        return {}


def summarize_tool_arguments(arguments: dict[str, Any]) -> dict[str, Any]:
    """Return the stable redacted argument shape used by Trace and Replay."""

    return {
        key: "<redacted>" if key in {"prompt", "content", "messages"} else _small(value)
        for key, value in arguments.items()
    }


def _summarize_result(result: Any) -> Any:
    if isinstance(result, dict):
        return {
            key: _small(value)
            for key, value in list(result.items())[:20]
            if key not in {"source_tables", "input"}
        }
    if isinstance(result, list):
        return {"count": len(result)}
    return _small(result)


def _small(value: Any) -> Any:
    if isinstance(value, (str, int, float, bool)) or value is None:
        return (
            value
            if not isinstance(value, str) or len(value) <= 120
            else f"{value[:117]}..."
        )
    if isinstance(value, dict):
        return {key: _small(item) for key, item in list(value.items())[:10]}
    if isinstance(value, list):
        return {"count": len(value)}
    return str(value)


def default_tool_registry() -> ToolRegistry:
    """Build the registry lazily so graph/node imports remain acyclic."""

    from app.agent import tools

    registry = ToolRegistry()
    cost_capability: list[CapabilityId] = ["cost_calculation"]
    for tool_id, function, description, output_schema in (
        (
            "resolve_part",
            tools.resolve_part_tool,
            "匹配唯一零件",
            {"type": ["object", "null"]},
        ),
        (
            "resolve_part_candidates",
            tools.resolve_part_candidates_tool,
            "列出零件候选",
            {"type": "array"},
        ),
        ("list_parts", tools.list_parts_tool, "列出授权零件", {"type": "array"}),
        (
            "load_finished_batches",
            tools.load_finished_batches_tool,
            "读取已发布产成品批次及卷积成本",
            {"type": "array"},
        ),
        (
            "calculate_finished_batch_cost",
            tools.calculate_finished_batch_cost_tool,
            "确定性计算批次三视图成本",
            {"type": "object"},
        ),
    ):
        registry.register(
            ToolSpec(
                tool_id=tool_id,
                description=description,
                input_schema=_callable_input_schema(function),
                output_schema=output_schema,
                required_capabilities=cost_capability,
                data_scope="published_cost_data",
                deterministic=True,
                read_only=True,
                retry_policy="transient",
                version="cost-tools-v2",
            ),
            function,
        )
    registry.register(
        ToolSpec(
            tool_id="build_report",
            description="生成结构化成本报告",
            input_schema=_callable_input_schema(tools.build_report_tool),
            output_schema={"type": "object"},
            required_capabilities=cost_capability,
            data_scope="none",
            deterministic=True,
            read_only=True,
            retry_policy="transient",
            version="cost-tools-v2",
        ),
        tools.build_report_tool,
    )
    return registry


class AgentHarness:
    """Prepare a run and enforce the capability/tool boundary."""

    def __init__(self, registry: ToolRegistry | None = None) -> None:
        self.registry = registry or default_tool_registry()

    def prepare(self, request: RuntimeRunRequest) -> HarnessPreparation:
        context = build_execution_context(
            request.routing_mode,
            request.requested_capabilities,
            principal=request.principal,
        )
        runtime_context = build_runtime_context(
            context,
            conversation_id=request.conversation_id,
            routing_mode=request.routing_mode,
            conversation_context=request.conversation_context,
            recent_messages=request.recent_messages,
        )
        tools = tuple(self.registry.available_for(context))
        return HarnessPreparation(
            request=request,
            execution_context=context,
            runtime_context=runtime_context,
            tools=tools,
            trace_metadata={
                "runtime_version": RUNTIME_VERSION,
                "workflow_version": WORKFLOW_VERSION,
                "capabilities": list(context.effective_capabilities),
                "requested_capabilities": list(context.requested_capabilities),
                "authorized_capabilities": list(context.authorized_capabilities),
                "tool_versions": {item.tool_id: item.version for item in tools},
                "context_summary": runtime_context.public_summary(),
                "policy_decisions": [
                    {
                        "decision": "capability_intersection",
                        "status": "allowed"
                        if context.effective_capabilities
                        else "denied",
                        "requested": list(context.requested_capabilities),
                        "authorized": list(context.authorized_capabilities),
                        "server_allowed": list(context.server_allowed_capabilities),
                        "effective": list(context.effective_capabilities),
                    }
                ],
            },
        )

    def validate_tool_access(
        self, tool_id: str, execution_context: ExecutionContext | dict[str, Any]
    ) -> ToolSpec:
        binding = self.registry.get(tool_id)
        context = _context(execution_context)
        for capability in binding.spec.required_capabilities:
            context.require_capability(capability)
        return binding.spec

    def preflight_tool(
        self,
        tool_id: str,
        arguments: dict[str, Any],
        execution_context: ExecutionContext | dict[str, Any],
    ) -> ToolSpec:
        return self.registry.preflight(tool_id, arguments, execution_context)

    def invoke_tool(
        self,
        tool_id: str,
        arguments: dict[str, Any],
        execution_context: ExecutionContext | dict[str, Any],
        *,
        call_id: str | None = None,
    ) -> tuple[ToolCall, ToolResult]:
        return self.registry.invoke(
            tool_id, arguments, execution_context, call_id=call_id
        )

    def available_tools(
        self, execution_context: ExecutionContext | dict[str, Any]
    ) -> list[ToolSpec]:
        return self.registry.available_for(execution_context)
