#!/usr/bin/env bash
# =============================================================================
# Takeplace — bring up the local development stack with one command.
#
#   bash scripts/dev.sh
#
# Steps:
#   1. create .env from .env.example with generated secrets if missing
#   2. build images
#   3. start PostgreSQL and wait for it to be healthy
#   4. run migrations (one-shot `migrate` service)
#   5. start API and frontend
#
# Never uses sleeps: it waits on compose healthchecks and service completion.
# =============================================================================
set -euo pipefail

cd "$(dirname "$0")/.."

if ! command -v docker >/dev/null 2>&1; then
  echo "error: docker is required" >&2
  exit 1
fi

if [[ ! -f .env ]]; then
  echo "==> .env not found; creating it with generated secrets"
  bash scripts/bootstrap-env.sh
fi

echo "==> building images"
docker compose build

echo "==> starting PostgreSQL"
docker compose up -d postgres

echo "==> waiting for PostgreSQL to become healthy"
# `docker compose up --wait` blocks until the service is healthy.
docker compose up -d --wait postgres

echo "==> applying migrations"
docker compose run --rm migrate

echo "==> starting API and frontend"
docker compose up -d api frontend

echo
echo "Takeplace is starting:"
echo "  frontend : http://localhost:${TAKEPLACE_FRONTEND_PORT:-5173}"
echo "  API docs : http://localhost:${TAKEPLACE_API_PORT:-8000}/docs"
echo "  health   : http://localhost:${TAKEPLACE_API_PORT:-8000}/health/ready"
echo
echo "Follow logs with: docker compose logs -f api frontend"
