"""Canonical client IP derivation (PROJECT-SPEC §39.5, §40).

The application never trusts a raw ``X-Forwarded-For`` header itself. The ASGI
server (uvicorn) rewrites the connection's ``client`` address from forwarded
headers **only** when the immediate peer is listed in
``TAKEPLACE_FORWARDED_ALLOW_IPS`` (Caddy / the internal Docker network). By the
time a request reaches application code, ``request.client.host`` is therefore
already the trusted upstream client address, and a forged ``X-Forwarded-For``
sent directly to the API has no effect.

This module's only job is to *canonicalise* that address so that different
textual forms of the same host (notably compressed vs expanded IPv6) map to a
single rate-limit / fingerprint identity. It never returns a raw address into
application logs; callers hash it with the abuse HMAC key.
"""

from __future__ import annotations

import ipaddress

#: Shared identity for a missing or unparseable client address. All such requests
#: intentionally share one bucket so an attacker cannot mint fresh identities.
UNKNOWN_CLIENT_IP = "unknown"


def canonical_client_ip(raw: str | None) -> str:
    """Return a canonical textual form for ``raw`` or the shared unknown marker.

    * IPv4/IPv6 are normalised, so ``0:0:0:0:0:0:0:1`` and ``::1`` collapse to
      one identity and ``2001:0db8::1`` becomes ``2001:db8::1``.
    * An IPv6 zone id (``fe80::1%eth0``) is dropped so the same host cannot get
      two identities.
    * A missing client, an empty value, or anything that is not a valid IP
      address collapses to :data:`UNKNOWN_CLIENT_IP`.
    """
    if not raw:
        return UNKNOWN_CLIENT_IP
    candidate = raw.strip()
    if not candidate:
        return UNKNOWN_CLIENT_IP
    # Drop an IPv6 zone id, if present (``addr%zone``).
    if "%" in candidate:
        candidate = candidate.split("%", 1)[0]
    try:
        return str(ipaddress.ip_address(candidate))
    except ValueError:
        return UNKNOWN_CLIENT_IP
