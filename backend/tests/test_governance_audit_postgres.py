from __future__ import annotations

import json
import os
from uuid import uuid4

import pytest
from sqlalchemy import delete, select, text, update
from sqlalchemy.exc import DBAPIError

from app.db.engine import get_session_factory, reset_engine
from app.db.models import GovernanceAuditEvent
from app.repositories.postgres_import_job_repository import (
    PostgresImportJobRepository,
)


@pytest.fixture
def audit_repository(monkeypatch: pytest.MonkeyPatch):
    database_url = os.getenv("TEST_COST_DATABASE_URL")
    if not database_url:
        pytest.skip("未配置 TEST_COST_DATABASE_URL，跳过真实 PostgreSQL 审计测试。")
    monkeypatch.setenv("COST_DATABASE_URL", database_url)
    reset_engine()
    tenant_id = f"audit-test-{uuid4().hex}"
    repository = PostgresImportJobRepository()
    yield repository, tenant_id
    # 审计表只允许追加，清理必须显式绕开保护，否则测试会永久留脏数据。
    with get_session_factory()() as session:
        session.execute(
            text(
                "ALTER TABLE cost_data.governance_audit_events "
                "DISABLE TRIGGER trg_governance_audit_events_append_only"
            )
        )
        session.execute(
            delete(GovernanceAuditEvent).where(
                GovernanceAuditEvent.tenant_id == tenant_id
            )
        )
        session.execute(
            text(
                "ALTER TABLE cost_data.governance_audit_events "
                "ENABLE TRIGGER trg_governance_audit_events_append_only"
            )
        )
        session.commit()
    reset_engine()


def _append(repository, tenant_id: str, **overrides):
    payload = {
        "tenant_id": tenant_id,
        "actor_principal_id": "audit-actor",
        "action": "submitted",
        "target_type": "import_job",
        "target_id": str(uuid4()),
        "outcome": "success",
        "detail": {"status": "submitted"},
    }
    payload.update(overrides)
    repository.append_audit_event(**payload)


def test_audit_events_cannot_be_updated_or_deleted(audit_repository) -> None:
    repository, tenant_id = audit_repository
    _append(repository, tenant_id)

    with get_session_factory()() as session:
        event_id = session.scalar(
            select(GovernanceAuditEvent.event_id).where(
                GovernanceAuditEvent.tenant_id == tenant_id
            )
        )
    assert event_id is not None

    with (
        pytest.raises(DBAPIError, match="只允许追加"),
        get_session_factory()() as session,
    ):
        session.execute(
            update(GovernanceAuditEvent)
            .where(GovernanceAuditEvent.event_id == event_id)
            .values(actor_principal_id="tampered")
        )
        session.commit()

    with (
        pytest.raises(DBAPIError, match="只允许追加"),
        get_session_factory()() as session,
    ):
        session.execute(
            delete(GovernanceAuditEvent).where(
                GovernanceAuditEvent.event_id == event_id
            )
        )
        session.commit()

    with get_session_factory()() as session:
        row = session.get(GovernanceAuditEvent, event_id)
        assert row is not None
        assert row.actor_principal_id == "audit-actor"
        assert row.action == "submitted"


def test_audit_detail_drops_raw_payload_and_secrets(audit_repository) -> None:
    repository, tenant_id = audit_repository
    _append(
        repository,
        tenant_id,
        detail={
            "status": "submitted",
            "total_rows": 12,
            "raw_payload": {"cost_records": [{"amount": "1.00"}]},
            "access_token": "Bearer sk-live-secret",
            "source_object_key": "imports/secret.json",
            "model_text": "模型正文",
        },
    )

    with get_session_factory()() as session:
        detail = session.scalar(
            select(GovernanceAuditEvent.detail).where(
                GovernanceAuditEvent.tenant_id == tenant_id
            )
        )
    assert detail == {"status": "submitted", "total_rows": 12}
    serialized = json.dumps(detail, ensure_ascii=False)
    for forbidden in ("raw_payload", "Bearer", "sk-live-secret", "模型正文"):
        assert forbidden not in serialized
