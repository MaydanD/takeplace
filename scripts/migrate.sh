#!/usr/bin/env bash
# =============================================================================
# Apply migrations.
#
#   bash scripts/migrate.sh            # run in the compose `migrate` service
#   bash scripts/migrate.sh local      # run Alembic directly (env must point at DB)
# =============================================================================
set -euo pipefail

cd "$(dirname "$0")/.."

mode="${1:-compose}"

if [[ "$mode" == "local" ]]; then
  (cd backend && alembic upgrade head)
else
  docker compose run --rm migrate
fi
