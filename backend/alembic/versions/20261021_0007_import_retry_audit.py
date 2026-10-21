"""Record import retry scheduling and failure reasons in the governance audit.

Revision ID: 20261021_0007
Revises: 20261021_0006
Create Date: 2026-10-21
"""

from __future__ import annotations

from alembic import op

revision: str = "20261021_0007"
down_revision: str | None = "20261021_0006"
branch_labels: str | None = None
depends_on: str | None = None

CONSTRAINT = "ck_governance_audit_events_action"
TABLE = "governance_audit_events"
SCHEMA = "cost_data"


def upgrade() -> None:
    op.drop_constraint(CONSTRAINT, TABLE, schema=SCHEMA, type_="check")
    op.create_check_constraint(
        CONSTRAINT,
        TABLE,
        "action in ('submitted', 'validated', 'validation_failed', 'approved', "
        "'rejected', 'publishing', 'published', 'publish_failed', 'cancelled', "
        "'iam.roles_updated', 'iam.scope_updated', 'validation_retry_scheduled', "
        "'publish_retry_scheduled')",
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_constraint(CONSTRAINT, TABLE, schema=SCHEMA, type_="check")
    op.create_check_constraint(
        CONSTRAINT,
        TABLE,
        "action in ('submitted', 'validated', 'validation_failed', 'approved', "
        "'rejected', 'publishing', 'published', 'publish_failed', 'cancelled', "
        "'iam.roles_updated', 'iam.scope_updated')",
        schema=SCHEMA,
    )
