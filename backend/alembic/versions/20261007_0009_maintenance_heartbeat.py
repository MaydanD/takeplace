"""Stage 13 remediation: durable maintenance heartbeat (§42.2, §47).

Revision ID: 20261007_0009
Revises: 20261006_0008

Retention/anonymization must not depend on VK delivery (audit FIX-02). The
background worker now always runs the periodic maintenance loop and records the
outcome of each pass in a single fixed row, so ``/health/ops`` can flag a
deployment whose retention has silently stopped. The migration is purely
additive: it creates one new table and touches nothing else.
"""

import sqlalchemy as sa
from alembic import op

revision = "20261007_0009"
down_revision = "20261006_0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "maintenance_heartbeat",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("last_success_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("last_error_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("maintenance_heartbeat")
