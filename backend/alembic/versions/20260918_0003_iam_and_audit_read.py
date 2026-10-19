"""Add IAM principal roles and data scopes.

Revision ID: 20260918_0003
Revises: 20260917_0002
Create Date: 2026-09-18
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "20260918_0003"
down_revision: str | None = "20260917_0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

COST_SCHEMA = "cost_data"

AUDIT_ACTIONS = (
    "submitted",
    "validated",
    "validation_failed",
    "approved",
    "rejected",
    "publishing",
    "published",
    "publish_failed",
    "cancelled",
    "iam.roles_updated",
    "iam.scope_updated",
)


def _audit_action_expression(actions: tuple[str, ...] = AUDIT_ACTIONS) -> str:
    values = ", ".join(f"'{action}'" for action in actions)
    return f"action in ({values})"


def upgrade() -> None:
    op.drop_constraint(
        "ck_governance_audit_events_action",
        "governance_audit_events",
        schema=COST_SCHEMA,
        type_="check",
    )
    op.create_check_constraint(
        "ck_governance_audit_events_action",
        "governance_audit_events",
        _audit_action_expression(),
        schema=COST_SCHEMA,
    )
    op.create_table(
        "iam_principal_roles",
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("principal_id", sa.String(length=128), nullable=False),
        sa.Column("roles", postgresql.ARRAY(sa.String(length=32)), nullable=False),
        sa.Column("updated_by", sa.String(length=128), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "cardinality(roles) > 0", name="ck_iam_principal_roles_non_empty"
        ),
        sa.CheckConstraint(
            "roles <@ ARRAY['viewer','data_operator','data_approver',"
            "'tenant_admin']::varchar[]",
            name="ck_iam_principal_roles_known",
        ),
        sa.PrimaryKeyConstraint("tenant_id", "principal_id"),
        schema=COST_SCHEMA,
    )
    op.create_index(
        "ix_iam_principal_roles_tenant",
        "iam_principal_roles",
        ["tenant_id"],
        schema=COST_SCHEMA,
    )
    op.create_table(
        "iam_data_scopes",
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("principal_id", sa.String(length=128), nullable=False),
        sa.Column(
            "allowed_part_ids",
            postgresql.ARRAY(sa.String(length=64)),
            nullable=False,
        ),
        sa.Column("allowed_period_start", sa.String(length=7), nullable=True),
        sa.Column("allowed_period_end", sa.String(length=7), nullable=True),
        sa.Column("updated_by", sa.String(length=128), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "allowed_period_start IS NULL OR "
            "allowed_period_start ~ '^\\d{4}-(0[1-9]|1[0-2])$'",
            name="ck_iam_data_scopes_start_format",
        ),
        sa.CheckConstraint(
            "allowed_period_end IS NULL OR "
            "allowed_period_end ~ '^\\d{4}-(0[1-9]|1[0-2])$'",
            name="ck_iam_data_scopes_end_format",
        ),
        sa.CheckConstraint(
            "allowed_period_start IS NULL OR allowed_period_end IS NULL "
            "OR allowed_period_start <= allowed_period_end",
            name="ck_iam_data_scopes_period_order",
        ),
        sa.PrimaryKeyConstraint("tenant_id", "principal_id"),
        schema=COST_SCHEMA,
    )
    op.create_index(
        "ix_iam_data_scopes_tenant",
        "iam_data_scopes",
        ["tenant_id"],
        schema=COST_SCHEMA,
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_governance_audit_events_action",
        "governance_audit_events",
        schema=COST_SCHEMA,
        type_="check",
    )
    op.create_check_constraint(
        "ck_governance_audit_events_action",
        "governance_audit_events",
        _audit_action_expression(AUDIT_ACTIONS[:9]),
        schema=COST_SCHEMA,
    )
    op.drop_index(
        "ix_iam_data_scopes_tenant",
        table_name="iam_data_scopes",
        schema=COST_SCHEMA,
    )
    op.drop_table("iam_data_scopes", schema=COST_SCHEMA)
    op.drop_index(
        "ix_iam_principal_roles_tenant",
        table_name="iam_principal_roles",
        schema=COST_SCHEMA,
    )
    op.drop_table("iam_principal_roles", schema=COST_SCHEMA)
