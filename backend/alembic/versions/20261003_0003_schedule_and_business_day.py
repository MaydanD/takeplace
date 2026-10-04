"""Schedule and business day: weekly_schedules, schedule_exceptions.

Revision ID: 20261003_0003
Revises: 20261003_0002
Create Date: 2026-10-03

Introduces the venue's recurring weekly schedule and its date-specific
exceptions (PROJECT-SPEC §5.2, §5.3), each tenant-scoped by ``venue_id``.

Invariants kept in the database:

* ``weekday BETWEEN 0 AND 6``;
* for an open rule both times are present, grid-aligned (whole minutes that are
  multiples of 5, zero seconds) and different; for a closed rule both are NULL;
* ``UNIQUE (venue_id, weekday)`` and ``UNIQUE (venue_id, date)`` — at most one
  rule per weekday and one exception per date per venue.

Adjacent-shift overlap and ``business_date`` are application/domain rules
(§44) — a shift's absolute span depends on the venue timezone, so it cannot be
expressed as a plain CHECK.

Rollback plan: additive only. Downgrade drops both tables; safe while later
stages' FKs on these tables do not yet exist (bookings reference schedule only
through their snapshot columns, added in Stage 5).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20261003_0003"
down_revision: str | None = "20261003_0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_GRID_SQL = (
    "EXTRACT(MINUTE FROM open_time)::int % 5 = 0 "
    "AND EXTRACT(SECOND FROM open_time) = 0 "
    "AND EXTRACT(MINUTE FROM close_time)::int % 5 = 0 "
    "AND EXTRACT(SECOND FROM close_time) = 0"
)
_OPEN_SQL = (
    f"open_time IS NOT NULL AND close_time IS NOT NULL AND open_time <> close_time AND {_GRID_SQL}"
)


def _timestamp(name: str) -> sa.Column:
    return sa.Column(
        name,
        postgresql.TIMESTAMP(timezone=True),
        server_default=sa.text("now()"),
        nullable=False,
    )


def upgrade() -> None:
    op.create_table(
        "weekly_schedules",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("venue_id", sa.BigInteger(), nullable=False),
        sa.Column("weekday", sa.SmallInteger(), nullable=False),
        sa.Column("is_open", sa.Boolean(), nullable=False),
        sa.Column("open_time", sa.Time(), nullable=True),
        sa.Column("close_time", sa.Time(), nullable=True),
        _timestamp("created_at"),
        _timestamp("updated_at"),
        sa.CheckConstraint(
            "weekday BETWEEN 0 AND 6", name=op.f("ck_weekly_schedules_weekday_bounds")
        ),
        sa.CheckConstraint(
            f"(is_open AND {_OPEN_SQL}) OR (NOT is_open AND open_time IS NULL AND close_time IS NULL)",
            name=op.f("ck_weekly_schedules_consistency"),
        ),
        sa.ForeignKeyConstraint(
            ["venue_id"],
            ["venues.id"],
            name=op.f("fk_weekly_schedules_venue_id_venues"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_weekly_schedules")),
        sa.UniqueConstraint("venue_id", "weekday", name=op.f("uq_weekly_schedules_venue_id_weekday")),
    )

    op.create_table(
        "schedule_exceptions",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("venue_id", sa.BigInteger(), nullable=False),
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column("is_closed", sa.Boolean(), nullable=False),
        sa.Column("open_time", sa.Time(), nullable=True),
        sa.Column("close_time", sa.Time(), nullable=True),
        _timestamp("created_at"),
        _timestamp("updated_at"),
        sa.CheckConstraint(
            "(is_closed AND open_time IS NULL AND close_time IS NULL) OR "
            f"(NOT is_closed AND {_OPEN_SQL})",
            name=op.f("ck_schedule_exceptions_consistency"),
        ),
        sa.ForeignKeyConstraint(
            ["venue_id"],
            ["venues.id"],
            name=op.f("fk_schedule_exceptions_venue_id_venues"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_schedule_exceptions")),
        sa.UniqueConstraint("venue_id", "date", name=op.f("uq_schedule_exceptions_venue_id_date")),
    )


def downgrade() -> None:
    op.drop_table("schedule_exceptions")
    op.drop_table("weekly_schedules")
