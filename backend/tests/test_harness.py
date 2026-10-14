import pytest

from app.agent.capabilities import CAPABILITY_REGISTRY
from app.agent.harness import AgentHarness
from app.agent.runtime_contracts import RuntimeRunRequest
from app.domain.authorization import (
    ExecutionPrincipal,
    build_execution_context,
    execution_context_from_state,
)


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
        principal_id="user-1", tenant_id="tenant-1", roles=("user",)
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
    assert prepared.execution_context.effective_capabilities == ("cost_calculation",)


def test_server_allowlist_still_denies_tool_invocation(monkeypatch) -> None:
    monkeypatch.setenv("AGENT_SERVER_ALLOWED_CAPABILITIES", "system_help")
    principal = ExecutionPrincipal(
        principal_id="viewer", tenant_id="tenant", roles=("user",)
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
    assert cost.required_roles == ("user", "admin")
    assert cost.data_scope == "published_cost_data"
    assert cost.risk_level == "low"
    assert cost.enabled is True
    reporting = CAPABILITY_REGISTRY["report_generation"]
    assert reporting.report_styles == ("presentation", "period_comparison")
    assert reporting.required_roles == ("user", "admin")


def test_roles_have_deterministic_permission_matrix() -> None:
    user = build_execution_context(
        "manual",
        ["system_help"],
        requested_permissions=["data_management.read"],
        principal=ExecutionPrincipal(principal_id="u", tenant_id="t", roles=("user",)),
    )
    admin = build_execution_context(
        "manual",
        ["system_help"],
        requested_permissions=["data_management.read"],
        principal=ExecutionPrincipal(principal_id="a", tenant_id="t", roles=("admin",)),
    )
    assert user.effective_permissions == ()
    assert admin.effective_permissions == ("data_management.read",)


def test_restored_context_recomputes_forged_effective_grants(monkeypatch) -> None:
    context = build_execution_context(
        "manual",
        ["system_help"],
        principal=ExecutionPrincipal(
            principal_id="u", tenant_id="tenant-a", roles=("user",)
        ),
    )
    monkeypatch.setattr(
        "app.domain.authorization.get_server_principal", lambda: context.principal
    )
    forged = context.model_dump(mode="json")
    forged["principal"]["tenant_id"] = "tenant-b"
    forged["effective_permissions"] = ["data_management.read"]
    with pytest.raises(PermissionError, match="主体与当前服务端主体不一致"):
        execution_context_from_state(forged)


def test_invalid_server_role_configuration_fails_closed(monkeypatch) -> None:
    monkeypatch.setenv("AGENT_PRINCIPAL_ROLES", "")
    from app.settings import reset_settings

    reset_settings()
    with pytest.raises(RuntimeError, match="必须配置"):
        # Empty configured roles are rejected before any endpoint can authorize.
        from app.domain.authorization import get_server_principal

        get_server_principal()


def test_report_generation_requires_cost_calculation_dependency() -> None:
    prepared = AgentHarness().prepare(
        RuntimeRunRequest(
            question="生成报表",
            requested_capabilities=["report_generation"],
        )
    )
    assert prepared.execution_context.effective_capabilities == ()
