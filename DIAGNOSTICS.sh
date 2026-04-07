#!/bin/bash

# 
# QuenBot - Simple Setup & Verification
#

echo "🔍 QuenBot System Diagnostic"
echo "=============================="
echo ""

# 1. Check PostgreSQL
echo "1. PostgreSQL Status:"
if docker ps | grep -q "quenbot-db"; then
    echo "   ✓ Running"
    docker ps | grep "quenbot-db"
else
    echo "   ✗ NOT running"
fi
echo ""

# 2. Check API Server
echo "2. API Server Health:"
if curl -s http://localhost:3001/api/health > /dev/null 2>&1; then
    echo "   ✓ Running on port 3001"
    curl -s http://localhost:3001/api/health | jq . 2>/dev/null || curl -s http://localhost:3001/api/health
else
    echo "   ✗ NOT responding on port 3001"
fi
echo ""

# 3. Check Dashboard
echo "3. Dashboard:"
if curl -s http://localhost:4173 > /dev/null 2>&1 || curl -s http://localhost:5173 > /dev/null 2>&1; then
    echo "   ✓ Dashboard running"
else
    echo "   ✗ Dashboard NOT running"
    echo "   (Check http://localhost:4173 or http://localhost:5173)"
fi
echo ""

# 4. Check Database Tables
echo "4. Database Tables:"
docker exec -e PGPASSWORD=password quenbot-db psql -U user -d trade_intel -c "
SELECT table_name FROM information_schema.tables WHERE table_schema='public' ORDER BY table_name;
" 2>/dev/null || echo "   ✗ Could not query database"
echo ""

# 5. Python Agents Status
echo "5. Python Agents:"
if [ -f "/workspaces/quenbot/python_agents/agents.log" ]; then
    echo "   ✓ Log file exists"
    echo "   Last 5 lines:"
    tail -5 /workspaces/quenbot/python_agents/agents.log | sed 's/^/     /'
else
    echo "   ✗ Not running yet"
fi
echo ""

# 6. Quick Stats
echo "6. Quick Database Stats:"
docker exec -e PGPASSWORD=password quenbot-db psql -U user -d trade_intel -c "
SELECT 
  (SELECT COUNT(*) FROM trades) as trades,
  (SELECT COUNT(*) FROM signals) as signals,
  (SELECT COUNT(*) FROM simulations) as simulations,
  (SELECT COUNT(*) FROM price_movements) as movements;
" 2>/dev/null || echo "   (Tables being created...)"
echo ""

echo "=============================="
echo ""
echo "📋 NEXT STEPS:"
echo ""
echo "Terminal 1 (Python Agents):"
echo "  cd /workspaces/quenbot/python_agents && python3 main.py"
echo ""
echo "Terminal 2 (API Server):"  
echo "  cd /workspaces/quenbot && pnpm --dir artifacts/api-server run dev"
echo ""
echo "Terminal 3 (Dashboard):"
echo "  cd /workspaces/quenbot && pnpm --dir artifacts/market-intel run preview"
echo ""
echo "Then open your browser to the dashboard URL"
echo ""
