from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query

from app.api.errors import domain_http_error
from app.auth.context import PrincipalDependency
from app.domain.errors import AgentDomainError
from app.schemas.iam import AuditEventList
from app.services.iam_service import IamService, get_iam_service

router = APIRouter()
ServiceDependency = Annotated[IamService, Depends(get_iam_service)]
PageQuery = Annotated[int, Query(ge=1)]
PageSizeQuery = Annotated[int, Query(ge=1, le=100)]
ActorQuery = Annotated[str | None, Query(max_length=128)]
ActionQuery = Annotated[str | None, Query(max_length=32)]


@router.get("/events", response_model=AuditEventList)
def list_audit_events(
    principal: PrincipalDependency,
    service: ServiceDependency,
    page: PageQuery = 1,
    page_size: PageSizeQuery = 20,
    actor_principal_id: ActorQuery = None,
    action: ActionQuery = None,
) -> AuditEventList:
    return _call(
        lambda: service.list_audit_events(
            principal,
            page=page,
            page_size=page_size,
            actor_principal_id=actor_principal_id,
            action=action,
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
                "code": "audit_unavailable",
                "message": "审计服务暂时不可用。",
                "details": {},
            },
        ) from exc
