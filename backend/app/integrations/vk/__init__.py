"""VK integration adapter (PROJECT-SPEC §38).

* :mod:`app.integrations.vk.crypto` — token encryption at rest (§38.5);
* :mod:`app.integrations.vk.client` — isolated async VK API client (§38.3-38.4);
* :mod:`app.integrations.vk.formatting` — pure message formatter (§38.6).
"""

from __future__ import annotations

from app.integrations.vk.client import (
    VKClient,
    VKDeliveryError,
    VKErrorClass,
    VKSendResult,
    random_id_for,
)
from app.integrations.vk.crypto import VKEncryptionError, VKTokenCipher
from app.integrations.vk.formatting import (
    BookingNotificationData,
    format_booking_notification,
    sanitize_user_text,
)

__all__ = [
    "BookingNotificationData",
    "VKClient",
    "VKDeliveryError",
    "VKEncryptionError",
    "VKErrorClass",
    "VKSendResult",
    "VKTokenCipher",
    "format_booking_notification",
    "random_id_for",
    "sanitize_user_text",
]
