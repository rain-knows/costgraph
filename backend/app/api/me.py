from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict

from app.agent.capabilities import CapabilityId
from app.domain.authorization import (
    ExecutionPrincipal,
    PermissionId,
    build_execution_context,
    get_server_principal,
)

router = APIRouter()
PrincipalDependency = Annotated[ExecutionPrincipal, Depends(get_server_principal)]


class MeResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    principal_id: str
    roles: list[str]
    capabilities: list[CapabilityId]
    permissions: list[PermissionId]


@router.get("", response_model=MeResponse)
def me(principal: PrincipalDependency) -> MeResponse:
    context = build_execution_context("auto", principal=principal)
    return MeResponse(
        principal_id=principal.principal_id,
        roles=list(principal.roles),
        capabilities=list(context.effective_capabilities),
        permissions=list(context.authorized_permissions),
    )
