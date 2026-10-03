"""Container healthcheck helper.

Docker healthchecks call ``python -m app.healthcheck --url ...`` so the probe
does not depend on curl/wget being present in the image.
"""

from __future__ import annotations

import argparse
import sys
import urllib.error
import urllib.request


def probe(url: str, timeout: float = 4.0) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:  # noqa: S310
            return 200 <= int(response.status) < 300
    except (urllib.error.URLError, TimeoutError, OSError):
        return False


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Takeplace container healthcheck")
    parser.add_argument("--url", required=True, help="URL to probe")
    parser.add_argument("--timeout", type=float, default=4.0)
    args = parser.parse_args(argv)
    ok = probe(args.url, args.timeout)
    print("healthy" if ok else "unhealthy")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
