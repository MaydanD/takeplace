"""Admin API (``/api/admin/v1``), PROJECT-SPEC §35.

Stage 2 exposes authentication, the authenticated identity and venue settings;
Stage 3 adds the weekly schedule, its exceptions and the business-day state;
Stage 4 adds halls, tables and the read-only canvas. Bookings and the rest of
the admin surface arrive in later stages.
"""

from fastapi import APIRouter

from app.api.admin import account, auth, halls, schedule, settings

router = APIRouter(prefix="/api/admin/v1")
router.include_router(auth.router)
router.include_router(account.router)
router.include_router(settings.router)
router.include_router(schedule.router)
router.include_router(halls.router)
