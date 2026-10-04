"""Weekly schedule and date exceptions (PROJECT-SPEC §5.2, §5.3).

Both tables are tenant-scoped and carry the 5-minute grid invariants as CHECK
constraints, so the database is the last arbiter: an invalid row cannot be
written even by direct SQL, not only through the API/domain validators.
"""

from __future__ import annotations

from datetime import date, datetime, time

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    ForeignKey,
    Identity,
    SmallInteger,
    Time,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import TIMESTAMP
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

# ``EXTRACT(...)::int % 5 = 0`` matches ``minute(...) % 5 = 0``; the SECONDS
# predicate also rejects fractional seconds, per PROJECT-SPEC §44.1.
_GRID_SQL = (
    "EXTRACT(MINUTE FROM open_time)::int % 5 = 0 "
    "AND EXTRACT(SECOND FROM open_time) = 0 "
    "AND EXTRACT(MINUTE FROM close_time)::int % 5 = 0 "
    "AND EXTRACT(SECOND FROM close_time) = 0"
)
_OPEN_SQL = (
    f"open_time IS NOT NULL AND close_time IS NOT NULL AND open_time <> close_time AND {_GRID_SQL}"
)


class WeeklySchedule(Base):
    """One recurring weekday rule. Exactly one row per ``(venue_id, weekday)``."""

    __tablename__ = "weekly_schedules"
    __table_args__ = (
        CheckConstraint("weekday BETWEEN 0 AND 6", name="weekday_bounds"),
        CheckConstraint(
            f"(is_open AND {_OPEN_SQL}) OR (NOT is_open AND open_time IS NULL "
            "AND close_time IS NULL)",
            name="consistency",
        ),
        UniqueConstraint("venue_id", "weekday"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    venue_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("venues.id", ondelete="CASCADE"), nullable=False
    )
    weekday: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    is_open: Mapped[bool] = mapped_column(Boolean, nullable=False)
    # close_time < open_time means the shift ends on the next calendar day (§5.2);
    # there is deliberately no `closes_next_day` column.
    open_time: Mapped[time | None] = mapped_column(Time, nullable=True)
    close_time: Mapped[time | None] = mapped_column(Time, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=text("now()")
    )


class ScheduleException(Base):
    """A date-specific override that fully replaces the weekly rule (§5.3)."""

    __tablename__ = "schedule_exceptions"
    __table_args__ = (
        CheckConstraint(
            "(is_closed AND open_time IS NULL AND close_time IS NULL) OR "
            f"(NOT is_closed AND {_OPEN_SQL})",
            name="consistency",
        ),
        UniqueConstraint("venue_id", "date"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    venue_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("venues.id", ondelete="CASCADE"), nullable=False
    )
    date: Mapped[date] = mapped_column(Date, nullable=False)
    is_closed: Mapped[bool] = mapped_column(Boolean, nullable=False)
    open_time: Mapped[time | None] = mapped_column(Time, nullable=True)
    close_time: Mapped[time | None] = mapped_column(Time, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=text("now()")
    )
