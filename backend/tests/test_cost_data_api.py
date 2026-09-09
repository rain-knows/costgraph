from decimal import Decimal

from fastapi.testclient import TestClient

from app.domain.authorization import ExecutionPrincipal, get_server_principal
from app.main import create_app
from app.services.cost_data_query_service import (
    CostDataQueryService,
    get_cost_data_query_service,
)
from app.settings import AppSettings
from evaluation.fixture_cost_repository import CostFixtureRepository

PRINCIPAL = ExecutionPrincipal(
    principal_id="test-user",
    tenant_id="test-tenant",
    roles=("cost_analyst",),
)


def _service() -> CostDataQueryService:
    return CostDataQueryService(CostFixtureRepository())


def _client(principal: ExecutionPrincipal = PRINCIPAL) -> TestClient:
    app = create_app(AppSettings(app_env="test"))
    app.dependency_overrides[get_cost_data_query_service] = _service
    app.dependency_overrides[get_server_principal] = lambda: principal
    return TestClient(app)


def test_product_a_june_acceptance_values_are_exact() -> None:
    detail = _service().product_detail("P001", "2026-06", PRINCIPAL)

    assert detail.total_cost == Decimal("139000.00")
    assert detail.unit_cost == Decimal("13.90")
    assert detail.output_qty == Decimal("10000.00")
    assert [item.total_cost for item in detail.processes] == [
        Decimal("61200.00"),
        Decimal("29000.00"),
        Decimal("26400.00"),
        Decimal("22400.00"),
    ]
    assert detail.source_summary.production_output_count > 0
    assert detail.source_summary.process_cost_entry_count > 0


def test_cost_data_endpoints_return_decimal_strings_and_pagination() -> None:
    client = _client()

    overview = client.get("/api/cost-data/overview?period=2026-06")
    products = client.get(
        "/api/cost-data/products",
        params={
            "period": "2026-06",
            "sort": "total_cost_desc",
            "page": 1,
            "page_size": 2,
        },
    )
    detail = client.get("/api/cost-data/products/P001?period=2026-06")

    assert overview.status_code == 200
    assert isinstance(overview.json()["total_cost"], str)
    assert products.status_code == 200
    assert products.json()["page"] == 1
    assert products.json()["page_size"] == 2
    assert len(products.json()["items"]) == 2
    assert detail.status_code == 200
    assert detail.json()["total_cost"] == "139000.00"
    assert detail.json()["unit_cost"] == "13.90"
    assert detail.json()["product"]["product_name"] == "产品A"


def test_cost_data_query_filter_and_missing_product() -> None:
    client = _client()
    filtered = client.get(
        "/api/cost-data/products",
        params={"period": "2026-06", "query": "产品A"},
    )
    missing = client.get("/api/cost-data/products/P999?period=2026-06")

    assert [item["product_id"] for item in filtered.json()["items"]] == ["P001"]
    assert missing.status_code == 404
    assert missing.json()["detail"]["code"] == "cost_data_not_found"


def test_cost_data_rejects_invalid_period_and_unauthorized_role() -> None:
    invalid = _client().get("/api/cost-data/overview?period=2026-13")
    viewer = ExecutionPrincipal(
        principal_id="viewer", tenant_id="test-tenant", roles=("system_viewer",)
    )
    denied = _client(viewer).get("/api/cost-data/overview?period=2026-06")

    assert invalid.status_code == 422
    assert denied.status_code == 403
    assert denied.json()["detail"]["code"] == "cost_data_access_denied"
