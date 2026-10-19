from datetime import UTC, datetime
from uuid import uuid4

from fastapi.testclient import TestClient

from app.auth.context import get_current_principal
from app.domain.authorization import (
    ExecutionContext,
    ExecutionPrincipal,
)
from app.main import create_app
from app.repositories.postgres_data_admin_repository import (
    _safe_error_message,
    _safe_source_file,
)
from app.services.data_admin_service import DataAdminService, get_data_admin_service

BATCH_ID = uuid4()
NOW = datetime.now(UTC)


def _batch(batch_id=BATCH_ID, tenant="tenant-a"):
    return {
        "batch_id": batch_id,
        "status": "published",
        "source_system": "erp",
        "source_file": "snapshot.json",
        "source_snapshot_hash": "a" * 64,
        "calculation_rule_version": "cost-v2",
        "total_rows": 12,
        "valid_rows": 12,
        "error_rows": 0,
        "started_at": NOW,
        "finished_at": NOW,
        "created_at": NOW,
    }


class FakeRepository:
    def __init__(self):
        self.batch = _batch()

    def list_batches(self, context: ExecutionContext, *, status, page, page_size):
        if context.principal.tenant_id != "tenant-a":
            return [], 0
        return ([self.batch] if status in (None, "published") else []), 1 if status in (
            None,
            "published",
        ) else 0

    def get_batch(self, context: ExecutionContext, batch_id):
        return (
            {**self.batch, "error_codes": []}
            if context.principal.tenant_id == "tenant-a" and batch_id == BATCH_ID
            else None
        )

    def list_batch_errors(
        self, context: ExecutionContext, batch_id, *, page, page_size
    ):
        if self.get_batch(context, batch_id) is None:
            return None
        return (
            [
                {
                    "error_id": 1,
                    "source_table": "parts",
                    "source_record_id": "P-1",
                    "error_code": "invalid_unit",
                    "error_message": "单位不受支持",
                    "created_at": NOW,
                }
            ],
            1,
        )

    def get_published_snapshot(self, context: ExecutionContext):
        return {
            "batch": self.batch if context.principal.tenant_id == "tenant-a" else None,
            "fact_rows": {
                "parts": 1,
                "cost_events": 2,
                "cost_event_inputs": 1,
                "cost_records": 2,
            },
            "projection_rows": 1,
            "part_count": 1,
            "period_start": "2026-06",
            "period_end": "2026-06",
            "rule_version_matches": True,
        }


def _client(principal):
    app = create_app()
    app.dependency_overrides[get_current_principal] = lambda: principal
    app.dependency_overrides[get_data_admin_service] = lambda: DataAdminService(
        FakeRepository()
    )
    return TestClient(app)


def test_viewer_is_denied_on_every_data_admin_endpoint_and_me_is_safe():
    client = _client(
        ExecutionPrincipal(
            principal_id="viewer", tenant_id="tenant-a", roles=("viewer",)
        )
    )
    for path in (
        "/api/data-admin/batches",
        f"/api/data-admin/batches/{BATCH_ID}",
        f"/api/data-admin/batches/{BATCH_ID}/errors",
        "/api/data-admin/published",
        "/api/data-admin/quality",
    ):
        response = client.get(path)
        assert response.status_code == 403
        assert response.json()["detail"]["code"] == "data_management_access_denied"
        assert response.headers["X-Request-ID"]
    me = client.get("/api/me")
    assert me.status_code == 200
    assert me.json()["roles"] == ["viewer"]
    assert me.json()["permissions"] == ["cost_data.read"]
    assert "tenant_id" not in me.json()


def test_tenant_admin_reads_tenant_scoped_batches_snapshot_and_errors():
    client = _client(
        ExecutionPrincipal(
            principal_id="tenant-admin", tenant_id="tenant-a", roles=("tenant_admin",)
        )
    )
    batches = client.get("/api/data-admin/batches?page=1&page_size=10")
    detail = client.get(f"/api/data-admin/batches/{BATCH_ID}")
    errors = client.get(f"/api/data-admin/batches/{BATCH_ID}/errors")
    published = client.get("/api/data-admin/published")
    quality = client.get("/api/data-admin/quality")

    assert (
        batches.status_code
        == detail.status_code
        == errors.status_code
        == published.status_code
        == quality.status_code
        == 200
    )
    assert batches.json()["items"][0]["source_snapshot_hash"] == "a" * 64
    assert detail.json()["error_codes"] == []
    assert "raw_payload" not in errors.json()["items"][0]
    assert published.json()["projection_rows"] == 1
    assert quality.json()["rule_version_matches"] is True


def test_database_factory_error_is_stable_503(monkeypatch):
    client_app = create_app()
    client_app.dependency_overrides[get_current_principal] = lambda: ExecutionPrincipal(
        principal_id="tenant-admin", tenant_id="tenant-a", roles=("tenant_admin",)
    )
    client_app.dependency_overrides[get_data_admin_service] = lambda: DataAdminService()
    monkeypatch.setattr(
        "app.services.data_admin_service.get_data_admin_repository",
        lambda: (_ for _ in ()).throw(RuntimeError("COST_DATABASE_URL secret")),
    )
    response = TestClient(client_app, raise_server_exceptions=False).get(
        "/api/data-admin/batches"
    )
    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "database_unavailable"
    assert "COST_DATABASE_URL" not in response.text


def test_source_and_error_details_are_redacted():
    canary = "SECRET_CANARY_4f88"
    assert _safe_source_file(r"C:\imports\snapshot.json") == "snapshot.json"
    assert _safe_source_file("/srv/imports/snapshot.json") == "snapshot.json"
    assert canary not in _safe_error_message(f"password={canary} /srv/{canary}.json")


def test_tenant_admin_cannot_cross_tenant_boundary():
    client = _client(
        ExecutionPrincipal(
            principal_id="tenant-admin", tenant_id="tenant-b", roles=("tenant_admin",)
        )
    )
    assert client.get(f"/api/data-admin/batches/{BATCH_ID}").status_code == 404
    assert client.get(f"/api/data-admin/batches/{BATCH_ID}/errors").status_code == 404
    assert client.get("/api/data-admin/batches").json() == {
        "items": [],
        "page": 1,
        "page_size": 20,
        "total": 0,
    }
