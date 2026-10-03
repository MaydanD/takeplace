"""Public API router (``/api/public/v1``).

Stage 1 establishes the mount point and prefix only. Venue/availability/booking
endpoints arrive in later stages (PROJECT-SPEC §34, Stages 4–6).
"""

from fastapi import APIRouter

router = APIRouter(prefix="/api/public/v1")
