"""Infrastructure/config regression tests (PROJECT-SPEC §37.4, §47).

These lock in the production Caddy contract without needing a running Caddy:

* the API (including the SSE stream) is reverse-proxied and never falls back to
  the SPA;
* SSE streams without buffering (``flush_interval -1``);
* the built frontend has a SPA fallback so deep React Router routes survive a
  direct open / F5 while real static files are still served as files;
* the baseline security headers are preserved.
"""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
CADDYFILE = REPO_ROOT / "infra" / "caddy" / "Caddyfile"


def _caddyfile() -> str:
    return CADDYFILE.read_text(encoding="utf-8")


def test_api_is_reverse_proxied_without_buffering() -> None:
    text = _caddyfile()
    assert "reverse_proxy /api/* api:8000" in text
    assert "flush_interval -1" in text


def test_frontend_has_spa_fallback_for_deep_routes() -> None:
    text = _caddyfile()
    # A missing path is served ``index.html``; real files still win because
    # ``try_files`` keeps ``{path}`` first.
    assert "try_files {path} /index.html" in text
    assert "file_server" in text
    assert "root * /srv" in text


def test_api_route_precedes_and_cannot_fall_back_to_spa() -> None:
    text = _caddyfile()
    api_index = text.index("reverse_proxy /api/* api:8000")
    spa_index = text.index("try_files {path} /index.html")
    # The API proxy is matched first; the SPA fallback never applies to /api/*.
    assert api_index < spa_index


def test_security_headers_are_preserved() -> None:
    text = _caddyfile()
    for header in (
        "Strict-Transport-Security",
        "X-Content-Type-Options",
        "Referrer-Policy",
        "X-Frame-Options",
    ):
        assert header in text
