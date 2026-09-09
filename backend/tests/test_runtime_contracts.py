import pytest
from pydantic import ValidationError

from app.agent.runtime_contracts import (
    ModelCall,
    RuntimeEvent,
    RuntimeRunRequest,
    RuntimeRunResult,
    ToolResult,
    ToolSpec,
)


def test_runtime_request_validates_capabilities() -> None:
    request = RuntimeRunRequest(
        question="核算产品A",
        requested_capabilities=["cost_calculation"],
    )
    assert request.requested_capabilities == ["cost_calculation"]

    with pytest.raises(ValueError, match="未知能力"):
        RuntimeRunRequest(question="x", requested_capabilities=["admin"])


def test_runtime_contracts_reject_invalid_outcome_and_tool_status() -> None:
    with pytest.raises(ValidationError):
        RuntimeRunResult(run_id="run-1", outcome="invented")
    with pytest.raises(ValidationError):
        ToolResult(call_id="call-1", tool_id="tool", status="pending")


def test_runtime_event_and_tool_spec_are_structured() -> None:
    event = RuntimeEvent(node="policy_gate", status="success", summary="ok")
    spec = ToolSpec(
        tool_id="demo",
        required_capabilities=["system_help"],
        input_schema={"type": "object"},
    )
    assert event.node == "policy_gate"
    assert spec.read_only is True
    assert spec.schema_version == "2020-12"
    assert spec.retry_policy == "never"
    assert spec.owner == "agent-runtime"


def test_model_call_rejects_unknown_operation() -> None:
    with pytest.raises(ValidationError):
        ModelCall(call_id="call-1", operation="delete_all", node="select_route")
