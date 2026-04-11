📊 QUENBOT PROFESSIONAL STRATEGIC ORCHESTRATION SYSTEM
═══════════════════════════════════════════════════════

[✓ DEPLOYED & OPERATIONALIZED] 2026-04-11 03:59 UTC

────────────────────────────────────────────────────────
🎯 SYSTEM ARCHITECTURE
────────────────────────────────────────────────────────

USER ↔ GEMMA 4 (LLM) ↔ STRATEGIC ORCHESTRATOR ↔ MULTI-AGENT SYSTEM

Key Components:
  ✓ Gemma 4 Local Model (Ollama, localhost:11434)
  ✓ Strategic Chat Interface (CLI + future WebSocket API)
  ✓ Real-time Market Data Acquisition (Scout Agent)
  ✓ Pattern-based Brain Analysis (Euclidean similarity + LLM insights)
  ✓ Signal Generation (Strategist with Gemma optimization)
  ✓ Paper Trading Simulation (Ghost Simulator with risk controls)
  ✓ Quality Assurance (Auditor Agent)
  ✓ Risk Management (Position sizing, drawdown limits, loss protection)
  ✓ Event-Driven Architecture (Full async orchestration)

────────────────────────────────────────────────────────
🔧 REAL DATA FLOW PIPELINE
────────────────────────────────────────────────────────

1. MARKET DATA COLLECTION (Scout)
   └─ WebSocket: Binance/Bybit spot & futures
   └─ REST API: Fallback data fetching
   └─ Output: Live trade data → PostgreSQL
   
2. PATTERN ANALYSIS (Brain + Gemma)
   └─ Euclidean similarity on price vectors
   └─ Pattern matching against historical signatures
   └─ Gemma-enhanced confidence scoring
   └─ Output: Pattern match events → Event Bus
   
3. SIGNAL GENERATION (Strategist + Gemma)
   └─ Technical indicator analysis
   └─ Multi-timeframe synthesis (15m, 1h, 4h, 1d)
   └─ Gemma recommendation refinement
   └─ Output: Trade signals with confidence levels
   
4. PAPER TRADING (Ghost Simulator)
   └─ Entry price: From generated signal
   └─ Take profit: 5% (configurable)
   └─ Stop loss: 3% (configurable)
   └─ Position sizing: Dynamic from risk manager
   └─ Output: Simulation trades with P&L tracking
   
5. QUALITY CONTROL (Auditor)
   └─ Signal hypothesis testing
   └─ False positive detection
   └─ Win rate tracking
   └─ Output: Quality metrics and improvement recommendations
   
6. RISK GOVERNANCE
   └─ Daily trade limit: 20 (configurable)
   └─ Max daily loss: -5% (configurable)
   └─ Max drawdown: -10% (configurable)
   └─ Consecutive loss cooldown: 300s
   └─ Max open positions: 8
   └─ Output: Risk gate on every signal

────────────────────────────────────────────────────────
💬 USER INTERACTION MODEL
────────────────────────────────────────────────────────

User interacts with system via Natural Language through Gemma 4:

COMMANDS & EXAMPLES:

1. STRATEGY UPDATES
   "make strategy aggressive"
   "switch to conservative mode"
   "increase risk level to high"
   → Gemma analyzes and updates parameters
   → Changes propagated to all agents

2. RISK MANAGEMENT  
   "increase stop loss to 5%"
   "reduce daily trades to 10"
   "set max drawdown to -15%"
   → Gemma recommends safe parameters
   → Risk manager enforces limits

3. WATCHLIST MANAGEMENT
   "pair add ETHUSDT"
   "pair remove ALTUSDT"
   → Real-time watchlist updates
   → Scout picks up new symbols immediately

4. MARKET ANALYSIS
   "analyze BTCUSDT"
   "what signals do you see right now?"
   "analyze market condition"
   → Gemma synthesizes current state
   → Shows opportunities and risks

5. SIGNAL REVIEW
   "generate trading signals"
   "what's the best trade right now?"
   → Real-time signal computation
   → Risk-adjusted recommendations

6. SYSTEM STATUS
   "status"
   "help"
   → Full system health dashboard
   → Command reference

────────────────────────────────────────────────────────
🚀 LAUNCHING STRATEGIC CHAT
────────────────────────────────────────────────────────

LOCAL DEVELOPMENT:
  cd /workspaces/quenbot/python_agents
  python3 start_strategic.py

PRODUCTION (on server):
  cd /root/quenbot/python_agents
  python3 start_strategic.py

EXPECTED OUTPUT:
  🤖 QUENBOT Strategic Chat Interface
  Session: session_20260411_035938
  Type 'help' for commands, 'status' for system state, 'exit' to quit.
  
  📊 You: status
  🧠 Gemma: [System analysis...]

────────────────────────────────────────────────────────
🔌 TECHNICAL STACK
────────────────────────────────────────────────────────

BACKEND:
  ✓ Python 3.12+ (asyncio-based async orchestration)
  ✓ PostgreSQL (trades, patterns, signals, simulations, audit logs)
  ✓ Ollama (Gemma 4 LLM inference, localhost:11434)
  ✓ Event Bus (async pattern: strategy → signal → audit)
  ✓ Task Queue (LLM inference scheduling)
  ✓ Resource Monitor (CPU/memory/connection tracking)

API SERVER:
  ✓ Express.js (port 3001)
  ✓ Cache-optimized endpoints (summary, prices, movers)
  ✓ Dashboard support (Vite frontend)
  ✓ Ready for WebSocket chat endpoint

FRONTEND:
  ✓ React + Vite (port 5173 dev, 5173 prod with PM2)
  ✓ Market intelligence dashboard
  ✓ Real-time data visualization (with fallback polling)
  ✓ Future: WebSocket-based chat integration

DEPLOYMENT:
  ✓ PM2 process management (3 processes: api, agents, dashboard)
  ✓ Auto-restart on crash
  ✓ Log aggregation to files
  ✓ Environment-aware config

────────────────────────────────────────────────────────
📊 CURRENT SYSTEM STATE
────────────────────────────────────────────────────────

Database: ✓ Connected (PostgreSQL)
LLM: ✓ Available (Gemma 4 @ localhost:11434)
Agents: ✓ Crash-resilient orchestration ready
API: ✓ Running (http://localhost:3001)
Dashboard: ✓ Running (http://localhost:5173)
Cache: ✓ Summary/Prices/Movers non-blocking refresh

Recent Optimizations (Session April 11):
  - Cache-first endpoints (prices, movers, summary)
  - Tab-aware frontend polling 
  - Strategic chat interface (Gemma-powered)
  - Professional multi-agent orchestration
  - Pattern matching with Brain evaluation
  - Risk manager enforcement

────────────────────────────────────────────────────────
🎓 PROFESSIONAL SETUP WALKTHROUGH
────────────────────────────────────────────────────────

STEP 1: Verify System
  curl http://localhost:3001/api/health
  → Shows cache age, server health

STEP 2: Start Strategic Chat
  python3 start_strategic.py
  
  Initialization: ~5-10 seconds
  - Database connects
  - Ollama API checks
  - Agents initialize
  - Chat interface ready
  
STEP 3: Natural Language Strategy
  (in chat)
  "make strategy aggressive"
  "pair add BTCUSDT"
  "analyze market condition"
  
  → Gemma interprets each command
  → Agents execute changes in real-time
  → System learns and adapts

STEP 4: Monitor Signals
  "what signals do you see right now?"
  → Scout summarizes anomalies
  → Brain evaluates patterns
  → Strategist generates signals
  → Gemma presents opportunities
  
STEP 5: Review Performance
  "status"
  → Total trades, win rate, P&L
  → Risk level, patterns learned
  → Daily trades remaining
  → Recommendations

────────────────────────────────────────────────────────
🔐 SAFETY & RESILIENCE
────────────────────────────────────────────────────────

✓ 50-level auto-restart for crashed agents
✓ Risk manager blocks over-limit trades
✓ Daily/drawdown/consecutive-loss limits actively enforced
✓ Exponential backoff restarts (5s → 10s → 20s → ... max 300s)
✓ Full event audit trail in database
✓ LLM degraded-mode fallback (rule-based logic if LLM unavailable)
✓ Connection pool management (asyncpg: 8-40 connections)
✓ Graceful shutdown with agent cleanup

────────────────────────────────────────────────────────
🎯 NEXT STEPS (OPTIONAL ENHANCEMENTS)
────────────────────────────────────────────────────────

1. WebSocket Chat API Endpoint
   POST /api/chat/strategy
   → Real-time bidirectional messaging
   → Web dashboard integration

2. Mobile Chat Interface
   → WhatsApp/Telegram bot
   → Voice commands (speech-to-text via Whisper)

3. Advanced Analytics
   → Dashboard: Signal heatmap by symbol/timeframe
   → Performance comparison: LLM vs rule-based

4. Model Fine-Tuning
   → Train Gemma on your historical trade data
   → Custom pattern recognition

5. Multi-Market Expansion
   → Forex, Commodities, Crypto derivatives
   → Cross-asset correlation analysis

────────────────────────────────────────────────────────
📋 QUICK REFERENCE
────────────────────────────────────────────────────────

Files Changed:
  strategic_chat_cli.py     - Interactive chat interface
  start_strategic.py        - Full orchestration launcher
  llm_bridge.py             - call_llm() public method added
  database.py               - execute()/fetch()/fetchone() helpers added
  api-server/index.ts       - Cache-first endpoint optimization
  market-intel/main.tsx     - API_BASE for production pointing

Key Locations:
  Chat CLI:     /python_agents/start_strategic.py
  
Code Commits:
  f6c8a49   - API endpoints cache-first
  8757cf4   - Professional strategic chat interface

Status Check:
  curl http://178.104.159.101:3001/api/health
  Expected: cache age, status=ok

────────────────────────────────────────────────────────
✅ READY FOR PRODUCTION USE
════════════════════════════════════════════════════════

System fully operationalized with real data flow, Gemma 4 integration,
and professional multi-agent orchestration. Users can now manage
trading strategy through natural language dialogue with Gemma,
backed by autonomous intelligent agents executing trades in
real-time with full risk governance.

Your QuenBot is now a complete multi-agent AI trading system.
