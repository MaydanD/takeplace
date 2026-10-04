"""Booking core: counters, bookings, occupancies and events.

Revision ID: 20261003_0005
Revises: 20261003_0004
Create Date: 2026-10-04

Introduces the booking core (PROJECT-SPEC §6.6-6.10, §12, §43, §44.1, §63):

* ``venue_booking_counters`` — the per-venue human-readable booking number;
* ``bookings`` — the reservation row with the shift snapshot and the HMAC
  idempotency / short-lived abuse-fingerprint fields;
* ``table_occupancies`` — the effective reservation intervals (BOOKING/BLOCK);
* ``booking_events`` — the append-only per-booking history;
* the ``btree_gist`` exclusion constraint that makes a double booking
  **technically impossible** in the database (the last arbiter).

Invariants kept in the database (§44.1), beyond plain FK/NOT NULL:

* ``ends_at > starts_at``; ``shift_ends_at > shift_starts_at``;
  ``[starts_at, ends_at)`` inside ``[shift_starts_at, shift_ends_at)``;
* ``party_size > 0``; ``version >= 1``;
* ``starts_at``/``ends_at`` on the 5-minute grid with zero seconds/microseconds;
* enum-like CHECKs for ``source``, ``status``, ``cancellation_reason``,
  ``kind`` and ``actor_type``;
* status <-> timestamp consistency for the whole state machine (§10);
* ``(public_idempotency_key IS NULL) = (public_request_hmac IS NULL)`` and the
  same for the admin pair;
* the source/idempotency invariant: non-anonymized ``ONLINE`` rows carry the
  public key + HMAC and no admin pair, all non-``ONLINE`` rows carry the admin
  pair and no public pair;
* after anonymization every key/HMAC/fingerprint column is ``NULL``;
* the guest-data invariant (``VK``/``WALK_IN`` may omit the phone);
* occupancy ``kind`` <-> ``booking_id`` consistency and ``ends_at > starts_at``;
* composite tenant-safe FKs ``(table_id, venue_id)`` and
  ``(booking_id, venue_id)`` so a cross-tenant reference is impossible (§7, §54.14).

The ``occupancy_no_overlap`` exclusion constraint is created by explicit raw SQL
(§12, §45): autogenerate cannot model an exclusion constraint. It uses the
half-open range ``tstzrange(starts_at, ends_at, '[)')`` so ``20:00-22:00`` and
``22:00-00:00`` are compatible.

Rollback plan: additive only. Downgrade drops ``booking_events``,
``table_occupancies`` (constraint first), ``bookings`` and
``venue_booking_counters`` in child-to-parent order. Safe while Stage 6+ has not
added dependent objects (no ``notification_outbox`` / ``booking_live_tables``).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20261003_0005"
down_revision: str | None = "20261003_0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_SOURCE_SQL = "source IN ('ONLINE', 'PHONE', 'VK', 'WALK_IN', 'OTHER')"
_STATUS_SQL = "status IN ('NEW', 'WAITING', 'OPEN', 'CLOSED', 'CANCELED')"
_REASON_SQL = (
    "cancellation_reason IN ("
    "'GUEST_CANCELED', 'NO_SHOW', 'DUPLICATE', 'UNREACHABLE', 'RESCHEDULED', "
    "'GUEST_LATE', 'CREATION_ERROR', 'TERMS_REFUSED', 'INVALID_DATA', "
    "'MOVED_ELSEWHERE', 'NO_TABLES', 'VENUE_CLOSED', 'ENTRY_REFUSED', 'OTHER')"
)

# 5-minute grid guard: the whole second must be on a 300s boundary and there
# must be no fractional seconds (§44.1). EXTRACT(EPOCH ...) is UTC-based; every
# supported IANA offset is a multiple of 15 minutes, so the grid is preserved.
_GRID_STARTS = (
    "mod(floor(extract(epoch from starts_at))::bigint, 300) = 0 "
    "AND extract(microseconds from starts_at) = 0"
)
_GRID_ENDS = (
    "mod(floor(extract(epoch from ends_at))::bigint, 300) = 0 "
    "AND extract(microseconds from ends_at) = 0"
)

# Status <-> timestamp consistency (§10).
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

# Source/idempotency invariant for non-anonymized rows (§44.1). A test/seed
# fixture must supply a valid admin pair; loosening this CHECK is forbidden.
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


def _timestamps() -> list[sa.Column]:
    return [
        sa.Column(
            "created_at",
            postgresql.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            postgresql.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    ]


def upgrade() -> None:
    op.create_table(
        "venue_booking_counters",
        sa.Column("venue_id", sa.BigInteger(), nullable=False),
        sa.Column("last_number", sa.BigInteger(), nullable=False),
        sa.ForeignKeyConstraint(
            ["venue_id"],
            ["venues.id"],
            name=op.f("fk_venue_booking_counters_venue_id_venues"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("venue_id", name=op.f("pk_venue_booking_counters")),
    )

    op.create_table(
        "bookings",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("venue_id", sa.BigInteger(), nullable=False),
        sa.Column("number", sa.BigInteger(), nullable=False),
        sa.Column("business_date", sa.Date(), nullable=False),
        sa.Column("shift_starts_at", postgresql.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("shift_ends_at", postgresql.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("starts_at", postgresql.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("ends_at", postgresql.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("guest_name", sa.Text(), nullable=True),
        sa.Column("guest_phone_raw", sa.Text(), nullable=True),
        sa.Column("guest_phone_normalized", sa.Text(), nullable=True),
        sa.Column("party_size", sa.Integer(), nullable=False),
        sa.Column("guest_comment", sa.Text(), nullable=True),
        sa.Column("public_idempotency_key", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("public_request_hmac", sa.Text(), nullable=True),
        sa.Column("admin_idempotency_key", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("admin_request_hmac", sa.Text(), nullable=True),
        sa.Column("request_ip_hmac", sa.Text(), nullable=True),
        sa.Column("request_ip_hmac_expires_at", postgresql.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("waiting_at", postgresql.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("opened_at", postgresql.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("closed_at", postgresql.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("canceled_at", postgresql.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("cancellation_reason", sa.Text(), nullable=True),
        sa.Column("cancellation_note", sa.Text(), nullable=True),
        sa.Column("privacy_policy_version", sa.Text(), nullable=True),
        sa.Column("privacy_accepted_at", postgresql.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("anonymized_at", postgresql.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("version", sa.BigInteger(), server_default=sa.text("1"), nullable=False),
        *_timestamps(),
        sa.CheckConstraint("party_size > 0", name=op.f("ck_bookings_party_size_positive")),
        sa.CheckConstraint("version >= 1", name=op.f("ck_bookings_version_positive")),
        sa.CheckConstraint("ends_at > starts_at", name=op.f("ck_bookings_ends_after_starts")),
        sa.CheckConstraint(
            "shift_ends_at > shift_starts_at", name=op.f("ck_bookings_shift_ends_after_starts")
        ),
        sa.CheckConstraint(
            "starts_at >= shift_starts_at AND ends_at <= shift_ends_at",
            name=op.f("ck_bookings_inside_shift"),
        ),
        sa.CheckConstraint(_GRID_STARTS, name=op.f("ck_bookings_grid_starts_at")),
        sa.CheckConstraint(_GRID_ENDS, name=op.f("ck_bookings_grid_ends_at")),
        sa.CheckConstraint(_SOURCE_SQL, name=op.f("ck_bookings_source_allowed")),
        sa.CheckConstraint(_STATUS_SQL, name=op.f("ck_bookings_status_allowed")),
        sa.CheckConstraint(
            "cancellation_reason IS NULL OR " + _REASON_SQL,
            name=op.f("ck_bookings_cancellation_reason_allowed"),
        ),
        sa.CheckConstraint(
            "(public_idempotency_key IS NULL) = (public_request_hmac IS NULL)",
            name=op.f("ck_bookings_public_idempotency_pair"),
        ),
        sa.CheckConstraint(
            "(admin_idempotency_key IS NULL) = (admin_request_hmac IS NULL)",
            name=op.f("ck_bookings_admin_idempotency_pair"),
        ),
        sa.CheckConstraint(_SOURCE_IDEMPOTENCY, name=op.f("ck_bookings_source_idempotency")),
        sa.CheckConstraint(_ANONYMIZED_CLEARED, name=op.f("ck_bookings_anonymized_cleared")),
        sa.CheckConstraint(_GUEST_DATA, name=op.f("ck_bookings_guest_data")),
        sa.CheckConstraint(_STATUS_TIMES, name=op.f("ck_bookings_status_timestamps")),
        sa.CheckConstraint(
            "guest_name IS NULL OR char_length(guest_name) <= 100",
            name=op.f("ck_bookings_guest_name_length"),
        ),
        sa.CheckConstraint(
            "guest_phone_raw IS NULL OR char_length(guest_phone_raw) <= 50",
            name=op.f("ck_bookings_guest_phone_raw_length"),
        ),
        sa.CheckConstraint(
            "guest_comment IS NULL OR char_length(guest_comment) <= 1000",
            name=op.f("ck_bookings_guest_comment_length"),
        ),
        sa.CheckConstraint(
            "cancellation_note IS NULL OR char_length(cancellation_note) <= 500",
            name=op.f("ck_bookings_cancellation_note_length"),
        ),
        sa.ForeignKeyConstraint(
            ["venue_id"],
            ["venues.id"],
            name=op.f("fk_bookings_venue_id_venues"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_bookings")),
        sa.UniqueConstraint("id", "venue_id", name=op.f("uq_bookings_id_venue_id")),
        sa.UniqueConstraint(
            "id", "venue_id", "business_date", name=op.f("uq_bookings_id_venue_id_business_date")
        ),
        sa.UniqueConstraint("venue_id", "number", name=op.f("uq_bookings_venue_id_number")),
    )
    op.create_index(
        "ix_bookings_venue_id_business_date_status",
        "bookings",
        ["venue_id", "business_date", "status"],
    )
    op.create_index("ix_bookings_venue_id_starts_at", "bookings", ["venue_id", "starts_at"])
    op.create_index(
        "ix_bookings_venue_id_guest_phone_normalized",
        "bookings",
        ["venue_id", "guest_phone_normalized"],
    )
    op.create_index(
        "ix_bookings_venue_id_request_ip_hmac",
        "bookings",
        ["venue_id", "request_ip_hmac"],
        postgresql_where=sa.text("request_ip_hmac IS NOT NULL"),
    )
    op.create_index(
        "uq_bookings_venue_id_public_idempotency_key",
        "bookings",
        ["venue_id", "public_idempotency_key"],
        unique=True,
        postgresql_where=sa.text("public_idempotency_key IS NOT NULL"),
    )
    op.create_index(
        "uq_bookings_venue_id_admin_idempotency_key",
        "bookings",
        ["venue_id", "admin_idempotency_key"],
        unique=True,
        postgresql_where=sa.text("admin_idempotency_key IS NOT NULL"),
    )

    op.create_table(
        "table_occupancies",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("venue_id", sa.BigInteger(), nullable=False),
        sa.Column("table_id", sa.BigInteger(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("booking_id", sa.BigInteger(), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("starts_at", postgresql.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("ends_at", postgresql.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        *_timestamps(),
        sa.CheckConstraint(
            "kind IN ('BOOKING', 'BLOCK')", name=op.f("ck_table_occupancies_kind_allowed")
        ),
        sa.CheckConstraint(_OCCUPANCY_KIND, name=op.f("ck_table_occupancies_kind_booking")),
        sa.CheckConstraint(
            "ends_at > starts_at", name=op.f("ck_table_occupancies_ends_after_starts")
        ),
        sa.ForeignKeyConstraint(
            ["table_id", "venue_id"],
            ["tables.id", "tables.venue_id"],
            name=op.f("fk_table_occupancies_table_id_venue_id_tables"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["booking_id", "venue_id"],
            ["bookings.id", "bookings.venue_id"],
            name=op.f("fk_table_occupancies_booking_id_venue_id_bookings"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["venue_id"],
            ["venues.id"],
            name=op.f("fk_table_occupancies_venue_id_venues"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_table_occupancies")),
    )
    op.create_index("ix_table_occupancies_booking_id", "table_occupancies", ["booking_id"])
    op.create_index(
        "ix_table_occupancies_venue_id_starts_at",
        "table_occupancies",
        ["venue_id", "starts_at"],
        postgresql_where=sa.text("is_active"),
    )

    # The last arbiter for double-booking (PROJECT-SPEC §12). Half-open range so
    # adjacent intervals are compatible; only active rows participate.
    op.execute(
        """
        ALTER TABLE table_occupancies
        ADD CONSTRAINT occupancy_no_overlap
        EXCLUDE USING gist (
            table_id WITH =,
            tstzrange(starts_at, ends_at, '[)') WITH &&
        )
        WHERE (is_active)
        """
    )

    op.create_table(
        "booking_events",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("venue_id", sa.BigInteger(), nullable=False),
        sa.Column("booking_id", sa.BigInteger(), nullable=False),
        sa.Column("event_type", sa.Text(), nullable=False),
        sa.Column("actor_type", sa.Text(), nullable=False),
        sa.Column("admin_session_id", sa.BigInteger(), nullable=True),
        sa.Column(
            "payload",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            postgresql.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "actor_type IN ('PUBLIC', 'ADMIN', 'SYSTEM')",
            name=op.f("ck_booking_events_actor_type_allowed"),
        ),
        sa.CheckConstraint(
            "jsonb_typeof(payload) = 'object'", name=op.f("ck_booking_events_payload_object")
        ),
        sa.ForeignKeyConstraint(
            ["booking_id", "venue_id"],
            ["bookings.id", "bookings.venue_id"],
            name=op.f("fk_booking_events_booking_id_venue_id_bookings"),
            ondelete="CASCADE",
        ),
        # PostgreSQL 15+: ON DELETE SET NULL (column list) nulls only the
        # session id, keeping venue_id NOT NULL and the history row alive (§6.10).
        sa.ForeignKeyConstraint(
            ["admin_session_id", "venue_id"],
            ["admin_sessions.id", "admin_sessions.venue_id"],
            name=op.f("fk_booking_events_admin_session_id_venue_id_admin_sessions"),
            ondelete="SET NULL (admin_session_id)",
        ),
        sa.ForeignKeyConstraint(
            ["venue_id"],
            ["venues.id"],
            name=op.f("fk_booking_events_venue_id_venues"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_booking_events")),
    )
    op.create_index("ix_booking_events_booking_id_id", "booking_events", ["booking_id", "id"])

    # Backfill the counter for venues that already exist (dev/staging data) so a
    # venue created before Stage 5 can still create bookings.
    op.execute(
        "INSERT INTO venue_booking_counters (venue_id, last_number) "
        "SELECT id, 0 FROM venues ON CONFLICT (venue_id) DO NOTHING"
    )


def downgrade() -> None:
    op.drop_index("ix_booking_events_booking_id_id", table_name="booking_events")
    op.drop_table("booking_events")

    op.execute("ALTER TABLE table_occupancies DROP CONSTRAINT IF EXISTS occupancy_no_overlap")
    op.drop_index("ix_table_occupancies_venue_id_starts_at", table_name="table_occupancies")
    op.drop_index("ix_table_occupancies_booking_id", table_name="table_occupancies")
    op.drop_table("table_occupancies")

    op.drop_index("uq_bookings_venue_id_admin_idempotency_key", table_name="bookings")
    op.drop_index("uq_bookings_venue_id_public_idempotency_key", table_name="bookings")
    op.drop_index("ix_bookings_venue_id_request_ip_hmac", table_name="bookings")
    op.drop_index("ix_bookings_venue_id_guest_phone_normalized", table_name="bookings")
    op.drop_index("ix_bookings_venue_id_starts_at", table_name="bookings")
    op.drop_index("ix_bookings_venue_id_business_date_status", table_name="bookings")
    op.drop_table("bookings")

    op.drop_table("venue_booking_counters")
