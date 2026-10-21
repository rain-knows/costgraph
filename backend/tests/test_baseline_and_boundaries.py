import re
from pathlib import Path

from app.db.models import Base
from app.main import app
from app.repositories.artifact_repository import get_artifact_repository
from app.repositories.conversation_repository import get_conversation_repository
from app.repositories.cost_repository import get_cost_repository
from app.repositories.data_admin_repository import get_data_admin_repository

BACKEND = Path(__file__).resolve().parents[1]
BASELINE_FILE = "20260907_0001_costgraph_baseline.py"
#: 最新迁移 head；允许在 baseline 之上叠加。
EXPECTED_HEAD = "20261021_0007"


def _migration_heads() -> list[str]:
    """返回 Alembic 迁移图的 head revision 集合（无后继的 revision）。"""
    versions = sorted((BACKEND / "alembic" / "versions").glob("*.py"))
    revisions: dict[str, str | None] = {}
    for path in versions:
        source = path.read_text(encoding="utf-8")
        revision = re.search(r'^revision:\s*str\s*=\s*"([^"]+)"', source, re.MULTILINE)
        assert revision, f"迁移缺少 revision：{path.name}"
        down_revision = re.search(
            r'^down_revision:\s*str\s*\|\s*None\s*=\s*"([^"]+)"',
            source,
            re.MULTILINE,
        )
        revisions[revision.group(1)] = down_revision.group(1) if down_revision else None
    referenced = {value for value in revisions.values() if value}
    return sorted(set(revisions) - referenced)


def test_baseline_exists_and_migration_head_matches_runtime_table_set() -> None:
    versions = sorted((BACKEND / "alembic" / "versions").glob("*.py"))
    names = {path.name for path in versions}
    # baseline 必须是迁移图根节点，增量迁移允许叠加在它之上。
    assert BASELINE_FILE in names
    source = (BACKEND / "alembic" / "versions" / BASELINE_FILE).read_text(
        encoding="utf-8"
    )
    assert 'revision: str = "20260907_0001"' in source
    assert "down_revision: str | None = None" in source
    assert "op.create_table" in source
    assert "op.create_index" in source
    assert "Base.metadata" not in source
    assert "create_all" not in source
    assert _migration_heads() == [EXPECTED_HEAD]
    assert set(Base.metadata.tables) == {
        "agent_checkpoint.checkpoint_blobs",
        "agent_checkpoint.checkpoint_migrations",
        "agent_checkpoint.checkpoint_writes",
        "agent_checkpoint.checkpoints",
        "agent_output.artifacts",
        "agent_runtime.agent_run_events",
        "agent_runtime.agent_runs",
        "agent_runtime.agent_workers",
        "agent_runtime.audit_traces",
        "agent_runtime.conversations",
        "agent_runtime.messages",
        "agent_runtime.turns",
        "cost_data.data_import_jobs",
        "cost_data.data_load_batches",
        "cost_data.data_load_errors",
        "cost_data.governance_audit_events",
        "cost_data.parts",
        "cost_data.cost_events",
        "cost_data.cost_event_inputs",
        "cost_data.cost_records",
        "cost_data.finished_batch_cost_projections",
        "cost_data.iam_principal_roles",
        "cost_data.iam_data_scopes",
    }


def test_production_app_exposes_only_canonical_api_paths() -> None:
    api_paths = {route.path for route in app.routes if route.path.startswith("/api/")}
    assert api_paths == {
        "/api/artifacts",
        "/api/artifacts/{artifact_id}",
        "/api/artifacts/{artifact_id}/restore",
        "/api/conversations",
        "/api/conversations/{conversation_id}",
        "/api/conversations/{conversation_id}/messages",
        "/api/cost-data/overview",
        "/api/cost-data/finished-batches",
        "/api/cost-data/finished-batches/{finished_batch_id}",
        "/api/data-admin/batches",
        "/api/data-admin/batches/{batch_id}",
        "/api/data-admin/batches/{batch_id}/errors",
        "/api/data-admin/imports",
        "/api/data-admin/imports/{job_id}",
        "/api/data-admin/imports/{job_id}/cancel",
        "/api/data-admin/imports/{job_id}/decision",
        "/api/data-admin/imports/{job_id}/errors",
        "/api/data-admin/published",
        "/api/data-admin/quality",
        "/api/audit/events",
        "/api/iam/principals",
        "/api/iam/principals/{principal_id}/roles",
        "/api/iam/principals/{principal_id}/scope",
        "/api/me",
        "/api/health",
        "/api/livez",
        "/api/readyz",
        "/api/v2/conversations/{conversation_id}/runs",
        "/api/v2/conversations/{conversation_id}/runs/active",
        "/api/v2/conversations/{conversation_id}/runs/{run_id}",
        "/api/v2/conversations/{conversation_id}/runs/{run_id}/cancel",
        "/api/v2/conversations/{conversation_id}/runs/{run_id}/events",
    }


def test_repository_factories_use_postgresql_implementations(monkeypatch) -> None:
    session_factory = object()
    monkeypatch.setattr(
        "app.repositories.postgres_cost_repository.get_session_factory",
        lambda: session_factory,
    )
    monkeypatch.setattr(
        "app.repositories.postgres_conversation_repository.get_session_factory",
        lambda: session_factory,
    )
    monkeypatch.setattr(
        "app.repositories.postgres_artifact_repository.get_session_factory",
        lambda: session_factory,
    )
    monkeypatch.setattr(
        "app.repositories.postgres_data_admin_repository.get_session_factory",
        lambda: session_factory,
    )
    assert type(get_cost_repository()).__name__ == "PostgresCostRepository"
    assert type(get_conversation_repository()).__name__ == (
        "PostgresConversationRepository"
    )
    assert type(get_artifact_repository()).__name__ == "PostgresArtifactRepository"
    assert type(get_data_admin_repository()).__name__ == "PostgresDataAdminRepository"
