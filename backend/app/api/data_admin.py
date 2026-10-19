from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Path, Query, Request
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
    ImportDecisionRequest,
    ImportJobCreateRequest,
    ImportJobDetail,
    ImportJobList,
    ImportJobStatus,
    ImportJobSubmission,
    PublishedSnapshot,
)
from app.services.data_admin_service import DataAdminService, get_data_admin_service
from app.services.import_job_service import (
    IDEMPOTENCY_KEY_MAX_LENGTH,
    ImportAccessDeniedError,
    ImportJobService,
    get_import_job_service,
)

router = APIRouter()
PrincipalDependency = Annotated[ExecutionPrincipal, Depends(get_server_principal)]
ServiceDependency = Annotated[DataAdminService, Depends(get_data_admin_service)]
ImportServiceDependency = Annotated[ImportJobService, Depends(get_import_job_service)]
PageQuery = Annotated[int, Query(ge=1)]
PageSizeQuery = Annotated[int, Query(ge=1, le=100)]
StatusQuery = Annotated[BatchStatus | None, Query()]
ImportStatusQuery = Annotated[ImportJobStatus | None, Query()]
IdempotencyKeyHeader = Annotated[str | None, Header(alias="Idempotency-Key")]


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


@router.post("/imports", response_model=ImportJobSubmission)
def submit_import(
    payload: ImportJobCreateRequest,
    request: Request,
    principal: PrincipalDependency,
    service: ImportServiceDependency,
    idempotency_key: IdempotencyKeyHeader = None,
) -> ImportJobSubmission:
    key = _idempotency_key(idempotency_key)
    return _call(
        lambda: service.submit(
            principal, key, payload, request_id=request.state.request_id
        )
    )


@router.get("/imports", response_model=ImportJobList)
def list_imports(
    principal: PrincipalDependency,
    service: ImportServiceDependency,
    status: ImportStatusQuery = None,
    page: PageQuery = 1,
    page_size: PageSizeQuery = 20,
) -> ImportJobList:
    return _call(
        lambda: service.list_jobs(
            principal, status=status, page=page, page_size=page_size
        )
    )


@router.get("/imports/{job_id}", response_model=ImportJobDetail)
def get_import(
    job_id: Annotated[UUID, Path()],
    principal: PrincipalDependency,
    service: ImportServiceDependency,
) -> ImportJobDetail:
    return _call(lambda: service.get_job(principal, job_id))


@router.get("/imports/{job_id}/errors", response_model=DataAdminErrorList)
def list_import_errors(
    job_id: Annotated[UUID, Path()],
    principal: PrincipalDependency,
    service: ImportServiceDependency,
    page: PageQuery = 1,
    page_size: PageSizeQuery = 20,
) -> DataAdminErrorList:
    return _call(
        lambda: service.list_errors(principal, job_id, page=page, page_size=page_size)
    )


@router.post("/imports/{job_id}/decision", response_model=ImportJobDetail)
def decide_import(
    job_id: Annotated[UUID, Path()],
    payload: ImportDecisionRequest,
    request: Request,
    principal: PrincipalDependency,
    service: ImportServiceDependency,
) -> ImportJobDetail:
    return _call(
        lambda: service.decide(
            principal, job_id, payload, request_id=request.state.request_id
        )
    )


@router.post("/imports/{job_id}/cancel", response_model=ImportJobDetail)
def cancel_import(
    job_id: Annotated[UUID, Path()],
    request: Request,
    principal: PrincipalDependency,
    service: ImportServiceDependency,
) -> ImportJobDetail:
    return _call(
        lambda: service.cancel(principal, job_id, request_id=request.state.request_id)
    )


def _idempotency_key(value: str | None) -> str:
    """校验 ``Idempotency-Key`` 请求头；缺失或非法一律 422。"""
    key = (value or "").strip()
    if not key or len(key) > IDEMPOTENCY_KEY_MAX_LENGTH:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "idempotency_key_required",
                "message": "提交数据导入任务必须携带有效的 Idempotency-Key 请求头。",
                "details": {"max_length": IDEMPOTENCY_KEY_MAX_LENGTH},
            },
        )
    return key


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
    except ImportAccessDeniedError as exc:
        raise HTTPException(
            status_code=403,
            detail={
                "code": exc.code,
                "message": exc.message,
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
