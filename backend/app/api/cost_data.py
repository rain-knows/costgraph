from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path, Query
from sqlalchemy.exc import SQLAlchemyError

from app.api.errors import domain_http_error
from app.domain.authorization import ExecutionPrincipal, get_server_principal
from app.domain.errors import AgentDomainError
from app.schemas.cost_data import (
    CostOverview,
    ProductPeriodDetail,
    ProductPeriodList,
)
from app.services.cost_data_query_service import (
    CostDataQueryService,
    ProductSort,
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
SortQuery = Annotated[ProductSort, Query()]
PageQuery = Annotated[int, Query(ge=1)]
PageSizeQuery = Annotated[int, Query(ge=1, le=100)]


@router.get("/overview", response_model=CostOverview)
def overview_endpoint(
    period: PeriodQuery,
    principal: PrincipalDependency,
    service: ServiceDependency,
) -> CostOverview:
    return _call(lambda: service.overview(period, principal))


@router.get("/products", response_model=ProductPeriodList)
def products_endpoint(
    period: PeriodQuery,
    principal: PrincipalDependency,
    service: ServiceDependency,
    query: QueryText = None,
    sort: SortQuery = "product_id",
    page: PageQuery = 1,
    page_size: PageSizeQuery = 20,
) -> ProductPeriodList:
    return _call(
        lambda: service.list_products(
            period=period,
            query=query,
            sort=sort,
            page=page,
            page_size=page_size,
            principal=principal,
        )
    )


@router.get("/products/{product_id}", response_model=ProductPeriodDetail)
def product_detail_endpoint(
    product_id: Annotated[str, Path(min_length=1, max_length=64)],
    period: PeriodQuery,
    principal: PrincipalDependency,
    service: ServiceDependency,
) -> ProductPeriodDetail:
    return _call(lambda: service.product_detail(product_id, period, principal))


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
