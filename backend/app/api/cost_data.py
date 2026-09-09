from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path, Query
from sqlalchemy.exc import SQLAlchemyError

from app.api.errors import domain_http_error
from app.domain.authorization import ExecutionPrincipal, get_server_principal
from app.domain.errors import AgentDomainError
from app.schemas.cost_data import (
    CostOverview,
    FinishedBatchCostDetail,
    FinishedBatchCostList,
)
from app.services.cost_data_query_service import (
    CostDataQueryService,
    FinishedBatchSort,
    get_cost_data_query_service,
)

router = APIRouter()
PrincipalDependency = Annotated[ExecutionPrincipal, Depends(get_server_principal)]
ServiceDependency = Annotated[
    CostDataQueryService, Depends(get_cost_data_query_service)
]
PeriodQuery = Annotated[
    str, Query(pattern=r"^\d{4}-(0[1-9]|1[0-2])$", examples=["2026-06"])
]
QueryText = Annotated[str | None, Query(max_length=200)]
SortQuery = Annotated[FinishedBatchSort, Query()]
PageQuery = Annotated[int, Query(ge=1)]
PageSizeQuery = Annotated[int, Query(ge=1, le=100)]


@router.get("/overview", response_model=CostOverview)
def overview_endpoint(
    period: PeriodQuery,
    principal: PrincipalDependency,
    service: ServiceDependency,
) -> CostOverview:
    return _call(lambda: service.overview(period, principal))


@router.get("/finished-batches", response_model=FinishedBatchCostList)
def finished_batches_endpoint(
    period: PeriodQuery,
    principal: PrincipalDependency,
    service: ServiceDependency,
    query: QueryText = None,
    cost_center_code: QueryText = None,
    sort: SortQuery = "completion_time_desc",
    page: PageQuery = 1,
    page_size: PageSizeQuery = 20,
) -> FinishedBatchCostList:
    return _call(
        lambda: service.list_finished_batches(
            period=period,
            query=query,
            cost_center_code=cost_center_code,
            sort=sort,
            page=page,
            page_size=page_size,
            principal=principal,
        )
    )


@router.get(
    "/finished-batches/{finished_batch_id}",
    response_model=FinishedBatchCostDetail,
)
def finished_batch_detail_endpoint(
    finished_batch_id: Annotated[str, Path(min_length=1, max_length=128)],
    principal: PrincipalDependency,
    service: ServiceDependency,
) -> FinishedBatchCostDetail:
    return _call(lambda: service.finished_batch_detail(finished_batch_id, principal))


def _call(function):
    try:
        return function()
    except AgentDomainError as exc:
        raise domain_http_error(exc) from exc
    except PermissionError as exc:
        raise HTTPException(
            status_code=403,
            detail={
                "code": "cost_data_access_denied",
                "message": "当前执行主体无权访问该成本数据。",
                "details": {},
            },
        ) from exc
    except SQLAlchemyError as exc:
        raise HTTPException(
            status_code=503,
            detail={
                "code": "database_unavailable",
                "message": "成本数据服务暂时不可用。",
                "details": {},
            },
        ) from exc
