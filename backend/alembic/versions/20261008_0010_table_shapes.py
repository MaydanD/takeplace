"""Extend ``tables.shape`` to the four editor shapes (rect/square/circle/ellipse).

Revision ID: 20261008_0010
Revises: 20261007_0009

The layout editor gains two new table shapes (PROJECT-SPEC §6.5 ``shape``). The
change is purely additive: ``rect`` and ``circle`` remain valid, so every
existing row keeps its value. Only the enum-like CHECK from revision
``20261003_0004`` is replaced with the wider value list.

The constraint is dropped with ``IF EXISTS`` and recreated, so the migration
reaches the correct state from any database that already carries the old list.
The name and expression mirror the ORM metadata, keeping ``alembic check``
green (§44 "enum-like").

Rollback plan: rebuild the old CHECK. A downgrade would fail if any row already
uses the new shapes, which is the correct, non-destructive behaviour: revert the
data first.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "20261008_0010"
down_revision: str | None = "20261007_0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_CHECK_NAME = "ck_tables_shape_allowed"
_NEW_SHAPES = "'rect','square','circle','ellipse'"
_OLD_SHAPES = "'rect','circle'"


def upgrade() -> None:
    op.execute(f"ALTER TABLE tables DROP CONSTRAINT IF EXISTS {_CHECK_NAME}")
    op.execute(f"ALTER TABLE tables ADD CONSTRAINT {_CHECK_NAME} CHECK (shape IN ({_NEW_SHAPES}))")


def downgrade() -> None:
    op.execute(f"ALTER TABLE tables DROP CONSTRAINT IF EXISTS {_CHECK_NAME}")
    op.execute(f"ALTER TABLE tables ADD CONSTRAINT {_CHECK_NAME} CHECK (shape IN ({_OLD_SHAPES}))")
