# Trade Intelligence Bot

A comprehensive multi-agent trading intelligence system built with TypeScript, Python, and React.

## 🚀 Quick Start

### Prerequisites
- Node.js 24+
- PostgreSQL 16+
- Python 3.9+
- pnpm

### Installation

1. **Clone and setup:**
```bash
git clone <repository-url>
cd trade-intelligence-bot
```

2. **Install dependencies:**
```bash
pnpm install
pip install -r python_agents/requirements.txt
```

3. **Setup database:**
```bash
# Create PostgreSQL database
createdb trade_intel

# Set environment variables
cp .env.example .env
# Edit .env with your database URL and API keys
```

4. **Initialize database:**
```bash
pnpm --filter @workspace/db run push
```

### Running the System

1. **Start Python agents:**
```bash
python python_agents/main.py
```

2. **Start API server:**
```bash
pnpm --filter @workspace/api-server run dev
```

3. **Start dashboard (in another terminal):**
```bash
pnpm --filter @workspace/market-intel run dev
```

## 📊 Architecture

### Multi-Agent System

- **🕵️ Scout Agent**: Real-time market data collection from Binance & Bybit
- **🧠 Strategist Agent**: ML-powered signal generation using cosine similarity
- **👻 Ghost Simulator**: Paper trading with risk management
- **🔍 Auditor Agent**: Self-learning from failures and continuous improvement

### Tech Stack

- **Backend**: Express.js + TypeScript
- **Frontend**: React + Vite + Tailwind CSS
- **Database**: PostgreSQL + Drizzle ORM
- **AI/ML**: scikit-learn for pattern recognition
- **Real-time**: WebSocket connections to exchanges
- **Validation**: Zod schemas
- **API**: OpenAPI 3.1 specification

## 🎯 Features

- ✅ Real-time price monitoring (10+ trading pairs)
- ✅ Automated signal generation with ML
- ✅ Paper trading simulation
- ✅ Risk management (stop-loss, take-profit)
- ✅ Self-learning blacklist patterns
- ✅ Interactive dashboard
- ✅ RESTful API
- ✅ Comprehensive logging and monitoring

## 📈 Dashboard

Access the dashboard at `http://localhost:5173` to monitor:

- Live agent status
- Trading signals
- Simulation performance
- Price movements
- Blacklist patterns
- System health metrics

## 🔧 Configuration

### Environment Variables

```bash
# Database
DATABASE_URL=postgresql://user:pass@localhost:5432/trade_intel

# Exchange APIs (optional for paper trading)
BINANCE_API_KEY=your_key
BINANCE_SECRET_KEY=your_secret
BYBIT_API_KEY=your_key
BYBIT_SECRET_KEY=your_secret
```

### Agent Configuration

Modify `python_agents/config.py` to adjust:

- Trading pairs to monitor
- Thresholds and parameters
- Risk management settings
- API endpoints

## 🏗️ Development

### Project Structure

```
trade-intelligence-bot/
├── artifacts/              # Deployable applications
│   ├── api-server/         # Express API server
│   └── market-intel/       # React dashboard
├── lib/                    # Shared libraries
│   ├── api-spec/           # OpenAPI spec + codegen
│   ├── api-client-react/   # Generated React hooks
│   ├── api-zod/            # Generated Zod schemas
│   └── db/                 # Database schema & connection
├── python_agents/          # Python multi-agent system
│   ├── config.py           # Global configuration
│   ├── database.py         # Async PostgreSQL layer
│   ├── scout_agent.py      # Market data collection
│   ├── strategist_agent.py # Signal generation
│   ├── ghost_simulator_agent.py # Paper trading
│   ├── auditor_agent.py    # Self-learning
│   ├── main.py             # Agent orchestration
│   └── requirements.txt    # Python dependencies
├── scripts/                # Utility scripts
└── pnpm-workspace.yaml     # Monorepo configuration
```

### Available Scripts

```bash
# Build all packages
pnpm run build

# Type checking
pnpm run typecheck

# Generate API client code
pnpm --filter @workspace/api-spec run codegen

# Push database schema
pnpm --filter @workspace/db run push

# Start development servers
pnpm --filter @workspace/api-server run dev
pnpm --filter @workspace/market-intel run dev
```

## 🔒 Safety & Security

- **Paper Trading Only**: No real money trading
- **Comprehensive Logging**: Full audit trail
- **Error Handling**: Graceful failure recovery
- **Rate Limiting**: Exchange API protection
- **Input Validation**: Zod schema validation

## 📚 API Documentation

API endpoints are documented in OpenAPI 3.1 format at `lib/api-spec/`.

Key endpoints:
- `GET /api/dashboard/summary` - System overview
- `GET /api/scout/trades` - Recent trades
- `GET /api/signals` - Trading signals
- `GET /api/simulations` - Paper trading results

## 🤝 Contributing

1. Fork the repository
2. Create a feature branch
3. Make your changes
4. Add tests
5. Submit a pull request

## 📄 License

MIT License - see LICENSE file for details.

## ⚠️ Disclaimer

This is a trading intelligence system for educational and research purposes. Always perform your own due diligence before making trading decisions. Past performance does not guarantee future results.