import os
from typing import Dict, Any
from dotenv import load_dotenv

load_dotenv()

class Config:
    # Database
    DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://user:password@localhost:5432/trade_intel")

    # Exchanges - 2026 Updated URLs
    # Binance WebSocket
    BINANCE_SPOT_WS_URL = "wss://stream.binance.com:9443/ws"
    # Binance Futures WebSocket (fapi stream)
    BINANCE_FUTURES_WS_URL = "wss://fstream.binance.com/ws"
    # Bybit WebSocket (V5 API)
    BYBIT_SPOT_WS_URL = "wss://stream.bybit.com/v5/public/spot"
    BYBIT_FUTURES_WS_URL = "wss://stream.bybit.com/v5/public/linear"
    
    # REST API Base URLs
    BINANCE_REST_API = "https://api.binance.com"
    BYBIT_REST_API = "https://api.bybit.com"

    # Trading pairs to monitor
    TRADING_PAIRS = [
        "BTCUSDT", "ETHUSDT", "BNBUSDT", "ADAUSDT", "SOLUSDT",
        "DOTUSDT", "LINKUSDT", "LTCUSDT", "XRPUSDT", "BCHUSDT"
    ]

    WATCHLIST = TRADING_PAIRS.copy()
    MARKET_TYPES = ["spot", "futures"]

    # Agent thresholds
    PRICE_MOVEMENT_THRESHOLD = 0.01  # 1%
    SIMILARITY_THRESHOLD = 0.4
    GHOST_SIMILARITY_THRESHOLD = 0.5
    AUDIT_LEARNING_RATE = 0.1

    # Time windows
    T10_WINDOW_MINUTES = 10
    SIMULATION_TIMEOUT_HOURS = 24

    # Evolutionary strategy parameters
    STRATEGY_POPULATION_SIZE = 20
    STRATEGY_GENERATIONS = 15
    STRATEGY_MIN_MEAN_PROFIT = 0.005

    # Paper trading thresholds
    GHOST_TAKE_PROFIT_PCT = 0.05
    GHOST_STOP_LOSS_PCT = 0.03

    # Risk Management
    RISK_MAX_DAILY_TRADES = 20
    RISK_MAX_DAILY_LOSS_PCT = -5.0
    RISK_MAX_CONSECUTIVE_LOSSES = 5
    RISK_MAX_DRAWDOWN_PCT = -10.0
    RISK_MAX_SAME_DIRECTION = 3
    RISK_COOLDOWN_AFTER_LOSS_SEC = 300
    RISK_MAX_OPEN_POSITIONS = 8

    # API Keys (optional for paper trading)
    BINANCE_API_KEY = os.getenv("BINANCE_API_KEY")
    BINANCE_SECRET_KEY = os.getenv("BINANCE_SECRET_KEY")
    BYBIT_API_KEY = os.getenv("BYBIT_API_KEY")
    BYBIT_SECRET_KEY = os.getenv("BYBIT_SECRET_KEY")

    @classmethod
    def get_agent_config(cls, agent_name: str) -> Dict[str, Any]:
        """Get configuration for specific agent"""
        configs = {
            "scout": {
                "reconnect_delay": 5,
                "max_reconnect_attempts": 10,
                "heartbeat_interval": 30,
                "rest_fetch_interval_seconds": 30,
                "rest_fetch_limit": 100
            },
            "strategist": {
                "analysis_window": 100,
                "min_samples": 50,
                "feature_weights": {
                    "price_change": 0.4,
                    "volume_change": 0.3,
                    "time_factor": 0.3
                }
            },
            "ghost_simulator": {
                "take_profit_pct": 0.05,  # 5%
                "stop_loss_pct": 0.03,    # 3%
                "max_position_size": 1000,
                "commission_pct": 0.001   # 0.1%
            },
            "auditor": {
                "review_interval_hours": 24,
                "min_audit_samples": 100,
                "false_positive_threshold": 0.7
            }
        }
        return configs.get(agent_name, {})