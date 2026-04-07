#!/bin/bash

echo "🚀 QuenBot - Final System Test"
echo "=============================="
echo ""

# Start Python Agents
echo "Starting Python Agents..."
cd /workspaces/quenbot/python_agents
python3 main.py > /tmp/agents.log 2>&1 &
AGENTS_PID=$!
echo "PID: $AGENTS_PID"
sleep 5

# Start API Server
echo "Starting API Server..."
cd /workspaces/quenbot
pnpm --dir artifacts/api-server run dev > /tmp/api.log 2>&1 &
API_PID=$!
echo "PID: $API_PID"
sleep 5

# Test endpoints
echo ""
echo "Testing endpoints..."
echo ""

echo "1. Health Check:"
curl -s http://localhost:3001/api/health | jq . 2>/dev/null || echo "API not ready"

echo ""
echo "2. Dashboard Summary:"
curl -s http://localhost:3001/api/dashboard/summary | jq . 2>/dev/null || echo "No data yet"

echo ""
echo "3. Checking logs..."
echo ""
echo "Python Agents (last 10 lines):"
tail -10 /tmp/agents.log | grep -E "(WebSocket|Connected|signal|ERROR)" | head -5

echo ""
echo "System running!"
echo "Agents PID: $AGENTS_PID"
echo "API PID: $API_PID"
echo ""
echo "To stop:"
echo "  kill $AGENTS_PID $API_PID"

wait
