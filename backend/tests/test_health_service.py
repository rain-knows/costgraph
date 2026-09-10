from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.exc import OperationalError

from app.db.models import Base
from app.main import create_app
from app.services import health_service
from app.settings import AppSettings


@pytest.fixture
def database_catalog(monkeypatch):
    engine = MagicMock()
    connection = engine.connect.return_value.__enter__.return_value
    connection.execute.return_value = [(health_service.EXPECTED_ALEMBIC_HEAD,)]
    monkeypatch.setattr(health_service, "get_engine", lambda: engine)
    monkeypatch.setattr(health_service, "_worker_status", lambda _: "healthy")
    catalog = {
        (table.schema, table.name): [{"name": column.name} for column in table.columns]
        for table in Base.metadata.tables.values()
    }
    inspector = MagicMock()
    inspector.get_multi_columns.side_effect = lambda **_: catalog
    monkeypatch.setattr(health_service, "inspect", lambda _: inspector)
    return catalog, connection


@pytest.mark.parametrize("missing", ["table", "column"])
def test_matching_revision_with_incomplete_schema_is_not_ready(
    database_catalog, missing, caplog
):
    catalog, _ = database_catalog
    if missing == "table":
        del catalog[("cost_data", "cost_events")]
    else:
        catalog[("agent_output", "artifacts")] = [
            column
            for column in catalog[("agent_output", "artifacts")]
            if column["name"] != "part_id"
        ]
    client = TestClient(create_app(AppSettings(app_env="test")))

    health = client.get("/api/health")
    ready = client.get("/api/readyz")

    assert health.json()["database"] == "reachable"
    assert health.json()["runtime_ready"] is False
    assert health.json()["alembic"] == "schema_mismatch"
    assert ready.status_code == 503
    assert ready.json()["checks"] == {
        "database": "reachable",
        "alembic": "schema_mismatch",
        "worker": "healthy",
    }
    assert "Database schema mismatch" in caplog.text


def test_current_schema_is_ready(database_catalog):
    response = TestClient(create_app(AppSettings(app_env="test"))).get("/api/readyz")
    assert response.status_code == 200
    assert response.json()["checks"]["alembic"] == "head"


def test_old_revision_is_behind(database_catalog):
    _, connection = database_catalog
    connection.execute.return_value = [("old_revision",)]
    assert health_service._migration_status() == "behind"


def test_unreachable_database_is_not_ready(database_catalog):
    _, connection = database_catalog
    connection.execute.side_effect = OperationalError("SELECT 1", {}, Exception())
    response = TestClient(create_app(AppSettings(app_env="test"))).get("/api/readyz")
    assert response.status_code == 503
    assert response.json()["checks"]["database"] == "unreachable"


def test_reflection_failure_does_not_report_ready(database_catalog, monkeypatch):
    def unavailable(_):
        raise OperationalError("catalog", {}, Exception())

    monkeypatch.setattr(health_service, "inspect", unavailable)
    assert health_service._migration_status() == "unavailable"
