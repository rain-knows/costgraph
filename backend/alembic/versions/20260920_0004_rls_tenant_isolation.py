"""租户级行级安全与受限应用角色

Revision ID: 20260920_0004
Revises: 20260918_0003
Create Date: 2026-09-20

RLS 是租户隔离的最后一道防线，Repository 的显式 ``WHERE tenant_id = ...``
仍是业务定界，两者必须同时存在。

两张表的策略依赖父表推导租户：
- ``agent_runtime.messages`` 经 ``conversations`` 归属；
- ``agent_runtime.agent_run_events`` 与 ``agent_runtime.audit_traces`` 经
  ``agent_runs`` 归属（均以 ``run_id`` 关联）；
- ``agent_runtime.agent_workers`` 经 ``current_run_id`` 归属；心跳行在无归属 run
  时（含空闲、启动、已结束）对应用角色不可见，但这不影响 Worker 自身以所有者
  连接维护心跳。

``agent_checkpoint.*`` 由 LangGraph 自己维护，没有租户概念，只经已鉴权的
run 记录访问，因此不在本迁移范围内。

策略刻意使用 ``ENABLE`` 而非 ``FORCE``：表所有者（迁移与 Worker 使用的受控
连接）不受策略约束，受限的应用角色 ``costgraph_app`` 必须显式写入
``app.tenant_id`` 才能看到本租户数据。缺少该设置时
``current_setting('app.tenant_id', true)`` 为 NULL，谓词不成立，返回空集而非
跨租户全集——失败方向是收敛而不是放开。
"""

from __future__ import annotations

from alembic import op

revision: str = "20260920_0004"
down_revision: str | None = "20260918_0003"
branch_labels: str | None = None
depends_on: str | None = None

APP_ROLE = "costgraph_app"

TENANT_TABLES: tuple[tuple[str, str], ...] = (
    ("cost_data", "parts"),
    ("cost_data", "cost_events"),
    ("cost_data", "cost_event_inputs"),
    ("cost_data", "cost_records"),
    ("cost_data", "finished_batch_cost_projections"),
    ("cost_data", "data_load_batches"),
    ("cost_data", "data_load_errors"),
    ("cost_data", "data_import_jobs"),
    ("cost_data", "governance_audit_events"),
    ("cost_data", "iam_principal_roles"),
    ("cost_data", "iam_data_scopes"),
    ("agent_output", "artifacts"),
    ("agent_runtime", "conversations"),
    ("agent_runtime", "turns"),
    ("agent_runtime", "agent_runs"),
)

PARENT_TABLES: tuple[tuple[str, str, str, str, str], ...] = (
    (
        "agent_runtime",
        "messages",
        "conversation_id",
        "agent_runtime.conversations",
        "conversation_id",
    ),
    (
        "agent_runtime",
        "agent_run_events",
        "run_id",
        "agent_runtime.agent_runs",
        "run_id",
    ),
    (
        "agent_runtime",
        "audit_traces",
        "run_id",
        "agent_runtime.agent_runs",
        "run_id",
    ),
    (
        "agent_runtime",
        "agent_workers",
        "current_run_id",
        "agent_runtime.agent_runs",
        "run_id",
    ),
)

TENANT_EXPR = "tenant_id = current_setting('app.tenant_id', true)"


def _parent_expr(parent: str, child_column: str, parent_column: str) -> str:
    return (
        f"EXISTS (SELECT 1 FROM {parent} AS parent_row "
        f"WHERE parent_row.{parent_column} = {child_column} "
        f"AND parent_row.tenant_id = current_setting('app.tenant_id', true))"
    )


def upgrade() -> None:
    op.execute(
        f"""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{APP_ROLE}') THEN
                CREATE ROLE {APP_ROLE} NOLOGIN NOINHERIT;
            END IF;
        END
        $$;
        """
    )

    targets: list[tuple[str, str, str]] = [
        (schema, table, TENANT_EXPR) for schema, table in TENANT_TABLES
    ]
    targets.extend(
        (schema, table, _parent_expr(parent, child_column, parent_column))
        for schema, table, child_column, parent, parent_column in PARENT_TABLES
    )

    for schema, table, predicate in targets:
        qualified = f"{schema}.{table}"
        for action in ("SELECT", "INSERT", "UPDATE", "DELETE"):
            op.execute(f"GRANT {action} ON {qualified} TO {APP_ROLE}")
        op.execute(f"ALTER TABLE {qualified} ENABLE ROW LEVEL SECURITY")
        op.execute(f"DROP POLICY IF EXISTS {table}_tenant_isolation ON {qualified}")
        op.execute(
            f"CREATE POLICY {table}_tenant_isolation ON {qualified} "
            f"AS PERMISSIVE FOR ALL TO {APP_ROLE} "
            f"USING ({predicate}) WITH CHECK ({predicate})"
        )

    for schema in ("cost_data", "agent_output", "agent_runtime"):
        op.execute(f"GRANT USAGE ON SCHEMA {schema} TO {APP_ROLE}")


def downgrade() -> None:
    for schema, table, _ in PARENT_TABLES:
        op.execute(
            f"DROP POLICY IF EXISTS {table}_tenant_isolation ON {schema}.{table}"
        )
        op.execute(f"ALTER TABLE {schema}.{table} DISABLE ROW LEVEL SECURITY")
    for schema, table in TENANT_TABLES:
        op.execute(
            f"DROP POLICY IF EXISTS {table}_tenant_isolation ON {schema}.{table}"
        )
        op.execute(f"ALTER TABLE {schema}.{table} DISABLE ROW LEVEL SECURITY")
