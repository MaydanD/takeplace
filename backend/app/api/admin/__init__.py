"""Admin API (``/api/admin/v1``), PROJECT-SPEC §35.

Stage 2 exposes authentication, the authenticated identity and venue settings.
Bookings and the rest of the admin surface arrive in later stages.
"""

from fastapi import APIRouter

from app.api.admin import account, auth, settings

router = APIRouter(prefix="/api/admin/v1")
router.include_router(auth.router)
router.include_router(account.router)
router.include_router(settings.router)
