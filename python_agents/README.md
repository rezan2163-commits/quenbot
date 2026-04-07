# Trade Intelligence Python Agents

This directory contains the Python-based multi-agent trading intelligence system that powers the Trade Intelligence Bot.

## Agents Overview

### 🕵️ Scout Agent (`scout_agent.py`)
- **Purpose**: Real-time market data collection
- **Responsibilities**:
  - WebSocket connections to Binance and Bybit
  - Real-time trade data streaming
  - Price movement detection (≥2% changes)
  - T-10 window data collection

### 🧠 Strategist Agent (`strategist_agent.py`)
- **Purpose**: Signal generation using machine learning
- **Responsibilities**:
  - Cosine similarity analysis
  - Pattern recognition
  - Confidence scoring
  - Signal validation against blacklist

### 👻 Ghost Simulator (`ghost_simulator_agent.py`)
- **Purpose**: Paper trading and risk management
- **Responsibilities**:
  - Paper trading execution
  - Take-profit and stop-loss management
  - Position sizing
  - Performance tracking

### 🔍 Auditor Agent (`auditor_agent.py`)
- **Purpose**: Self-learning and continuous improvement
- **Responsibilities**:
  - Failure analysis
  - Pattern learning from mistakes
  - Threshold adjustment
  - Blacklist pattern updates

## Configuration

All configuration is managed through `config.py`. Key settings include:

- Database connection
- Exchange WebSocket URLs
- Trading pairs to monitor
- Agent thresholds and parameters
- API keys (for live trading, optional for paper trading)

## Database Schema

The system uses PostgreSQL with the following tables:

- `trades` - Raw trade data
- `price_movements` - Detected price movements
- `signals` - Generated trading signals
- `simulations` - Paper trading results
- `blacklist_patterns` - False positive patterns
- `audit_reports` - Failure analysis reports
- `agent_config` - Agent configuration storage

## Installation

```bash
pip install -r requirements.txt
```

## Usage

### Development Mode
```bash
python main.py
```

### Production Mode
```bash
# Set environment variables
export DATABASE_URL="postgresql://user:pass@localhost:5432/trade_intel"

# Run with logging
python main.py 2>&1 | tee trade_intelligence.log
```

## Monitoring

The system provides comprehensive monitoring:

- Real-time agent status
- Performance metrics
- Error logging
- Health checks
- Dashboard integration

## API Integration

The agents integrate with the TypeScript API server for:

- Dashboard data serving
- Configuration management
- Real-time updates
- Historical data access

## Safety Features

- Paper trading only (no real money)
- Comprehensive error handling
- Automatic reconnection
- Rate limiting
- Blacklist pattern learning

## Development

### Adding New Agents

1. Create new agent class inheriting from base `Agent` class
2. Implement required methods: `initialize()`, `start()`, `stop()`
3. Add to `main.py` agent list
4. Update configuration in `config.py`

### Testing

```bash
# Run specific agent tests
python -m pytest tests/test_scout_agent.py -v

# Run all tests
python -m pytest tests/ -v
```

## Troubleshooting

### Common Issues

1. **Database Connection Failed**
   - Check DATABASE_URL environment variable
   - Ensure PostgreSQL is running
   - Verify database permissions

2. **WebSocket Connection Failed**
   - Check internet connection
   - Verify exchange URLs in config
   - Check firewall settings

3. **Agent Not Starting**
   - Check agent logs
   - Verify dependencies installed
   - Check configuration values

### Logs

All logs are written to `trade_intelligence.log` with the following levels:
- INFO: Normal operations
- WARNING: Potential issues
- ERROR: Critical errors

## Contributing

1. Follow the existing code style
2. Add comprehensive logging
3. Write tests for new features
4. Update documentation
5. Ensure all agents handle shutdown gracefully