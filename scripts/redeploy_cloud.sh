#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

echo "[quenbot] Redeploy basladi..."

echo "[1/5] API build"
pnpm --dir artifacts/api-server run build

echo "[2/5] Dashboard build"
pnpm --dir dashboard run build

echo "[3/5] Python syntax quick check"
/workspaces/quenbot/.venv/bin/python -m py_compile python_agents/main.py python_agents/scout_agent.py python_agents/strategist_agent.py

if command -v pm2 >/dev/null 2>&1; then
  echo "[4/5] PM2 apps restart"
  pm2 startOrRestart ecosystem.config.js --update-env
  pm2 save || true
else
  echo "[4/5] PM2 bulunamadi, manuel restart gerekli"
  echo "  bash START_ALL_ASYNC.sh"
fi

echo "[5/5] Health checks"
if command -v curl >/dev/null 2>&1; then
  curl -sf http://127.0.0.1:3001/api/health >/dev/null && echo "  API: OK" || echo "  API: FAIL"
  curl -sf http://127.0.0.1:5173 >/dev/null && echo "  Dashboard: OK" || echo "  Dashboard: FAIL"
fi

echo "[quenbot] Redeploy tamamlandi."
