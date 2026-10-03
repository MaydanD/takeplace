"""Admin API router (``/api/admin/v1``).

Stage 1 establishes the mount point and prefix only. Auth, bookings and the
rest of the admin surface arrive in later stages (PROJECT-SPEC §35, Stages 2+).
"""

from fastapi import APIRouter

router = APIRouter(prefix="/api/admin/v1")
