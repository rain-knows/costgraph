from app.agent.capabilities import CAPABILITY_REGISTRY
from app.agent.harness import AgentHarness
from app.agent.runtime_contracts import RuntimeRunRequest
from app.domain.authorization import ExecutionPrincipal


def test_harness_intersects_requested_authorized_and_server_capabilities(
    monkeypatch,
) -> None:
    monkeypatch.setenv("AGENT_SERVER_ALLOWED_CAPABILITIES", "system_help")
    prepared = AgentHarness().prepare(
        RuntimeRunRequest(
            question="核算产品A",
            requested_capabilities=["system_help", "cost_calculation"],
        )
    )
    assert prepared.execution_context.effective_capabilities == ("system_help",)
    assert all(
        "cost_calculation" not in tool.required_capabilities for tool in prepared.tools
    )


def test_harness_keeps_tenant_and_principal_in_context() -> None:
    principal = ExecutionPrincipal(
        principal_id="user-1", tenant_id="tenant-1", roles=("system_viewer",)
    )
    prepared = AgentHarness().prepare(
        RuntimeRunRequest(
            question="系统说明",
            principal=principal,
            requested_capabilities=["cost_calculation"],
        )
    )
    assert prepared.runtime_context.principal_id == "user-1"
    assert prepared.runtime_context.tenant_id == "tenant-1"
    assert prepared.execution_context.effective_capabilities == ()


def test_unauthorized_tool_invocation_is_denied() -> None:
    principal = ExecutionPrincipal(
        principal_id="viewer", tenant_id="tenant", roles=("system_viewer",)
    )
    prepared = AgentHarness().prepare(
        RuntimeRunRequest(
            question="x",
            principal=principal,
            requested_capabilities=["cost_calculation"],
        )
    )
    _, result = AgentHarness().registry.invoke(
        "list_parts", {}, prepared.execution_context
    )
    assert result.status == "denied"
    assert result.error_category == "authorization"


def test_capability_registry_declares_governance_metadata() -> None:
    cost = CAPABILITY_REGISTRY["cost_calculation"]
    assert cost.required_roles == ("cost_analyst", "agent_admin")
    assert cost.data_scope == "published_cost_data"
    assert cost.risk_level == "low"
    assert cost.enabled is True
