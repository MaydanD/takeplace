"""Baseline: required PostgreSQL extensions.

Revision ID: 20261003_0001
Revises:
Create Date: 2026-10-03

Stage 1 baseline. The only schema change is the ``btree_gist`` extension, which
the booking core's exclusion constraint requires (PROJECT-SPEC §3.3, §12, §45).
It is installed up front so every later migration can rely on it without
re-implementing extension bootstrapping.

This migration must be hand-written: autogenerate cannot express extensions.

Rollback plan: the extension is dropped only if no exclusion constraint depends
on it. In a fresh database downgrade is safe; in a database that already has
occupancy tables the exclusion constraint must be removed first.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "20261003_0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS btree_gist")


def downgrade() -> None:
    op.execute("DROP EXTENSION IF EXISTS btree_gist")
