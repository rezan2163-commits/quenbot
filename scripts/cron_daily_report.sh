#!/usr/bin/env bash
# Aşama 3 — installs cron entries for daily report (placeholder, no-op currently),
# weekly strategic review (Sunday 18:00), monthly self-audit (1st 03:00 UTC),
# and the weekly ack watchdog hourly check.
#
# Idempotent: removes any prior `# QUENBOT_ASAMA3` block before re-installing.
#
# Usage:
#   bash scripts/cron_daily_report.sh           # install / refresh
#   bash scripts/cron_daily_report.sh --remove  # remove
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PYTHON_BIN="${QUENBOT_PYTHON:-${REPO_ROOT}/.venv/bin/python}"
LOG_DIR="${QUENBOT_CRON_LOG_DIR:-${REPO_ROOT}/python_agents/.cron_logs}"
mkdir -p "${LOG_DIR}"

MARK_BEGIN="# QUENBOT_ASAMA3 begin"
MARK_END="# QUENBOT_ASAMA3 end"

remove_block() {
    crontab -l 2>/dev/null | sed "/${MARK_BEGIN}/,/${MARK_END}/d" || true
}

if [[ "${1:-}" == "--remove" ]]; then
    remove_block | crontab -
    echo "✅ removed Aşama 3 cron block"
    exit 0
fi

NEW_BLOCK=$(cat <<EOF
${MARK_BEGIN}
# Daily report (placeholder hook, 06:00 UTC)
0 6 * * * cd ${REPO_ROOT} && ${PYTHON_BIN} -c "print('quenbot daily heartbeat')" >> ${LOG_DIR}/daily.log 2>&1
# Weekly strategic review (Sunday 18:00 Europe/Istanbul == 15:00 UTC)
0 15 * * 0 cd ${REPO_ROOT} && ${PYTHON_BIN} python_agents/scripts/weekly_strategic_review.py >> ${LOG_DIR}/weekly_review.log 2>&1
# Monthly Qwen self-audit (1st of month 03:00 UTC)
0 3 1 * * cd ${REPO_ROOT} && ${PYTHON_BIN} python_agents/scripts/qwen_self_audit.py >> ${LOG_DIR}/self_audit.log 2>&1
# Weekly ack watchdog (hourly)
0 * * * * cd ${REPO_ROOT} && ${PYTHON_BIN} -c "from weekly_ack_watchdog import get_weekly_ack_watchdog; print(get_weekly_ack_watchdog().check_once())" >> ${LOG_DIR}/ack_watchdog.log 2>&1
${MARK_END}
EOF
)

EXISTING=$(crontab -l 2>/dev/null | sed "/${MARK_BEGIN}/,/${MARK_END}/d" || true)
printf '%s\n%s\n' "${EXISTING}" "${NEW_BLOCK}" | crontab -
echo "✅ installed Aşama 3 cron block"
crontab -l | sed -n "/${MARK_BEGIN}/,/${MARK_END}/p"
