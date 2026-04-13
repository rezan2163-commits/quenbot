#!/bin/bash

# Bu script tüm servisleri arka planda başlatır

set -e

echo "🚀 QuenBot - Tüm Servisler Başlatılıyor"
echo "========================================"

cd /workspaces/quenbot

# Python Agents
echo "→ Python Agents başlatılıyor..."
cd /workspaces/quenbot/python_agents
nohup python3 main.py > agents.log 2>&1 &
AGENTS_PID=$!
echo "  PID: $AGENTS_PID (agents.log'u izlemek için: tail -f agents.log)"

sleep 3

# API Server
echo "→ API Server başlatılıyor..."
pnpm --dir artifacts/api-server run start > /tmp/api-server.log 2>&1 &
API_PID=$!
echo "  PID: $API_PID → http://localhost:3001"

sleep 3

# Dashboard
echo "→ Dashboard başlatılıyor..."
pnpm --dir dashboard run start > /tmp/dashboard.log 2>&1 &
DASHBOARD_PID=$!
echo "  PID: $DASHBOARD_PID"

echo ""
echo "✅ Tüm servisler başlatıldı!"
echo ""
echo "Açık portlar:"
echo "  - API Server: http://localhost:3001"
echo "  - Dashboard: http://localhost:4173 (veya farklı port)"
echo ""
echo "Günlükleri izlemek:"
echo "  - Python Agents: tail -f /workspaces/quenbot/python_agents/agents.log"
echo "  - API Server: tail -f /tmp/api-server.log"
echo "  - Dashboard: tail -f /tmp/dashboard.log"
echo ""
echo "Durdurmak için:"
echo "  kill $AGENTS_PID $API_PID $DASHBOARD_PID"
echo ""
echo "Tüm prosesleri durdurmak:"
echo "  killall python3 node npm pnpm"
echo ""

# Keep script running
wait $AGENTS_PID
