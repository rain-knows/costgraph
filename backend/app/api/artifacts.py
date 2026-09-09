from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query

from app.api.errors import domain_http_error
from app.domain.authorization import ExecutionPrincipal, get_server_principal
from app.domain.errors import AgentDomainError
from app.schemas.artifact import (
    ArtifactDetailResponse,
    ArtifactListResponse,
    ArtifactSummaryResponse,
)
from app.services.artifact_service import (
    get_artifact,
    list_artifacts,
    restore_artifact,
    trash_artifact,
)

router = APIRouter()
PrincipalDependency = Annotated[ExecutionPrincipal, Depends(get_server_principal)]


def _http_error(exc: AgentDomainError) -> HTTPException:
    return domain_http_error(exc)


@router.get("", response_model=ArtifactListResponse)
def list_artifacts_endpoint(
    principal: PrincipalDependency,
    state: Literal["active", "trashed"] = Query(default="active"),
    query: str | None = Query(default=None, max_length=200),
    period: str | None = Query(default=None, max_length=64),
    run_id: str | None = Query(default=None, max_length=128),
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
) -> ArtifactListResponse:
    try:
        return ArtifactListResponse(
            **list_artifacts(
                state=state,
                query=query,
                period=period,
                run_id=run_id,
                limit=limit,
                offset=offset,
                principal=principal,
            )
        )
    except AgentDomainError as exc:
        raise _http_error(exc) from exc


@router.delete("/{artifact_id}", status_code=204)
def trash_artifact_endpoint(
    artifact_id: str,
    principal: PrincipalDependency,
) -> None:
    try:
        trash_artifact(artifact_id, principal)
    except AgentDomainError as exc:
        raise _http_error(exc) from exc


@router.post("/{artifact_id}/restore", response_model=ArtifactSummaryResponse)
def restore_artifact_endpoint(
    artifact_id: str,
    principal: PrincipalDependency,
) -> ArtifactSummaryResponse:
    try:
        return ArtifactSummaryResponse(**restore_artifact(artifact_id, principal))
    except AgentDomainError as exc:
        raise _http_error(exc) from exc


@router.get("/{artifact_id}", response_model=ArtifactDetailResponse)
def get_artifact_endpoint(
    artifact_id: str,
    principal: PrincipalDependency,
) -> ArtifactDetailResponse:
    try:
        return ArtifactDetailResponse(**get_artifact(artifact_id, principal))
    except AgentDomainError as exc:
        raise _http_error(exc) from exc
