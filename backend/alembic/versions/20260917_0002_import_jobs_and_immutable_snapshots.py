"""Add import jobs, governance audit events and immutable snapshot keys.

Revision ID: 20260917_0002
Revises: 20260907_0001
Create Date: 2026-09-17
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "20260917_0002"
down_revision: str | None = "20260907_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

COST_SCHEMA = "cost_data"

# 不可变快照表 -> 旧主键列 / 新主键列（逻辑 ID 入键）
LEGACY_PRIMARY_KEYS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("parts", ("tenant_id", "part_id")),
    ("cost_events", ("tenant_id", "event_id")),
    ("cost_event_inputs", ("tenant_id", "input_id")),
    ("cost_records", ("tenant_id", "cost_record_id")),
    ("finished_batch_cost_projections", ("tenant_id", "event_id")),
)

SNAPSHOT_PRIMARY_KEYS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("parts", ("tenant_id", "batch_id", "part_id")),
    ("cost_events", ("tenant_id", "batch_id", "event_id")),
    ("cost_event_inputs", ("tenant_id", "batch_id", "input_id")),
    ("cost_records", ("tenant_id", "batch_id", "cost_record_id")),
    ("finished_batch_cost_projections", ("tenant_id", "batch_id", "event_id")),
)

# 旧租户级全局唯一约束（与多快照并存冲突），需替换为含 batch_id 的版本。
# 只列出 baseline 中真实存在、且必须删除的约束；主键隐含的唯一性由主键替换处理。
LEGACY_UNIQUE_CONSTRAINTS: tuple[tuple[str, str], ...] = (
    ("parts", "uq_parts_source_record"),
    ("cost_events", "uq_cost_events_tenant_output_batch"),
    ("cost_events", "uq_cost_events_source_record"),
    ("cost_records", "uq_cost_records_source_record"),
    ("finished_batch_cost_projections", "uq_finished_cost_projection_batch"),
)

LEGACY_UNIQUE_CONSTRAINT_DEFS: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    (
        "parts",
        "uq_parts_source_record",
        ("tenant_id", "source_system", "source_record_id"),
    ),
    (
        "cost_events",
        "uq_cost_events_tenant_output_batch",
        ("tenant_id", "output_batch_id"),
    ),
    (
        "cost_events",
        "uq_cost_events_source_record",
        ("tenant_id", "source_system", "source_record_id"),
    ),
    (
        "cost_records",
        "uq_cost_records_source_record",
        ("tenant_id", "source_system", "source_record_id"),
    ),
    (
        "finished_batch_cost_projections",
        "uq_finished_cost_projection_batch",
        ("tenant_id", "finished_batch_id"),
    ),
)

SNAPSHOT_UNIQUE_CONSTRAINTS: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("parts", "uq_parts_tenant_part", ("tenant_id", "batch_id", "part_id")),
    (
        "parts",
        "uq_parts_batch_source_record",
        ("tenant_id", "batch_id", "source_system", "source_record_id"),
    ),
    (
        "cost_events",
        "uq_cost_events_tenant_event",
        ("tenant_id", "batch_id", "event_id"),
    ),
    (
        "cost_events",
        "uq_cost_events_batch_source_record",
        ("tenant_id", "batch_id", "source_system", "source_record_id"),
    ),
    (
        "cost_event_inputs",
        "uq_cost_event_inputs_id",
        ("tenant_id", "batch_id", "input_id"),
    ),
    (
        "cost_records",
        "uq_cost_records_id",
        ("tenant_id", "batch_id", "cost_record_id"),
    ),
    (
        "cost_records",
        "uq_cost_records_batch_source_record",
        ("tenant_id", "batch_id", "source_system", "source_record_id"),
    ),
    (
        "finished_batch_cost_projections",
        "uq_finished_cost_projection_batch",
        ("tenant_id", "batch_id", "finished_batch_id"),
    ),
)

# 主子表外键必须组合化：引用父表 (tenant_id, batch_id, 逻辑 ID)
SCOPED_FOREIGN_KEYS: tuple[
    tuple[str, str, str, tuple[str, ...], tuple[str, ...]], ...
] = (
    (
        "fk_cost_events_part",
        "cost_events",
        "parts",
        ("tenant_id", "batch_id", "part_id"),
        ("tenant_id", "batch_id", "part_id"),
    ),
    (
        "fk_cost_event_inputs_target",
        "cost_event_inputs",
        "cost_events",
        ("tenant_id", "batch_id", "event_id"),
        ("tenant_id", "batch_id", "event_id"),
    ),
    (
        "fk_cost_event_inputs_source",
        "cost_event_inputs",
        "cost_events",
        ("tenant_id", "batch_id", "source_event_id"),
        ("tenant_id", "batch_id", "event_id"),
    ),
    (
        "fk_cost_records_event",
        "cost_records",
        "cost_events",
        ("tenant_id", "batch_id", "event_id"),
        ("tenant_id", "batch_id", "event_id"),
    ),
    (
        "fk_finished_cost_projection_event",
        "finished_batch_cost_projections",
        "cost_events",
        ("tenant_id", "batch_id", "event_id"),
        ("tenant_id", "batch_id", "event_id"),
    ),
    (
        "fk_finished_cost_projection_part",
        "finished_batch_cost_projections",
        "parts",
        ("tenant_id", "batch_id", "part_id"),
        ("tenant_id", "batch_id", "part_id"),
    ),
)

LEGACY_FOREIGN_KEYS: tuple[
    tuple[str, str, str, tuple[str, ...], tuple[str, ...]], ...
] = (
    (
        "fk_cost_events_part",
        "cost_events",
        "parts",
        ("tenant_id", "part_id"),
        ("tenant_id", "part_id"),
    ),
    (
        "fk_cost_event_inputs_target",
        "cost_event_inputs",
        "cost_events",
        ("tenant_id", "event_id"),
        ("tenant_id", "event_id"),
    ),
    (
        "fk_cost_event_inputs_source",
        "cost_event_inputs",
        "cost_events",
        ("tenant_id", "source_event_id"),
        ("tenant_id", "event_id"),
    ),
    (
        "fk_cost_records_event",
        "cost_records",
        "cost_events",
        ("tenant_id", "event_id"),
        ("tenant_id", "event_id"),
    ),
    (
        "fk_finished_cost_projection_event",
        "finished_batch_cost_projections",
        "cost_events",
        ("tenant_id", "event_id"),
        ("tenant_id", "event_id"),
    ),
    (
        "fk_finished_cost_projection_part",
        "finished_batch_cost_projections",
        "parts",
        ("tenant_id", "part_id"),
        ("tenant_id", "part_id"),
    ),
)


def _jsonb() -> postgresql.JSONB:
    return postgresql.JSONB(astext_type=sa.Text())


def _create_import_job_tables() -> None:
    op.create_table(
        "data_import_jobs",
        sa.Column("job_id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("principal_id", sa.String(length=128), nullable=False),
        sa.Column("principal_roles", _jsonb(), nullable=False),
        sa.Column("source_system", sa.String(length=64), nullable=False),
        sa.Column("source_object_key", sa.String(length=512), nullable=False),
        sa.Column("source_object_size", sa.BigInteger(), nullable=False),
        sa.Column("source_object_sha256", sa.String(length=64), nullable=False),
        sa.Column("snapshot_hash", sa.String(length=64), nullable=False),
        sa.Column("calculation_rule_version", sa.String(length=64), nullable=False),
        sa.Column("idempotency_key", sa.String(length=128), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("total_rows", sa.Integer(), nullable=False),
        sa.Column("valid_rows", sa.Integer(), nullable=False),
        sa.Column("error_rows", sa.Integer(), nullable=False),
        sa.Column("error_summary", _jsonb(), nullable=True),
        sa.Column("decision", sa.String(length=16), nullable=True),
        sa.Column("decision_reason", sa.String(length=500), nullable=True),
        sa.Column("decided_by", sa.String(length=128), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("published_batch_id", sa.Uuid(), nullable=True),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("lease_owner", sa.String(length=128), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "status in ('submitted', 'validating', 'validation_failed', "
            "'approval_pending', 'approved', 'rejected', 'publishing', "
            "'published', 'publish_failed', 'cancelled')",
            name="ck_data_import_jobs_status",
        ),
        sa.CheckConstraint(
            "total_rows >= 0 and valid_rows >= 0 and error_rows >= 0",
            name="ck_data_import_jobs_nonnegative_counts",
        ),
        sa.PrimaryKeyConstraint("job_id"),
        sa.UniqueConstraint(
            "tenant_id",
            "idempotency_key",
            name="uq_data_import_jobs_idempotency",
        ),
        schema=COST_SCHEMA,
    )
    op.create_index(
        "ix_data_import_jobs_claim",
        "data_import_jobs",
        ["status", "available_at", "lease_expires_at"],
        unique=False,
        schema=COST_SCHEMA,
    )
    op.create_index(
        "ix_data_import_jobs_tenant_created",
        "data_import_jobs",
        ["tenant_id", "created_at"],
        unique=False,
        schema=COST_SCHEMA,
    )
    op.create_table(
        "governance_audit_events",
        sa.Column("event_id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("job_id", sa.Uuid(), nullable=True),
        sa.Column("batch_id", sa.Uuid(), nullable=True),
        sa.Column("actor_principal_id", sa.String(length=128), nullable=False),
        sa.Column("action", sa.String(length=32), nullable=False),
        sa.Column("target_type", sa.String(length=32), nullable=False),
        sa.Column("target_id", sa.String(length=128), nullable=False),
        sa.Column("outcome", sa.String(length=16), nullable=False),
        sa.Column("request_id", sa.String(length=64), nullable=True),
        sa.Column("detail", _jsonb(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "action in ('submitted', 'validated', 'validation_failed', "
            "'approved', 'rejected', 'publishing', 'published', "
            "'publish_failed', 'cancelled')",
            name="ck_governance_audit_events_action",
        ),
        sa.CheckConstraint(
            "outcome in ('success', 'failure')",
            name="ck_governance_audit_events_outcome",
        ),
        sa.PrimaryKeyConstraint("event_id"),
        schema=COST_SCHEMA,
    )
    op.create_index(
        "ix_governance_audit_events_tenant_created",
        "governance_audit_events",
        ["tenant_id", "created_at"],
        unique=False,
        schema=COST_SCHEMA,
    )
    op.create_index(
        "ix_governance_audit_events_job",
        "governance_audit_events",
        ["job_id"],
        unique=False,
        schema=COST_SCHEMA,
    )


def _drop_legacy_foreign_keys() -> None:
    for name, table, _parent, _cols, _refcols in LEGACY_FOREIGN_KEYS:
        op.drop_constraint(name, table, type_="foreignkey", schema=COST_SCHEMA)


def _create_audit_immutability_guard() -> None:
    """审计事件只允许追加。

    Repository 只暴露 ``append_audit_event``，但后续代码仍可能直接写 SQL；
    在数据库层拦下 UPDATE/DELETE，让“审计不可修改”由约束而不是约定保证。
    """
    op.execute(
        f"""
        CREATE OR REPLACE FUNCTION {COST_SCHEMA}.reject_audit_event_mutation()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION 'governance_audit_events 只允许追加，禁止 %', TG_OP
                USING ERRCODE = 'restrict_violation';
        END;
        $$
        """
    )
    op.execute(
        f"""
        CREATE TRIGGER trg_governance_audit_events_append_only
        BEFORE UPDATE OR DELETE ON {COST_SCHEMA}.governance_audit_events
        FOR EACH ROW EXECUTE FUNCTION {COST_SCHEMA}.reject_audit_event_mutation()
        """
    )


def _drop_audit_immutability_guard() -> None:
    op.execute(
        f"DROP TRIGGER IF EXISTS trg_governance_audit_events_append_only "
        f"ON {COST_SCHEMA}.governance_audit_events"
    )
    op.execute(f"DROP FUNCTION IF EXISTS {COST_SCHEMA}.reject_audit_event_mutation()")


def _drop_legacy_unique_constraints() -> None:
    for table, name in LEGACY_UNIQUE_CONSTRAINTS:
        op.drop_constraint(name, table, type_="unique", schema=COST_SCHEMA)


def _drop_snapshot_primary_keys() -> None:
    for table, _columns in LEGACY_PRIMARY_KEYS:
        op.drop_constraint(f"{table}_pkey", table, type_="primary", schema=COST_SCHEMA)


def _create_snapshot_unique_constraints() -> None:
    for table, name, columns in SNAPSHOT_UNIQUE_CONSTRAINTS:
        op.create_unique_constraint(name, table, list(columns), schema=COST_SCHEMA)


def _create_snapshot_primary_keys() -> None:
    for table, columns in SNAPSHOT_PRIMARY_KEYS:
        op.create_primary_key(f"{table}_pkey", table, list(columns), schema=COST_SCHEMA)


def _create_scoped_foreign_keys() -> None:
    for name, table, parent, columns, refcolumns in SCOPED_FOREIGN_KEYS:
        op.create_foreign_key(
            name,
            table,
            parent,
            list(columns),
            list(refcolumns),
            source_schema=COST_SCHEMA,
            referent_schema=COST_SCHEMA,
        )


def upgrade() -> None:
    _create_import_job_tables()
    _create_audit_immutability_guard()

    # 先拆子表外键，才能替换父表主键与唯一约束
    _drop_legacy_foreign_keys()
    _drop_legacy_unique_constraints()
    _drop_snapshot_primary_keys()

    _create_snapshot_unique_constraints()
    _create_snapshot_primary_keys()
    _create_scoped_foreign_keys()


def _drop_snapshot_unique_constraints() -> None:
    for table, name, _columns in SNAPSHOT_UNIQUE_CONSTRAINTS:
        op.drop_constraint(name, table, type_="unique", schema=COST_SCHEMA)


def _restore_legacy_primary_keys() -> None:
    for table, columns in LEGACY_PRIMARY_KEYS:
        op.create_primary_key(f"{table}_pkey", table, list(columns), schema=COST_SCHEMA)


def _restore_legacy_unique_constraints() -> None:
    for table, name, columns in LEGACY_UNIQUE_CONSTRAINT_DEFS:
        op.create_unique_constraint(name, table, list(columns), schema=COST_SCHEMA)


def _restore_legacy_foreign_keys() -> None:
    for name, table, parent, columns, refcolumns in LEGACY_FOREIGN_KEYS:
        op.create_foreign_key(
            name,
            table,
            parent,
            list(columns),
            list(refcolumns),
            source_schema=COST_SCHEMA,
            referent_schema=COST_SCHEMA,
        )


def downgrade() -> None:
    _drop_audit_immutability_guard()
    for name, table, _parent, _cols, _refcols in SCOPED_FOREIGN_KEYS:
        op.drop_constraint(name, table, type_="foreignkey", schema=COST_SCHEMA)
    _drop_snapshot_unique_constraints()
    _drop_snapshot_primary_keys()

    _restore_legacy_primary_keys()
    _restore_legacy_unique_constraints()
    _restore_legacy_foreign_keys()

    op.drop_index(
        "ix_governance_audit_events_job",
        table_name="governance_audit_events",
        schema=COST_SCHEMA,
    )
    op.drop_index(
        "ix_governance_audit_events_tenant_created",
        table_name="governance_audit_events",
        schema=COST_SCHEMA,
    )
    op.drop_table("governance_audit_events", schema=COST_SCHEMA)
    op.drop_index(
        "ix_data_import_jobs_tenant_created",
        table_name="data_import_jobs",
        schema=COST_SCHEMA,
    )
    op.drop_index(
        "ix_data_import_jobs_claim",
        table_name="data_import_jobs",
        schema=COST_SCHEMA,
    )
    op.drop_table("data_import_jobs", schema=COST_SCHEMA)
