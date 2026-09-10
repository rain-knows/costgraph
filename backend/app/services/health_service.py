from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any

from sqlalchemy import inspect, select, text
from sqlalchemy.exc import SQLAlchemyError

from app.db.engine import get_engine, get_session_factory
from app.db.models import Base, RuntimeAgentWorker
from app.repositories.run_repository import utc_now
from app.settings import AppSettings, get_settings

EXPECTED_ALEMBIC_HEAD = "20260907_0001"
LOGGER = logging.getLogger(__name__)


def health_snapshot(settings: AppSettings | None = None) -> dict[str, Any]:
    configured = settings or get_settings()
    database = "unreachable"
    try:
        with get_engine().connect() as connection:
            connection.execute(text("SELECT 1"))
        database = "reachable"
    except (RuntimeError, SQLAlchemyError):
        pass
    worker = _worker_status(configured)
    migration = _migration_status() if database == "reachable" else "not_required"
    runtime_ready = (
        database == "reachable" and migration == "head" and worker == "healthy"
    )
    return {
        "status": "ok" if database != "unreachable" else "degraded",
        "service": "costgraph",
        "app_env": configured.app_env,
        "app_version": configured.app_version,
        "execution_backend": "postgres_worker",
        "durable_runs": True,
        "database": database,
        "alembic": migration,
        "worker": worker,
        "runtime_api_versions": ["2.0"],
        "preferred_runtime_api": "2.0",
        "runtime_ready": runtime_ready,
    }


def readiness_snapshot(
    settings: AppSettings | None = None,
) -> tuple[bool, dict[str, Any]]:
    configured = settings or get_settings()
    snapshot = health_snapshot(configured)
    checks: dict[str, str] = {"database": snapshot["database"]}
    ready = snapshot["database"] != "unreachable"
    if snapshot["database"] == "reachable":
        migration = snapshot["alembic"]
        checks["alembic"] = migration
        ready = ready and migration == "head"
    else:
        checks["alembic"] = "not_required"
    checks["worker"] = snapshot["worker"]
    ready = ready and snapshot["worker"] == "healthy"
    return ready, {
        "status": "ready" if ready else "not_ready",
        "service": "costgraph",
        "app_env": configured.app_env,
        "execution_backend": "postgres_worker",
        "durable_runs": True,
        "checks": checks,
    }


def _worker_status(settings: AppSettings) -> str:
    try:
        with get_session_factory()() as session:
            cutoff = utc_now() - timedelta(
                seconds=settings.agent_run_heartbeat_seconds * 2
            )
            row = session.scalar(
                select(RuntimeAgentWorker.worker_id)
                .where(
                    RuntimeAgentWorker.status == "running",
                    RuntimeAgentWorker.last_heartbeat_at >= cutoff,
                )
                .limit(1)
            )
            return "healthy" if row else "unavailable"
    except (RuntimeError, SQLAlchemyError):
        return "unavailable"


def _migration_status() -> str:
    try:
        with get_engine().connect() as connection:
            current = {
                str(row[0])
                for row in connection.execute(
                    text("SELECT version_num FROM alembic_version")
                )
            }
            if current != {EXPECTED_ALEMBIC_HEAD}:
                return "behind"
            # A revision stamp alone cannot detect an outdated development
            # baseline. Reflect required tables/columns without reading data.
            inspector = inspect(connection)
            tables = list(Base.metadata.tables.values())
            for schema in sorted({table.schema for table in tables}):
                required = [table for table in tables if table.schema == schema]
                columns = inspector.get_multi_columns(
                    schema=schema, filter_names=[table.name for table in required]
                )
                for table in required:
                    actual = {
                        column["name"]
                        for column in columns.get((schema, table.name), [])
                    }
                    missing = set(table.columns.keys()) - actual
                    if missing:
                        LOGGER.error(
                            "Database schema mismatch: %s missing columns: %s. "
                            "Initialize a fresh database with the current Alembic "
                            "baseline; see docs/operations/runbook.md.",
                            table.fullname,
                            ", ".join(sorted(missing)),
                        )
                        return "schema_mismatch"
        return "head"
    except (RuntimeError, SQLAlchemyError):
        return "unavailable"
