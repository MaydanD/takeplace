#!/usr/bin/env bash
# =============================================================================
# Takeplace — install the daily backup schedule (idempotent).
#
#   bash deploy/scripts/install-backup-cron.sh [--remove]
#
# Writes ONE file, /etc/cron.d/takeplace-backup, so the schedule is visible,
# reviewable and removable without touching any other host automation. The
# server's root crontab and /etc/cron.d are left otherwise untouched, and no
# other service (SMM Planner, Control Panel) is referenced.
#
# Refuses to install unless the backup script passes `bash -n`.
# =============================================================================
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cron_path=/etc/cron.d/takeplace-backup
repo_root="$(cd "$script_dir/../.." && pwd)"
hour="${TAKEPLACE_BACKUP_HOUR:-3}"
minute="${TAKEPLACE_BACKUP_MINUTE:-20}"

if [[ "${1:-}" == "--remove" ]]; then
  rm -f "$cron_path" && echo "removed $cron_path"
  exit 0
fi

bash -n "$script_dir/cron-backup.sh"
bash -n "$script_dir/backup-db.sh"

tmp="$(mktemp)"
cat >"$tmp" <<EOF
# Takeplace closed test stand — daily logical backup of the isolated
# 'takeplace' database only (never smm_planner / control).
# Installed by deploy/scripts/install-backup-cron.sh; remove this file to stop.
# Dumps land in /opt/takeplace/backups (newest 14 kept), log: /var/log/takeplace-backup.log
SHELL=/bin/bash
PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
${minute} ${hour} * * * root cd ${repo_root} && bash deploy/scripts/cron-backup.sh
EOF
install -o root -g root -m 0644 "$tmp" "$cron_path"
rm -f "$tmp"

echo "installed $cron_path (daily at ${hour}:${minute}):"
sed 's/^/  /' "$cron_path"
echo
echo "cron reloads /etc/cron.d automatically; verify with:"
echo "  run-parts --test /etc/cron.d   # or wait for the next run"
echo "  tail -n 20 /var/log/takeplace-backup.log"
