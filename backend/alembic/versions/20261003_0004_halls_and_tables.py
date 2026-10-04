"""Halls and tables: halls, tables.

Revision ID: 20261003_0004
Revises: 20261003_0003
Create Date: 2026-10-03

Introduces the venue's halls (canvases) and the tables placed on them
(PROJECT-SPEC §6.4, §6.5), plus the geometry fields the read-only canvas and
the JSON/CLI import need.

Invariants kept in the database:

* ``tables(hall_id, venue_id) -> halls(id, venue_id)`` composite FK — a table
  can never point at a hall of another venue (§7, §44);
* ``halls(id, venue_id)`` and ``tables(id, venue_id)`` are UNIQUE, the
  composite targets later tenant-safe FKs use;
* ``capacity > 0``; ``width > 0 AND height > 0``; ``x >= 0 AND y >= 0``;
  ``rotation BETWEEN 0 AND 360``; ``shape IN ('rect', 'circle')`` (enum-like,
  §44); ``canvas_width > 0 AND canvas_height > 0``;
  ``jsonb_typeof(static_elements) = 'array'``;
* partial ``UNIQUE (hall_id, number) WHERE archived_at IS NULL`` — a number is
  unique among a hall's non-archived tables only (§6.5).

Rollback plan: additive only. Downgrade drops ``tables`` then ``halls``; safe
while Stage 5's occupancy/live FKs on tables do not yet exist.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20261003_0004"
down_revision: str | None = "20261003_0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_SHAPE_SQL = "shape IN ('rect', 'circle')"


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
        "halls",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("venue_id", sa.BigInteger(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("is_bookable", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("canvas_width", sa.Integer(), nullable=False),
        sa.Column("canvas_height", sa.Integer(), nullable=False),
        sa.Column("layout_revision", sa.BigInteger(), server_default=sa.text("1"), nullable=False),
        sa.Column(
            "static_elements",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column("archived_at", postgresql.TIMESTAMP(timezone=True), nullable=True),
        *_timestamps(),
        sa.CheckConstraint("canvas_width > 0", name=op.f("ck_halls_canvas_width_positive")),
        sa.CheckConstraint("canvas_height > 0", name=op.f("ck_halls_canvas_height_positive")),
        sa.CheckConstraint(
            "jsonb_typeof(static_elements) = 'array'",
            name=op.f("ck_halls_static_elements_array"),
        ),
        sa.ForeignKeyConstraint(
            ["venue_id"],
            ["venues.id"],
            name=op.f("fk_halls_venue_id_venues"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_halls")),
        sa.UniqueConstraint("id", "venue_id", name=op.f("uq_halls_id_venue_id")),
    )
    op.create_index("ix_halls_venue_id", "halls", ["venue_id"], unique=False)

    op.create_table(
        "tables",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("venue_id", sa.BigInteger(), nullable=False),
        sa.Column("hall_id", sa.BigInteger(), nullable=False),
        sa.Column("number", sa.Text(), nullable=False),
        sa.Column("capacity", sa.Integer(), nullable=False),
        sa.Column("is_bookable", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("archived_at", postgresql.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("x", sa.Numeric(), nullable=False),
        sa.Column("y", sa.Numeric(), nullable=False),
        sa.Column("width", sa.Numeric(), nullable=False),
        sa.Column("height", sa.Numeric(), nullable=False),
        sa.Column("rotation", sa.Numeric(), server_default=sa.text("0"), nullable=False),
        sa.Column("shape", sa.Text(), nullable=False),
        sa.Column("z_index", sa.Integer(), server_default=sa.text("0"), nullable=False),
        *_timestamps(),
        sa.CheckConstraint("capacity > 0", name=op.f("ck_tables_capacity_positive")),
        sa.CheckConstraint("width > 0 AND height > 0", name=op.f("ck_tables_size_positive")),
        sa.CheckConstraint("x >= 0 AND y >= 0", name=op.f("ck_tables_position_non_negative")),
        sa.CheckConstraint(
            "rotation >= 0 AND rotation <= 360", name=op.f("ck_tables_rotation_range")
        ),
        sa.CheckConstraint(_SHAPE_SQL, name=op.f("ck_tables_shape_allowed")),
        sa.ForeignKeyConstraint(
            ["hall_id", "venue_id"],
            ["halls.id", "halls.venue_id"],
            name=op.f("fk_tables_hall_id_venue_id_halls"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["venue_id"],
            ["venues.id"],
            name=op.f("fk_tables_venue_id_venues"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_tables")),
        sa.UniqueConstraint("id", "venue_id", name=op.f("uq_tables_id_venue_id")),
    )
    op.create_index("ix_tables_venue_id_hall_id", "tables", ["venue_id", "hall_id"], unique=False)
    op.create_index(
        "uq_tables_hall_id_number_active",
        "tables",
        ["hall_id", "number"],
        unique=True,
        postgresql_where=sa.text("archived_at IS NULL"),
    )


def downgrade() -> None:
    op.drop_index("uq_tables_hall_id_number_active", table_name="tables")
    op.drop_index("ix_tables_venue_id_hall_id", table_name="tables")
    op.drop_table("tables")
    op.drop_index("ix_halls_venue_id", table_name="halls")
    op.drop_table("halls")
