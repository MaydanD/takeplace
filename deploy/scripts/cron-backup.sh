#!/usr/bin/env bash
# =============================================================================
# Takeplace — unattended entry point for the scheduled daily backup.
#
#   bash deploy/scripts/cron-backup.sh
#
# Installed as /etc/cron.d/takeplace-backup (see deploy/README.md §5). It only
# wraps backup-db.sh so the cron line stays trivial and the log cannot grow
# without bound on a 60 GB disk:
#
#   * rotates <log> to <log>.1 once it exceeds MAX_LOG_BYTES;
#   * runs the logical dump + checksum (backup-db.sh, retention: newest 14);
#   * keeps the exit status of the dump, so cron mails/logs a real failure.
#
# It never touches the `smm_planner` or `control` databases.
# =============================================================================
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
log_path="${TAKEPLACE_BACKUP_LOG:-/var/log/takeplace-backup.log}"
max_log_bytes="${TAKEPLACE_BACKUP_LOG_MAX_BYTES:-1048576}"

# Rotate a single generation; the dump itself is retention-limited by
# backup-db.sh, so one previous log file is enough history.
if [[ -f "$log_path" ]]; then
  size="$(stat -c %s "$log_path" 2>/dev/null || echo 0)"
  if (( size > max_log_bytes )); then
    mv -f "$log_path" "${log_path}.1"
  fi
fi

{
  echo "=== $(date -Is) takeplace scheduled backup start ==="
  bash "$script_dir/backup-db.sh"
  echo "=== $(date -Is) done (exit 0) ==="
} >>"$log_path" 2>&1
