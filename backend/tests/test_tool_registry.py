import pytest

from app.agent.harness import ToolRegistry
from app.agent.runtime_contracts import ToolSpec
from app.domain.authorization import build_execution_context


def test_tool_registry_schema_and_deterministic_metadata() -> None:
    registry = ToolRegistry()
    registry.register(
        ToolSpec(
            tool_id="echo",
            input_schema={"type": "object"},
            output_schema={"type": "string"},
            required_capabilities=["system_help"],
            deterministic=True,
            version="echo-v1",
        ),
        lambda value, execution_context: value,
    )
    context = build_execution_context("manual", ["system_help"])
    specs = registry.available_for(context)
    assert specs[0].tool_id == "echo"
    assert specs[0].version == "echo-v1"
    call, result = registry.invoke("echo", {"value": "ok"}, context)
    assert call.status == "success"
    assert result.status == "success"
    assert result.result_summary == "ok"


def test_tool_registry_classifies_invalid_arguments() -> None:
    registry = ToolRegistry()
    registry.register(
        ToolSpec(tool_id="needs-value", required_capabilities=["system_help"]),
        lambda required, execution_context: required,
    )
    context = build_execution_context("manual", ["system_help"])
    _, result = registry.invoke("needs-value", {}, context)
    assert result.status == "error"
    assert result.error_category == "validation"


def test_tool_registry_classifies_unknown_tool() -> None:
    registry = ToolRegistry()
    context = build_execution_context("manual", ["system_help"])
    _, result = registry.invoke("missing", {}, context)
    assert result.status == "error"
    assert result.error_category == "not_found"


def test_tool_registry_validates_input_before_execution() -> None:
    executed = False

    def tool(value: str, execution_context) -> str:
        nonlocal executed
        executed = True
        return value

    registry = ToolRegistry()
    registry.register(
        ToolSpec(
            tool_id="typed",
            input_schema={
                "type": "object",
                "properties": {"value": {"type": "string"}},
                "required": ["value"],
                "additionalProperties": False,
            },
            output_schema={"type": "string"},
            required_capabilities=["system_help"],
        ),
        tool,
    )
    context = build_execution_context("manual", ["system_help"])
    _, result = registry.invoke("typed", {"value": 42}, context)
    assert result.status == "error"
    assert result.error_category == "validation"
    assert result.error == "工具参数不符合契约。"
    assert executed is False


def test_tool_registry_validates_output_after_execution() -> None:
    registry = ToolRegistry()
    registry.register(
        ToolSpec(
            tool_id="bad-output",
            output_schema={"type": "string"},
            required_capabilities=["system_help"],
        ),
        lambda execution_context: 42,
    )
    context = build_execution_context("manual", ["system_help"])
    _, result = registry.invoke("bad-output", {}, context)
    assert result.status == "error"
    assert result.error_category == "validation"


def test_tool_registry_rejects_invalid_schema_at_registration() -> None:
    registry = ToolRegistry()
    with pytest.raises(ValueError, match="input_schema 无效"):
        registry.register(
            ToolSpec(tool_id="invalid", input_schema={"type": "not-a-type"}),
            lambda: None,
        )


def test_tool_registry_rejects_runtime_context_override() -> None:
    registry = ToolRegistry()
    registry.register(
        ToolSpec(tool_id="context", required_capabilities=["system_help"]),
        lambda execution_context: execution_context,
    )
    context = build_execution_context("manual", ["system_help"])
    _, result = registry.invoke(
        "context", {"execution_context": {"principal": "forged"}}, context
    )
    assert result.status == "error"
    assert result.error_category == "validation"


def test_tool_retryability_is_controlled_by_spec() -> None:
    registry = ToolRegistry()

    def timeout(execution_context):
        raise TimeoutError("sensitive")

    registry.register(
        ToolSpec(
            tool_id="never-retry",
            required_capabilities=["system_help"],
            retry_policy="never",
        ),
        timeout,
    )
    registry.register(
        ToolSpec(
            tool_id="retry-transient",
            required_capabilities=["system_help"],
            retry_policy="transient",
        ),
        timeout,
    )
    context = build_execution_context("manual", ["system_help"])
    _, never = registry.invoke("never-retry", {}, context)
    _, transient = registry.invoke("retry-transient", {}, context)
    assert never.retryable is False
    assert transient.retryable is True
    assert "sensitive" not in (transient.error or "")
