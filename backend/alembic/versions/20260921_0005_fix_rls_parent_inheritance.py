"""修正父表推导策略的作用域串扰并补建 worker 角色

Revision ID: 20260921_0005
Revises: 20260920_0004
Create Date: 2026-09-21

``20260920_0004`` 的父表推导策略写成了：

    EXISTS (SELECT 1 FROM agent_runtime.agent_runs AS parent_row
            WHERE parent_row.run_id = run_id
              AND parent_row.tenant_id = current_setting('app.tenant_id', true))

谓词里 ``parent_row.run_id = run_id`` 右侧的裸列 ``run_id`` 被解析为**内层**
``parent_row`` 的同名列，条件退化为恒真，只剩租户条件生效。于是
``audit_traces``/``agent_run_events`` 在设置了合法 ``app.tenant_id`` 后能读到
**全部租户**的行——正是 RLS 要消除的跨租户泄漏。这里改为用子表名限定外层列。

同时补建 ``costgraph_worker``：策略原先并未引用该角色，但 Worker 需要跨租户
读取两张调度表（``cost_data.data_import_jobs``、``agent_runtime.agent_runs``），
否则无法发现待领取工作。该角色同理 `NOLOGIN`，由部署时授予登录与凭据。
"""

from __future__ import annotations

from alembic import op

revision: str = "20260921_0005"
down_revision: str | None = "20260920_0004"
branch_labels: str | None = None
depends_on: str | None = None

APP_ROLE = "costgraph_app"
WORKER_ROLE = "costgraph_worker"

# (schema, child_table, child_column, parent_table, parent_column)
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

# Worker 只在两张调度表上放开跨租户领取；其余表仍由 owner 连接承担。
SCHEDULING_TABLES: tuple[tuple[str, str], ...] = (
    ("cost_data", "data_import_jobs"),
    ("agent_runtime", "agent_runs"),
)


def _parent_expr(
    child_table: str, child_column: str, parent: str, parent_column: str
) -> str:
    """外层列必须用子表名限定，否则会被解析成内层同名列而恒真。"""
    return (
        f"EXISTS (SELECT 1 FROM {parent} AS parent_row "
        f"WHERE parent_row.{parent_column} = {child_table}.{child_column} "
        f"AND parent_row.tenant_id = current_setting('app.tenant_id', true))"
    )


def upgrade() -> None:
    for schema, table, child_column, parent, parent_column in PARENT_TABLES:
        qualified = f"{schema}.{table}"
        predicate = _parent_expr(table, child_column, parent, parent_column)
        op.execute(f"DROP POLICY IF EXISTS {table}_tenant_isolation ON {qualified}")
        op.execute(
            f"CREATE POLICY {table}_tenant_isolation ON {qualified} "
            f"AS PERMISSIVE FOR ALL TO {APP_ROLE} "
            f"USING ({predicate}) WITH CHECK ({predicate})"
        )

    op.execute(
        f"""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{WORKER_ROLE}') THEN
                CREATE ROLE {WORKER_ROLE} NOLOGIN NOINHERIT;
            END IF;
        END
        $$;
        """
    )
    for schema, table in SCHEDULING_TABLES:
        qualified = f"{schema}.{table}"
        for action in ("SELECT", "INSERT", "UPDATE", "DELETE"):
            op.execute(f"GRANT {action} ON {qualified} TO {WORKER_ROLE}")
    for schema in ("cost_data", "agent_runtime", "agent_output"):
        op.execute(f"GRANT USAGE ON SCHEMA {schema} TO {WORKER_ROLE}")


def downgrade() -> None:
    for schema, table, child_column, parent, parent_column in PARENT_TABLES:
        qualified = f"{schema}.{table}"
        # 回到 0004 的定义，仅用于迁移回退；该定义存在跨租户串扰，不可用于生产。
        predicate = (
            f"EXISTS (SELECT 1 FROM {parent} AS parent_row "
            f"WHERE parent_row.{parent_column} = {child_column} "
            f"AND parent_row.tenant_id = current_setting('app.tenant_id', true))"
        )
        op.execute(f"DROP POLICY IF EXISTS {table}_tenant_isolation ON {qualified}")
        op.execute(
            f"CREATE POLICY {table}_tenant_isolation ON {qualified} "
            f"AS PERMISSIVE FOR ALL TO {APP_ROLE} "
            f"USING ({predicate}) WITH CHECK ({predicate})"
        )

    for schema, table in SCHEDULING_TABLES:
        op.execute(f"REVOKE ALL ON {schema}.{table} FROM {WORKER_ROLE}")
    for schema in ("cost_data", "agent_runtime", "agent_output"):
        op.execute(f"REVOKE USAGE ON SCHEMA {schema} FROM {WORKER_ROLE}")
    op.execute(f"DROP ROLE IF EXISTS {WORKER_ROLE}")
