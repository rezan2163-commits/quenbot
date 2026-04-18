#!/usr/bin/env bash
# install_systemd.sh — idempotent installer for quenbot systemd unit (§12)
set -euo pipefail

UNIT_SRC="$(cd "$(dirname "$0")" && pwd)/quenbot.service"
UNIT_DST="/etc/systemd/system/quenbot.service"
WATCHDOG_SRC="$(cd "$(dirname "$0")" && pwd)/watchdog.sh"
WATCHDOG_DST="/usr/local/bin/quenbot_watchdog.sh"

if [[ $EUID -ne 0 ]]; then
  echo "[install] sudo gereklidir" >&2
  exit 1
fi

if [[ ! -f "$UNIT_SRC" ]]; then
  echo "[install] kaynak bulunamadı: $UNIT_SRC" >&2
  exit 2
fi

echo "[install] copying $UNIT_SRC → $UNIT_DST"
install -m 0644 "$UNIT_SRC" "$UNIT_DST"

if [[ -f "$WATCHDOG_SRC" ]]; then
  echo "[install] copying $WATCHDOG_SRC → $WATCHDOG_DST"
  install -m 0755 "$WATCHDOG_SRC" "$WATCHDOG_DST"
fi

systemctl daemon-reload
echo "[install] systemd reloaded."
echo ""
echo "Enable + start with:"
echo "  sudo systemctl enable --now quenbot.service"
echo "Status:"
echo "  systemctl status quenbot.service"
echo "Logs:"
echo "  journalctl -u quenbot.service -f"
echo ""
echo "Optional watchdog cron (every 2 min):"
echo "  */2 * * * * /usr/local/bin/quenbot_watchdog.sh"
