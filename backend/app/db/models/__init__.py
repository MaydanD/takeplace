"""ORM models.

Importing this package registers every model on ``Base.metadata`` so Alembic
autogenerate and the drift check see the full schema.
"""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from app.db.models.admin import AdminAccount, AdminSession
from app.db.models.venue import Venue

__all__ = ["AdminAccount", "AdminSession", "Venue", "REQUIRED_EXTENSIONS", "ensure_extensions"]

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
