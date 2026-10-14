from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Path, Query
from sqlalchemy.exc import SQLAlchemyError

from app.api.errors import domain_http_error
from app.domain.authorization import ExecutionPrincipal, get_server_principal
from app.domain.errors import AgentDomainError, CostProjectionUnavailableError
from app.schemas.data_admin import (
    BatchStatus,
    DataAdminBatchDetail,
    DataAdminBatchList,
    DataAdminErrorList,
    DataQualitySummary,
    PublishedSnapshot,
)
from app.services.data_admin_service import DataAdminService, get_data_admin_service

router = APIRouter()
PrincipalDependency = Annotated[ExecutionPrincipal, Depends(get_server_principal)]
ServiceDependency = Annotated[DataAdminService, Depends(get_data_admin_service)]
PageQuery = Annotated[int, Query(ge=1)]
PageSizeQuery = Annotated[int, Query(ge=1, le=100)]
StatusQuery = Annotated[BatchStatus | None, Query()]


@router.get("/batches", response_model=DataAdminBatchList)
def list_batches(
    principal: PrincipalDependency,
    service: ServiceDependency,
    status: StatusQuery = None,
    page: PageQuery = 1,
    page_size: PageSizeQuery = 20,
) -> DataAdminBatchList:
    return _call(
        lambda: service.list_batches(
            principal, status=status, page=page, page_size=page_size
        )
    )


@router.get("/batches/{batch_id}", response_model=DataAdminBatchDetail)
def get_batch(
    batch_id: Annotated[UUID, Path()],
    principal: PrincipalDependency,
    service: ServiceDependency,
) -> DataAdminBatchDetail:
    return _call(lambda: service.get_batch(principal, batch_id))


@router.get("/batches/{batch_id}/errors", response_model=DataAdminErrorList)
def list_batch_errors(
    batch_id: Annotated[UUID, Path()],
    principal: PrincipalDependency,
    service: ServiceDependency,
    page: PageQuery = 1,
    page_size: PageSizeQuery = 20,
) -> DataAdminErrorList:
    return _call(
        lambda: service.list_errors(principal, batch_id, page=page, page_size=page_size)
    )


@router.get("/published", response_model=PublishedSnapshot)
def published(
    principal: PrincipalDependency, service: ServiceDependency
) -> PublishedSnapshot:
    return _call(lambda: service.published(principal))


@router.get("/quality", response_model=DataQualitySummary)
def quality(
    principal: PrincipalDependency, service: ServiceDependency
) -> DataQualitySummary:
    return _call(lambda: service.quality(principal))


def _call(function):
    try:
        return function()
    except CostProjectionUnavailableError as exc:
        raise HTTPException(
            status_code=503,
            detail={
                "code": "database_unavailable",
                "message": "数据治理服务暂时不可用。",
                "details": {},
            },
        ) from exc
    except AgentDomainError as exc:
        raise domain_http_error(exc) from exc
    except PermissionError as exc:
        raise HTTPException(
            status_code=403,
            detail={
                "code": "data_management_access_denied",
                "message": "当前执行主体无权访问数据治理信息。",
                "details": {},
            },
        ) from exc
    except (SQLAlchemyError, RuntimeError) as exc:
        raise HTTPException(
            status_code=503,
            detail={
                "code": "database_unavailable",
                "message": "数据治理服务暂时不可用。",
                "details": {},
            },
        ) from exc
