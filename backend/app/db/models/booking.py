"""Booking-core models (PROJECT-SPEC §6.6-6.10, §12, §43, §44.1).

``Bookings``, ``TableOccupancy``, ``BookingEvent`` and ``VenueBookingCounter``.

Everything the database can enforce is declared here *and* in migration
``20261003_0005`` — the DB is the last arbiter for double booking, tenant
isolation and the status/idempotency invariants. The ``occupancy_no_overlap``
``EXCLUDE`` constraint is not expressible as SQLAlchemy metadata, so it lives
only in the migration (§12, §45); this is expected and does not make
``alembic check`` report drift.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    ForeignKey,
    ForeignKeyConstraint,
    Identity,
    Index,
    Integer,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, TIMESTAMP, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.domain.booking import (
    BOOKING_REASONS,
    BOOKING_SOURCES,
    BOOKING_STATUSES,
    OCCUPANCY_KINDS,
)

_SOURCE_LIST = ", ".join(f"'{value}'" for value in BOOKING_SOURCES)
_STATUS_LIST = ", ".join(f"'{value}'" for value in BOOKING_STATUSES)
_REASON_LIST = ", ".join(f"'{value}'" for value in BOOKING_REASONS)
_KIND_LIST = ", ".join(f"'{value}'" for value in OCCUPANCY_KINDS)

# ``mod(floor(epoch), 300)`` pins the whole second to the 5-minute grid and the
# MICROSECONDS predicate rejects fractional seconds (§44.1).
_GRID_STARTS = (
    "mod(floor(extract(epoch from starts_at))::bigint, 300) = 0 "
    "AND extract(microseconds from starts_at) = 0"
)
_GRID_ENDS = (
    "mod(floor(extract(epoch from ends_at))::bigint, 300) = 0 "
    "AND extract(microseconds from ends_at) = 0"
)

_STATUS_TIMES = (
    "(status = 'NEW' AND waiting_at IS NULL AND opened_at IS NULL "
    "AND closed_at IS NULL AND canceled_at IS NULL) OR "
    "(status = 'WAITING' AND waiting_at IS NOT NULL AND opened_at IS NULL "
    "AND closed_at IS NULL AND canceled_at IS NULL) OR "
    "(status = 'OPEN' AND opened_at IS NOT NULL AND closed_at IS NULL "
    "AND canceled_at IS NULL) OR "
    "(status = 'CLOSED' AND opened_at IS NOT NULL AND closed_at IS NOT NULL "
    "AND canceled_at IS NULL) OR "
    "(status = 'CANCELED' AND opened_at IS NULL AND closed_at IS NULL "
    "AND canceled_at IS NOT NULL AND cancellation_reason IS NOT NULL)"
)

_SOURCE_IDEMPOTENCY = (
    "anonymized_at IS NOT NULL OR ("
    "(source = 'ONLINE' AND public_idempotency_key IS NOT NULL "
    "AND public_request_hmac IS NOT NULL AND admin_idempotency_key IS NULL) OR "
    "(source <> 'ONLINE' AND public_idempotency_key IS NULL "
    "AND public_request_hmac IS NULL AND admin_idempotency_key IS NOT NULL))"
)

_ANONYMIZED_CLEARED = (
    "anonymized_at IS NULL OR ("
    "public_idempotency_key IS NULL AND public_request_hmac IS NULL "
    "AND admin_idempotency_key IS NULL AND admin_request_hmac IS NULL "
    "AND request_ip_hmac IS NULL)"
)

_GUEST_DATA = (
    "anonymized_at IS NOT NULL OR (guest_name IS NOT NULL AND "
    "(source IN ('WALK_IN', 'VK') OR guest_phone_raw IS NOT NULL))"
)

_OCCUPANCY_KIND = (
    "(kind = 'BOOKING' AND booking_id IS NOT NULL) OR (kind = 'BLOCK' AND booking_id IS NULL)"
)


class VenueBookingCounter(Base):
    """Per-venue monotonic human-readable booking number (§6.7)."""

    __tablename__ = "venue_booking_counters"

    venue_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("venues.id", ondelete="CASCADE"), primary_key=True
    )
    last_number: Mapped[int] = mapped_column(BigInteger, nullable=False)


class Booking(Base):
    """A reservation with an immutable shift snapshot and HMAC idempotency (§6.6)."""

    __tablename__ = "bookings"
    __table_args__ = (
        UniqueConstraint("id", "venue_id"),
        UniqueConstraint("id", "venue_id", "business_date"),
        UniqueConstraint("venue_id", "number"),
        CheckConstraint("party_size > 0", name="party_size_positive"),
        CheckConstraint("version >= 1", name="version_positive"),
        CheckConstraint("ends_at > starts_at", name="ends_after_starts"),
        CheckConstraint("shift_ends_at > shift_starts_at", name="shift_ends_after_starts"),
        CheckConstraint(
            "starts_at >= shift_starts_at AND ends_at <= shift_ends_at", name="inside_shift"
        ),
        CheckConstraint(_GRID_STARTS, name="grid_starts_at"),
        CheckConstraint(_GRID_ENDS, name="grid_ends_at"),
        CheckConstraint(f"source IN ({_SOURCE_LIST})", name="source_allowed"),
        CheckConstraint(f"status IN ({_STATUS_LIST})", name="status_allowed"),
        CheckConstraint(
            f"cancellation_reason IS NULL OR cancellation_reason IN ({_REASON_LIST})",
            name="cancellation_reason_allowed",
        ),
        CheckConstraint(
            "(public_idempotency_key IS NULL) = (public_request_hmac IS NULL)",
            name="public_idempotency_pair",
        ),
        CheckConstraint(
            "(admin_idempotency_key IS NULL) = (admin_request_hmac IS NULL)",
            name="admin_idempotency_pair",
        ),
        CheckConstraint(_SOURCE_IDEMPOTENCY, name="source_idempotency"),
        CheckConstraint(_ANONYMIZED_CLEARED, name="anonymized_cleared"),
        CheckConstraint(_GUEST_DATA, name="guest_data"),
        CheckConstraint(_STATUS_TIMES, name="status_timestamps"),
        CheckConstraint(
            "guest_name IS NULL OR char_length(guest_name) <= 100", name="guest_name_length"
        ),
        CheckConstraint(
            "guest_phone_raw IS NULL OR char_length(guest_phone_raw) <= 50",
            name="guest_phone_raw_length",
        ),
        CheckConstraint(
            "guest_comment IS NULL OR char_length(guest_comment) <= 1000",
            name="guest_comment_length",
        ),
        CheckConstraint(
            "cancellation_note IS NULL OR char_length(cancellation_note) <= 500",
            name="cancellation_note_length",
        ),
        Index("ix_bookings_venue_id_business_date_status", "venue_id", "business_date", "status"),
        Index("ix_bookings_venue_id_starts_at", "venue_id", "starts_at"),
        Index("ix_bookings_venue_id_guest_phone_normalized", "venue_id", "guest_phone_normalized"),
        Index(
            "ix_bookings_venue_id_request_ip_hmac",
            "venue_id",
            "request_ip_hmac",
            postgresql_where=text("request_ip_hmac IS NOT NULL"),
        ),
        Index(
            "uq_bookings_venue_id_public_idempotency_key",
            "venue_id",
            "public_idempotency_key",
            unique=True,
            postgresql_where=text("public_idempotency_key IS NOT NULL"),
        ),
        Index(
            "uq_bookings_venue_id_admin_idempotency_key",
            "venue_id",
            "admin_idempotency_key",
            unique=True,
            postgresql_where=text("admin_idempotency_key IS NOT NULL"),
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    venue_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("venues.id", ondelete="CASCADE"), nullable=False
    )
    number: Mapped[int] = mapped_column(BigInteger, nullable=False)
    business_date: Mapped[date] = mapped_column(Date, nullable=False)
    shift_starts_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False)
    shift_ends_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False)
    starts_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False)
    ends_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False)
    guest_name: Mapped[str | None] = mapped_column(Text)
    guest_phone_raw: Mapped[str | None] = mapped_column(Text)
    guest_phone_normalized: Mapped[str | None] = mapped_column(Text)
    party_size: Mapped[int] = mapped_column(Integer, nullable=False)
    guest_comment: Mapped[str | None] = mapped_column(Text)
    public_idempotency_key: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    public_request_hmac: Mapped[str | None] = mapped_column(Text)
    admin_idempotency_key: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    admin_request_hmac: Mapped[str | None] = mapped_column(Text)
    request_ip_hmac: Mapped[str | None] = mapped_column(Text)
    request_ip_hmac_expires_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    source: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    waiting_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    opened_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    closed_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    canceled_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    cancellation_reason: Mapped[str | None] = mapped_column(Text)
    cancellation_note: Mapped[str | None] = mapped_column(Text)
    privacy_policy_version: Mapped[str | None] = mapped_column(Text)
    privacy_accepted_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    anonymized_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    version: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default=text("1"))
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=text("now()")
    )


class TableOccupancy(Base):
    """An effective reservation interval for one table (§6.8, §12)."""

    __tablename__ = "table_occupancies"
    __table_args__ = (
        CheckConstraint(f"kind IN ({_KIND_LIST})", name="kind_allowed"),
        CheckConstraint(_OCCUPANCY_KIND, name="kind_booking"),
        CheckConstraint("ends_at > starts_at", name="ends_after_starts"),
        ForeignKeyConstraint(
            ["table_id", "venue_id"],
            ["tables.id", "tables.venue_id"],
            name="fk_table_occupancies_table_id_venue_id_tables",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["booking_id", "venue_id"],
            ["bookings.id", "bookings.venue_id"],
            name="fk_table_occupancies_booking_id_venue_id_bookings",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["venue_id"],
            ["venues.id"],
            name="fk_table_occupancies_venue_id_venues",
            ondelete="CASCADE",
        ),
        Index("ix_table_occupancies_booking_id", "booking_id"),
        Index(
            "ix_table_occupancies_venue_id_starts_at",
            "venue_id",
            "starts_at",
            postgresql_where=text("is_active"),
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    venue_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    table_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    booking_id: Mapped[int | None] = mapped_column(BigInteger)
    note: Mapped[str | None] = mapped_column(Text)
    starts_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False)
    ends_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=text("now()")
    )


class BookingLiveTable(Base):
    """Minimal factual placement for atomic WALK_IN (§6.9, §19.1)."""

    __tablename__ = "booking_live_tables"
    __table_args__ = (
        UniqueConstraint("table_id", "business_date"),
        ForeignKeyConstraint(
            ["booking_id", "venue_id", "business_date"],
            ["bookings.id", "bookings.venue_id", "bookings.business_date"],
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["table_id", "venue_id"], ["tables.id", "tables.venue_id"], ondelete="CASCADE"
        ),
        Index("ix_booking_live_tables_booking_id", "booking_id"),
        Index("ix_booking_live_tables_venue_id_business_date", "venue_id", "business_date"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    venue_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    booking_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    business_date: Mapped[date] = mapped_column(Date, nullable=False)
    table_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    live_since: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False)


class BookingEvent(Base):
    """Append-only booking history; ordered by ``id``, never by ``created_at`` (§6.10)."""

    __tablename__ = "booking_events"
    __table_args__ = (
        CheckConstraint("actor_type IN ('PUBLIC', 'ADMIN', 'SYSTEM')", name="actor_type_allowed"),
        CheckConstraint("jsonb_typeof(payload) = 'object'", name="payload_object"),
        ForeignKeyConstraint(
            ["booking_id", "venue_id"],
            ["bookings.id", "bookings.venue_id"],
            name="fk_booking_events_booking_id_venue_id_bookings",
            ondelete="CASCADE",
        ),
        # ON DELETE SET NULL (column list) keeps venue_id NOT NULL and the row
        # alive when a session is cleaned up (PostgreSQL 15+, §6.10).
        ForeignKeyConstraint(
            ["admin_session_id", "venue_id"],
            ["admin_sessions.id", "admin_sessions.venue_id"],
            name="fk_booking_events_admin_session_id_venue_id_admin_sessions",
            ondelete="SET NULL (admin_session_id)",
        ),
        ForeignKeyConstraint(
            ["venue_id"],
            ["venues.id"],
            name="fk_booking_events_venue_id_venues",
            ondelete="CASCADE",
        ),
        Index("ix_booking_events_booking_id_id", "booking_id", "id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    venue_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    booking_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    event_type: Mapped[str] = mapped_column(Text, nullable=False)
    actor_type: Mapped[str] = mapped_column(Text, nullable=False)
    admin_session_id: Mapped[int | None] = mapped_column(BigInteger)
    payload: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=text("now()")
    )
