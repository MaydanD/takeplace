"""``halls`` and ``tables`` models (PROJECT-SPEC §6.4, §6.5).

A hall belongs to exactly one venue; a table belongs to exactly one hall *and*
venue. The composite FK ``tables(hall_id, venue_id) -> halls(id, venue_id)``
makes a table whose ``venue_id`` disagrees with its hall's venue impossible at
the database level, not just in application code (§7, §44).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    ForeignKey,
    ForeignKeyConstraint,
    Identity,
    Index,
    Integer,
    Numeric,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, TIMESTAMP
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.domain.layout import VALID_TABLE_SHAPES

_SHAPE_LIST = ", ".join(f"'{shape}'" for shape in VALID_TABLE_SHAPES)


class Hall(Base):
    """A hall (canvas) inside a venue. Archived halls keep their row."""

    __tablename__ = "halls"
    __table_args__ = (
        # Composite target for tenant-safe table FKs.
        UniqueConstraint("id", "venue_id"),
        CheckConstraint("canvas_width > 0", name="canvas_width_positive"),
        CheckConstraint("canvas_height > 0", name="canvas_height_positive"),
        CheckConstraint("jsonb_typeof(static_elements) = 'array'", name="static_elements_array"),
        Index("ix_halls_venue_id", "venue_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    venue_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("venues.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(Text, nullable=False)
    is_bookable: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    canvas_width: Mapped[int] = mapped_column(Integer, nullable=False)
    canvas_height: Mapped[int] = mapped_column(Integer, nullable=False)
    layout_revision: Mapped[int] = mapped_column(
        BigInteger, nullable=False, server_default=text("1")
    )
    static_elements: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    archived_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=text("now()")
    )


class Table(Base):
    """A bookable table with canvas geometry. Archived tables keep their row."""

    __tablename__ = "tables"
    __table_args__ = (
        # Ownership: hall must belong to the same venue (cross-tenant impossible).
        ForeignKeyConstraint(
            ["hall_id", "venue_id"],
            ["halls.id", "halls.venue_id"],
            name="fk_tables_hall_id_venue_id_halls",
            ondelete="CASCADE",
        ),
        UniqueConstraint("id", "venue_id"),
        CheckConstraint("capacity > 0", name="capacity_positive"),
        CheckConstraint("width > 0 AND height > 0", name="size_positive"),
        CheckConstraint("x >= 0 AND y >= 0", name="position_non_negative"),
        CheckConstraint("rotation >= 0 AND rotation <= 360", name="rotation_range"),
        CheckConstraint(f"shape IN ({_SHAPE_LIST})", name="shape_allowed"),
        Index("ix_tables_venue_id_hall_id", "venue_id", "hall_id"),
        Index(
            "uq_tables_hall_id_number_active",
            "hall_id",
            "number",
            unique=True,
            postgresql_where=text("archived_at IS NULL"),
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    venue_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("venues.id", ondelete="CASCADE"), nullable=False
    )
    hall_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    number: Mapped[str] = mapped_column(Text, nullable=False)
    capacity: Mapped[int] = mapped_column(Integer, nullable=False)
    is_bookable: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    archived_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
    x: Mapped[Any] = mapped_column(Numeric, nullable=False)
    y: Mapped[Any] = mapped_column(Numeric, nullable=False)
    width: Mapped[Any] = mapped_column(Numeric, nullable=False)
    height: Mapped[Any] = mapped_column(Numeric, nullable=False)
    rotation: Mapped[Any] = mapped_column(Numeric, nullable=False, server_default=text("0"))
    shape: Mapped[str] = mapped_column(Text, nullable=False)
    z_index: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=text("now()")
    )
