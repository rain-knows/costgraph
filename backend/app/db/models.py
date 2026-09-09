from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKeyConstraint,
    Index,
    Integer,
    LargeBinary,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

COST_SCHEMA = "cost_data"
ARTIFACT_SCHEMA = "agent_output"
RUNTIME_SCHEMA = "agent_runtime"
CHECKPOINT_SCHEMA = "agent_checkpoint"


class Base(DeclarativeBase):
    pass


class Part(Base):
    __tablename__ = "parts"
    __table_args__ = (
        UniqueConstraint("tenant_id", "part_id", name="uq_parts_tenant_part"),
        UniqueConstraint(
            "tenant_id",
            "source_system",
            "source_record_id",
            name="uq_parts_source_record",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "batch_id"],
            [
                f"{COST_SCHEMA}.data_load_batches.tenant_id",
                f"{COST_SCHEMA}.data_load_batches.batch_id",
            ],
            name="fk_parts_batch",
        ),
        {"schema": COST_SCHEMA},
    )
    tenant_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    part_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    part_number: Mapped[str] = mapped_column(String(128), nullable=False)
    part_description: Mapped[str] = mapped_column(String(500), nullable=False)
    part_type: Mapped[str] = mapped_column(String(32), nullable=False)
    product_family: Mapped[str | None] = mapped_column(String(128))
    unit: Mapped[str] = mapped_column(String(32), nullable=False)
    source_system: Mapped[str] = mapped_column(String(64), nullable=False)
    source_record_id: Mapped[str] = mapped_column(String(128), nullable=False)
    batch_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    raw_payload: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )


class CostEvent(Base):
    __tablename__ = "cost_events"
    __table_args__ = (
        UniqueConstraint("tenant_id", "event_id", name="uq_cost_events_tenant_event"),
        UniqueConstraint(
            "tenant_id",
            "output_batch_id",
            name="uq_cost_events_tenant_output_batch",
        ),
        UniqueConstraint(
            "tenant_id",
            "source_system",
            "source_record_id",
            name="uq_cost_events_source_record",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "part_id"],
            [f"{COST_SCHEMA}.parts.tenant_id", f"{COST_SCHEMA}.parts.part_id"],
            name="fk_cost_events_part",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "batch_id"],
            [
                f"{COST_SCHEMA}.data_load_batches.tenant_id",
                f"{COST_SCHEMA}.data_load_batches.batch_id",
            ],
            name="fk_cost_events_batch",
        ),
        CheckConstraint(
            "event_type in ('purchase', 'process')", name="ck_cost_events_type"
        ),
        CheckConstraint(
            "qualified_quantity >= 0 and defective_quantity >= 0",
            name="ck_cost_events_quantities",
        ),
        CheckConstraint(
            "qualified_quantity + defective_quantity > 0",
            name="ck_cost_events_positive_output",
        ),
        CheckConstraint(
            "to_char(completion_time, 'YYYY-MM') = period",
            name="ck_cost_events_period_date",
        ),
        Index("ix_cost_events_period", "tenant_id", "period"),
        Index("ix_cost_events_finished", "tenant_id", "part_id", "period"),
        {"schema": COST_SCHEMA},
    )
    tenant_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    event_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    event_type: Mapped[str] = mapped_column(String(16), nullable=False)
    output_batch_id: Mapped[str] = mapped_column(String(128), nullable=False)
    part_id: Mapped[str] = mapped_column(String(64), nullable=False)
    period: Mapped[str] = mapped_column(String(7), nullable=False)
    cost_center_code: Mapped[str | None] = mapped_column(String(64))
    cost_center_name: Mapped[str | None] = mapped_column(String(200))
    work_order_number: Mapped[str | None] = mapped_column(String(128))
    lot_number: Mapped[str | None] = mapped_column(String(128))
    process_code: Mapped[str | None] = mapped_column(String(64))
    process_name: Mapped[str | None] = mapped_column(String(200))
    completion_time: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    qualified_quantity: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False)
    defective_quantity: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False)
    unit: Mapped[str] = mapped_column(String(32), nullable=False)
    machine_hours: Mapped[Decimal] = mapped_column(
        Numeric(18, 4), nullable=False, default=0
    )
    labor_hours: Mapped[Decimal] = mapped_column(
        Numeric(18, 4), nullable=False, default=0
    )
    source_system: Mapped[str] = mapped_column(String(64), nullable=False)
    source_record_id: Mapped[str] = mapped_column(String(128), nullable=False)
    raw_payload: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    batch_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )


class CostEventInput(Base):
    __tablename__ = "cost_event_inputs"
    __table_args__ = (
        UniqueConstraint("tenant_id", "input_id", name="uq_cost_event_inputs_id"),
        ForeignKeyConstraint(
            ["tenant_id", "event_id"],
            [
                f"{COST_SCHEMA}.cost_events.tenant_id",
                f"{COST_SCHEMA}.cost_events.event_id",
            ],
            name="fk_cost_event_inputs_target",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "source_event_id"],
            [
                f"{COST_SCHEMA}.cost_events.tenant_id",
                f"{COST_SCHEMA}.cost_events.event_id",
            ],
            name="fk_cost_event_inputs_source",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "batch_id"],
            [
                f"{COST_SCHEMA}.data_load_batches.tenant_id",
                f"{COST_SCHEMA}.data_load_batches.batch_id",
            ],
            name="fk_cost_event_inputs_batch",
        ),
        CheckConstraint("consumed_quantity > 0", name="ck_cost_event_inputs_positive"),
        {"schema": COST_SCHEMA},
    )
    tenant_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    input_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    event_id: Mapped[str] = mapped_column(String(128), nullable=False)
    source_event_id: Mapped[str] = mapped_column(String(128), nullable=False)
    consumed_quantity: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False)
    unit: Mapped[str] = mapped_column(String(32), nullable=False)
    source_system: Mapped[str] = mapped_column(String(64), nullable=False)
    source_record_id: Mapped[str] = mapped_column(String(128), nullable=False)
    raw_payload: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    batch_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )


class CostRecord(Base):
    __tablename__ = "cost_records"
    __table_args__ = (
        UniqueConstraint("tenant_id", "cost_record_id", name="uq_cost_records_id"),
        UniqueConstraint(
            "tenant_id",
            "source_system",
            "source_record_id",
            name="uq_cost_records_source_record",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "event_id"],
            [
                f"{COST_SCHEMA}.cost_events.tenant_id",
                f"{COST_SCHEMA}.cost_events.event_id",
            ],
            name="fk_cost_records_event",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "batch_id"],
            [
                f"{COST_SCHEMA}.data_load_batches.tenant_id",
                f"{COST_SCHEMA}.data_load_batches.batch_id",
            ],
            name="fk_cost_records_batch",
        ),
        CheckConstraint("amount >= 0", name="ck_cost_records_nonnegative"),
        CheckConstraint("currency = 'CNY'", name="ck_cost_records_currency"),
        {"schema": COST_SCHEMA},
    )
    tenant_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    cost_record_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    event_id: Mapped[str] = mapped_column(String(128), nullable=False)
    cost_code: Mapped[str] = mapped_column(String(64), nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    incurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    source_system: Mapped[str] = mapped_column(String(64), nullable=False)
    source_document_no: Mapped[str] = mapped_column(String(128), nullable=False)
    source_document_line: Mapped[str | None] = mapped_column(String(64))
    source_record_id: Mapped[str] = mapped_column(String(128), nullable=False)
    raw_payload: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    batch_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )


class DataLoadBatch(Base):
    __tablename__ = "data_load_batches"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "source_system",
            "source_snapshot_hash",
            name="uq_data_load_batches_snapshot",
        ),
        UniqueConstraint(
            "tenant_id",
            "batch_id",
            name="uq_data_load_batches_tenant_batch",
        ),
        CheckConstraint(
            "status in ('created', 'validating', 'validated', 'published', "
            "'superseded', 'failed')",
            name="ck_data_load_batches_status",
        ),
        CheckConstraint(
            "total_rows >= 0 and valid_rows >= 0 and error_rows >= 0",
            name="ck_data_load_batches_nonnegative_counts",
        ),
        CheckConstraint(
            "valid_rows + error_rows <= total_rows",
            name="ck_data_load_batches_bounded_counts",
        ),
        Index(
            "uq_data_load_batches_active_tenant",
            "tenant_id",
            unique=True,
            postgresql_where=text("status = 'published'"),
        ),
        {"schema": COST_SCHEMA},
    )

    batch_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False)
    source_system: Mapped[str] = mapped_column(String(64), nullable=False)
    source_file: Mapped[str | None] = mapped_column(String(500))
    source_snapshot_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    total_rows: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    valid_rows: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error_rows: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error_summary: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )


class DataLoadError(Base):
    __tablename__ = "data_load_errors"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "batch_id"],
            [
                f"{COST_SCHEMA}.data_load_batches.tenant_id",
                f"{COST_SCHEMA}.data_load_batches.batch_id",
            ],
            name="fk_data_load_errors_batch",
        ),
        {"schema": COST_SCHEMA},
    )

    error_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False)
    batch_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    source_table: Mapped[str] = mapped_column(String(64), nullable=False)
    source_record_id: Mapped[str | None] = mapped_column(String(128))
    error_code: Mapped[str] = mapped_column(String(64), nullable=False)
    error_message: Mapped[str] = mapped_column(Text, nullable=False)
    raw_payload: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )


class AgentArtifact(Base):
    __tablename__ = "artifacts"
    __table_args__ = (
        UniqueConstraint("tenant_id", "run_id", name="uq_agent_artifacts_tenant_run"),
        UniqueConstraint(
            "tenant_id", "message_id", name="uq_agent_artifacts_tenant_message"
        ),
        CheckConstraint(
            "artifact_type = 'cost_report'",
            name="ck_agent_artifacts_type",
        ),
        CheckConstraint(
            "char_length(report_sha256) = 64",
            name="ck_agent_artifacts_report_sha256",
        ),
        CheckConstraint(
            "char_length(data_snapshot_id) = 64",
            name="ck_agent_artifacts_data_snapshot_id",
        ),
        Index(
            "ix_agent_artifacts_owner_lifecycle_created",
            "tenant_id",
            "principal_id",
            "deleted_at",
            "created_at",
        ),
        Index(
            "ix_agent_artifacts_owner_part_period",
            "tenant_id",
            "principal_id",
            "part_id",
            "period",
        ),
        {"schema": ARTIFACT_SCHEMA},
    )

    artifact_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    artifact_type: Mapped[str] = mapped_column(String(32), nullable=False)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False)
    principal_id: Mapped[str] = mapped_column(String(128), nullable=False)
    conversation_id: Mapped[str] = mapped_column(String(128), nullable=False)
    turn_id: Mapped[str] = mapped_column(String(128), nullable=False)
    message_id: Mapped[str] = mapped_column(String(128), nullable=False)
    run_id: Mapped[str] = mapped_column(String(128), nullable=False)
    conversation_title: Mapped[str] = mapped_column(String(200), nullable=False)
    part_id: Mapped[str] = mapped_column(String(64), nullable=False)
    part_number: Mapped[str] = mapped_column(String(128), nullable=False)
    part_description: Mapped[str] = mapped_column(String(500), nullable=False)
    period: Mapped[str] = mapped_column(String(64), nullable=False)
    report_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    report_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    data_snapshot_id: Mapped[str] = mapped_column(String(64), nullable=False)
    report_schema_version: Mapped[str] = mapped_column(String(32), nullable=False)
    rule_version: Mapped[str] = mapped_column(String(64), nullable=False)
    prompt_version: Mapped[str] = mapped_column(String(64), nullable=False)
    code_version: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class RuntimeConversation(Base):
    __tablename__ = "conversations"
    __table_args__ = (
        Index(
            "ix_agent_runtime_conversations_owner_updated",
            "tenant_id",
            "principal_id",
            "updated_at",
        ),
        {"schema": RUNTIME_SCHEMA},
    )

    conversation_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    principal_id: Mapped[str] = mapped_column(String(128), nullable=False)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    routing_mode: Mapped[str] = mapped_column(String(16), nullable=False)
    enabled_capabilities_json: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    context_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )


class RuntimeTurn(Base):
    __tablename__ = "turns"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "message_id", name="uq_agent_runtime_turns_tenant_message"
        ),
        ForeignKeyConstraint(
            ["conversation_id"],
            [f"{RUNTIME_SCHEMA}.conversations.conversation_id"],
            name="fk_agent_runtime_turns_conversation",
            ondelete="CASCADE",
        ),
        {"schema": RUNTIME_SCHEMA},
    )

    turn_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    conversation_id: Mapped[str] = mapped_column(String(128), nullable=False)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False)
    principal_id: Mapped[str] = mapped_column(String(128), nullable=False)
    message_id: Mapped[str] = mapped_column(String(128), nullable=False)
    question: Mapped[str] = mapped_column(Text, nullable=False)
    result_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )


class RuntimeMessage(Base):
    __tablename__ = "messages"
    __table_args__ = (
        ForeignKeyConstraint(
            ["conversation_id"],
            [f"{RUNTIME_SCHEMA}.conversations.conversation_id"],
            name="fk_agent_runtime_messages_conversation",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["turn_id"],
            [f"{RUNTIME_SCHEMA}.turns.turn_id"],
            name="fk_agent_runtime_messages_turn",
            ondelete="CASCADE",
        ),
        CheckConstraint(
            "role in ('user', 'assistant')", name="ck_agent_runtime_messages_role"
        ),
        Index(
            "ix_agent_runtime_messages_conversation_created",
            "conversation_id",
            "created_at",
        ),
        {"schema": RUNTIME_SCHEMA},
    )

    message_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    conversation_id: Mapped[str] = mapped_column(String(128), nullable=False)
    turn_id: Mapped[str | None] = mapped_column(String(128))
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    run_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )


class RuntimeAuditTrace(Base):
    __tablename__ = "audit_traces"
    __table_args__ = (
        ForeignKeyConstraint(
            ["conversation_id"],
            [f"{RUNTIME_SCHEMA}.conversations.conversation_id"],
            name="fk_agent_runtime_audit_conversation",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["turn_id"],
            [f"{RUNTIME_SCHEMA}.turns.turn_id"],
            name="fk_agent_runtime_audit_turn",
            ondelete="CASCADE",
        ),
        Index("ix_agent_runtime_audit_created", "created_at"),
        {"schema": RUNTIME_SCHEMA},
    )

    run_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    conversation_id: Mapped[str] = mapped_column(String(128), nullable=False)
    turn_id: Mapped[str] = mapped_column(String(128), nullable=False)
    trace_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )


class RuntimeAgentRun(Base):
    __tablename__ = "agent_runs"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "message_id", name="uq_agent_runs_tenant_message"
        ),
        ForeignKeyConstraint(
            ["conversation_id"],
            [f"{RUNTIME_SCHEMA}.conversations.conversation_id"],
            name="fk_agent_runs_conversation",
            ondelete="CASCADE",
        ),
        CheckConstraint(
            "status in ('queued', 'running', 'finalizing', 'retry_wait', "
            "'succeeded', 'failed', 'cancelled')",
            name="ck_agent_runs_status",
        ),
        CheckConstraint("attempt_count >= 0", name="ck_agent_runs_attempt_count"),
        CheckConstraint("api_version = '2.0'", name="ck_agent_runs_api_version"),
        Index(
            "uq_agent_runs_active_conversation",
            "conversation_id",
            unique=True,
            postgresql_where=text(
                "status in ('queued', 'running', 'retry_wait', 'finalizing')"
            ),
        ),
        Index("ix_agent_runs_claim", "status", "available_at", "lease_expires_at"),
        Index(
            "ix_agent_runs_owner_created",
            "tenant_id",
            "principal_id",
            "created_at",
        ),
        {"schema": RUNTIME_SCHEMA},
    )

    run_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    conversation_id: Mapped[str] = mapped_column(String(128), nullable=False)
    turn_id: Mapped[str] = mapped_column(String(128), nullable=False)
    message_id: Mapped[str] = mapped_column(String(128), nullable=False)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False)
    principal_id: Mapped[str] = mapped_column(String(128), nullable=False)
    request_id: Mapped[str] = mapped_column(String(36), nullable=False)
    request_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    request_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    api_version: Mapped[str] = mapped_column(
        String(16), nullable=False, default="2.0", server_default="2.0"
    )
    runtime_version: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        default="runtime-harness-v2",
        server_default="runtime-harness-v2",
    )
    workflow_version: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        default="cost-agent-graph-v2",
        server_default="cost-agent-graph-v2",
    )
    budget_limits_json: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict
    )
    budget_usage_json: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict
    )
    next_event_sequence: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    available_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    lease_owner: Mapped[str | None] = mapped_column(String(128))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finalizing_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancel_requested_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    result_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    public_error_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    last_event_id: Mapped[int | None] = mapped_column(BigInteger)
    checkpoint_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )


class RuntimeAgentRunEvent(Base):
    __tablename__ = "agent_run_events"
    __table_args__ = (
        UniqueConstraint(
            "run_id", "graph_sequence", name="uq_agent_run_events_run_sequence"
        ),
        UniqueConstraint("event_key", name="uq_agent_run_events_event_key"),
        ForeignKeyConstraint(
            ["run_id"],
            [f"{RUNTIME_SCHEMA}.agent_runs.run_id"],
            name="fk_agent_run_events_run",
            ondelete="CASCADE",
        ),
        CheckConstraint(
            "event_type in ('status', 'event', 'node', 'tool', 'policy', 'budget', "
            "'clarification', 'result', 'error')",
            name="ck_agent_run_events_type",
        ),
        CheckConstraint(
            "schema_version = '2.0'", name="ck_agent_run_events_schema_version"
        ),
        Index("ix_agent_run_events_run_event", "run_id", "event_id"),
        {"schema": RUNTIME_SCHEMA},
    )

    event_id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, autoincrement=True
    )
    run_id: Mapped[str] = mapped_column(String(128), nullable=False)
    graph_sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    schema_version: Mapped[str] = mapped_column(
        String(16), nullable=False, default="2.0", server_default="2.0"
    )
    event_key: Mapped[str | None] = mapped_column(String(255))
    event_type: Mapped[str] = mapped_column(String(24), nullable=False)
    name: Mapped[str | None] = mapped_column(String(128))
    status: Mapped[str | None] = mapped_column(String(24))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    payload_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )


class RuntimeAgentWorker(Base):
    __tablename__ = "agent_workers"
    __table_args__ = (
        CheckConstraint(
            "status in ('running', 'stopping', 'stopped')",
            name="ck_agent_workers_status",
        ),
        Index("ix_agent_workers_heartbeat", "last_heartbeat_at"),
        {"schema": RUNTIME_SCHEMA},
    )

    worker_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    hostname: Mapped[str] = mapped_column(String(255), nullable=False)
    process_id: Mapped[int] = mapped_column(Integer, nullable=False)
    app_version: Mapped[str] = mapped_column(String(64), nullable=False)
    current_run_id: Mapped[str | None] = mapped_column(String(128))
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    last_heartbeat_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )


class CheckpointMigration(Base):
    __tablename__ = "checkpoint_migrations"
    __table_args__ = ({"schema": CHECKPOINT_SCHEMA},)

    v: Mapped[int] = mapped_column(Integer, primary_key=True)


class AgentCheckpoint(Base):
    __tablename__ = "checkpoints"
    __table_args__ = ({"schema": CHECKPOINT_SCHEMA},)

    thread_id: Mapped[str] = mapped_column(Text, primary_key=True)
    checkpoint_ns: Mapped[str] = mapped_column(Text, primary_key=True, default="")
    checkpoint_id: Mapped[str] = mapped_column(Text, primary_key=True)
    parent_checkpoint_id: Mapped[str | None] = mapped_column(Text)
    type: Mapped[str | None] = mapped_column(Text)
    checkpoint: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(
        "metadata", JSONB, nullable=False, default=dict
    )


class AgentCheckpointBlob(Base):
    __tablename__ = "checkpoint_blobs"
    __table_args__ = ({"schema": CHECKPOINT_SCHEMA},)

    thread_id: Mapped[str] = mapped_column(Text, primary_key=True)
    checkpoint_ns: Mapped[str] = mapped_column(Text, primary_key=True, default="")
    channel: Mapped[str] = mapped_column(Text, primary_key=True)
    version: Mapped[str] = mapped_column(Text, primary_key=True)
    type: Mapped[str] = mapped_column(Text, nullable=False)
    blob: Mapped[bytes | None] = mapped_column(LargeBinary)


class AgentCheckpointWrite(Base):
    __tablename__ = "checkpoint_writes"
    __table_args__ = ({"schema": CHECKPOINT_SCHEMA},)

    thread_id: Mapped[str] = mapped_column(Text, primary_key=True)
    checkpoint_ns: Mapped[str] = mapped_column(Text, primary_key=True, default="")
    checkpoint_id: Mapped[str] = mapped_column(Text, primary_key=True)
    task_id: Mapped[str] = mapped_column(Text, primary_key=True)
    idx: Mapped[int] = mapped_column(Integer, primary_key=True)
    channel: Mapped[str] = mapped_column(Text, nullable=False)
    type: Mapped[str | None] = mapped_column(Text)
    blob: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    task_path: Mapped[str] = mapped_column(Text, nullable=False, default="")
