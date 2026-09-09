from __future__ import annotations

import json
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, func, select, text, update

import app.repositories.run_repository as run_repository_module
from app.agent.checkpoint import open_postgres_checkpointer
from app.agent.graph import create_cost_agent_graph
from app.db.engine import get_session_factory, reset_engine
from app.db.models import (
    AgentArtifact,
    RuntimeAgentRun,
    RuntimeAgentWorker,
    RuntimeAuditTrace,
    RuntimeConversation,
    RuntimeMessage,
    RuntimeTurn,
)
from app.domain.authorization import ExecutionPrincipal, get_server_principal
from app.domain.errors import AgentDomainError
from app.main import create_app
from app.repositories.postgres_conversation_repository import (
    PostgresConversationRepository,
)
from app.repositories.run_repository import PostgresRunRepository, utc_now
from app.services.health_service import readiness_snapshot
from app.settings import get_settings
from app.worker import AgentWorker

QUESTION = "查询产品A 2026年6月单位成本，并说明成本构成 AUDIT_CANARY_51a2"


@pytest.fixture
def durable_settings(monkeypatch: pytest.MonkeyPatch):
    database_url = os.getenv("TEST_COST_DATABASE_URL")
    if not database_url:
        pytest.skip("未配置 TEST_COST_DATABASE_URL，跳过真实 PostgreSQL durable 测试。")
    monkeypatch.setenv("COST_DATABASE_URL", database_url)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "")
    monkeypatch.setenv("APP_VERSION", "durable-test")
    reset_engine()
    yield get_settings()
    reset_engine()


def test_postgres_worker_claim_checkpoint_recovery_and_atomic_finalize(
    durable_settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tenant_id = durable_settings.agent_tenant_id
    owner_id = f"durable-owner-{uuid4().hex}"
    owner = ExecutionPrincipal(
        principal_id=owner_id,
        tenant_id=tenant_id,
        roles=("cost_analyst",),
    )
    intruder = ExecutionPrincipal(
        principal_id=f"durable-intruder-{uuid4().hex}",
        tenant_id=tenant_id,
        roles=("cost_analyst",),
    )
    conversations = PostgresConversationRepository()
    repository = PostgresRunRepository(settings=durable_settings)
    conversation = conversations.create_conversation(principal=owner)
    conversation_id = conversation["conversation_id"]
    run_id = ""
    try:
        created = repository.create_run(
            conversation_id=conversation_id,
            message_id=f"message-{uuid4().hex}",
            question=QUESTION,
            routing_mode="auto",
            enabled_capabilities=None,
            reply_to_clarification_id=None,
            principal=owner,
            request_id=str(uuid4()),
        )
        run_id = created["run_id"]
        duplicate = repository.create_run(
            conversation_id=conversation_id,
            message_id=created["message_id"],
            question=QUESTION,
            routing_mode="auto",
            enabled_capabilities=None,
            reply_to_clarification_id=None,
            principal=owner,
            request_id=str(uuid4()),
        )
        assert duplicate["run_id"] == run_id
        with pytest.raises(AgentDomainError, match="message_id"):
            repository.create_run(
                conversation_id=conversation_id,
                message_id=created["message_id"],
                question="different content",
                routing_mode="auto",
                enabled_capabilities=None,
                reply_to_clarification_id=None,
                principal=owner,
                request_id=str(uuid4()),
            )

        with ThreadPoolExecutor(max_workers=2) as executor:
            claims = list(
                executor.map(
                    repository.claim_next,
                    ("worker-first", "worker-second"),
                )
            )
        claimed_runs = [item for item in claims if item is not None]
        assert len(claimed_runs) == 1
        claimed = claimed_runs[0]

        first_worker = AgentWorker(durable_settings)
        first_worker.worker_id = claimed["claimed_from"] + "-owner"
        config = {"configurable": {"thread_id": run_id}}
        with open_postgres_checkpointer(durable_settings) as checkpointer:
            graph = create_cost_agent_graph(checkpointer=checkpointer)
            partial = graph.invoke(
                first_worker._initial_state(claimed, run_id),
                config=config,
                interrupt_after=["understand_question"],
            )
            assert (
                sum(
                    event["node"] == "understand_question"
                    for event in partial.get("events", [])
                )
                == 1
            )
            assert checkpointer.get_tuple(config) is not None

        with get_session_factory()() as session:
            session.execute(
                update(RuntimeAgentRun)
                .where(RuntimeAgentRun.run_id == run_id)
                .values(lease_expires_at=utc_now() - timedelta(seconds=1))
            )
            session.commit()

        recovery_worker = AgentWorker(durable_settings)
        recovered = repository.claim_next(recovery_worker.worker_id)
        assert recovered is not None
        assert recovered["run_id"] == run_id
        assert recovered["claimed_from"] == "running"

        original_build_artifact = run_repository_module.build_artifact_record

        def fail_artifact_persistence(*_args, **_kwargs):
            raise RuntimeError("artifact persistence canary")

        monkeypatch.setattr(
            run_repository_module,
            "build_artifact_record",
            fail_artifact_persistence,
        )
        recovery_worker._execute_run(recovered)

        finalizing = repository.get_run(conversation_id, run_id, owner)
        assert finalizing["status"] == "finalizing"
        assert finalizing["attempt_count"] == 2
        with get_session_factory()() as session:
            assert (
                session.scalar(
                    select(func.count())
                    .select_from(RuntimeTurn)
                    .where(RuntimeTurn.turn_id == created["turn_id"])
                )
                == 0
            )
            assert (
                session.scalar(
                    select(func.count())
                    .select_from(RuntimeMessage)
                    .where(RuntimeMessage.turn_id == created["turn_id"])
                )
                == 0
            )
            assert (
                session.scalar(
                    select(func.count())
                    .select_from(AgentArtifact)
                    .where(AgentArtifact.run_id == run_id)
                )
                == 0
            )

        monkeypatch.setattr(
            run_repository_module,
            "build_artifact_record",
            original_build_artifact,
        )
        with get_session_factory()() as session:
            session.execute(
                update(RuntimeAgentRun)
                .where(RuntimeAgentRun.run_id == run_id)
                .values(available_at=utc_now() - timedelta(seconds=1))
            )
            session.commit()
        finalizing_retry = repository.claim_next(recovery_worker.worker_id)
        assert finalizing_retry is not None
        assert finalizing_retry["claimed_from"] == "finalizing"
        assert finalizing_retry["attempt_count"] == 3
        recovery_worker._execute_run(finalizing_retry)

        finished = repository.get_run(conversation_id, run_id, owner)
        assert finished["status"] == "succeeded"
        assert finished["attempt_count"] == 3
        result = finished["result"]
        assert (
            sum(event["node"] == "understand_question" for event in result["events"])
            == 1
        )
        events = repository.list_events(conversation_id, run_id, owner)
        sequences = [event["graph_sequence"] for event in events]
        assert len(sequences) == len(set(sequences))
        assert "AUDIT_CANARY_51a2" not in json.dumps(events, ensure_ascii=False)

        with pytest.raises(AgentDomainError):
            repository.get_run(conversation_id, run_id, intruder)
        with get_session_factory()() as session:
            assert (
                session.scalar(
                    select(func.count())
                    .select_from(RuntimeTurn)
                    .where(RuntimeTurn.turn_id == created["turn_id"])
                )
                == 1
            )
            assert (
                session.scalar(
                    select(func.count())
                    .select_from(RuntimeMessage)
                    .where(RuntimeMessage.turn_id == created["turn_id"])
                )
                == 2
            )
            assert (
                session.scalar(
                    select(func.count())
                    .select_from(AgentArtifact)
                    .where(AgentArtifact.run_id == run_id)
                )
                == 1
            )
            audit = session.scalar(
                select(RuntimeAuditTrace.trace_json).where(
                    RuntimeAuditTrace.run_id == run_id
                )
            )
            assert audit is not None
            assert "AUDIT_CANARY_51a2" not in json.dumps(audit, ensure_ascii=False)
            checkpoint_count = session.scalar(
                text(
                    "SELECT count(*) FROM agent_checkpoint.checkpoints "
                    "WHERE thread_id = :run_id"
                ),
                {"run_id": run_id},
            )
            assert checkpoint_count > 0
            session.execute(
                update(RuntimeAgentRun)
                .where(RuntimeAgentRun.run_id == run_id)
                .values(checkpoint_expires_at=utc_now() - timedelta(seconds=1))
            )
            session.commit()
        assert repository.cleanup_expired_checkpoints() == 1
        with get_session_factory()() as session:
            assert (
                session.scalar(
                    text(
                        "SELECT count(*) FROM agent_checkpoint.checkpoints "
                        "WHERE thread_id = :run_id"
                    ),
                    {"run_id": run_id},
                )
                == 0
            )
            session.execute(
                text(
                    "INSERT INTO agent_checkpoint.checkpoints "
                    "(thread_id, checkpoint_ns, checkpoint_id, type, "
                    "checkpoint, metadata) VALUES "
                    "(:run_id, '', 'delete-cleanup-test', 'json', "
                    "'{}'::jsonb, '{}'::jsonb)"
                ),
                {"run_id": run_id},
            )
            session.commit()
        conversations.delete_conversation(conversation_id, principal=owner)
        with get_session_factory()() as session:
            assert session.get(RuntimeAgentRun, run_id) is None
            assert (
                session.scalar(
                    text(
                        "SELECT count(*) FROM agent_checkpoint.checkpoints "
                        "WHERE thread_id = :run_id"
                    ),
                    {"run_id": run_id},
                )
                == 0
            )
    finally:
        with get_session_factory()() as session:
            if run_id:
                for table_name in (
                    "checkpoint_writes",
                    "checkpoint_blobs",
                    "checkpoints",
                ):
                    session.execute(
                        text(
                            f"DELETE FROM agent_checkpoint.{table_name} "
                            "WHERE thread_id = :run_id"
                        ),
                        {"run_id": run_id},
                    )
            if run_id:
                session.execute(
                    delete(AgentArtifact).where(AgentArtifact.run_id == run_id)
                )
            session.execute(
                delete(RuntimeConversation).where(
                    RuntimeConversation.conversation_id == conversation_id
                )
            )
            session.commit()


def test_durable_run_api_sse_cancel_owner_isolation_and_readiness(
    durable_settings,
) -> None:
    tenant_id = f"durable-api-test-{uuid4().hex}"
    owner = ExecutionPrincipal(
        principal_id="api-owner",
        tenant_id=tenant_id,
        roles=("cost_analyst",),
    )
    intruder = ExecutionPrincipal(
        principal_id="api-intruder",
        tenant_id=tenant_id,
        roles=("cost_analyst",),
    )
    conversations = PostgresConversationRepository()
    repository = PostgresRunRepository(settings=durable_settings)
    conversation = conversations.create_conversation(principal=owner)
    conversation_id = conversation["conversation_id"]
    worker_id = f"worker_api_test_{uuid4().hex}"
    app = create_app(durable_settings)
    app.dependency_overrides[get_server_principal] = lambda: owner
    client = TestClient(app, raise_server_exceptions=False)
    run_id = ""
    repository.register_worker(
        worker_id,
        hostname="test-host",
        process_id=1,
        app_version="durable-test",
    )
    try:
        request_id = str(uuid4())
        message_id = f"message-{uuid4().hex}"
        payload = {
            "message_id": message_id,
            "content": "API cancel canary AUDIT_CANARY_9c31",
        }
        created = client.post(
            f"/api/v2/conversations/{conversation_id}/runs",
            headers={"X-Request-ID": request_id},
            json=payload,
        )
        assert created.status_code == 202
        assert created.headers["X-Request-ID"] == request_id
        body = created.json()
        run_id = body["run_id"]
        assert body["status"] == "queued"
        assert body["events_url"].endswith(f"/{run_id}/events")

        duplicate = client.post(
            f"/api/v2/conversations/{conversation_id}/runs",
            json=payload,
        )
        assert duplicate.status_code == 202
        assert duplicate.json()["run_id"] == run_id

        active = client.get(f"/api/v2/conversations/{conversation_id}/runs/active")
        assert active.status_code == 200
        assert active.json()["run_id"] == run_id

        busy = client.delete(f"/api/conversations/{conversation_id}")
        assert busy.status_code == 409
        assert busy.json()["detail"]["code"] == "conversation_busy"

        cancelled = client.post(
            f"/api/v2/conversations/{conversation_id}/runs/{run_id}/cancel"
        )
        assert cancelled.status_code == 200
        assert cancelled.json()["status"] == "cancelled"
        repeated_cancel = client.post(
            f"/api/v2/conversations/{conversation_id}/runs/{run_id}/cancel"
        )
        assert repeated_cancel.status_code == 200
        assert repeated_cancel.json()["status"] == "cancelled"
        assert (
            client.get(f"/api/v2/conversations/{conversation_id}/runs/active").json()
            is None
        )

        persisted_events = repository.list_events(conversation_id, run_id, owner)
        assert len(persisted_events) == 2
        first_event_id = persisted_events[0]["event_id"]
        replay = client.get(
            f"/api/v2/conversations/{conversation_id}/runs/{run_id}/events",
            params={"after_event_id": 0},
            headers={"Last-Event-ID": str(first_event_id)},
        )
        assert replay.status_code == 200
        assert f"id: {persisted_events[1]['event_id']}" in replay.text
        assert f"id: {first_event_id}" not in replay.text
        assert "AUDIT_CANARY_9c31" not in replay.text

        app.dependency_overrides[get_server_principal] = lambda: intruder
        hidden = client.get(f"/api/v2/conversations/{conversation_id}/runs/{run_id}")
        assert hidden.status_code == 404
        assert hidden.json()["detail"]["code"] == "run_not_found"
        hidden_events = client.get(
            f"/api/v2/conversations/{conversation_id}/runs/{run_id}/events"
        )
        assert hidden_events.status_code == 404

        ready, readiness = readiness_snapshot(durable_settings)
        assert ready is True
        assert readiness["checks"] == {
            "database": "reachable",
            "alembic": "head",
            "worker": "healthy",
        }
        with get_session_factory()() as session:
            session.execute(
                update(RuntimeAgentWorker)
                .where(RuntimeAgentWorker.worker_id == worker_id)
                .values(
                    last_heartbeat_at=utc_now()
                    - timedelta(
                        seconds=durable_settings.agent_run_heartbeat_seconds * 2 + 1
                    )
                )
            )
            session.commit()
        stale_ready, stale_readiness = readiness_snapshot(durable_settings)
        assert stale_ready is False
        assert stale_readiness["checks"]["worker"] == "unavailable"
    finally:
        app.dependency_overrides.clear()
        with get_session_factory()() as session:
            session.execute(
                delete(RuntimeAgentWorker).where(
                    RuntimeAgentWorker.worker_id == worker_id
                )
            )
            session.execute(
                delete(RuntimeConversation).where(
                    RuntimeConversation.tenant_id == tenant_id
                )
            )
            session.commit()


def test_runtime_postgres_budget_events_and_fixed_protocol_version(
    durable_settings,
) -> None:
    tenant_id = f"runtime-test-{uuid4().hex}"
    owner = ExecutionPrincipal(
        principal_id="runtime-owner",
        tenant_id=tenant_id,
        roles=("cost_analyst",),
    )
    conversations = PostgresConversationRepository()
    repository = PostgresRunRepository(settings=durable_settings)
    conversation = conversations.create_conversation(principal=owner)
    conversation_id = conversation["conversation_id"]
    run_id = ""
    try:
        created = repository.create_run(
            conversation_id=conversation_id,
            message_id=f"message-{uuid4().hex}",
            question="查询产品A 2026年6月单位成本",
            routing_mode="auto",
            enabled_capabilities=None,
            reply_to_clarification_id=None,
            principal=owner,
            request_id=str(uuid4()),
        )
        run_id = created["run_id"]
        assert created["schema_version"] == "2.0"
        assert created["api_version"] == "2.0"
        assert created["budget"]["usage"]["model_calls"] == 0
        assert repository.get_active_run(conversation_id, owner)["run_id"] == run_id
        assert (
            repository.get_run(conversation_id, run_id, owner)["api_version"] == "2.0"
        )

        def reserve(index: int):
            return repository.reserve_runtime_call(
                run_id,
                "model",
                event={
                    "event_key": f"{run_id}:attempt-1:model:{index}:start",
                    "name": "parse_cost_question",
                    "summary": "模型调用已开始。",
                    "started_at": utc_now().isoformat(),
                },
            )

        with ThreadPoolExecutor(max_workers=4) as executor:
            reservations = list(executor.map(reserve, range(4)))
        assert sum(item is not None for item in reservations) == 3
        usage = repository.get_runtime_usage(run_id)
        assert usage.model_calls == 3

        events = repository.list_events(conversation_id, run_id, owner)
        sequences = [event["sequence"] for event in events]
        assert sequences == sorted(sequences)
        assert len(sequences) == len(set(sequences))
        start_events = [event for event in events if event["status"] == "running"]
        assert len(start_events) == 3

        duplicate_key = start_events[0]["event_key"]
        duplicate_usage = repository.reserve_runtime_call(
            run_id,
            "model",
            event={
                "event_key": duplicate_key,
                "name": "parse_cost_question",
                "summary": "模型调用已开始。",
            },
        )
        assert duplicate_usage is not None
        assert duplicate_usage.usage.model_calls == 3
        assert repository.get_runtime_usage(run_id).model_calls == 3

        first_finish = repository.append_runtime_event(
            run_id,
            event_key=f"{run_id}:attempt-1:model:finish",
            event_type="node",
            name="parse_cost_question",
            status="success",
            summary="模型调用已完成。",
        )
        repeated_finish = repository.append_runtime_event(
            run_id,
            event_key=f"{run_id}:attempt-1:model:finish",
            event_type="node",
            name="parse_cost_question",
            status="success",
            summary="模型调用已完成。",
        )
        assert repeated_finish["event_id"] == first_finish["event_id"]

        cancelled = repository.cancel(conversation_id, run_id, owner)
        assert cancelled["status"] == "cancelled"
    finally:
        with get_session_factory()() as session:
            session.execute(
                delete(RuntimeConversation).where(
                    RuntimeConversation.tenant_id == tenant_id
                )
            )
            session.commit()
