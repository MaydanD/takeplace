"""Public API router (``/api/public/v1``).

Stage 1 establishes the mount point and prefix only. Stage 6 adds the public
venue/availability/booking endpoints (PROJECT-SPEC §34, §50).
"""

from fastapi import APIRouter

from app.api.public import venues

router = APIRouter(prefix="/api/public/v1")
router.include_router(venues.router)
