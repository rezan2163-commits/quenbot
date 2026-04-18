#!/usr/bin/env bash
# watchdog.sh — External heartbeat watchdog (§12)
# Reads heartbeat file; if stale > timeout, optionally restarts the service.
# Default: observe-only (no restart). Enable QB_WATCHDOG_RESTART=1 to act.
set -euo pipefail

HEARTBEAT_PATH="${QUENBOT_WATCHDOG_HEARTBEAT_PATH:-/tmp/quenbot_heartbeat}"
TIMEOUT_SEC="${QUENBOT_WATCHDOG_TIMEOUT_SEC:-120}"
SERVICE="${QUENBOT_WATCHDOG_SERVICE:-quenbot.service}"
RESTART="${QB_WATCHDOG_RESTART:-0}"

now=$(date +%s)

if [[ ! -f "$HEARTBEAT_PATH" ]]; then
  echo "[watchdog] missing heartbeat file: $HEARTBEAT_PATH"
  exit 2
fi

last=$(cat "$HEARTBEAT_PATH" 2>/dev/null || echo 0)
age=$(( now - last ))

echo "[watchdog] heartbeat age=${age}s timeout=${TIMEOUT_SEC}s"

if (( age > TIMEOUT_SEC )); then
  echo "[watchdog] STALE heartbeat (> ${TIMEOUT_SEC}s)"
  if [[ "$RESTART" == "1" ]]; then
    if command -v systemctl >/dev/null 2>&1; then
      echo "[watchdog] restarting $SERVICE ..."
      sudo systemctl restart "$SERVICE" || echo "[watchdog] restart failed"
    else
      echo "[watchdog] systemctl not available; skipping restart"
    fi
  fi
  exit 1
fi

echo "[watchdog] OK"
exit 0
