"""Create the CostGraph PostgreSQL baseline.

Revision ID: 20260907_0001
Revises:
Create Date: 2026-09-07
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "20260907_0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

COST_SCHEMA = "cost_data"
RUNTIME_SCHEMA = "agent_runtime"
CHECKPOINT_SCHEMA = "agent_checkpoint"
ARTIFACT_SCHEMA = "agent_output"


def _jsonb() -> postgresql.JSONB:
    return postgresql.JSONB(astext_type=sa.Text())


def upgrade() -> None:
    for schema in (
        COST_SCHEMA,
        RUNTIME_SCHEMA,
        CHECKPOINT_SCHEMA,
        ARTIFACT_SCHEMA,
    ):
        op.execute(f'CREATE SCHEMA IF NOT EXISTS "{schema}"')

    op.create_table(
        "data_load_batches",
        sa.Column("batch_id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("source_system", sa.String(length=64), nullable=False),
        sa.Column("source_file", sa.String(length=500), nullable=True),
        sa.Column("source_snapshot_hash", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("total_rows", sa.Integer(), nullable=False),
        sa.Column("valid_rows", sa.Integer(), nullable=False),
        sa.Column("error_rows", sa.Integer(), nullable=False),
        sa.Column("error_summary", _jsonb(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "status in ('created', 'validating', 'validated', 'published', "
            "'superseded', 'failed')",
            name="ck_data_load_batches_status",
        ),
        sa.CheckConstraint(
            "total_rows >= 0 and valid_rows >= 0 and error_rows >= 0",
            name="ck_data_load_batches_nonnegative_counts",
        ),
        sa.CheckConstraint(
            "valid_rows + error_rows <= total_rows",
            name="ck_data_load_batches_bounded_counts",
        ),
        sa.PrimaryKeyConstraint("batch_id"),
        sa.UniqueConstraint(
            "tenant_id",
            "source_system",
            "source_snapshot_hash",
            name="uq_data_load_batches_snapshot",
        ),
        sa.UniqueConstraint(
            "tenant_id", "batch_id", name="uq_data_load_batches_tenant_batch"
        ),
        schema=COST_SCHEMA,
    )
    op.create_index(
        "uq_data_load_batches_active_tenant",
        "data_load_batches",
        ["tenant_id"],
        unique=True,
        schema=COST_SCHEMA,
        postgresql_where=sa.text("status = 'published'"),
    )
    op.create_table(
        "parts",
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("part_id", sa.String(length=64), nullable=False),
        sa.Column("part_number", sa.String(length=128), nullable=False),
        sa.Column("part_description", sa.String(length=500), nullable=False),
        sa.Column("part_type", sa.String(length=32), nullable=False),
        sa.Column("product_family", sa.String(length=128), nullable=True),
        sa.Column("unit", sa.String(length=32), nullable=False),
        sa.Column("source_system", sa.String(length=64), nullable=False),
        sa.Column("source_record_id", sa.String(length=128), nullable=False),
        sa.Column("batch_id", sa.Uuid(), nullable=False),
        sa.Column("raw_payload", _jsonb(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("tenant_id", "part_id"),
        sa.UniqueConstraint(
            "tenant_id",
            "source_system",
            "source_record_id",
            name="uq_parts_source_record",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "batch_id"],
            [
                "cost_data.data_load_batches.tenant_id",
                "cost_data.data_load_batches.batch_id",
            ],
            name="fk_parts_batch",
        ),
        schema=COST_SCHEMA,
    )
    op.create_table(
        "cost_events",
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("event_id", sa.String(length=128), nullable=False),
        sa.Column("event_type", sa.String(length=16), nullable=False),
        sa.Column("output_batch_id", sa.String(length=128), nullable=False),
        sa.Column("part_id", sa.String(length=64), nullable=False),
        sa.Column("period", sa.String(length=7), nullable=False),
        sa.Column("cost_center_code", sa.String(length=64), nullable=True),
        sa.Column("cost_center_name", sa.String(length=200), nullable=True),
        sa.Column("work_order_number", sa.String(length=128), nullable=True),
        sa.Column("lot_number", sa.String(length=128), nullable=True),
        sa.Column("process_code", sa.String(length=64), nullable=True),
        sa.Column("process_name", sa.String(length=200), nullable=True),
        sa.Column("completion_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("qualified_quantity", sa.Numeric(18, 4), nullable=False),
        sa.Column("defective_quantity", sa.Numeric(18, 4), nullable=False),
        sa.Column("unit", sa.String(length=32), nullable=False),
        sa.Column("machine_hours", sa.Numeric(18, 4), nullable=False),
        sa.Column("labor_hours", sa.Numeric(18, 4), nullable=False),
        sa.Column("source_system", sa.String(length=64), nullable=False),
        sa.Column("source_record_id", sa.String(length=128), nullable=False),
        sa.Column("raw_payload", _jsonb(), nullable=True),
        sa.Column("batch_id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "event_type in ('purchase', 'process')", name="ck_cost_events_type"
        ),
        sa.CheckConstraint(
            "qualified_quantity >= 0 and defective_quantity >= 0",
            name="ck_cost_events_quantities",
        ),
        sa.CheckConstraint(
            "qualified_quantity + defective_quantity > 0",
            name="ck_cost_events_positive_output",
        ),
        sa.CheckConstraint(
            "to_char(completion_time, 'YYYY-MM') = period",
            name="ck_cost_events_period_date",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "part_id"],
            ["cost_data.parts.tenant_id", "cost_data.parts.part_id"],
            name="fk_cost_events_part",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "batch_id"],
            [
                "cost_data.data_load_batches.tenant_id",
                "cost_data.data_load_batches.batch_id",
            ],
            name="fk_cost_events_batch",
        ),
        sa.PrimaryKeyConstraint("tenant_id", "event_id"),
        sa.UniqueConstraint(
            "tenant_id",
            "output_batch_id",
            name="uq_cost_events_tenant_output_batch",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "source_system",
            "source_record_id",
            name="uq_cost_events_source_record",
        ),
        schema=COST_SCHEMA,
    )
    op.create_index(
        "ix_cost_events_period",
        "cost_events",
        ["tenant_id", "period"],
        unique=False,
        schema=COST_SCHEMA,
    )
    op.create_index(
        "ix_cost_events_finished",
        "cost_events",
        ["tenant_id", "part_id", "period"],
        unique=False,
        schema=COST_SCHEMA,
    )
    op.create_table(
        "cost_event_inputs",
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("input_id", sa.String(length=128), nullable=False),
        sa.Column("event_id", sa.String(length=128), nullable=False),
        sa.Column("source_event_id", sa.String(length=128), nullable=False),
        sa.Column("consumed_quantity", sa.Numeric(18, 4), nullable=False),
        sa.Column("unit", sa.String(length=32), nullable=False),
        sa.Column("source_system", sa.String(length=64), nullable=False),
        sa.Column("source_record_id", sa.String(length=128), nullable=False),
        sa.Column("raw_payload", _jsonb(), nullable=True),
        sa.Column("batch_id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "consumed_quantity > 0", name="ck_cost_event_inputs_positive"
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "event_id"],
            ["cost_data.cost_events.tenant_id", "cost_data.cost_events.event_id"],
            name="fk_cost_event_inputs_target",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "source_event_id"],
            ["cost_data.cost_events.tenant_id", "cost_data.cost_events.event_id"],
            name="fk_cost_event_inputs_source",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "batch_id"],
            [
                "cost_data.data_load_batches.tenant_id",
                "cost_data.data_load_batches.batch_id",
            ],
            name="fk_cost_event_inputs_batch",
        ),
        sa.PrimaryKeyConstraint("tenant_id", "input_id"),
        schema=COST_SCHEMA,
    )
    op.create_table(
        "cost_records",
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("cost_record_id", sa.String(length=128), nullable=False),
        sa.Column("event_id", sa.String(length=128), nullable=False),
        sa.Column("cost_code", sa.String(length=64), nullable=False),
        sa.Column("amount", sa.Numeric(18, 2), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("incurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source_system", sa.String(length=64), nullable=False),
        sa.Column("source_document_no", sa.String(length=128), nullable=False),
        sa.Column("source_document_line", sa.String(length=64), nullable=True),
        sa.Column("source_record_id", sa.String(length=128), nullable=False),
        sa.Column("raw_payload", _jsonb(), nullable=True),
        sa.Column("batch_id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("amount >= 0", name="ck_cost_records_nonnegative"),
        sa.CheckConstraint("currency = 'CNY'", name="ck_cost_records_currency"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "event_id"],
            ["cost_data.cost_events.tenant_id", "cost_data.cost_events.event_id"],
            name="fk_cost_records_event",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "batch_id"],
            [
                "cost_data.data_load_batches.tenant_id",
                "cost_data.data_load_batches.batch_id",
            ],
            name="fk_cost_records_batch",
        ),
        sa.PrimaryKeyConstraint("tenant_id", "cost_record_id"),
        sa.UniqueConstraint(
            "tenant_id",
            "source_system",
            "source_record_id",
            name="uq_cost_records_source_record",
        ),
        schema=COST_SCHEMA,
    )
    op.create_table(
        "data_load_errors",
        sa.Column("error_id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("batch_id", sa.Uuid(), nullable=False),
        sa.Column("source_table", sa.String(length=64), nullable=False),
        sa.Column("source_record_id", sa.String(length=128), nullable=True),
        sa.Column("error_code", sa.String(length=64), nullable=False),
        sa.Column("error_message", sa.Text(), nullable=False),
        sa.Column("raw_payload", _jsonb(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["tenant_id", "batch_id"],
            [
                "cost_data.data_load_batches.tenant_id",
                "cost_data.data_load_batches.batch_id",
            ],
            name="fk_data_load_errors_batch",
        ),
        sa.PrimaryKeyConstraint("error_id"),
        schema=COST_SCHEMA,
    )

    _create_artifact_tables()
    _create_runtime_tables()
    _create_checkpoint_tables()


def _create_artifact_tables() -> None:
    op.create_table(
        "artifacts",
        sa.Column("artifact_id", sa.String(length=64), nullable=False),
        sa.Column("artifact_type", sa.String(length=32), nullable=False),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("principal_id", sa.String(length=128), nullable=False),
        sa.Column("conversation_id", sa.String(length=128), nullable=False),
        sa.Column("turn_id", sa.String(length=128), nullable=False),
        sa.Column("message_id", sa.String(length=128), nullable=False),
        sa.Column("run_id", sa.String(length=128), nullable=False),
        sa.Column("conversation_title", sa.String(length=200), nullable=False),
        sa.Column("part_id", sa.String(length=64), nullable=False),
        sa.Column("part_number", sa.String(length=128), nullable=False),
        sa.Column("part_description", sa.String(length=500), nullable=False),
        sa.Column("period", sa.String(length=64), nullable=False),
        sa.Column("report_json", _jsonb(), nullable=False),
        sa.Column("report_sha256", sa.String(length=64), nullable=False),
        sa.Column("data_snapshot_id", sa.String(length=64), nullable=False),
        sa.Column("report_schema_version", sa.String(length=32), nullable=False),
        sa.Column("rule_version", sa.String(length=64), nullable=False),
        sa.Column("prompt_version", sa.String(length=64), nullable=False),
        sa.Column("code_version", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "artifact_type = 'cost_report'", name="ck_agent_artifacts_type"
        ),
        sa.CheckConstraint(
            "char_length(report_sha256) = 64",
            name="ck_agent_artifacts_report_sha256",
        ),
        sa.CheckConstraint(
            "char_length(data_snapshot_id) = 64",
            name="ck_agent_artifacts_data_snapshot_id",
        ),
        sa.PrimaryKeyConstraint("artifact_id"),
        sa.UniqueConstraint(
            "tenant_id", "run_id", name="uq_agent_artifacts_tenant_run"
        ),
        sa.UniqueConstraint(
            "tenant_id", "message_id", name="uq_agent_artifacts_tenant_message"
        ),
        schema=ARTIFACT_SCHEMA,
    )
    op.create_index(
        "ix_agent_artifacts_owner_lifecycle_created",
        "artifacts",
        ["tenant_id", "principal_id", "deleted_at", "created_at"],
        unique=False,
        schema=ARTIFACT_SCHEMA,
    )
    op.create_index(
        "ix_agent_artifacts_owner_part_period",
        "artifacts",
        ["tenant_id", "principal_id", "part_id", "period"],
        unique=False,
        schema=ARTIFACT_SCHEMA,
    )


def _create_runtime_tables() -> None:
    op.create_table(
        "conversations",
        sa.Column("conversation_id", sa.String(length=128), nullable=False),
        sa.Column("principal_id", sa.String(length=128), nullable=False),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("routing_mode", sa.String(length=16), nullable=False),
        sa.Column("enabled_capabilities_json", _jsonb(), nullable=False),
        sa.Column("context_json", _jsonb(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("conversation_id"),
        schema=RUNTIME_SCHEMA,
    )
    op.create_index(
        "ix_agent_runtime_conversations_owner_updated",
        "conversations",
        ["tenant_id", "principal_id", "updated_at"],
        unique=False,
        schema=RUNTIME_SCHEMA,
    )
    op.create_table(
        "turns",
        sa.Column("turn_id", sa.String(length=128), nullable=False),
        sa.Column("conversation_id", sa.String(length=128), nullable=False),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("principal_id", sa.String(length=128), nullable=False),
        sa.Column("message_id", sa.String(length=128), nullable=False),
        sa.Column("question", sa.Text(), nullable=False),
        sa.Column("result_json", _jsonb(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["conversation_id"],
            ["agent_runtime.conversations.conversation_id"],
            name="fk_agent_runtime_turns_conversation",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("turn_id"),
        sa.UniqueConstraint(
            "tenant_id", "message_id", name="uq_agent_runtime_turns_tenant_message"
        ),
        schema=RUNTIME_SCHEMA,
    )
    op.create_table(
        "messages",
        sa.Column("message_id", sa.String(length=128), nullable=False),
        sa.Column("conversation_id", sa.String(length=128), nullable=False),
        sa.Column("turn_id", sa.String(length=128), nullable=True),
        sa.Column("role", sa.String(length=16), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("run_json", _jsonb(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "role in ('user', 'assistant')",
            name="ck_agent_runtime_messages_role",
        ),
        sa.ForeignKeyConstraint(
            ["conversation_id"],
            ["agent_runtime.conversations.conversation_id"],
            name="fk_agent_runtime_messages_conversation",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["turn_id"],
            ["agent_runtime.turns.turn_id"],
            name="fk_agent_runtime_messages_turn",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("message_id"),
        schema=RUNTIME_SCHEMA,
    )
    op.create_index(
        "ix_agent_runtime_messages_conversation_created",
        "messages",
        ["conversation_id", "created_at"],
        unique=False,
        schema=RUNTIME_SCHEMA,
    )
    op.create_table(
        "audit_traces",
        sa.Column("run_id", sa.String(length=128), nullable=False),
        sa.Column("conversation_id", sa.String(length=128), nullable=False),
        sa.Column("turn_id", sa.String(length=128), nullable=False),
        sa.Column("trace_json", _jsonb(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["conversation_id"],
            ["agent_runtime.conversations.conversation_id"],
            name="fk_agent_runtime_audit_conversation",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["turn_id"],
            ["agent_runtime.turns.turn_id"],
            name="fk_agent_runtime_audit_turn",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("run_id"),
        schema=RUNTIME_SCHEMA,
    )
    op.create_index(
        "ix_agent_runtime_audit_created",
        "audit_traces",
        ["created_at"],
        unique=False,
        schema=RUNTIME_SCHEMA,
    )
    op.create_table(
        "agent_runs",
        sa.Column("run_id", sa.String(length=128), nullable=False),
        sa.Column("conversation_id", sa.String(length=128), nullable=False),
        sa.Column("turn_id", sa.String(length=128), nullable=False),
        sa.Column("message_id", sa.String(length=128), nullable=False),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("principal_id", sa.String(length=128), nullable=False),
        sa.Column("request_id", sa.String(length=36), nullable=False),
        sa.Column("request_json", _jsonb(), nullable=False),
        sa.Column("request_sha256", sa.String(length=64), nullable=False),
        sa.Column(
            "api_version", sa.String(length=16), server_default="2.0", nullable=False
        ),
        sa.Column(
            "runtime_version",
            sa.String(length=64),
            server_default="runtime-harness-v2",
            nullable=False,
        ),
        sa.Column(
            "workflow_version",
            sa.String(length=64),
            server_default="cost-agent-graph-v2",
            nullable=False,
        ),
        sa.Column("budget_limits_json", _jsonb(), nullable=False),
        sa.Column("budget_usage_json", _jsonb(), nullable=False),
        sa.Column("next_event_sequence", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("lease_owner", sa.String(length=128), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finalizing_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancel_requested_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("result_json", _jsonb(), nullable=True),
        sa.Column("public_error_json", _jsonb(), nullable=True),
        sa.Column("last_event_id", sa.BigInteger(), nullable=True),
        sa.Column("checkpoint_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "status in ('queued', 'running', 'finalizing', 'retry_wait', "
            "'succeeded', 'failed', 'cancelled')",
            name="ck_agent_runs_status",
        ),
        sa.CheckConstraint("attempt_count >= 0", name="ck_agent_runs_attempt_count"),
        sa.CheckConstraint("api_version = '2.0'", name="ck_agent_runs_api_version"),
        sa.ForeignKeyConstraint(
            ["conversation_id"],
            ["agent_runtime.conversations.conversation_id"],
            name="fk_agent_runs_conversation",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("run_id"),
        sa.UniqueConstraint(
            "tenant_id", "message_id", name="uq_agent_runs_tenant_message"
        ),
        schema=RUNTIME_SCHEMA,
    )
    op.create_index(
        "uq_agent_runs_active_conversation",
        "agent_runs",
        ["conversation_id"],
        unique=True,
        schema=RUNTIME_SCHEMA,
        postgresql_where=sa.text(
            "status in ('queued', 'running', 'retry_wait', 'finalizing')"
        ),
    )
    op.create_index(
        "ix_agent_runs_claim",
        "agent_runs",
        ["status", "available_at", "lease_expires_at"],
        unique=False,
        schema=RUNTIME_SCHEMA,
    )
    op.create_index(
        "ix_agent_runs_owner_created",
        "agent_runs",
        ["tenant_id", "principal_id", "created_at"],
        unique=False,
        schema=RUNTIME_SCHEMA,
    )
    op.create_table(
        "agent_run_events",
        sa.Column("event_id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("run_id", sa.String(length=128), nullable=False),
        sa.Column("graph_sequence", sa.Integer(), nullable=False),
        sa.Column(
            "schema_version",
            sa.String(length=16),
            server_default="2.0",
            nullable=False,
        ),
        sa.Column("event_key", sa.String(length=255), nullable=True),
        sa.Column("event_type", sa.String(length=24), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=True),
        sa.Column("status", sa.String(length=24), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("payload_json", _jsonb(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "event_type in ('status', 'event', 'node', 'tool', 'policy', 'budget', "
            "'clarification', 'result', 'error')",
            name="ck_agent_run_events_type",
        ),
        sa.CheckConstraint(
            "schema_version = '2.0'",
            name="ck_agent_run_events_schema_version",
        ),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["agent_runtime.agent_runs.run_id"],
            name="fk_agent_run_events_run",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("event_id"),
        sa.UniqueConstraint(
            "run_id", "graph_sequence", name="uq_agent_run_events_run_sequence"
        ),
        sa.UniqueConstraint("event_key", name="uq_agent_run_events_event_key"),
        schema=RUNTIME_SCHEMA,
    )
    op.create_index(
        "ix_agent_run_events_run_event",
        "agent_run_events",
        ["run_id", "event_id"],
        unique=False,
        schema=RUNTIME_SCHEMA,
    )
    op.create_table(
        "agent_workers",
        sa.Column("worker_id", sa.String(length=128), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("hostname", sa.String(length=255), nullable=False),
        sa.Column("process_id", sa.Integer(), nullable=False),
        sa.Column("app_version", sa.String(length=64), nullable=False),
        sa.Column("current_run_id", sa.String(length=128), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_heartbeat_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "status in ('running', 'stopping', 'stopped')",
            name="ck_agent_workers_status",
        ),
        sa.PrimaryKeyConstraint("worker_id"),
        schema=RUNTIME_SCHEMA,
    )
    op.create_index(
        "ix_agent_workers_heartbeat",
        "agent_workers",
        ["last_heartbeat_at"],
        unique=False,
        schema=RUNTIME_SCHEMA,
    )


def _create_checkpoint_tables() -> None:
    op.create_table(
        "checkpoint_migrations",
        sa.Column("v", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("v"),
        schema=CHECKPOINT_SCHEMA,
    )
    op.create_table(
        "checkpoints",
        sa.Column("thread_id", sa.Text(), nullable=False),
        sa.Column("checkpoint_ns", sa.Text(), nullable=False),
        sa.Column("checkpoint_id", sa.Text(), nullable=False),
        sa.Column("parent_checkpoint_id", sa.Text(), nullable=True),
        sa.Column("type", sa.Text(), nullable=True),
        sa.Column("checkpoint", _jsonb(), nullable=False),
        sa.Column("metadata", _jsonb(), nullable=False),
        sa.PrimaryKeyConstraint("thread_id", "checkpoint_ns", "checkpoint_id"),
        schema=CHECKPOINT_SCHEMA,
    )
    op.create_table(
        "checkpoint_blobs",
        sa.Column("thread_id", sa.Text(), nullable=False),
        sa.Column("checkpoint_ns", sa.Text(), nullable=False),
        sa.Column("channel", sa.Text(), nullable=False),
        sa.Column("version", sa.Text(), nullable=False),
        sa.Column("type", sa.Text(), nullable=False),
        sa.Column("blob", sa.LargeBinary(), nullable=True),
        sa.PrimaryKeyConstraint("thread_id", "checkpoint_ns", "channel", "version"),
        schema=CHECKPOINT_SCHEMA,
    )
    op.create_table(
        "checkpoint_writes",
        sa.Column("thread_id", sa.Text(), nullable=False),
        sa.Column("checkpoint_ns", sa.Text(), nullable=False),
        sa.Column("checkpoint_id", sa.Text(), nullable=False),
        sa.Column("task_id", sa.Text(), nullable=False),
        sa.Column("idx", sa.Integer(), nullable=False),
        sa.Column("channel", sa.Text(), nullable=False),
        sa.Column("type", sa.Text(), nullable=True),
        sa.Column("blob", sa.LargeBinary(), nullable=False),
        sa.Column("task_path", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint(
            "thread_id", "checkpoint_ns", "checkpoint_id", "task_id", "idx"
        ),
        schema=CHECKPOINT_SCHEMA,
    )


def downgrade() -> None:
    for schema in (
        ARTIFACT_SCHEMA,
        CHECKPOINT_SCHEMA,
        RUNTIME_SCHEMA,
        COST_SCHEMA,
    ):
        op.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
