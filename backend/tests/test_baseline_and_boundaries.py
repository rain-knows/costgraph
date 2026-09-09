from pathlib import Path

from app.db.models import Base
from app.main import app
from app.repositories.artifact_repository import get_artifact_repository
from app.repositories.conversation_repository import get_conversation_repository
from app.repositories.cost_repository import get_cost_repository

BACKEND = Path(__file__).resolve().parents[1]


def test_single_explicit_baseline_matches_runtime_table_set() -> None:
    versions = sorted((BACKEND / "alembic" / "versions").glob("*.py"))
    assert [path.name for path in versions] == ["20260907_0001_costgraph_baseline.py"]
    source = versions[0].read_text(encoding="utf-8")
    assert 'revision: str = "20260907_0001"' in source
    assert "op.create_table" in source
    assert "op.create_index" in source
    assert "Base.metadata" not in source
    assert "create_all" not in source
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
        "cost_data.data_load_batches",
        "cost_data.data_load_errors",
        "cost_data.process_cost_entries",
        "cost_data.production_outputs",
        "cost_data.products",
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
        "/api/cost-data/products",
        "/api/cost-data/products/{product_id}",
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
    assert type(get_cost_repository()).__name__ == "PostgresCostRepository"
    assert type(get_conversation_repository()).__name__ == (
        "PostgresConversationRepository"
    )
    assert type(get_artifact_repository()).__name__ == "PostgresArtifactRepository"
