from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict

from app.agent.capabilities import CapabilityId
from app.auth.context import PrincipalDependency
from app.domain.authorization import PermissionId, build_execution_context

router = APIRouter()


class MeResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    principal_id: str
    roles: list[str]
    capabilities: list[CapabilityId]
    permissions: list[PermissionId]
    data_scope: dict[str, object]


@router.get("", response_model=MeResponse)
def me(principal: PrincipalDependency) -> MeResponse:
    context = build_execution_context("auto", principal=principal)
    return MeResponse(
        principal_id=principal.principal_id,
        roles=list(principal.roles),
        capabilities=list(context.effective_capabilities),
        permissions=list(context.authorized_permissions),
        data_scope=context.data_scope.model_dump(mode="json"),
    )
