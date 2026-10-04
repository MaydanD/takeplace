"""Minimal live placement for Stage 7 atomic WALK_IN.

Revision ID: 20261004_0006
Revises: 20261003_0005
"""

import sqlalchemy as sa
from alembic import op

revision = "20261004_0006"
down_revision = "20261003_0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "booking_live_tables",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column("venue_id", sa.BigInteger(), nullable=False),
        sa.Column("booking_id", sa.BigInteger(), nullable=False),
        sa.Column("business_date", sa.Date(), nullable=False),
        sa.Column("table_id", sa.BigInteger(), nullable=False),
        sa.Column("live_since", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("table_id", "business_date"),
        sa.ForeignKeyConstraint(
            ["booking_id", "venue_id", "business_date"],
            ["bookings.id", "bookings.venue_id", "bookings.business_date"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["table_id", "venue_id"], ["tables.id", "tables.venue_id"], ondelete="CASCADE"
        ),
    )
    op.create_index("ix_booking_live_tables_booking_id", "booking_live_tables", ["booking_id"])
    op.create_index(
        "ix_booking_live_tables_venue_id_business_date",
        "booking_live_tables",
        ["venue_id", "business_date"],
    )


def downgrade() -> None:
    op.drop_table("booking_live_tables")
