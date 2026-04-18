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
    # Phase 4 Finalization — DB-backed counterfactual store (JSONL WAL + DB persist)
    ONLINE_LEARNING_PERSIST_DB = os.getenv("QUENBOT_ONLINE_LEARNING_PERSIST_DB", "1").lower() in {"1", "true", "yes", "on"}
    ONLINE_LEARNING_DB_OFFSET_PATH = os.getenv("QUENBOT_ONLINE_LEARNING_DB_OFFSET_PATH", "python_agents/.online_learning_db_offset.json")

    # Phase 5
    METRICS_EXPORTER_ENABLED = os.getenv("QUENBOT_METRICS_ENABLED", "0").lower() in {"1", "true", "yes", "on"}
    METRICS_EXPORTER_PORT = int(os.getenv("QUENBOT_METRICS_PORT", "9108"))

    # Phase 5 Finalization — Safety Net (accuracy + drift + feature-store guards)
    # Hepsi default OFF, running path'i etkilemez. Ramp plan icin bkz.
    # FINALIZATION_REPORT.md. SAFETY_NET_ENABLED=1 ilk aktiflestirilmesi
    # gereken flag; DECISION_ROUTER_ENABLED'dan onceki guvenlik katmani.
    SAFETY_NET_ENABLED = os.getenv("QUENBOT_SAFETY_NET_ENABLED", "0").lower() in {"1", "true", "yes", "on"}
    SAFETY_NET_BRIER_TOL = float(os.getenv("QUENBOT_SAFETY_NET_BRIER_TOL", "1.25"))
    SAFETY_NET_HITRATE_TOL = float(os.getenv("QUENBOT_SAFETY_NET_HITRATE_TOL", "0.80"))
    SAFETY_NET_DEGRADATION_WINDOW_MIN = int(os.getenv("QUENBOT_SAFETY_NET_DEGRADATION_WINDOW_MIN", "120"))
    SAFETY_NET_CONFLUENCE_DRIFT_SIGMA = float(os.getenv("QUENBOT_SAFETY_NET_CONFLUENCE_DRIFT_SIGMA", "3.0"))
    SAFETY_NET_FS_FAILURE_TOL = float(os.getenv("QUENBOT_SAFETY_NET_FS_FAILURE_TOL", "0.05"))
    SAFETY_NET_BASELINE_PATH = os.getenv("QUENBOT_SAFETY_NET_BASELINE_PATH", "python_agents/.safety_net_baseline.json")
    SAFETY_NET_TRIP_SENTINEL = os.getenv("QUENBOT_SAFETY_NET_TRIP_SENTINEL", "python_agents/.safety_net_trip.json")
    SAFETY_NET_BG_INTERVAL_SEC = int(os.getenv("QUENBOT_SAFETY_NET_BG_INTERVAL_SEC", "30"))

    # ─────────────────────────────────────────────────────────────
    # Phase 6 — Oracle Stack (8 dedektör + füzyon + brain + supervisor)
    # Tüm flag'ler default OFF. Ramp planı: ORACLE_OPERATIONS_MANUAL.md.
    # Davranışsal hiçbir yol bu PR ile değişmez; eklemeler salt additive.
    # ─────────────────────────────────────────────────────────────
    # §9 Oracle Signal Bus (read-only registry; default ON, hiçbir yan etki)
    ORACLE_BUS_ENABLED = os.getenv("QUENBOT_ORACLE_BUS_ENABLED", "1").lower() in {"1", "true", "yes", "on"}

    # §1 BOCPD — Bayesian Online Changepoint Detection
    BOCPD_ENABLED = os.getenv("QUENBOT_BOCPD_ENABLED", "0").lower() in {"1", "true", "yes", "on"}
    BOCPD_HAZARD_LAMBDA_SEC = float(os.getenv("QUENBOT_BOCPD_HAZARD_LAMBDA_SEC", "1800"))
    BOCPD_MIN_STREAMS = int(os.getenv("QUENBOT_BOCPD_MIN_STREAMS", "4"))
    BOCPD_CONSENSUS_WINDOW_SEC = int(os.getenv("QUENBOT_BOCPD_CONSENSUS_WINDOW_SEC", "60"))
    BOCPD_CP_THRESHOLD = float(os.getenv("QUENBOT_BOCPD_CP_THRESHOLD", "0.9"))
    BOCPD_RUN_LENGTH_TRUNCATION = int(os.getenv("QUENBOT_BOCPD_RUN_LENGTH_TRUNCATION", "300"))
    BOCPD_PUBLISH_HZ = float(os.getenv("QUENBOT_BOCPD_PUBLISH_HZ", "1.0"))

    # §2 Hawkes — Self-exciting order flow
    HAWKES_ENABLED = os.getenv("QUENBOT_HAWKES_ENABLED", "0").lower() in {"1", "true", "yes", "on"}
    HAWKES_WINDOW_MIN = int(os.getenv("QUENBOT_HAWKES_WINDOW_MIN", "30"))
    HAWKES_EM_ITER = int(os.getenv("QUENBOT_HAWKES_EM_ITER", "50"))
    HAWKES_MIN_EVENTS = int(os.getenv("QUENBOT_HAWKES_MIN_EVENTS", "500"))
    HAWKES_PUBLISH_HZ = float(os.getenv("QUENBOT_HAWKES_PUBLISH_HZ", "0.5"))

    # §3 LOB Thermodynamics — Shannon entropy + production rate
    LOB_THERMO_ENABLED = os.getenv("QUENBOT_LOB_THERMO_ENABLED", "0").lower() in {"1", "true", "yes", "on"}
    LOB_THERMO_COOLING_WINDOW_SEC = int(os.getenv("QUENBOT_LOB_THERMO_COOLING_WINDOW_SEC", "180"))
    LOB_THERMO_COOLING_THRESHOLD = float(os.getenv("QUENBOT_LOB_THERMO_COOLING_THRESHOLD", "1e-4"))
    LOB_THERMO_LEVELS = int(os.getenv("QUENBOT_LOB_THERMO_LEVELS", "20"))
    LOB_THERMO_PUBLISH_HZ = float(os.getenv("QUENBOT_LOB_THERMO_PUBLISH_HZ", "0.5"))

    # §4 Wasserstein — Distributional drift
    WASSERSTEIN_ENABLED = os.getenv("QUENBOT_WASSERSTEIN_ENABLED", "0").lower() in {"1", "true", "yes", "on"}
    WASSERSTEIN_BASELINE_HOURS = int(os.getenv("QUENBOT_WASSERSTEIN_BASELINE_HOURS", "24"))
    WASSERSTEIN_WINDOW_MIN = int(os.getenv("QUENBOT_WASSERSTEIN_WINDOW_MIN", "60"))
    WASSERSTEIN_PUBLISH_HZ = float(os.getenv("QUENBOT_WASSERSTEIN_PUBLISH_HZ", "0.2"))

    # §5 Path Signature — Lyons rough paths
    PATH_SIGNATURE_ENABLED = os.getenv("QUENBOT_PATH_SIGNATURE_ENABLED", "0").lower() in {"1", "true", "yes", "on"}
    PATH_SIG_WINDOW_SEC = int(os.getenv("QUENBOT_PATH_SIG_WINDOW_SEC", "30"))
    PATH_SIG_DEPTH = int(os.getenv("QUENBOT_PATH_SIG_DEPTH", "3"))
    PATH_SIG_MIN_SIMILARITY = float(os.getenv("QUENBOT_PATH_SIG_MIN_SIMILARITY", "0.85"))
    PATH_SIG_CHROMA_COLLECTION = os.getenv("QUENBOT_PATH_SIG_CHROMA_COLLECTION", "whale_execution_signatures")
    PATH_SIG_PUBLISH_HZ = float(os.getenv("QUENBOT_PATH_SIG_PUBLISH_HZ", "0.5"))

    # §6 Mirror Flow — Cross-exchange synchronized execution
    MIRROR_FLOW_ENABLED = os.getenv("QUENBOT_MIRROR_FLOW_ENABLED", "0").lower() in {"1", "true", "yes", "on"}
    MIRROR_DTW_WINDOW_MIN = int(os.getenv("QUENBOT_MIRROR_DTW_WINDOW_MIN", "30"))
    MIRROR_DTW_RADIUS = int(os.getenv("QUENBOT_MIRROR_DTW_RADIUS", "10"))
    MIRROR_SIG_PVALUE = float(os.getenv("QUENBOT_MIRROR_SIG_PVALUE", "0.01"))
    MIRROR_PUBLISH_HZ = float(os.getenv("QUENBOT_MIRROR_PUBLISH_HZ", "0.1"))

    # §7 TDA — Topological data analysis (persistent homology)
    TDA_ENABLED = os.getenv("QUENBOT_TDA_ENABLED", "0").lower() in {"1", "true", "yes", "on"}
    TDA_WINDOW_MIN = int(os.getenv("QUENBOT_TDA_WINDOW_MIN", "5"))
    TDA_PERSISTENCE_THRESHOLD = float(os.getenv("QUENBOT_TDA_PERSISTENCE_THRESHOLD", "0.15"))
    TDA_UPDATE_HZ = float(os.getenv("QUENBOT_TDA_UPDATE_HZ", "0.1"))

    # §8 Onchain — Convergent Cross Mapping (Sugihara 2012)
    ONCHAIN_ENABLED = os.getenv("QUENBOT_ONCHAIN_ENABLED", "0").lower() in {"1", "true", "yes", "on"}
    ONCHAIN_POLL_SEC = int(os.getenv("QUENBOT_ONCHAIN_POLL_SEC", "300"))
    CCM_LIBRARY_SIZES = os.getenv("QUENBOT_CCM_LIBRARY_SIZES", "100,500,2000")
    CCM_SATURATION_THRESHOLD = float(os.getenv("QUENBOT_CCM_SATURATION_THRESHOLD", "0.6"))
    ETHERSCAN_API_KEY = os.getenv("ETHERSCAN_API_KEY", "")
    BSCSCAN_API_KEY = os.getenv("BSCSCAN_API_KEY", "")

    # §10 Factor Graph Fusion (loopy belief propagation → IFI)
    FACTOR_GRAPH_ENABLED = os.getenv("QUENBOT_FACTOR_GRAPH_ENABLED", "0").lower() in {"1", "true", "yes", "on"}
    FG_BP_ITER = int(os.getenv("QUENBOT_FG_BP_ITER", "100"))
    FG_DAMPING = float(os.getenv("QUENBOT_FG_DAMPING", "0.5"))
    FG_PUBLISH_HZ = float(os.getenv("QUENBOT_FG_PUBLISH_HZ", "0.5"))

    # §11 Qwen Oracle Brain — central orchestration brain
    ORACLE_BRAIN_ENABLED = os.getenv("QUENBOT_ORACLE_BRAIN_ENABLED", "0").lower() in {"1", "true", "yes", "on"}
    ORACLE_BRAIN_SHADOW = os.getenv("QUENBOT_ORACLE_BRAIN_SHADOW", "1").lower() in {"1", "true", "yes", "on"}
    ORACLE_BRAIN_LEARN_INTERVAL_MIN = int(os.getenv("QUENBOT_ORACLE_BRAIN_LEARN_INTERVAL_MIN", "10"))
    ORACLE_BRAIN_TEACH_INTERVAL_MIN = int(os.getenv("QUENBOT_ORACLE_BRAIN_TEACH_INTERVAL_MIN", "60"))
    ORACLE_BRAIN_DAILY_REPORT_HOUR = int(os.getenv("QUENBOT_ORACLE_BRAIN_DAILY_REPORT_HOUR", "3"))
    ORACLE_BRAIN_DIRECTIVES_PATH = os.getenv("QUENBOT_ORACLE_BRAIN_DIRECTIVES_PATH", "python_agents/directives.json")
    ORACLE_BRAIN_REASONING_CHROMA_COLLECTION = os.getenv("QUENBOT_ORACLE_BRAIN_REASONING_CHROMA_COLLECTION", "oracle_reasoning")
    ORACLE_BRAIN_RAG_TOP_K = int(os.getenv("QUENBOT_ORACLE_BRAIN_RAG_TOP_K", "5"))
    ORACLE_BRAIN_TRUST_SCORE_PATH = os.getenv("QUENBOT_ORACLE_BRAIN_TRUST_SCORE_PATH", "python_agents/.channel_trust_scores.json")
    ORACLE_BRAIN_MAX_PROMPT_TOKENS = int(os.getenv("QUENBOT_ORACLE_BRAIN_MAX_PROMPT_TOKENS", "8192"))
    SELF_PLAY_ENABLED = os.getenv("QUENBOT_SELF_PLAY_ENABLED", "0").lower() in {"1", "true", "yes", "on"}

    # §12 Runtime Supervisor & watchdog
    RUNTIME_SUPERVISOR_ENABLED = os.getenv("QUENBOT_RUNTIME_SUPERVISOR_ENABLED", "0").lower() in {"1", "true", "yes", "on"}
    RUNTIME_HEALTH_CHECK_INTERVAL_SEC = int(os.getenv("QUENBOT_RUNTIME_HEALTH_CHECK_INTERVAL_SEC", "30"))
    RUNTIME_MAX_RESTART_ATTEMPTS = int(os.getenv("QUENBOT_RUNTIME_MAX_RESTART_ATTEMPTS", "3"))
    RUNTIME_STATUS_PATH = os.getenv("QUENBOT_RUNTIME_STATUS_PATH", "python_agents/.runtime_status.json")
    WATCHDOG_ENABLED = os.getenv("QUENBOT_WATCHDOG_ENABLED", "0").lower() in {"1", "true", "yes", "on"}
    WATCHDOG_HEARTBEAT_PATH = os.getenv("QUENBOT_WATCHDOG_HEARTBEAT_PATH", "/tmp/quenbot_heartbeat")
    WATCHDOG_TIMEOUT_SEC = int(os.getenv("QUENBOT_WATCHDOG_TIMEOUT_SEC", "120"))

    # ─────────────────────────────────────────────────────────────
    # Aşama 1 — Directive Gatekeeper + Auto-Rollback + Historical Warmup
    # Hepsi additive; disabled state byte-identical to pre-Aşama-1.
    # ─────────────────────────────────────────────────────────────
    DIRECTIVE_GATEKEEPER_ENABLED = os.getenv("QUENBOT_DIRECTIVE_GATEKEEPER_ENABLED", "1").lower() in {"1", "true", "yes", "on"}
    ORACLE_BRAIN_DIRECTIVE_CONFIDENCE_MIN = float(os.getenv("QUENBOT_ORACLE_BRAIN_DIRECTIVE_CONFIDENCE_MIN", "0.80"))
    ORACLE_BRAIN_MAX_DIRECTIVES_PER_HOUR = int(os.getenv("QUENBOT_ORACLE_BRAIN_MAX_DIRECTIVES_PER_HOUR", "3"))
    ORACLE_BRAIN_DIRECTIVE_ALLOWLIST = [
        s.strip() for s in os.getenv(
            "QUENBOT_ORACLE_BRAIN_DIRECTIVE_ALLOWLIST",
            "ADJUST_CONFIDENCE_THRESHOLD,ADJUST_POSITION_SIZE_MULT,PAUSE_SYMBOL",
        ).split(",") if s.strip()
    ]
    # Permanently blocked regardless of allowlist — cannot be overridden.
    ORACLE_BRAIN_DIRECTIVE_BLOCKLIST_HARD = ["CHANGE_STRATEGY", "OVERRIDE_VETO", "FORCE_TRADE"]
    DIRECTIVE_REJECTED_LOG_PATH = os.getenv("QUENBOT_DIRECTIVE_REJECTED_LOG", "python_agents/.directive_rejected.jsonl")

    AUTO_ROLLBACK_ENABLED = os.getenv("QUENBOT_AUTO_ROLLBACK_ENABLED", "1").lower() in {"1", "true", "yes", "on"}
    AUTO_ROLLBACK_REJECTION_RATE_THRESHOLD = float(os.getenv("QUENBOT_AUTO_ROLLBACK_REJECTION_RATE", "0.60"))
    AUTO_ROLLBACK_REJECTION_WINDOW_MIN = int(os.getenv("QUENBOT_AUTO_ROLLBACK_REJECTION_WINDOW_MIN", "30"))
    AUTO_ROLLBACK_ACCURACY_THRESHOLD = float(os.getenv("QUENBOT_AUTO_ROLLBACK_ACCURACY_MIN", "0.45"))
    AUTO_ROLLBACK_ACCURACY_WINDOW = int(os.getenv("QUENBOT_AUTO_ROLLBACK_ACCURACY_WINDOW", "50"))
    AUTO_ROLLBACK_META_CONF_MIN = float(os.getenv("QUENBOT_AUTO_ROLLBACK_META_CONF_MIN", "0.40"))
    AUTO_ROLLBACK_META_CONF_STREAK = int(os.getenv("QUENBOT_AUTO_ROLLBACK_META_CONF_STREAK", "10"))
    AUTO_ROLLBACK_UNHEALTHY_GRACE_SEC = int(os.getenv("QUENBOT_AUTO_ROLLBACK_UNHEALTHY_GRACE_SEC", "300"))
    AUTO_ROLLBACK_FORCE_SENTINEL = os.getenv("QUENBOT_AUTO_ROLLBACK_FORCE_SENTINEL", "/tmp/quenbot_force_shadow")
    AUTO_ROLLBACK_SHADOW_FORCED_PATH = os.getenv("QUENBOT_AUTO_ROLLBACK_SHADOW_FORCED_PATH", "python_agents/.oracle_shadow_forced.json")
    AUTO_ROLLBACK_FORENSIC_DIR = os.getenv("QUENBOT_AUTO_ROLLBACK_FORENSIC_DIR", "python_agents/.auto_rollback")
    AUTO_ROLLBACK_CHECK_INTERVAL_SEC = int(os.getenv("QUENBOT_AUTO_ROLLBACK_CHECK_INTERVAL_SEC", "15"))

    # Historical warmup
    WARMUP_TRUST_SCORES_PATH = os.getenv("QUENBOT_WARMUP_TRUST_PATH", "python_agents/.channel_trust_scores.json")
    WARMUP_RAG_SOURCE_TAG = "historical_warmup"
    WARMUP_CHECKPOINT_PATH = os.getenv("QUENBOT_WARMUP_CHECKPOINT_PATH", "python_agents/.warmup_checkpoint.json")
    WARMUP_REPORT_DIR = os.getenv("QUENBOT_WARMUP_REPORT_DIR", "python_agents/.warmup_reports")
