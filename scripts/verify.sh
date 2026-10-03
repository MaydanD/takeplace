#!/usr/bin/env bash
# =============================================================================
# Takeplace — run the full local verification suite (mirrors CI).
#
#   bash scripts/verify.sh
#
# Requires Python 3.11+, Node 20+ and a running PostgreSQL for integration
# tests. Integration tests are skipped automatically when
# TAKEPLACE_TEST_DATABASE_URL is not set.
# =============================================================================
set -euo pipefail

cd "$(dirname "$0")/.."

echo "==> backend: install"
python -m pip install -e "backend[dev]" >/dev/null

echo "==> backend: lint"
(cd backend && python -m ruff check .)

echo "==> backend: format check"
(cd backend && python -m ruff format --check .)

echo "==> backend: typecheck"
(cd backend && python -m mypy app)

echo "==> backend: tests"
(cd backend && python -m pytest)

echo "==> openapi: export"
(cd backend && python scripts/export_openapi.py ../frontend/openapi/openapi.json)

echo "==> frontend: install"
(cd frontend && npm ci --no-audit --no-fund)

echo "==> frontend: api types"
(cd frontend && npm run api:generate)

echo "==> frontend: lint"
(cd frontend && npm run lint)

echo "==> frontend: format check"
(cd frontend && npm run format:check)

echo "==> frontend: typecheck"
(cd frontend && npm run typecheck)

echo "==> frontend: tests"
(cd frontend && npm run test)

echo "==> frontend: build"
(cd frontend && npm run build)

echo
echo "All checks passed."
