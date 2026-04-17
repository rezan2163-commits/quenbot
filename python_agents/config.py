import os
from typing import Dict, Any
from dotenv import load_dotenv

load_dotenv()

class Config:
    # Database
    DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://user:password@localhost:5432/trade_intel")

    # Exchanges - 2026 Updated URLs
    # Binance WebSocket (port 443 — port 9443 blocked on EU servers)
    BINANCE_SPOT_WS_URL = "wss://stream.binance.com:443/ws"
    # Binance Futures WebSocket (fapi stream)
    BINANCE_FUTURES_WS_URL = "wss://fstream.binance.com/ws"
    # Bybit WebSocket (V5 API)
    BYBIT_SPOT_WS_URL = os.getenv("QUENBOT_BYBIT_SPOT_WS_URL", "wss://stream.bybit.com/v5/public/spot")
    BYBIT_FUTURES_WS_URL = os.getenv("QUENBOT_BYBIT_FUTURES_WS_URL", "wss://stream.bybit.com/v5/public/linear")
    
    # REST API Base URLs
    BINANCE_REST_API = "https://api.binance.com"
    BYBIT_REST_API = os.getenv("QUENBOT_BYBIT_REST_API", "https://api.bybit.com")
    BYBIT_REST_FAST_API = os.getenv("QUENBOT_BYBIT_REST_FAST_API", "https://api.bytick.com")

    # Trading pairs to monitor - Varsayılan watchlist
    TRADING_PAIRS = [
        "BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT",
        "DOGEUSDT", "ADAUSDT", "AVAXUSDT", "APTUSDT", "LINKUSDT",
        "DOTUSDT", "SUIUSDT", "OPUSDT", "ARBUSDT"
    ]

    WATCHLIST = TRADING_PAIRS.copy()
    MARKET_TYPES = ["spot", "futures"]

    # Agent thresholds
    PRICE_MOVEMENT_THRESHOLD = 0.01  # 1%
    SIMILARITY_THRESHOLD = 0.6      # ≥60% triggers SuperGemma brain
    GHOST_SIMILARITY_THRESHOLD = 0.6
    AUDIT_LEARNING_RATE = 0.1

    # Time windows
    T10_WINDOW_MINUTES = 10
    SIMULATION_TIMEOUT_HOURS = 24

    # Evolutionary strategy parameters
    STRATEGY_POPULATION_SIZE = 40
    STRATEGY_GENERATIONS = 30
    STRATEGY_MIN_MEAN_PROFIT = 0.005
    STRATEGY_MAX_TARGET_PCT = 0.12
    HISTORICAL_LOOKBACK_HOURS = int(os.getenv("QUENBOT_HISTORICAL_LOOKBACK_HOURS", "720"))
    SIGNATURE_CACHE_LIMIT = int(os.getenv("QUENBOT_SIGNATURE_CACHE_LIMIT", "5000"))
    SIGNATURE_BACKFILL_LIMIT = int(os.getenv("QUENBOT_SIGNATURE_BACKFILL_LIMIT", "10000"))
    VECTOR_MATCH_LOOKBACK_HOURS = int(os.getenv("QUENBOT_VECTOR_MATCH_LOOKBACK_HOURS", "720"))

    # Paper trading thresholds
    GHOST_TAKE_PROFIT_PCT = 0.05
    GHOST_STOP_LOSS_PCT = 0.03

    # Scout ingestion/runtime tuning
    SCOUT_TRADE_INGEST_WORKERS = int(os.getenv("QUENBOT_SCOUT_INGEST_WORKERS", "8"))
    SCOUT_TRADE_QUEUE_SIZE = int(os.getenv("QUENBOT_SCOUT_TRADE_QUEUE_SIZE", "50000"))
    SCOUT_TRADE_BATCH_SIZE = int(os.getenv("QUENBOT_SCOUT_TRADE_BATCH_SIZE", "96"))
    BINANCE_WS_MAX_QUEUE = int(os.getenv("QUENBOT_BINANCE_WS_MAX_QUEUE", "40000"))
    BYBIT_WS_MAX_QUEUE = int(os.getenv("QUENBOT_BYBIT_WS_MAX_QUEUE", "40000"))

    # Risk Management
    RISK_MAX_DAILY_TRADES = 20
    RISK_MAX_DAILY_LOSS_PCT = -5.0
    RISK_DISABLE_DAILY_LOSS_GATE = os.getenv("QUENBOT_DISABLE_DAILY_LOSS_GATE", "1").lower() in {"1", "true", "yes", "on"}
    RISK_DISABLE_DRAWDOWN_GATE = os.getenv("QUENBOT_DISABLE_DRAWDOWN_GATE", "1").lower() in {"1", "true", "yes", "on"}
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
    def get_bybit_ws_candidates(cls, market_type: str) -> list[str]:
        """Return tunnel-aware Bybit WebSocket candidates in priority order."""
        market = 'spot' if market_type == 'spot' else 'futures'
        tunnel_key = f"QUENBOT_BYBIT_{market.upper()}_TUNNEL_WS_URL"
        fallback_key = f"QUENBOT_BYBIT_{market.upper()}_WS_FALLBACKS"
        primary = cls.BYBIT_SPOT_WS_URL if market == 'spot' else cls.BYBIT_FUTURES_WS_URL
        values = [
            os.getenv(tunnel_key, ''),
            primary,
            *[item.strip() for item in os.getenv(fallback_key, '').split(',') if item.strip()],
        ]
        seen = set()
        result = []
        for value in values:
            if value and value not in seen:
                seen.add(value)
                result.append(value)
        return result

    @classmethod
    def get_bybit_rest_candidates(cls) -> list[str]:
        """Return tunnel-aware Bybit REST candidates in priority order."""
        values = [
            os.getenv('QUENBOT_BYBIT_REST_TUNNEL_URL', ''),
            cls.BYBIT_REST_FAST_API,
            cls.BYBIT_REST_API,
            *[item.strip() for item in os.getenv('QUENBOT_BYBIT_REST_FALLBACKS', '').split(',') if item.strip()],
        ]
        seen = set()
        result = []
        for value in values:
            if value and value not in seen:
                seen.add(value)
                result.append(value.rstrip('/'))
        return result

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

    # ─────────────────────────────────────────────────────────────
    # Intel Upgrade — Pre-Move Detection Engine (Phase 1+)
    # Tüm flag'ler default konservatif. Shadow doğrulaması olmadan
    # live path davranışını değiştiremeyecek şekilde tasarlandı.
    # ─────────────────────────────────────────────────────────────
    FEATURE_STORE_ENABLED = os.getenv("QUENBOT_FEATURE_STORE_ENABLED", "1").lower() in {"1", "true", "yes", "on"}
    FEATURE_STORE_WRITE = os.getenv("QUENBOT_FEATURE_STORE_WRITE", "1").lower() in {"1", "true", "yes", "on"}
    FEATURE_STORE_PATH = os.getenv("QUENBOT_FEATURE_STORE_PATH", "python_agents/.feature_store")
    FEATURE_STORE_FLUSH_SECONDS = float(os.getenv("QUENBOT_FEATURE_STORE_FLUSH_SECONDS", "15"))
    FEATURE_STORE_FLUSH_ROWS = int(os.getenv("QUENBOT_FEATURE_STORE_FLUSH_ROWS", "2000"))
    FEATURE_STORE_QUEUE_MAX = int(os.getenv("QUENBOT_FEATURE_STORE_QUEUE_MAX", "20000"))

    OFI_ENABLED = os.getenv("QUENBOT_OFI_ENABLED", "1").lower() in {"1", "true", "yes", "on"}
    OFI_PUBLISH_HZ = float(os.getenv("QUENBOT_OFI_PUBLISH_HZ", "2.0"))

    MULTI_HORIZON_SIGNATURES_ENABLED = os.getenv("QUENBOT_MH_SIGNATURES_ENABLED", "1").lower() in {"1", "true", "yes", "on"}
    MULTI_HORIZON_WINDOWS_SEC = [300, 1800, 7200, 21600]  # 5m, 30m, 2h, 6h
    MULTI_HORIZON_PUBLISH_HZ = float(os.getenv("QUENBOT_MH_PUBLISH_HZ", "0.5"))

    CONFLUENCE_ENABLED = os.getenv("QUENBOT_CONFLUENCE_ENABLED", "1").lower() in {"1", "true", "yes", "on"}
    CONFLUENCE_WEIGHTS_PATH = os.getenv("QUENBOT_CONFLUENCE_WEIGHTS_PATH", "python_agents/.confluence_weights.json")
    CONFLUENCE_PUBLISH_HZ = float(os.getenv("QUENBOT_CONFLUENCE_PUBLISH_HZ", "1.0"))
    CONFLUENCE_INJECT_LLM = os.getenv("QUENBOT_CONFLUENCE_INJECT_LLM", "1").lower() in {"1", "true", "yes", "on"}

    # Phase 2
    CROSS_ASSET_ENABLED = os.getenv("QUENBOT_CROSS_ASSET_ENABLED", "0").lower() in {"1", "true", "yes", "on"}
    CROSS_ASSET_REBUILD_INTERVAL_MIN = int(os.getenv("QUENBOT_CROSS_ASSET_REBUILD_MIN", "15"))
    CROSS_ASSET_MIN_EDGE_STRENGTH = float(os.getenv("QUENBOT_CROSS_ASSET_MIN_EDGE", "0.08"))
    CROSS_ASSET_MAX_LAG_SEC = int(os.getenv("QUENBOT_CROSS_ASSET_MAX_LAG_SEC", "300"))   # ±5 dk
    CROSS_ASSET_LAG_STEP_SEC = int(os.getenv("QUENBOT_CROSS_ASSET_LAG_STEP_SEC", "15"))  # 15sn bin
    CROSS_ASSET_HISTORY_SEC = int(os.getenv("QUENBOT_CROSS_ASSET_HISTORY_SEC", "7200"))  # 2 saatlik pencere
    CROSS_ASSET_MIN_SAMPLES = int(os.getenv("QUENBOT_CROSS_ASSET_MIN_SAMPLES", "60"))
    CROSS_ASSET_GRAPH_PATH = os.getenv("QUENBOT_CROSS_ASSET_GRAPH_PATH", "python_agents/.cross_asset/latest_graph.json")
    CROSS_ASSET_ALERT_COOLDOWN_SEC = int(os.getenv("QUENBOT_CROSS_ASSET_ALERT_COOLDOWN_SEC", "60"))
    CROSS_ASSET_LEADER_MIN_MOVE_BPS = float(os.getenv("QUENBOT_CROSS_ASSET_LEADER_MIN_BPS", "15"))  # 0.15%

    # Phase 3
    FAST_BRAIN_ENABLED = os.getenv("QUENBOT_FAST_BRAIN_ENABLED", "0").lower() in {"1", "true", "yes", "on"}
    FAST_BRAIN_MODEL_PATH = os.getenv("QUENBOT_FAST_BRAIN_MODEL_PATH", "python_agents/.models/fast_brain_latest.lgb")
    FAST_BRAIN_CALIBRATION_PATH = os.getenv("QUENBOT_FAST_BRAIN_CALIB_PATH", "python_agents/.models/fast_brain_latest.calib.json")
    FAST_BRAIN_T_HIGH = float(os.getenv("QUENBOT_FAST_BRAIN_T_HIGH", "0.65"))
    FAST_BRAIN_T_LOW = float(os.getenv("QUENBOT_FAST_BRAIN_T_LOW", "0.45"))
    FAST_BRAIN_MIN_FEATURES = int(os.getenv("QUENBOT_FAST_BRAIN_MIN_FEATURES", "4"))
    DECISION_ROUTER_ENABLED = os.getenv("QUENBOT_DECISION_ROUTER_ENABLED", "0").lower() in {"1", "true", "yes", "on"}
    DECISION_ROUTER_SHADOW = os.getenv("QUENBOT_DECISION_ROUTER_SHADOW", "1").lower() in {"1", "true", "yes", "on"}
    DECISION_ROUTER_LOG_PATH = os.getenv("QUENBOT_DECISION_ROUTER_LOG_PATH", "python_agents/.decision_router_shadow.jsonl")
    DECISION_ROUTER_MAX_LOG_ROWS = int(os.getenv("QUENBOT_DECISION_ROUTER_MAX_LOG_ROWS", "50000"))

    # Phase 4 — Online learning loop (shadow JSONL + realized moves → rolling accuracy/calibration)
    ONLINE_LEARNING_ENABLED = os.getenv("QUENBOT_ONLINE_LEARNING_ENABLED", "0").lower() in {"1", "true", "yes", "on"}
    ONLINE_LEARNING_INTERVAL_MIN = int(os.getenv("QUENBOT_ONLINE_LEARNING_INTERVAL_MIN", "15"))
    ONLINE_LEARNING_HORIZON_MIN = int(os.getenv("QUENBOT_ONLINE_LEARNING_HORIZON_MIN", "60"))
    ONLINE_LEARNING_MIN_SAMPLES = int(os.getenv("QUENBOT_ONLINE_LEARNING_MIN_SAMPLES", "50"))
    ONLINE_LEARNING_STATE_PATH = os.getenv("QUENBOT_ONLINE_LEARNING_STATE_PATH", "python_agents/.online_learning_state.json")

    # Phase 5
    METRICS_EXPORTER_ENABLED = os.getenv("QUENBOT_METRICS_ENABLED", "0").lower() in {"1", "true", "yes", "on"}
    METRICS_EXPORTER_PORT = int(os.getenv("QUENBOT_METRICS_PORT", "9108"))