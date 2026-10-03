"""ORM models.

Stage 1 intentionally defines no domain tables: bookings, halls, schedules and
so on belong to later stages (PROJECT-SPEC §63). What Stage 1 *does* establish
is the extension the booking core depends on, so it exists in the very first
migration and later migrations can use ``EXCLUDE`` immediately.

``btree_gist`` is created by a hand-written migration (§45); this module records
it in metadata only so ``alembic check`` understands the schema is intentional.
"""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

# The extension required by the table_occupancies exclusion constraint (§12).
REQUIRED_EXTENSIONS: tuple[str, ...] = ("btree_gist",)


async def ensure_extensions(connection: AsyncConnection) -> None:
    """Ensure required PostgreSQL extensions exist.

    Used by the baseline migration and by the integration test bootstrap.
    ``btree_gist`` is a trusted extension in PostgreSQL 13+, so the migrator
    role may install it without superuser rights.
    """
    for extension in REQUIRED_EXTENSIONS:
        await connection.execute(text(f'CREATE EXTENSION IF NOT EXISTS "{extension}"'))
