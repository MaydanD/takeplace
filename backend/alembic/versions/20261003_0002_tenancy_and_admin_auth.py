"""Tenancy and admin auth: venues, admin_accounts, admin_sessions.

Revision ID: 20261003_0002
Revises: 20261003_0001
Create Date: 2026-10-03

Introduces the tenant root (``venues``), the single shared admin account per
venue (``admin_accounts``) and server-side sessions (``admin_sessions``) per
PROJECT-SPEC §6.1–6.3.

Invariants kept in the database (not only in Python):

* venue ``slug`` format and the reserved-slug list are CHECK constraints;
* ``admin_accounts.venue_id`` and ``admin_accounts.login`` are UNIQUE
  (one account per venue, globally unique login);
* ``admin_sessions.token_hash`` is UNIQUE; only the hash is ever stored;
* ``admin_sessions`` carries a UNIQUE ``(id, venue_id)`` — the composite target
  later stages use for tenant-safe FKs such as
  ``booking_events(admin_session_id, venue_id)``;
* ``admin_sessions.expires_at > created_at``;
* indexes on ``admin_sessions(expires_at)`` and ``admin_sessions(venue_id)``.

Rollback plan: this migration only adds tables and indexes. Downgrade drops
them in reverse dependency order. It is safe while later stages' dependents
(booking_events etc.) are not yet present; once they exist, drop those first.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20261003_0002"
down_revision: str | None = "20261003_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_RESERVED_SLUGS = (
    "admin",
    "api",
    "assets",
    "booking",
    "bookings",
    "favicon.ico",
    "health",
    "login",
    "logout",
    "privacy",
    "robots.txt",
    "settings",
    "static",
    "terms",
)
_RESERVED_SQL = ", ".join(f"'{slug}'" for slug in _RESERVED_SLUGS)


def upgrade() -> None:
    op.create_table(
        "venues",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("slug", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("address", sa.Text(), nullable=True),
        sa.Column("phone", sa.Text(), nullable=True),
        sa.Column("timezone", sa.Text(), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column(
            "online_booking_enabled",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
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
        sa.CheckConstraint("slug ~ '^[a-z0-9-]{2,40}$'", name=op.f("ck_venues_slug_format")),
        sa.CheckConstraint(
            f"slug NOT IN ({_RESERVED_SQL})", name=op.f("ck_venues_slug_not_reserved")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_venues")),
        sa.UniqueConstraint("slug", name=op.f("uq_venues_slug")),
    )

    op.create_table(
        "admin_accounts",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("venue_id", sa.BigInteger(), nullable=False),
        sa.Column("login", sa.Text(), nullable=False),
        sa.Column("password_hash", sa.Text(), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
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
        sa.ForeignKeyConstraint(
            ["venue_id"],
            ["venues.id"],
            name=op.f("fk_admin_accounts_venue_id_venues"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_admin_accounts")),
        sa.UniqueConstraint("login", name=op.f("uq_admin_accounts_login")),
        sa.UniqueConstraint("venue_id", name=op.f("uq_admin_accounts_venue_id")),
    )

    op.create_table(
        "admin_sessions",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("venue_id", sa.BigInteger(), nullable=False),
        sa.Column("token_hash", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            postgresql.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("expires_at", postgresql.TIMESTAMP(timezone=True), nullable=False),
        sa.Column(
            "last_seen_at",
            postgresql.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "expires_at > created_at",
            name=op.f("ck_admin_sessions_expires_after_created"),
        ),
        sa.ForeignKeyConstraint(
            ["venue_id"],
            ["venues.id"],
            name=op.f("fk_admin_sessions_venue_id_venues"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_admin_sessions")),
        sa.UniqueConstraint("token_hash", name=op.f("uq_admin_sessions_token_hash")),
        sa.UniqueConstraint("id", "venue_id", name=op.f("uq_admin_sessions_id_venue_id")),
    )
    op.create_index("ix_admin_sessions_expires_at", "admin_sessions", ["expires_at"], unique=False)
    op.create_index("ix_admin_sessions_venue_id", "admin_sessions", ["venue_id"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_admin_sessions_venue_id", table_name="admin_sessions")
    op.drop_index("ix_admin_sessions_expires_at", table_name="admin_sessions")
    op.drop_table("admin_sessions")
    op.drop_table("admin_accounts")
    op.drop_table("venues")
