#!/usr/bin/env bash
# =============================================================================
# Takeplace — logical backup of the test-stand database.
#
#   bash deploy/scripts/backup-db.sh [--env <path>] [--out <dir>] [--keep N] [--label <tag>]
#
# * `pg_dump` runs inside the EXISTING PostgreSQL container over its local socket,
#   so no database password is ever placed on a command line;
# * writes a gzipped plain-SQL dump plus a `.sha256` checksum;
# * prunes all but the newest N dumps (default 14) — never touches anything
#   outside its own output directory;
# * NEVER touches the `smm_planner` or `control` databases.
#
# This is a same-host backup only. Offsite copies, an RPO/RTO commitment and a
# retention schedule that reflects the personal-data rules are still open
# (PROJECT-SPEC §65, Stage 14) — see deploy/README.md.
#
# Restore drill: deploy/scripts/restore-verify.sh
# =============================================================================
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(cd "$script_dir/../.." && pwd)"
env_path="$repo_root/.env"
out_dir="$(cd "$repo_root/.." && pwd)/backups"
pg_container="data-postgres-1"
keep=14
label=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --env) env_path="${2:?}"; shift 2 ;;
    --out) out_dir="${2:?}"; shift 2 ;;
    --keep) keep="${2:?}"; shift 2 ;;
    --label) label="${2:?}"; shift 2 ;;
    --pg-container) pg_container="${2:?}"; shift 2 ;;
    -h|--help) sed -n '2,18p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "unknown argument: $1" >&2; exit 1 ;;
  esac
done

[[ -r "$env_path" ]] || { echo "error: cannot read $env_path" >&2; exit 1; }
db_name="$(grep -E '^TAKEPLACE_DB_NAME=' "$env_path" | tail -n1 | cut -d= -f2-)"
[[ -n "$db_name" ]] || { echo "error: TAKEPLACE_DB_NAME is not set in $env_path" >&2; exit 1; }

docker inspect "$pg_container" >/dev/null 2>&1 \
  || { echo "error: container $pg_container not found" >&2; exit 1; }

mkdir -p "$out_dir"
chmod 700 "$out_dir"

stamp="$(date +%Y%m%d-%H%M%S)"
suffix=""
[[ -n "$label" ]] && suffix="-${label}"
target="$out_dir/${db_name}-${stamp}${suffix}.sql.gz"

echo "backing up database '$db_name' -> $target"

# --clean/--if-exists makes the dump self-contained for a restore drill;
# --no-password relies on the container's trusted local socket.
set -o pipefail
docker exec -i "$pg_container" pg_dump -U postgres -d "$db_name" \
    --clean --if-exists --no-password \
  | gzip -9 > "$target"

if [[ ! -s "$target" ]]; then
  echo "error: dump is empty; removing $target" >&2
  rm -f "$target"
  exit 1
fi

sha256sum "$target" > "$target.sha256"
chmod 600 "$target" "$target.sha256"

size="$(du -h "$target" | cut -f1)"
echo "backup complete: $(basename "$target") ($size)"

# --- retention: keep the newest N dumps (and their checksums) ----------------
mapfile -t old < <(ls -1t "$out_dir"/${db_name}-*.sql.gz 2>/dev/null | tail -n +$((keep + 1)))
for file in "${old[@]:-}"; do
  [[ -n "$file" ]] || continue
  echo "pruning old backup: $(basename "$file")"
  rm -f "$file" "$file.sha256"
done

echo
echo "verify recovery with: bash deploy/scripts/restore-verify.sh"
