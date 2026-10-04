"""Run the backend test suite against a dedicated PostgreSQL test database.

The helper derives the disposable test DSN from application settings and reads
the migrator password from the environment, so the integration/concurrency
suites run against a real PostgreSQL database instead of being skipped — and no
credentials are ever hardcoded here.

Run it as a module from the ``backend`` directory (or ``/app`` inside the API
container, which is the documented Compose context):

    # recommended: inside the Compose API container
    docker compose exec -T api python -m scripts.run_local_tests -q

    # equivalent from the backend directory when the package and database are
    # reachable and ``TAKEPLACE_DB_*`` are exported, e.g. integration only:
    cd backend && python -m scripts.run_local_tests -m integration

Any extra arguments are passed straight through to ``pytest``.
"""

from __future__ import annotations

import os
import sys

import pytest
from app.settings import Settings
from sqlalchemy.engine import make_url


def main() -> int:
    """Configure the test environment and delegate to pytest."""
    settings = Settings()
    test_db = os.environ.get("TAKEPLACE_TEST_DB", f"{settings.db_name}_test")
    os.environ.setdefault(
        "TAKEPLACE_TEST_DATABASE_URL",
        make_url(settings.app_database_url)
        .set(database=test_db)
        .render_as_string(hide_password=False),
    )
    os.environ["TAKEPLACE_DB_MIGRATOR_PASSWORD"] = settings.db_migrator_password
    return pytest.main(sys.argv[1:])


if __name__ == "__main__":
    raise SystemExit(main())
