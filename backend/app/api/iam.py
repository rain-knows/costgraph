from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request

from app.api.errors import domain_http_error
from app.auth.context import PrincipalDependency
from app.domain.errors import AgentDomainError
from app.schemas.iam import (
    IamPrincipal,
    IamPrincipalList,
    RolesUpdateRequest,
    ScopeUpdateRequest,
)
from app.services.iam_service import IamService, get_iam_service

router = APIRouter()
ServiceDependency = Annotated[IamService, Depends(get_iam_service)]
PrincipalIdPath = Annotated[str, Path(min_length=1, max_length=128)]
PageQuery = Annotated[int, Query(ge=1)]
PageSizeQuery = Annotated[int, Query(ge=1, le=100)]


@router.get("/principals", response_model=IamPrincipalList)
def list_principals(
    principal: PrincipalDependency,
    service: ServiceDependency,
    page: PageQuery = 1,
    page_size: PageSizeQuery = 20,
) -> IamPrincipalList:
    return _call(
        lambda: service.list_principals(principal, page=page, page_size=page_size)
    )


@router.put("/principals/{principal_id}/roles", response_model=IamPrincipal)
def set_principal_roles(
    principal_id: PrincipalIdPath,
    payload: RolesUpdateRequest,
    request: Request,
    principal: PrincipalDependency,
    service: ServiceDependency,
) -> IamPrincipal:
    return _call(
        lambda: service.set_roles(
            principal,
            principal_id,
            payload,
            request_id=request.state.request_id,
        )
    )


@router.put("/principals/{principal_id}/scope", response_model=IamPrincipal)
def set_principal_scope(
    principal_id: PrincipalIdPath,
    payload: ScopeUpdateRequest,
    request: Request,
    principal: PrincipalDependency,
    service: ServiceDependency,
) -> IamPrincipal:
    return _call(
        lambda: service.set_scope(
            principal,
            principal_id,
            payload,
            request_id=request.state.request_id,
        )
    )


def _call(function):
    try:
        return function()
    except AgentDomainError as exc:
        raise domain_http_error(exc) from exc
    except HTTPException:
        raise
    except (PermissionError, RuntimeError) as exc:
        raise HTTPException(
            status_code=503,
            detail={
                "code": "iam_unavailable",
                "message": "身份与权限服务暂时不可用。",
                "details": {},
            },
        ) from exc
