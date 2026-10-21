"""允许受限 Worker 角色跨租户处理调度表。

Revision ID: 20261021_0006
Revises: 20260921_0005
Create Date: 2026-10-21

调度查询必须发现所有租户的待领取任务。表级 RLS 已启用，但 0005 只授予了
``costgraph_worker`` 表权限，没有策略；PostgreSQL 因此默认拒绝该角色访问。
此迁移仅对两张调度表放开该专用角色，其他业务表仍无跨租户策略。
"""

from __future__ import annotations

from alembic import op

revision: str = "20261021_0006"
down_revision: str | None = "20260921_0005"
branch_labels: str | None = None
depends_on: str | None = None

SCHEDULING_TABLES: tuple[tuple[str, str], ...] = (
    ("cost_data", "data_import_jobs"),
    ("agent_runtime", "agent_runs"),
)
WORKER_ROLE = "costgraph_worker"


def upgrade() -> None:
    for schema, table in SCHEDULING_TABLES:
        op.execute(
            f"CREATE POLICY {table}_worker_scheduling ON {schema}.{table} "
            f"AS PERMISSIVE FOR ALL TO {WORKER_ROLE} "
            "USING (true) WITH CHECK (true)"
        )


def downgrade() -> None:
    for schema, table in SCHEDULING_TABLES:
        op.execute(
            f"DROP POLICY IF EXISTS {table}_worker_scheduling ON {schema}.{table}"
        )
