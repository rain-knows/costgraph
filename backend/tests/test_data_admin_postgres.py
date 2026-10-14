from __future__ import annotations

import json
import os
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, update

from app.db.engine import get_session_factory, reset_engine
from app.db.models import (
    CostEvent,
    CostEventInput,
    CostRecord,
    DataLoadBatch,
    DataLoadError,
    FinishedBatchCostProjection,
    Part,
)
from app.domain.authorization import (
    DataScope,
    ExecutionPrincipal,
    build_execution_context,
    get_server_principal,
)
from app.domain.cost import CALCULATION_RULE_VERSION
from app.domain.errors import CostProjectionUnavailableError
from app.main import create_app
from app.repositories.postgres_data_admin_repository import (
    PostgresDataAdminRepository,
)
from app.services.cost_data_import_service import CostDataImporter
from app.services.data_admin_service import DataAdminService, get_data_admin_service
from app.settings import get_settings

SAMPLES = Path(__file__).parents[2] / "data" / "samples"
SELECTED_EVENTS = {"E-RAW-001", "E-SEMI-001", "E-INJ-001", "E-PAINT-001", "E-FG-001"}


def _sample_records() -> dict[str, list[dict]]:
    def load(name: str) -> list[dict]:
        return json.loads((SAMPLES / name).read_text(encoding="utf-8"))

    events = [
        row for row in load("cost_events.json") if row["event_id"] in SELECTED_EVENTS
    ]
    part_ids = {row["part_id"] for row in events}
    parts = [row for row in load("parts.json") if row["part_id"] in part_ids]
    inputs = [
        row
        for row in load("cost_event_inputs.json")
        if row["event_id"] in SELECTED_EVENTS
    ]
    records = [
        row for row in load("cost_records.json") if row["event_id"] in SELECTED_EVENTS
    ]
    return {"parts": parts, "events": events, "inputs": inputs, "records": records}


@pytest.fixture
def postgres_data_admin(monkeypatch: pytest.MonkeyPatch):
    database_url = os.getenv("TEST_COST_DATABASE_URL")
    if not database_url:
        pytest.skip("未配置 TEST_COST_DATABASE_URL，跳过真实 PostgreSQL 数据治理测试。")
    monkeypatch.setenv("COST_DATABASE_URL", database_url)
    reset_engine()
    settings = get_settings()
    tenant_id = f"data-admin-test-{uuid4().hex}"
    repository = PostgresDataAdminRepository()
    importer = CostDataImporter()
    records = _sample_records()
    try:
        published = importer.import_records(
            tenant_id=tenant_id,
            source_system="data-admin-integration",
            source_file=r"C:\imports\data-admin-secret.json",
            **records,
        )
        assert published.status == "published"
        failed_records = {
            key: [dict(row) for row in value] for key, value in records.items()
        }
        failed_records["parts"][0]["unit"] = ""
        failed = importer.import_records(
            tenant_id=tenant_id,
            source_system="data-admin-integration-failed",
            source_file="/srv/imports/failed.json",
            **failed_records,
        )
        assert failed.status == "failed"
        yield settings, repository, tenant_id, published.batch_id, failed.batch_id
    finally:
        with get_session_factory()() as session:
            session.execute(
                delete(DataLoadError).where(DataLoadError.tenant_id == tenant_id)
            )
            session.execute(
                delete(FinishedBatchCostProjection).where(
                    FinishedBatchCostProjection.tenant_id == tenant_id
                )
            )
            session.execute(delete(CostRecord).where(CostRecord.tenant_id == tenant_id))
            session.execute(
                delete(CostEventInput).where(CostEventInput.tenant_id == tenant_id)
            )
            session.execute(delete(CostEvent).where(CostEvent.tenant_id == tenant_id))
            session.execute(delete(Part).where(Part.tenant_id == tenant_id))
            session.execute(
                delete(DataLoadBatch).where(DataLoadBatch.tenant_id == tenant_id)
            )
            session.commit()
        reset_engine()


def _context(tenant_id: str, *, scope: DataScope | None = None):
    context = build_execution_context(
        "manual",
        ["system_help"],
        requested_permissions=["data_management.read"],
        principal=ExecutionPrincipal(
            principal_id="data-admin-integration",
            tenant_id=tenant_id,
            roles=("admin",),
        ),
    )
    return context.model_copy(update={"data_scope": scope or context.data_scope})


def test_postgres_data_admin_tenant_pagination_status_and_scope(postgres_data_admin):
    _, repository, tenant_id, published_id, failed_id = postgres_data_admin
    context = _context(tenant_id)

    page, total = repository.list_batches(context, status=None, page=1, page_size=1)
    assert total == 2
    assert len(page) == 1
    second_page, second_total = repository.list_batches(
        context, status=None, page=2, page_size=1
    )
    assert second_total == 2
    assert len(second_page) == 1
    assert second_page[0]["batch_id"] != page[0]["batch_id"]
    published_page, published_total = repository.list_batches(
        context, status="published", page=1, page_size=10
    )
    assert published_total == 1
    assert published_page[0]["batch_id"] == published_id
    failed_detail = repository.get_batch(context, failed_id)
    assert failed_detail is not None
    errors, error_total = repository.list_batch_errors(
        context, failed_id, page=1, page_size=1
    )
    assert error_total >= 1
    assert errors[0]["error_message"] == "导入校验错误详情已脱敏。"
    assert "raw_payload" not in errors[0]
    assert failed_detail["source_file"] == "failed.json"

    scoped = _context(
        tenant_id,
        scope=DataScope(
            allowed_part_ids=("P-FG-001",),
            allowed_period_start="2026-06",
            allowed_period_end="2026-06",
        ),
    )
    snapshot = repository.get_published_snapshot(scoped)
    assert snapshot["batch"]["batch_id"] == published_id
    assert snapshot["projection_rows"] == 1
    assert snapshot["part_count"] == 1
    assert snapshot["fact_rows"]["parts"] == 1
    assert snapshot["fact_rows"]["cost_events"] == 1
    assert snapshot["fact_rows"]["cost_event_inputs"] == 2
    assert snapshot["fact_rows"]["cost_records"] > 0
    assert snapshot["rule_version_matches"] is True

    other = _context(f"other-{tenant_id}")
    assert repository.list_batches(other, status=None, page=1, page_size=10) == ([], 0)
    assert repository.get_batch(other, published_id) is None
    assert repository.list_batch_errors(other, failed_id, page=1, page_size=10) is None


def test_postgres_data_admin_api_uses_real_repository(postgres_data_admin):
    _, repository, tenant_id, published_id, failed_id = postgres_data_admin
    principal = ExecutionPrincipal(
        principal_id="data-admin-api-integration",
        tenant_id=tenant_id,
        roles=("admin",),
    )
    app = create_app()
    app.dependency_overrides[get_server_principal] = lambda: principal
    app.dependency_overrides[get_data_admin_service] = lambda: DataAdminService(
        repository
    )
    try:
        client = TestClient(app)
        response = client.get(
            "/api/data-admin/batches",
            params={"status": "published", "page": 1, "page_size": 10},
        )
        assert response.status_code == 200
        assert response.json()["items"][0]["batch_id"] == str(published_id)
        assert response.headers["X-Request-ID"]
        detail = client.get(f"/api/data-admin/batches/{failed_id}/errors")
        assert detail.status_code == 200
        assert detail.json()["total"] >= 1
    finally:
        app.dependency_overrides.clear()


def test_postgres_data_admin_rejects_mixed_projection_rule(postgres_data_admin):
    _, repository, tenant_id, published_id, _ = postgres_data_admin
    context = _context(tenant_id)
    with get_session_factory()() as session:
        session.execute(
            update(FinishedBatchCostProjection)
            .where(
                FinishedBatchCostProjection.tenant_id == tenant_id,
                FinishedBatchCostProjection.batch_id == published_id,
            )
            .values(rule_version="old-rule")
        )
        session.commit()
    with pytest.raises(CostProjectionUnavailableError):
        repository.get_published_snapshot(context)
    with get_session_factory()() as session:
        session.execute(
            update(FinishedBatchCostProjection)
            .where(
                FinishedBatchCostProjection.tenant_id == tenant_id,
                FinishedBatchCostProjection.batch_id == published_id,
            )
            .values(rule_version=CALCULATION_RULE_VERSION)
        )
        session.commit()
