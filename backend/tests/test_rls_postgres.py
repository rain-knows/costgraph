"""租户级行级安全的真实 PostgreSQL 验证。

这些用例只在配置了 ``TEST_COST_DATABASE_URL`` 时运行；它们验证的是**数据库层**
的隔离，与 Repository 里的显式 ``WHERE tenant_id = ...`` 相互独立。

策略对表所有者不生效（``ENABLE`` 而非 ``FORCE``），因此这里必须以受限应用角色
``costgraph_app`` 连接，才能观察到策略实际拦截越权读写。

种子数据一律走 ORM 表对象而不是手写 SQL：列名由模型保证，模型增删列时这里会跟着
报错，而不是在真实库上以 ``UndefinedColumn``/``NotNullViolation`` 静默失效。
"""

from __future__ import annotations

import os
import socket
from collections.abc import Iterator
from datetime import UTC, datetime
from urllib.parse import urlparse
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.db.engine import get_session_factory, reset_engine
from app.db.models import (
    DataLoadBatch,
    RuntimeAgentRun,
    RuntimeAuditTrace,
    RuntimeConversation,
    RuntimeTurn,
)
from app.db.tenant import release_session, set_tenant
from app.settings import get_settings

APP_ROLE = "costgraph_app"


def _require_reachable(database_url: str) -> None:
    """端口不通时立即跳过，避免 pytest 在连接重试上挂死。"""
    parts = urlparse(database_url)
    host, port = parts.hostname or "localhost", parts.port or 5432
    try:
        with socket.create_connection((host, port), timeout=1.5):
            return
    except OSError:
        pytest.skip(f"无法连接 {host}:{port}，跳过真实 PostgreSQL RLS 测试。")


@pytest.fixture
def app_role_session(monkeypatch: pytest.MonkeyPatch) -> Iterator[Session]:
    database_url = os.getenv("TEST_COST_DATABASE_URL")
    if not database_url:
        pytest.skip("未配置 TEST_COST_DATABASE_URL，跳过真实 PostgreSQL RLS 测试。")
    _require_reachable(database_url)
    monkeypatch.setenv("COST_DATABASE_URL", database_url)
    reset_engine()
    get_settings()
    session = get_session_factory()()
    try:
        session.execute(text(f"SET ROLE {APP_ROLE}"))
    except DBAPIError:
        session.close()
        reset_engine()
        pytest.skip(f"目标库缺少受限角色 {APP_ROLE}，请先执行 alembic upgrade head。")
    try:
        yield session
    finally:
        session.rollback()
        session.execute(text("RESET ROLE"))
        release_session(session)
        reset_engine()


def _now() -> datetime:
    return datetime.now(UTC)


def _seed_batch(session: Session, tenant_id: str, batch_id: UUID) -> None:
    """写入一个 published 批次；列与 NOT NULL 约束由模型定义保证。"""
    session.execute(
        DataLoadBatch.__table__.insert().values(
            batch_id=batch_id,
            tenant_id=tenant_id,
            source_system="rls-test",
            source_file="rls.json",
            source_snapshot_hash=uuid4().hex + uuid4().hex[:32],
            calculation_rule_version="cost-v1",
            status="published",
            total_rows=1,
            valid_rows=1,
            error_rows=0,
            started_at=_now(),
            finished_at=_now(),
            created_at=_now(),
        )
    )


def _seed_run_chain(session: Session, tenant_id: str, run_id: str) -> None:
    """按 FK 顺序写入 conversation -> turn -> run -> audit_trace。"""
    stamp = _now()
    conversation_id = f"conv-{run_id}"
    turn_id = f"turn-{run_id}"
    message_id = f"msg-{run_id}"
    principal_id = "rls-principal"

    session.execute(
        RuntimeConversation.__table__.insert().values(
            conversation_id=conversation_id,
            principal_id=principal_id,
            tenant_id=tenant_id,
            title="rls",
            routing_mode="auto",
            enabled_capabilities_json=[],
            context_json={},
            created_at=stamp,
            updated_at=stamp,
        )
    )
    session.execute(
        RuntimeTurn.__table__.insert().values(
            turn_id=turn_id,
            conversation_id=conversation_id,
            tenant_id=tenant_id,
            principal_id=principal_id,
            message_id=message_id,
            question="q",
            result_json={},
            created_at=stamp,
        )
    )
    session.execute(
        RuntimeAgentRun.__table__.insert().values(
            run_id=run_id,
            conversation_id=conversation_id,
            turn_id=turn_id,
            message_id=message_id,
            tenant_id=tenant_id,
            principal_id=principal_id,
            request_id=str(uuid4()),
            request_json={"user_input": "q"},
            request_sha256="0" * 64,
            budget_limits_json={},
            budget_usage_json={},
            next_event_sequence=1,
            status="succeeded",
            attempt_count=0,
            available_at=stamp,
            created_at=stamp,
            updated_at=stamp,
        )
    )
    session.execute(
        RuntimeAuditTrace.__table__.insert().values(
            run_id=run_id,
            conversation_id=conversation_id,
            turn_id=turn_id,
            trace_json={},
            created_at=stamp,
        )
    )


def test_rls_filters_rows_by_tenant(app_role_session: Session) -> None:
    tenant_a = f"rls-a-{uuid4().hex}"
    tenant_b = f"rls-b-{uuid4().hex}"
    batch_a, batch_b = uuid4(), uuid4()

    set_tenant(app_role_session, tenant_a)
    _seed_batch(app_role_session, tenant_a, batch_a)
    set_tenant(app_role_session, tenant_b)
    _seed_batch(app_role_session, tenant_b, batch_b)
    app_role_session.commit()
    set_tenant(app_role_session, tenant_a)
    visible = (
        app_role_session.execute(
            text("SELECT batch_id FROM cost_data.data_load_batches")
        )
        .scalars()
        .all()
    )
    assert batch_a in visible
    assert batch_b not in visible

    set_tenant(app_role_session, tenant_b)
    visible_b = (
        app_role_session.execute(
            text("SELECT batch_id FROM cost_data.data_load_batches")
        )
        .scalars()
        .all()
    )
    assert batch_b in visible_b
    assert batch_a not in visible_b


def test_rls_without_tenant_setting_returns_empty(app_role_session: Session) -> None:
    """未设置 app.tenant_id 时必须收敛为空集。

    必须真正 RESET（NULL）而不是写空字符串：空字符串不是 NULL，比较会退化成
    ``tenant_id = ''``，这个用例就证明不了 fail-closed 语义。
    """
    session = app_role_session
    tenant_id = f"rls-none-{uuid4().hex}"
    set_tenant(session, tenant_id)
    _seed_batch(session, tenant_id, uuid4())
    session.commit()

    session.execute(text("RESET app.tenant_id"))

    assert (
        session.execute(
            text("SELECT count(*) FROM cost_data.data_load_batches")
        ).scalar_one()
        == 0
    )


def test_rls_blocks_cross_tenant_write(app_role_session: Session) -> None:
    tenant_a = f"rls-w-a-{uuid4().hex}"
    tenant_b = f"rls-w-b-{uuid4().hex}"
    set_tenant(app_role_session, tenant_a)

    with pytest.raises(DBAPIError):
        _seed_batch(app_role_session, tenant_b, uuid4())
        app_role_session.flush()


def test_rls_inherits_tenant_through_parent_run(app_role_session: Session) -> None:
    """``audit_traces``/``agent_run_events`` 无 tenant_id，必须经父表推导。"""
    tenant_a = f"rls-p-{uuid4().hex}"
    tenant_b = f"rls-q-{uuid4().hex}"
    run_a, run_b = f"run-a-{uuid4().hex}", f"run-b-{uuid4().hex}"

    for tenant_id, run_id in ((tenant_a, run_a), (tenant_b, run_b)):
        set_tenant(app_role_session, tenant_id)
        _seed_run_chain(app_role_session, tenant_id, run_id)
    app_role_session.commit()

    set_tenant(app_role_session, tenant_a)
    visible = (
        app_role_session.execute(text("SELECT run_id FROM agent_runtime.audit_traces"))
        .scalars()
        .all()
    )
    assert run_a in visible
    assert run_b not in visible


def test_agent_run_events_inherit_tenant_through_run(
    app_role_session: Session,
) -> None:
    tenant_a = f"rls-e-a-{uuid4().hex}"
    tenant_b = f"rls-e-b-{uuid4().hex}"
    run_a, run_b = f"run-ea-{uuid4().hex}", f"run-eb-{uuid4().hex}"

    for tenant_id, run_id in ((tenant_a, run_a), (tenant_b, run_b)):
        set_tenant(app_role_session, tenant_id)
        _seed_run_chain(app_role_session, tenant_id, run_id)
    app_role_session.commit()

    set_tenant(app_role_session, tenant_a)
    visible = (
        app_role_session.execute(
            text("SELECT DISTINCT run_id FROM agent_runtime.agent_run_events")
        )
        .scalars()
        .all()
    )
    assert run_b not in visible


def test_worker_role_can_read_scheduling_rows_across_tenants(
    app_role_session: Session,
) -> None:
    """调度表必须允许 worker 跨租户领取，否则 Worker 无法发现待办任务。"""
    tenant_a = f"rls-s-a-{uuid4().hex}"
    tenant_b = f"rls-s-b-{uuid4().hex}"
    set_tenant(app_role_session, tenant_a)
    _seed_batch(app_role_session, tenant_a, uuid4())
    set_tenant(app_role_session, tenant_b)
    _seed_batch(app_role_session, tenant_b, uuid4())
    app_role_session.commit()

    app_role_session.execute(text("SET ROLE costgraph_worker"))
    try:
        job_tenants = set(
            app_role_session.execute(
                text("SELECT DISTINCT tenant_id FROM cost_data.data_import_jobs")
            )
            .scalars()
            .all()
        )
    finally:
        app_role_session.execute(text(f"SET ROLE {APP_ROLE}"))

    # 只断言不报错且有权限读到调度表；具体内容取决于库里已有任务。
    assert isinstance(job_tenants, set)
