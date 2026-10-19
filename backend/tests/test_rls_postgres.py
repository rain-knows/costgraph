"""租户级行级安全的真实 PostgreSQL 验证。

这些用例只在配置了 ``TEST_COST_DATABASE_URL`` 时运行；它们验证的是**数据库层**
的隔离，与 Repository 里的显式 ``WHERE tenant_id = ...`` 相互独立。

策略对表所有者不生效（``ENABLE`` 而非 ``FORCE``），因此这里必须以受限应用角色
``costgraph_app`` 连接，才能观察到策略实际拦截越权读写。
"""

from __future__ import annotations

import os
import socket
from urllib.parse import urlparse
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.db.engine import get_session_factory, reset_engine
from app.db.tenant import release_session, set_tenant
from app.settings import get_settings


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
def app_role_session(monkeypatch: pytest.MonkeyPatch):
    database_url = os.getenv("TEST_COST_DATABASE_URL")
    if not database_url:
        pytest.skip("未配置 TEST_COST_DATABASE_URL，跳过真实 PostgreSQL RLS 测试。")
    _require_reachable(database_url)
    monkeypatch.setenv("COST_DATABASE_URL", database_url)
    reset_engine()
    get_settings()
    session = get_session_factory()()
    # 表所有者不受策略约束，用受限角色观察真实拦截效果。
    session.execute(text("SET ROLE costgraph_app"))
    try:
        yield session
    finally:
        session.rollback()
        session.execute(text("RESET ROLE"))
        release_session(session)


def _seed_batch(session, tenant_id: str, batch_id: str) -> None:
    session.execute(
        text(
            "INSERT INTO cost_data.data_load_batches "
            "(batch_id, tenant_id, source_system, source_file, content_sha256, "
            " calculation_rule_version, status, published_at) "
            "VALUES (:batch_id, :tenant_id, 'rls-test', 'rls.json', :sha, "
            " 'cost-v1', 'published', now())"
        ),
        {"batch_id": batch_id, "tenant_id": tenant_id, "sha": "0" * 64},
    )


def test_rls_filters_rows_by_tenant(app_role_session) -> None:
    tenant_a = f"rls-a-{uuid4().hex}"
    tenant_b = f"rls-b-{uuid4().hex}"
    batch_a, batch_b = str(uuid4()), str(uuid4())

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


def test_rls_without_tenant_setting_returns_empty(app_role_session) -> None:
    session = app_role_session
    session.execute(text("SELECT set_config('app.tenant_id', '', true)"))
    assert (
        session.execute(
            text("SELECT count(*) FROM cost_data.cost_records")
        ).scalar_one()
        == 0
    )


def test_rls_blocks_cross_tenant_write(app_role_session) -> None:
    tenant_a = f"rls-w-a-{uuid4().hex}"
    tenant_b = f"rls-w-b-{uuid4().hex}"
    set_tenant(app_role_session, tenant_a)

    with pytest.raises(DBAPIError):
        _seed_batch(app_role_session, tenant_b, str(uuid4()))
        app_role_session.flush()


def test_rls_inherits_tenant_through_parent_run(app_role_session) -> None:
    """``audit_traces``/``agent_run_events`` 无 tenant_id，必须经父表推导。"""
    tenant_a = f"rls-p-{uuid4().hex}"
    tenant_b = f"rls-q-{uuid4().hex}"
    run_a, run_b = str(uuid4()), str(uuid4())

    for tenant_id, run_id in ((tenant_a, run_a), (tenant_b, run_b)):
        set_tenant(app_role_session, tenant_id)
        app_role_session.execute(
            text(
                "INSERT INTO agent_runtime.agent_runs "
                "(run_id, tenant_id, principal_id, thread_id, user_input, status, "
                " routing_mode, created_at, updated_at) VALUES "
                "(:run_id, :tenant_id, 'p', :thread, 'q', 'succeeded', 'auto', "
                " now(), now())"
            ),
            {"run_id": run_id, "tenant_id": tenant_id, "thread": run_id},
        )
        app_role_session.execute(
            text(
                "INSERT INTO agent_runtime.audit_traces "
                "(run_id, conversation_id, turn_id, trace_json, created_at) "
                "VALUES (:run_id, NULL, NULL, '{}'::jsonb, now())"
            ),
            {"run_id": run_id},
        )
    app_role_session.commit()

    set_tenant(app_role_session, tenant_a)
    visible = (
        app_role_session.execute(text("SELECT run_id FROM agent_runtime.audit_traces"))
        .scalars()
        .all()
    )
    assert run_a in visible
    assert run_b not in visible
