from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Literal, cast

CapabilityId = Literal["system_help", "cost_calculation", "report_generation"]
RouteId = Literal["system_help", "cost_calculation", "report_generation", "blocked"]
RoutingMode = Literal["auto", "manual"]


@dataclass(frozen=True)
class CapabilitySpec:
    id: CapabilityId
    label: str
    route: Literal["system_help", "cost_calculation", "report_generation"]
    read_only: bool
    deterministic: bool
    mode: str
    required_slots: tuple[str, ...]
    status_plan: str
    status_nodes: tuple[str, ...]
    report_styles: tuple[str, ...] = ()
    required_roles: tuple[str, ...] = ()
    data_scope: str = "none"
    risk_level: str = "low"
    enabled: bool = True
    version: str = "v1"


CAPABILITY_SPECS: tuple[CapabilitySpec, ...] = (
    CapabilitySpec(
        id="system_help",
        label="系统说明",
        route="system_help",
        read_only=True,
        deterministic=True,
        mode="read_only",
        required_slots=(),
        status_plan="system_help",
        status_nodes=(
            "load_conversation_context",
            "policy_gate",
            "select_route",
            "final_answer",
        ),
        required_roles=("system_viewer", "cost_analyst", "agent_admin"),
        data_scope="none",
        risk_level="low",
    ),
    CapabilitySpec(
        id="cost_calculation",
        label="成本核算",
        route="cost_calculation",
        read_only=True,
        deterministic=True,
        mode="read_only_deterministic",
        required_slots=("part", "period_or_date_range"),
        status_plan="cost_calculation",
        status_nodes=(
            "load_conversation_context",
            "policy_gate",
            "select_route",
            "understand_question",
            "merge_context_slots",
            "clarification_gate",
            "resolve_part",
            "load_finished_batches",
            "calculate_cost",
            "build_report_json",
            "final_answer",
        ),
        required_roles=("cost_analyst", "agent_admin"),
        data_scope="published_cost_data",
        risk_level="low",
    ),
    CapabilitySpec(
        id="report_generation",
        label="报表生成",
        route="report_generation",
        read_only=True,
        deterministic=True,
        mode="read_only_deterministic",
        required_slots=("part", "period_or_date_range", "report_style"),
        status_plan="report_generation",
        status_nodes=(
            "load_conversation_context",
            "policy_gate",
            "select_route",
            "understand_question",
            "merge_context_slots",
            "clarification_gate",
            "resolve_part",
            "load_finished_batches",
            "calculate_cost",
            "build_report_json",
            "final_answer",
        ),
        report_styles=("presentation", "period_comparison"),
        required_roles=("cost_analyst", "agent_admin"),
        data_scope="published_cost_data",
        risk_level="low",
    ),
)

CAPABILITY_REGISTRY: dict[CapabilityId, CapabilitySpec] = {
    item.id: item for item in CAPABILITY_SPECS
}
ALL_CAPABILITY_IDS: tuple[CapabilityId, ...] = tuple(
    item.id for item in CAPABILITY_SPECS
)
DEFAULT_AUTO_CAPABILITIES: tuple[CapabilityId, ...] = ALL_CAPABILITY_IDS
DEFAULT_MANUAL_CAPABILITIES: tuple[CapabilityId, ...] = ("system_help",)


def canonical_capability_id(value: str) -> CapabilityId:
    if value not in CAPABILITY_REGISTRY:
        raise ValueError(f"未知能力：{value}")
    return cast(CapabilityId, value)


def validate_capability_ids(
    capabilities: list[str] | tuple[str, ...],
) -> list[CapabilityId]:
    unknown = sorted(
        {
            capability
            for capability in capabilities
            if capability not in CAPABILITY_REGISTRY
        }
    )
    if unknown:
        raise ValueError(f"未知能力：{', '.join(unknown)}")

    normalized: list[CapabilityId] = []
    for capability in capabilities:
        canonical = canonical_capability_id(capability)
        if canonical not in normalized:
            normalized.append(canonical)
    return normalized


def normalize_capabilities(
    routing_mode: RoutingMode,
    enabled_capabilities: list[str] | None = None,
) -> list[CapabilityId]:
    """Normalize the client-requested capability set.

    This function does not grant authorization. The authorization layer later
    intersects this request with the principal and server policy.
    """

    supplied = enabled_capabilities
    if supplied is None:
        supplied = list(
            DEFAULT_AUTO_CAPABILITIES
            if routing_mode == "auto"
            else DEFAULT_MANUAL_CAPABILITIES
        )
    return validate_capability_ids(supplied)


def routes_for_capabilities(capabilities: list[str]) -> list[str]:
    return [
        CAPABILITY_REGISTRY[item].route
        for item in validate_capability_ids(capabilities)
    ]


def capability_manifest(
    effective_capabilities: list[str],
    *,
    requested_capabilities: list[str] | None = None,
    authorized_capabilities: list[str] | None = None,
) -> list[dict[str, Any]]:
    effective = set(validate_capability_ids(effective_capabilities))
    requested = set(
        validate_capability_ids(requested_capabilities or effective_capabilities)
    )
    authorized = set(
        validate_capability_ids(authorized_capabilities or effective_capabilities)
    )
    result: list[dict[str, Any]] = []
    for spec in CAPABILITY_SPECS:
        item = asdict(spec)
        item["required_slots"] = list(spec.required_slots)
        item["report_styles"] = list(spec.report_styles)
        item.pop("status_nodes")
        item.update(
            {
                "requested": spec.id in requested,
                "authorized": spec.id in authorized,
                "enabled": spec.id in effective,
            }
        )
        result.append(item)
    return result
