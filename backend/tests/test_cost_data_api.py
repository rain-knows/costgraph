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


def test_golden_finished_batch_has_three_view_values() -> None:
    detail = _service().finished_batch_detail("FG-A-2026-06", PRINCIPAL)

    assert detail.completed_quantity == Decimal("1000.0000")
    assert detail.qualified_quantity == Decimal("950.0000")
    assert detail.defective_quantity == Decimal("50.0000")
    assert detail.quality_rate == Decimal("95.00")
    assert detail.manufacturing_view.total.amount == Decimal("50000.00")
    assert detail.manufacturing_view.total.unit_cost == Decimal("50.00")
    assert detail.material_labor_overhead_view.material.unit_cost == Decimal("20.00")
    assert detail.material_labor_overhead_view.labor.unit_cost == Decimal("12.00")
    assert detail.material_labor_overhead_view.overhead.unit_cost == Decimal("18.00")
    assert detail.variable_fixed_view.variable_cost_1.unit_cost == Decimal("30.00")
    assert detail.variable_fixed_view.fixed_cost_1.unit_cost == Decimal("20.00")
    assert detail.variable_fixed_view.total_cost_2.unit_cost == Decimal("53.50")
    assert detail.trace.root_event_id == "E-FG-001"
    assert len(detail.trace.nodes) >= 1
    assert len(detail.trace.records) >= 1
    assert {edge.input_id for edge in detail.trace.edges} >= {
        "I-RAW-FG1",
        "I-SEMI-FG1",
    }


def test_cost_data_endpoints_return_decimal_strings_and_pagination() -> None:
    client = _client()

    overview = client.get("/api/cost-data/overview?period=2026-06")
    batches = client.get(
        "/api/cost-data/finished-batches",
        params={
            "period": "2026-06",
            "sort": "unit_cost_desc",
            "page": 1,
            "page_size": 2,
        },
    )
    detail = client.get("/api/cost-data/finished-batches/FG-A-2026-06")

    assert overview.status_code == 200
    assert isinstance(overview.json()["total_cost"], str)
    assert batches.status_code == 200
    assert batches.json()["page"] == 1
    assert batches.json()["page_size"] == 2
    assert batches.json()["total"] == 3
    assert len(batches.json()["items"]) == 2
    assert isinstance(
        batches.json()["items"][0]["variable_fixed_view"]["total_cost_2"]["unit_cost"],
        str,
    )
    all_batches = client.get(
        "/api/cost-data/finished-batches",
        params={"period": "2026-06", "page": 1, "page_size": 100},
    ).json()["items"]
    manufacturing = sum(
        (
            Decimal(item["manufacturing_view"]["total"]["amount"])
            for item in all_batches
        ),
        Decimal(0),
    )
    post_manufacturing = sum(
        (
            Decimal(item["variable_fixed_view"]["after_sales_compensation"]["amount"])
            + Decimal(item["variable_fixed_view"]["transportation"]["amount"])
            + Decimal(item["variable_fixed_view"]["storage_fee"]["amount"])
            for item in all_batches
        ),
        Decimal(0),
    )
    assert Decimal(overview.json()["manufacturing_cost"]) == manufacturing
    assert Decimal(overview.json()["post_manufacturing_cost"]) == post_manufacturing
    assert Decimal(overview.json()["total_cost"]) == manufacturing + post_manufacturing
    assert detail.status_code == 200
    assert detail.json()["manufacturing_view"]["total"]["amount"] == "50000.00"
    assert detail.json()["part"]["part_number"] == "FG-001"
    assert detail.json()["trace"]["root_event_id"] == "E-FG-001"


def test_cost_data_query_filter_and_missing_batch() -> None:
    client = _client()
    filtered = client.get(
        "/api/cost-data/finished-batches",
        params={"period": "2026-06", "query": "FG-001"},
    )
    missing = client.get("/api/cost-data/finished-batches/UNKNOWN")

    assert [item["finished_batch_id"] for item in filtered.json()["items"]] == [
        "FG-A-2026-06"
    ]
    assert missing.status_code == 404
    assert missing.json()["detail"]["code"] == "cost_data_not_found"


def test_cost_data_accepts_a_valid_ratio_that_rounds_to_zero() -> None:
    client = _client()
    response = client.get("/api/cost-data/finished-batches/FG-A-2026-07")

    assert response.status_code == 200
    edge = next(
        item
        for item in response.json()["trace"]["edges"]
        if item["input_id"] == "I-SEMI-FG4"
    )
    assert edge["allocation_ratio"] == "0.000000"
    assert edge["allocated_costs"]["total_amount"] == "0.00"


def test_cost_data_rejects_invalid_period_and_unauthorized_role() -> None:
    invalid = _client().get("/api/cost-data/overview?period=2026-13")
    viewer = ExecutionPrincipal(
        principal_id="viewer", tenant_id="test-tenant", roles=("system_viewer",)
    )
    denied = _client(viewer).get("/api/cost-data/overview?period=2026-06")

    assert invalid.status_code == 422
    # A principal without cost_calculation receives a stable authorization error.
    assert denied.status_code == 403
    assert denied.json()["detail"]["code"] == "cost_data_access_denied"
