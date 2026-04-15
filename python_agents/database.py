import asyncpg
import json
import logging
import numpy as np
from datetime import datetime, timedelta, date, timezone
from decimal import Decimal
from typing import List, Dict, Any, Optional
from config import Config

logger = logging.getLogger(__name__)

TARGET_CARD_MIN_CONFIDENCE = float(Config.get_env('QUENBOT_TARGET_CARD_MIN_CONF', 0.62)) if hasattr(Config, 'get_env') else float(__import__('os').getenv('QUENBOT_TARGET_CARD_MIN_CONF', '0.62'))
TARGET_CARD_MIN_QUALITY = float(Config.get_env('QUENBOT_TARGET_CARD_MIN_QUALITY', 0.68)) if hasattr(Config, 'get_env') else float(__import__('os').getenv('QUENBOT_TARGET_CARD_MIN_QUALITY', '0.68'))
MAMIS_TARGET_CARD_MIN_CONFIDENCE = float(Config.get_env('QUENBOT_MAMIS_TARGET_CARD_MIN_CONF', 0.72)) if hasattr(Config, 'get_env') else float(__import__('os').getenv('QUENBOT_MAMIS_TARGET_CARD_MIN_CONF', '0.72'))
MAMIS_TARGET_CARD_MIN_VOLATILITY = float(Config.get_env('QUENBOT_MAMIS_TARGET_CARD_MIN_VOLATILITY', 0.0035)) if hasattr(Config, 'get_env') else float(__import__('os').getenv('QUENBOT_MAMIS_TARGET_CARD_MIN_VOLATILITY', '0.0035'))


def _json_serial(obj):
    if isinstance(obj, (datetime, date)):
        return obj.isoformat()
    if isinstance(obj, timedelta):
        return obj.total_seconds()
    if isinstance(obj, Decimal):
        return float(obj)
    if isinstance(obj, (np.integer,)):
        return int(obj)
    if isinstance(obj, (np.floating,)):
        return float(obj)
    if isinstance(obj, np.ndarray):
        return obj.tolist()
    if isinstance(obj, set):
        return list(obj)
    return str(obj)  # fallback: convert to string instead of crashing


def _dumps(obj):
    return json.dumps(obj, default=_json_serial)


def _utc_isoformat(value: Any) -> str:
    if isinstance(value, datetime):
        aware = value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)
        return aware.isoformat()
    return str(value)


def _utc_naive(value: Any) -> Any:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value
        return value.astimezone(timezone.utc).replace(tzinfo=None)
    return value


def _infer_signal_source(signal_type: str, metadata: Dict[str, Any]) -> str:
    explicit = str(metadata.get('source') or metadata.get('signal_provider') or '').strip().lower()
    if explicit:
        return explicit

    lowered = str(signal_type or '').lower()
    if lowered.startswith('mamis_'):
        return 'mamis'
    if 'pattern' in lowered or 'signature' in lowered:
        return 'pattern_matcher'
    return 'strategist'


def _infer_signal_model(source: str, metadata: Dict[str, Any]) -> str:
    explicit = str(metadata.get('source_model') or '').strip()
    if explicit:
        return explicit
    if metadata.get('mamis_ensemble'):
        return 'Strategist + MAMIS Ensemble'
    if source == 'mamis':
        return 'MAMIS Microstructure'
    if source == 'pattern_matcher':
        return 'PatternMatcher + Qwen Decision Core'
    return 'Strategist Engine'


def _normalize_signal_target_pct(value: Any) -> float:
    numeric = abs(float(value or 0.0))
    if numeric > 0.5:
        numeric /= 100.0
    return numeric


def _signal_quality_score(confidence: float, target_pct: float) -> float:
    c = min(max(float(confidence or 0.0), 0.0), 1.0)
    tp = _normalize_signal_target_pct(target_pct)
    ideal = 0.025
    target_component = 1.0 - min(abs(tp - ideal) / 0.03, 1.0)
    return min(max(c * 0.8 + target_component * 0.2, 0.0), 1.0)


def _is_target_card_candidate(signal: Dict[str, Any]) -> bool:
    metadata = signal.get('metadata', {}) or {}
    if isinstance(metadata, str):
        try:
            metadata = json.loads(metadata)
        except (json.JSONDecodeError, TypeError):
            metadata = {}

    status = str(signal.get('status', 'pending') or 'pending').lower()
    if status not in {'pending', 'active', 'open'}:
        return False

    confidence = float(signal.get('confidence', 0.0) or 0.0)
    source = str(metadata.get('source') or metadata.get('signal_provider') or _infer_signal_source(signal.get('signal_type', ''), metadata)).lower()
    target_pct = _normalize_signal_target_pct(
        metadata.get('target_pct', metadata.get('predicted_magnitude', 0.0))
    )
    quality = float(metadata.get('quality_score', _signal_quality_score(confidence, target_pct)) or 0.0)
    explicit_candidate = str(metadata.get('dashboard_candidate', '')).lower() == 'true' or bool(metadata.get('dashboard_candidate') is True)

    if target_pct < 0.02:
        return False

    if source not in {'strategist', 'pattern_matcher'}:
        return False

    return explicit_candidate or (
        confidence >= TARGET_CARD_MIN_CONFIDENCE
        and quality >= TARGET_CARD_MIN_QUALITY
    )


def _coerce_optional_int(value: Any) -> Optional[int]:
    if value is None:
        return None
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            return None
        if stripped.startswith('sig:'):
            stripped = stripped.split(':', 1)[1]
        try:
            return int(stripped)
        except ValueError:
            return None
    return None


class Database:
    def __init__(self):
        self.pool: Optional[asyncpg.Pool] = None

    async def _get_symbol_learning_profile_conn(
        self,
        conn: asyncpg.Connection,
        symbol: str,
        lookback_days: int = 21,
    ) -> Dict[str, Any]:
        normalized_symbol = str(symbol or '').upper().strip()
        if not normalized_symbol:
            return {
                'symbol': '',
                'total': 0,
                'correct': 0,
                'accuracy': 0.0,
                'avg_pnl': 0.0,
                'score': 0.0,
                'status': 'cold',
                'last_learning_at': None,
                'recent_reasons': [],
            }

        cutoff = datetime.utcnow() - timedelta(days=max(1, int(lookback_days)))
        aggregate = await conn.fetchrow(
            """
            SELECT
                COUNT(*)::int AS total,
                COUNT(*) FILTER (WHERE was_correct = TRUE)::int AS correct,
                COALESCE(AVG(pnl_pct), 0)::double precision AS avg_pnl,
                MAX(created_at) AS last_learning_at
            FROM brain_learning_log
            WHERE UPPER(COALESCE(context->>'symbol', '')) = $1
              AND created_at >= $2
            """,
            normalized_symbol,
            cutoff,
        )
        total = int((aggregate or {}).get('total') or 0)
        correct = int((aggregate or {}).get('correct') or 0)
        avg_pnl = float((aggregate or {}).get('avg_pnl') or 0.0)
        accuracy = (correct / total) if total else 0.0

        recent_rows = await conn.fetch(
            """
            SELECT context, created_at
            FROM brain_learning_log
            WHERE UPPER(COALESCE(context->>'symbol', '')) = $1
              AND created_at >= $2
            ORDER BY created_at DESC
            LIMIT 5
            """,
            normalized_symbol,
            cutoff,
        )
        recent_reasons: List[str] = []
        for row in recent_rows:
            context = row.get('context')
            if isinstance(context, str):
                try:
                    context = json.loads(context)
                except (json.JSONDecodeError, TypeError):
                    context = {}
            context = context if isinstance(context, dict) else {}
            reason = str(
                context.get('loss_explanation')
                or context.get('close_reason')
                or context.get('reasoning')
                or ''
            ).strip()
            if reason and reason not in recent_reasons:
                recent_reasons.append(reason[:180])

        sample_component = min(total / 6.0, 1.0) * 0.15
        pnl_component = max(min(avg_pnl / 6.0, 0.20), -0.20)
        score = min(max(accuracy * 0.65 + sample_component + pnl_component, 0.0), 1.0)
        if total >= 3 and accuracy >= 0.55 and avg_pnl > 0:
            status = 'promote'
        elif total >= 2 and score >= 0.45:
            status = 'monitor'
        else:
            status = 'cold'

        last_learning_at = (aggregate or {}).get('last_learning_at')
        return {
            'symbol': normalized_symbol,
            'total': total,
            'correct': correct,
            'accuracy': round(accuracy, 4),
            'avg_pnl': round(avg_pnl, 4),
            'score': round(score, 4),
            'status': status,
            'last_learning_at': last_learning_at.isoformat() if isinstance(last_learning_at, datetime) else None,
            'recent_reasons': recent_reasons,
        }

    async def connect(self):
        """Initialize database connection pool"""
        try:
            self.pool = await asyncpg.create_pool(
                Config.DATABASE_URL,
                min_size=8,
                max_size=40,
                command_timeout=60
            )
            await self.create_tables()
            logger.info("Database connected successfully")
        except Exception as e:
            logger.error(f"Database connection failed: {e}")
            raise

    async def disconnect(self):
        """Close database connection pool"""
        if self.pool:
            await self.pool.close()
            logger.info("Database disconnected")

    async def create_tables(self):
        """Create all necessary tables"""
        async with self.pool.acquire() as conn:
            # Trades table
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS trades (
                    id SERIAL PRIMARY KEY,
                    exchange VARCHAR(50) NOT NULL,
                    market_type VARCHAR(20) NOT NULL DEFAULT 'spot',
                    symbol VARCHAR(20) NOT NULL,
                    price DECIMAL(20, 8) NOT NULL,
                    quantity DECIMAL(20, 8) NOT NULL,
                    timestamp TIMESTAMP NOT NULL,
                    side VARCHAR(10) NOT NULL,
                    trade_id VARCHAR(100) UNIQUE,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            # Price movements table
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS price_movements (
                    id SERIAL PRIMARY KEY,
                    exchange VARCHAR(50) NOT NULL,
                    market_type VARCHAR(20) NOT NULL DEFAULT 'spot',
                    symbol VARCHAR(20) NOT NULL,
                    start_price DECIMAL(20, 8) NOT NULL,
                    end_price DECIMAL(20, 8) NOT NULL,
                    change_pct DECIMAL(10, 4) NOT NULL,
                    volume DECIMAL(20, 8),
                    buy_volume DECIMAL(20, 8),
                    sell_volume DECIMAL(20, 8),
                    direction VARCHAR(10),
                    aggressiveness DECIMAL(10, 4),
                    start_time TIMESTAMP NOT NULL,
                    end_time TIMESTAMP NOT NULL,
                    t10_data JSONB,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            # Signals table
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS signals (
                    id SERIAL PRIMARY KEY,
                    market_type VARCHAR(20) NOT NULL DEFAULT 'spot',
                    symbol VARCHAR(20) NOT NULL,
                    signal_type VARCHAR(50) NOT NULL,
                    confidence DECIMAL(5, 4) NOT NULL,
                    price DECIMAL(20, 8) NOT NULL,
                    timestamp TIMESTAMP NOT NULL,
                    status VARCHAR(20) DEFAULT 'pending',
                    metadata JSONB,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            # Simulations table
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS simulations (
                    id SERIAL PRIMARY KEY,
                    signal_id INTEGER REFERENCES signals(id),
                    market_type VARCHAR(20) NOT NULL DEFAULT 'spot',
                    symbol VARCHAR(20) NOT NULL,
                    entry_price DECIMAL(20, 8) NOT NULL,
                    exit_price DECIMAL(20, 8),
                    quantity DECIMAL(20, 8) NOT NULL,
                    side VARCHAR(10) NOT NULL,
                    status VARCHAR(20) DEFAULT 'open',
                    pnl DECIMAL(20, 8),
                    pnl_pct DECIMAL(10, 4),
                    entry_time TIMESTAMP NOT NULL,
                    exit_time TIMESTAMP,
                    stop_loss DECIMAL(20, 8),
                    take_profit DECIMAL(20, 8),
                    metadata JSONB,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            # Watchlist table
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS watchlist (
                    id SERIAL PRIMARY KEY,
                    symbol VARCHAR(20) NOT NULL,
                    market_type VARCHAR(20) NOT NULL DEFAULT 'spot',
                    description TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            # Blacklist patterns table
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS blacklist_patterns (
                    id SERIAL PRIMARY KEY,
                    pattern_type VARCHAR(50) NOT NULL,
                    pattern_data JSONB NOT NULL,
                    confidence DECIMAL(5, 4) NOT NULL,
                    reason TEXT,
                    created_by VARCHAR(50),
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            # Audit reports table
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS audit_reports (
                    id SERIAL PRIMARY KEY,
                    signal_id INTEGER REFERENCES signals(id),
                    simulation_id INTEGER REFERENCES simulations(id),
                    analysis JSONB NOT NULL,
                    lessons_learned TEXT,
                    recommendations JSONB,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            # Agent config table
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS agent_config (
                    id SERIAL PRIMARY KEY,
                    agent_name VARCHAR(50) NOT NULL,
                    config_key VARCHAR(100) NOT NULL,
                    config_value JSONB NOT NULL,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(agent_name, config_key)
                )
            """)

            # Audit records table (extended)
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS audit_records (
                    id SERIAL PRIMARY KEY,
                    timestamp TIMESTAMP NOT NULL,
                    total_simulations INTEGER,
                    successful_simulations INTEGER,
                    failed_simulations INTEGER,
                    success_rate DECIMAL(5, 4),
                    avg_win_pct DECIMAL(10, 4),
                    avg_loss_pct DECIMAL(10, 4),
                    metadata JSONB,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            # Failure analysis table
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS failure_analysis (
                    id SERIAL PRIMARY KEY,
                    timestamp TIMESTAMP NOT NULL,
                    signal_type VARCHAR(50),
                    failure_count INTEGER,
                    avg_loss_pct DECIMAL(10, 4),
                    recommendation TEXT,
                    metadata JSONB,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            # Pattern records table (Brain memory)
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS pattern_records (
                    id SERIAL PRIMARY KEY,
                    symbol VARCHAR(20) NOT NULL,
                    exchange VARCHAR(50),
                    market_type VARCHAR(20) DEFAULT 'spot',
                    snapshot_data JSONB NOT NULL,
                    outcome_15m DECIMAL(10, 6),
                    outcome_1h DECIMAL(10, 6),
                    outcome_4h DECIMAL(10, 6),
                    outcome_1d DECIMAL(10, 6),
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            # Chat messages table
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS chat_messages (
                    id SERIAL PRIMARY KEY,
                    role VARCHAR(20) NOT NULL,
                    message TEXT NOT NULL,
                    agent_name VARCHAR(50),
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            # User watchlist table (enhanced)
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS user_watchlist (
                    id SERIAL PRIMARY KEY,
                    symbol VARCHAR(20) NOT NULL,
                    exchange VARCHAR(50) NOT NULL DEFAULT 'all',
                    market_type VARCHAR(20) NOT NULL DEFAULT 'spot',
                    active BOOLEAN DEFAULT TRUE,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(symbol, exchange, market_type)
                )
            """)

            # Brain learning log
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS brain_learning_log (
                    id SERIAL PRIMARY KEY,
                    signal_type VARCHAR(50),
                    was_correct BOOLEAN,
                    pnl_pct DECIMAL(10, 4),
                    context JSONB,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            # Bot state table (StateTracker persistence)
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS bot_state (
                    id SERIAL PRIMARY KEY,
                    state_key VARCHAR(100) NOT NULL UNIQUE,
                    state_value JSONB NOT NULL,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            # State history table (time series)
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS state_history (
                    id SERIAL PRIMARY KEY,
                    timestamp TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    mode VARCHAR(20) NOT NULL DEFAULT 'BOOTSTRAP',
                    cumulative_pnl DECIMAL(20, 8) DEFAULT 0,
                    daily_pnl DECIMAL(20, 8) DEFAULT 0,
                    daily_trade_count INTEGER DEFAULT 0,
                    current_drawdown DECIMAL(10, 4) DEFAULT 0,
                    win_rate DECIMAL(5, 4) DEFAULT 0,
                    active_positions INTEGER DEFAULT 0,
                    total_trades INTEGER DEFAULT 0,
                    metadata JSONB
                )
            """)

            # RCA results table
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS rca_results (
                    id SERIAL PRIMARY KEY,
                    simulation_id INTEGER REFERENCES simulations(id),
                    failure_type VARCHAR(50) NOT NULL,
                    confidence DECIMAL(5, 4) DEFAULT 0,
                    explanation TEXT,
                    recommendations JSONB,
                    context JSONB,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            # Agent heartbeat table
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS agent_heartbeat (
                    id SERIAL PRIMARY KEY,
                    agent_name VARCHAR(50) NOT NULL UNIQUE,
                    status VARCHAR(20) NOT NULL DEFAULT 'running',
                    last_heartbeat TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    metadata JSONB,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            # Correction notes table (RCA → Strategist feedback loop)
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS correction_notes (
                    id SERIAL PRIMARY KEY,
                    signal_type VARCHAR(50) NOT NULL,
                    failure_type VARCHAR(50) NOT NULL,
                    adjustment_key VARCHAR(50) NOT NULL,
                    adjustment_value DECIMAL(10, 6) NOT NULL,
                    reason TEXT,
                    applied BOOLEAN DEFAULT FALSE,
                    simulation_id INTEGER,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            # Historical signatures table (pre-move patterns for similarity matching)
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS historical_signatures (
                    id SERIAL PRIMARY KEY,
                    symbol VARCHAR(20) NOT NULL,
                    market_type VARCHAR(20) NOT NULL DEFAULT 'spot',
                    timeframe VARCHAR(10) NOT NULL,
                    direction VARCHAR(10) NOT NULL,
                    change_pct DECIMAL(10, 6) NOT NULL,
                    pre_move_vector JSONB NOT NULL,
                    pre_move_indicators JSONB,
                    volume_profile JSONB,
                    movement_id INTEGER,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            # Pattern match results table
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS pattern_match_results (
                    id SERIAL PRIMARY KEY,
                    symbol VARCHAR(20) NOT NULL,
                    timeframe VARCHAR(10) NOT NULL,
                    matched_signature_id INTEGER,
                    similarity DECIMAL(6, 4) NOT NULL,
                    euclidean_distance DECIMAL(12, 6) NOT NULL,
                    matched_direction VARCHAR(10),
                    matched_change_pct DECIMAL(10, 6),
                    predicted_direction VARCHAR(10),
                    predicted_magnitude DECIMAL(10, 6),
                    current_vector JSONB,
                    confidence DECIMAL(5, 4) DEFAULT 0,
                    brain_decision VARCHAR(20),
                    brain_reasoning TEXT,
                    outcome_pct DECIMAL(10, 6),
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            # Create indexes
            await conn.execute("CREATE INDEX IF NOT EXISTS idx_trades_symbol_timestamp ON trades(symbol, timestamp)")
            await conn.execute("CREATE INDEX IF NOT EXISTS idx_trades_timestamp_exchange_market ON trades(timestamp DESC, exchange, market_type)")
            await conn.execute("CREATE INDEX IF NOT EXISTS idx_price_movements_symbol_time ON price_movements(symbol, start_time)")
            await conn.execute("CREATE INDEX IF NOT EXISTS idx_signals_status_timestamp ON signals(status, timestamp)")
            await conn.execute("CREATE INDEX IF NOT EXISTS idx_simulations_status_time ON simulations(status, entry_time)")
            await conn.execute("CREATE INDEX IF NOT EXISTS idx_pattern_records_symbol ON pattern_records(symbol, created_at)")
            await conn.execute("CREATE INDEX IF NOT EXISTS idx_chat_messages_time ON chat_messages(created_at)")
            await conn.execute("CREATE INDEX IF NOT EXISTS idx_user_watchlist_active ON user_watchlist(active)")
            await conn.execute("CREATE INDEX IF NOT EXISTS idx_correction_notes_type ON correction_notes(signal_type, applied)")
            await conn.execute("CREATE INDEX IF NOT EXISTS idx_historical_signatures_symbol ON historical_signatures(symbol, timeframe)")
            await conn.execute("CREATE INDEX IF NOT EXISTS idx_pattern_match_results_symbol ON pattern_match_results(symbol, created_at)")

            # Signature matches table (Neuro-Symbolic Engine results)
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS signature_matches (
                    id SERIAL PRIMARY KEY,
                    symbol VARCHAR(20) NOT NULL,
                    timeframe VARCHAR(10) NOT NULL,
                    direction VARCHAR(10) NOT NULL DEFAULT 'neutral',
                    similarity DECIMAL(6, 4) NOT NULL,
                    dtw_score DECIMAL(6, 4) DEFAULT 0,
                    fft_score DECIMAL(6, 4) DEFAULT 0,
                    cosine_score DECIMAL(6, 4) DEFAULT 0,
                    poly_score DECIMAL(6, 4) DEFAULT 0,
                    matched_signature_id INTEGER,
                    match_label VARCHAR(100),
                    pattern_name VARCHAR(200),
                    historical_timestamp TIMESTAMP,
                    historical_price DECIMAL(20, 8) DEFAULT 0,
                    historical_end_price DECIMAL(20, 8) DEFAULT 0,
                    historical_volume_ratio DECIMAL(8, 4) DEFAULT 0,
                    context_string TEXT,
                    current_price DECIMAL(20, 8) DEFAULT 0,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            await conn.execute("CREATE INDEX IF NOT EXISTS idx_signature_matches_symbol ON signature_matches(symbol, created_at DESC)")
            await conn.execute("CREATE INDEX IF NOT EXISTS idx_signature_matches_similarity ON signature_matches(similarity DESC)")

            # ── Migrations: widen VARCHAR columns that were too narrow ──
            await conn.execute("""
                ALTER TABLE signals ALTER COLUMN signal_type TYPE VARCHAR(50)
            """)

    # Trade operations
    async def insert_trade(self, trade_data: Dict[str, Any]) -> int:
        """Insert a new trade"""
        async with self.pool.acquire() as conn:
            return await conn.fetchval("""
                INSERT INTO trades (exchange, market_type, symbol, price, quantity, timestamp, side, trade_id)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
                ON CONFLICT (trade_id) DO NOTHING
                RETURNING id
            """, trade_data['exchange'], trade_data.get('market_type', 'spot'), trade_data['symbol'],
                trade_data['price'], trade_data['quantity'], trade_data['timestamp'], trade_data['side'], trade_data['trade_id'])

    async def get_recent_trades(self, symbol: str, limit: int = 1000, market_type: str = None) -> List[Dict[str, Any]]:
        """Get recent trades for a symbol and optional market type"""
        async with self.pool.acquire() as conn:
            if market_type:
                rows = await conn.fetch("""
                    SELECT * FROM trades
                    WHERE symbol = $1 AND market_type = $2
                    ORDER BY timestamp DESC
                    LIMIT $3
                """, symbol, market_type, limit)
            else:
                rows = await conn.fetch("""
                    SELECT * FROM trades
                    WHERE symbol = $1
                    ORDER BY timestamp DESC
                    LIMIT $2
                """, symbol, limit)

            return [dict(row) for row in rows]

    # Price movement operations
    async def insert_price_movement(self, movement_data: Dict[str, Any]) -> int:
        """Insert a price movement"""
        safe_change_pct = float(max(min(float(movement_data.get('change_pct', 0) or 0), 999999.9999), -999999.9999))
        async with self.pool.acquire() as conn:
            return await conn.fetchval("""
                INSERT INTO price_movements
                (exchange, market_type, symbol, start_price, end_price, change_pct, volume,
                 buy_volume, sell_volume, direction, aggressiveness, start_time, end_time, t10_data)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14)
                RETURNING id
            """, movement_data['exchange'], movement_data.get('market_type', 'spot'), movement_data['symbol'],
                movement_data['start_price'], movement_data['end_price'], safe_change_pct, movement_data['volume'],
                movement_data.get('buy_volume'), movement_data.get('sell_volume'), movement_data.get('direction'),
                movement_data.get('aggressiveness'), movement_data['start_time'], movement_data['end_time'],
                _dumps(movement_data['t10_data']))

    async def get_recent_movements(self, symbol: str, hours: int = 24, market_type: str = None) -> List[Dict[str, Any]]:
        """Get recent price movements — lightweight, no JSONB (max 100)"""
        async with self.pool.acquire() as conn:
            cutoff_time = datetime.utcnow() - timedelta(hours=hours)
            if market_type:
                rows = await conn.fetch("""
                    SELECT id, symbol, market_type, direction, change_pct,
                           start_price, end_price, volume, start_time, end_time
                    FROM price_movements
                    WHERE symbol = $1 AND market_type = $2 AND start_time >= $3
                    ORDER BY start_time DESC LIMIT 100
                """, symbol, market_type, cutoff_time)
            else:
                rows = await conn.fetch("""
                    SELECT id, symbol, market_type, direction, change_pct,
                           start_price, end_price, volume, start_time, end_time
                    FROM price_movements
                    WHERE symbol = $1 AND start_time >= $2
                    ORDER BY start_time DESC LIMIT 100
                """, symbol, cutoff_time)
            return [dict(row) for row in rows]

    async def get_movement_profiles(self, symbol: str, hours: int = 24, market_type: str = None, limit: int = 50) -> List[List[float]]:
        """Get only price_profile arrays from recent movements (memory-efficient)"""
        async with self.pool.acquire() as conn:
            cutoff_time = datetime.utcnow() - timedelta(hours=hours)
            if market_type:
                rows = await conn.fetch("""
                    SELECT t10_data->'price_profile' as profile
                    FROM price_movements
                    WHERE symbol = $1 AND market_type = $2 AND start_time >= $3
                          AND t10_data IS NOT NULL
                    ORDER BY start_time DESC LIMIT $4
                """, symbol, market_type, cutoff_time, limit)
            else:
                rows = await conn.fetch("""
                    SELECT t10_data->'price_profile' as profile
                    FROM price_movements
                    WHERE symbol = $1 AND start_time >= $2
                          AND t10_data IS NOT NULL
                    ORDER BY start_time DESC LIMIT $3
                """, symbol, cutoff_time, limit)
            results = []
            for row in rows:
                profile = row['profile']
                if profile:
                    if isinstance(profile, str):
                        try:
                            profile = json.loads(profile)
                        except (json.JSONDecodeError, TypeError):
                            continue
                    if isinstance(profile, list) and len(profile) > 2:
                        results.append(profile)
            return results

    # Signal operations
    async def insert_signal(self, signal_data: Dict[str, Any]) -> int:
        """Insert a new signal"""
        metadata = signal_data.get('metadata', {}) or {}
        market_type = signal_data.get('market_type', 'spot')
        symbol = signal_data['symbol']
        entry_price = float(signal_data.get('price', 0) or 0)
        timestamp = _utc_naive(signal_data['timestamp'])

        # Enforce mandatory signal fields and minimum 2% target for all signals.
        direction = str(metadata.get('position_bias') or signal_data.get('direction') or 'long').lower()
        if direction not in ('long', 'short'):
            direction = 'long'
        raw_target_pct = float(metadata.get('target_pct', 0.02) or 0.02)
        # If upstream sends percent units (e.g. 2 for 2%), convert to decimal.
        normalized_target_pct = raw_target_pct / 100.0 if raw_target_pct > 0.5 else raw_target_pct
        target_pct = max(normalized_target_pct, 0.02)
        target_price = entry_price * (1.0 + target_pct) if direction == 'long' else entry_price * (1.0 - target_pct)
        eta_minutes = int(metadata.get('estimated_duration_to_target_minutes', 60) or 60)

        metadata.setdefault('position_bias', direction)
        metadata['target_pct'] = target_pct
        metadata['signal_time'] = str(metadata.get('signal_time') or _utc_isoformat(timestamp))
        metadata['entry_price'] = float(metadata.get('entry_price', entry_price) or entry_price)
        metadata['current_price_at_signal'] = float(metadata.get('current_price_at_signal', entry_price) or entry_price)
        metadata['target_price'] = float(metadata.get('target_price', target_price) or target_price)
        metadata['estimated_duration_to_target_minutes'] = max(1, eta_minutes)
        metadata.setdefault('market_type', market_type)
        metadata.setdefault('exchange', 'mixed')
        signal_source = _infer_signal_source(signal_data.get('signal_type', ''), metadata)
        metadata['source'] = signal_source
        metadata.setdefault('signal_provider', signal_source)
        metadata.setdefault('source_model', _infer_signal_model(signal_source, metadata))
        expires_at = timestamp + timedelta(hours=24) if isinstance(timestamp, datetime) else datetime.utcnow() + timedelta(hours=24)
        metadata['expires_at'] = str(metadata.get('expires_at') or _utc_isoformat(expires_at))

        async with self.pool.acquire() as conn:
            learning_profile = await self._get_symbol_learning_profile_conn(conn, symbol)
            if int(learning_profile.get('total', 0) or 0) > 0:
                metadata['learning_profile'] = learning_profile
                metadata['learning_priority'] = float(learning_profile.get('score', 0.0) or 0.0)
                metadata['learned_symbol'] = learning_profile.get('status') in {'monitor', 'promote'}
            return await conn.fetchval("""
                INSERT INTO signals (market_type, symbol, signal_type, confidence, price, timestamp, metadata)
                VALUES ($1, $2, $3, $4, $5, $6, $7)
                RETURNING id
            """, market_type, symbol, signal_data['signal_type'],
                signal_data['confidence'], entry_price, timestamp,
                _dumps(metadata))

    async def record_signal_outcome(
        self,
        signal_id: int,
        *,
        target_hit: bool,
        was_correct: bool,
        pnl_pct: float,
        outcome_details: Optional[Dict[str, Any]] = None,
    ) -> bool:
        """Persist the realized outcome back onto the source signal for history and learning traceability."""
        if not signal_id:
            return False

        details = dict(outcome_details or {})
        details.update({
            'target_hit': bool(target_hit),
            'was_correct': bool(was_correct),
            'realized_pnl_pct': float(pnl_pct or 0.0),
        })
        status = 'target_hit' if target_hit else 'target_missed'
        async with self.pool.acquire() as conn:
            result = await conn.execute(
                """
                UPDATE signals
                SET status = $2,
                    metadata = COALESCE(metadata, '{}'::jsonb) || $3::jsonb
                WHERE id = $1
                """,
                int(signal_id),
                status,
                _dumps(details),
            )
            return result == 'UPDATE 1'

    async def update_signal_status(self, signal_id: int, status: str) -> bool:
        """Update signal status"""
        async with self.pool.acquire() as conn:
            result = await conn.execute("""
                UPDATE signals SET status = $1 WHERE id = $2
            """, status, signal_id)
            return result == "UPDATE 1"

    async def get_pending_signals(self) -> List[Dict[str, Any]]:
        """Get pending signals (max 200)"""
        async with self.pool.acquire() as conn:
            rows = await conn.fetch("""
                SELECT * FROM signals
                WHERE status = 'pending' AND timestamp >= NOW() - INTERVAL '24 hours'
                ORDER BY timestamp DESC LIMIT 200
            """)
            results = [dict(row) for row in rows]
            return [row for row in results if _is_target_card_candidate(row)]

    async def cleanup_stale_signals(self, ttl_hours: int = 24) -> Dict[str, int]:
        """Expire or delete stale active signals older than the given TTL."""
        hours = max(1, int(ttl_hours))
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                WITH deleted AS (
                    DELETE FROM signals s
                    WHERE s.timestamp < NOW() - make_interval(hours => $1)
                      AND s.status IN ('pending', 'active', 'open')
                      AND NOT EXISTS (
                        SELECT 1 FROM simulations sim WHERE sim.signal_id = s.id
                      )
                    RETURNING s.id
                ), updated AS (
                    UPDATE signals s
                    SET status = 'expired',
                        metadata = COALESCE(s.metadata, '{}'::jsonb) || jsonb_build_object(
                            'expired_at', NOW()::text,
                            'expired_reason', '24h_ttl'
                        )
                    WHERE s.timestamp < NOW() - make_interval(hours => $1)
                      AND s.status IN ('pending', 'active', 'open')
                      AND EXISTS (
                        SELECT 1 FROM simulations sim WHERE sim.signal_id = s.id
                      )
                    RETURNING s.id
                )
                SELECT
                    COALESCE((SELECT COUNT(*)::int FROM deleted), 0) AS deleted_count,
                    COALESCE((SELECT COUNT(*)::int FROM updated), 0) AS expired_count
                """,
                hours,
            )
        return {
            'deleted_count': int(row['deleted_count'] or 0),
            'expired_count': int(row['expired_count'] or 0),
        }

    async def get_signal_pipeline_snapshot(self, hours: int = 6) -> Dict[str, Any]:
        """Return recent signal flow summary for chat/system narration."""
        hrs = max(1, min(int(hours), 168))
        cutoff = datetime.utcnow() - timedelta(hours=hrs)
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT
                    COUNT(*)::int AS total,
                    COUNT(CASE WHEN status = 'pending' THEN 1 END)::int AS pending,
                    COUNT(CASE WHEN status = 'processed' THEN 1 END)::int AS processed,
                    COUNT(CASE WHEN status LIKE 'risk_%' THEN 1 END)::int AS risk_rejected
                FROM signals
                WHERE timestamp >= $1
                """,
                cutoff,
            )
            latest = await conn.fetchrow(
                """
                SELECT symbol, signal_type, status, confidence::double precision AS confidence, timestamp
                FROM signals
                ORDER BY timestamp DESC
                LIMIT 1
                """
            )

        return {
            "window_hours": hrs,
            "total": int(row["total"] or 0),
            "pending": int(row["pending"] or 0),
            "processed": int(row["processed"] or 0),
            "risk_rejected": int(row["risk_rejected"] or 0),
            "latest": dict(latest) if latest else None,
        }

    # Simulation operations
    async def insert_simulation(self, sim_data: Dict[str, Any]) -> int:
        """Insert a new simulation"""
        async with self.pool.acquire() as conn:
            return await conn.fetchval("""
                INSERT INTO simulations
                (signal_id, market_type, symbol, entry_price, quantity, side, entry_time,
                 stop_loss, take_profit, metadata)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
                RETURNING id
            """, sim_data.get('signal_id'), sim_data.get('market_type', 'spot'), sim_data['symbol'],
                sim_data['entry_price'], sim_data['quantity'], sim_data['side'], sim_data['entry_time'],
                sim_data.get('stop_loss'), sim_data.get('take_profit'),
                _dumps(sim_data.get('metadata', {})))

    async def update_simulation(self, sim_id: int, update_data: Dict[str, Any]) -> bool:
        """Update simulation with exit data"""
        async with self.pool.acquire() as conn:
            set_parts = []
            values = []
            param_count = 1

            for key, value in update_data.items():
                if key in ['exit_price', 'exit_time', 'pnl', 'pnl_pct', 'status']:
                    set_parts.append(f"{key} = ${param_count}")
                    values.append(value)
                    param_count += 1

            if not set_parts:
                return False

            query = f"UPDATE simulations SET {', '.join(set_parts)} WHERE id = ${param_count}"
            values.append(sim_id)

            result = await conn.execute(query, *values)
            return result == "UPDATE 1"

    async def get_open_simulations(self) -> List[Dict[str, Any]]:
        """Get open simulations (max 100)"""
        async with self.pool.acquire() as conn:
            rows = await conn.fetch("""
                SELECT * FROM simulations
                WHERE status = 'open'
                ORDER BY entry_time DESC LIMIT 100
            """)
            return [dict(row) for row in rows]

    async def get_watchlist(self) -> List[Dict[str, Any]]:
        """Get current watchlist entries"""
        async with self.pool.acquire() as conn:
            rows = await conn.fetch("""
                SELECT * FROM watchlist
                ORDER BY symbol, market_type
            """)
            return [dict(row) for row in rows]

    async def add_watchlist_symbol(self, symbol: str, market_type: str = 'spot', description: str = None) -> int:
        """Add a symbol to the watchlist"""
        async with self.pool.acquire() as conn:
            return await conn.fetchval("""
                INSERT INTO watchlist (symbol, market_type, description)
                VALUES ($1, $2, $3)
                ON CONFLICT DO NOTHING
                RETURNING id
            """, symbol, market_type, description)

    # Blacklist operations
    async def insert_blacklist_pattern(self, pattern_data: Dict[str, Any]) -> int:
        """Insert a blacklist pattern"""
        async with self.pool.acquire() as conn:
            return await conn.fetchval("""
                INSERT INTO blacklist_patterns (pattern_type, pattern_data, confidence, reason, created_by)
                VALUES ($1, $2, $3, $4, $5)
                RETURNING id
            """, pattern_data['pattern_type'], _dumps(pattern_data['pattern_data']),
                pattern_data['confidence'], pattern_data.get('reason'), pattern_data.get('created_by'))

    async def get_blacklist_patterns(self, pattern_type: str = None) -> List[Dict[str, Any]]:
        """Get blacklist patterns"""
        async with self.pool.acquire() as conn:
            if pattern_type:
                rows = await conn.fetch("""
                    SELECT * FROM blacklist_patterns
                    WHERE pattern_type = $1
                    ORDER BY created_at DESC
                """, pattern_type)
            else:
                rows = await conn.fetch("""
                    SELECT * FROM blacklist_patterns
                    ORDER BY created_at DESC LIMIT 200
                """)
            return [dict(row) for row in rows]

    # Audit operations
    async def insert_audit_report(self, audit_data: Dict[str, Any]) -> int:
        """Insert an audit report"""
        async with self.pool.acquire() as conn:
            return await conn.fetchval("""
                INSERT INTO audit_reports (signal_id, simulation_id, analysis, lessons_learned, recommendations)
                VALUES ($1, $2, $3, $4, $5)
                RETURNING id
            """, audit_data.get('signal_id'), audit_data.get('simulation_id'),
                _dumps(audit_data['analysis']), audit_data.get('lessons_learned'),
                _dumps(audit_data.get('recommendations', [])))

    # Agent config operations
    async def get_agent_config(self, agent_name: str, config_key: str) -> Any:
        """Get agent configuration value"""
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow("""
                SELECT config_value FROM agent_config
                WHERE agent_name = $1 AND config_key = $2
            """, agent_name, config_key)
            return row['config_value'] if row else None

    async def set_agent_config(self, agent_name: str, config_key: str, config_value: Any):
        """Set agent configuration value"""
        async with self.pool.acquire() as conn:
            await conn.execute("""
                INSERT INTO agent_config (agent_name, config_key, config_value)
                VALUES ($1, $2, $3)
                ON CONFLICT (agent_name, config_key)
                DO UPDATE SET config_value = EXCLUDED.config_value, updated_at = CURRENT_TIMESTAMP
            """, agent_name, config_key, _dumps(config_value))

    # Analytics queries
    async def get_dashboard_summary(self) -> Dict[str, Any]:
        """Get dashboard summary statistics"""
        async with self.pool.acquire() as conn:
            # Raw exchange trade ticks (NOT strategy executions)
            market_ticks_total = await conn.fetchval("SELECT COUNT(*) FROM trades")

            # Recent movements (last 24h)
            cutoff = datetime.utcnow() - timedelta(hours=24)
            recent_movements = await conn.fetchval("""
                SELECT COUNT(*) FROM price_movements WHERE start_time >= $1
            """, cutoff)

            # Active signals
            active_signals = await conn.fetchval("""
                SELECT COUNT(*) FROM signals WHERE status = 'pending'
            """)

            # Open simulations
            open_sims = await conn.fetchval("""
                SELECT COUNT(*) FROM simulations WHERE status = 'open'
            """)

            # Total PnL from closed simulations
            total_pnl = await conn.fetchval("""
                SELECT COALESCE(SUM(pnl), 0) FROM simulations WHERE status = 'closed'
            """)

            # Strategy execution metrics (paper-trade lifecycle)
            strategy_closed_trades = await conn.fetchval("""
                SELECT COUNT(*) FROM simulations WHERE status = 'closed'
            """)
            strategy_wins = await conn.fetchval("""
                SELECT COUNT(*) FROM simulations WHERE status = 'closed' AND pnl > 0
            """)
            win_rate = (float(strategy_wins) / float(strategy_closed_trades)) if strategy_closed_trades else 0.0

            # Recent signal risk rejections (last 24h) for health narration
            cutoff = datetime.utcnow() - timedelta(hours=24)
            risk_rejected_24h = await conn.fetchval("""
                SELECT COUNT(*) FROM signals
                WHERE timestamp >= $1 AND status LIKE 'risk_%'
            """, cutoff)

            return {
                # Keep backward-compatible key for callers, but now map to strategy trades.
                "total_trades": int(strategy_closed_trades or 0),
                "strategy_closed_trades": int(strategy_closed_trades or 0),
                "strategy_wins": int(strategy_wins or 0),
                "win_rate": float(win_rate),
                "recent_movements_24h": recent_movements,
                "active_signals": active_signals,
                "open_simulations": open_sims,
                "total_pnl": float(total_pnl) if total_pnl else 0,
                "market_ticks_total": int(market_ticks_total or 0),
                "risk_rejected_24h": int(risk_rejected_24h or 0),
            }

    async def get_closed_simulations(self, limit: int = 100) -> List[Dict[str, Any]]:
        """Get recently closed simulations"""
        async with self.pool.acquire() as conn:
            rows = await conn.fetch("""
                SELECT * FROM simulations
                WHERE status = 'closed'
                ORDER BY exit_time DESC
                LIMIT $1
            """, limit)
            return [dict(row) for row in rows]

    async def insert_audit_record(self, audit_data: Dict[str, Any]) -> int:
        """Insert an audit record"""
        async with self.pool.acquire() as conn:
            return await conn.fetchval("""
                INSERT INTO audit_records
                (timestamp, total_simulations, successful_simulations, failed_simulations,
                 success_rate, avg_win_pct, avg_loss_pct, metadata)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
                RETURNING id
            """, audit_data['timestamp'], audit_data['total_simulations'],
                audit_data['successful_simulations'], audit_data['failed_simulations'],
                audit_data['success_rate'], audit_data['avg_win_pct'], audit_data['avg_loss_pct'],
                _dumps(audit_data.get('metadata', {})))

    async def get_recent_audits(self, limit: int = 10) -> List[Dict[str, Any]]:
        """Get recent audit records"""
        async with self.pool.acquire() as conn:
            rows = await conn.fetch("""
                SELECT * FROM audit_records
                ORDER BY timestamp DESC
                LIMIT $1
            """, limit)
            return [dict(row) for row in rows]

    async def insert_failure_analysis(self, analysis_data: Dict[str, Any]) -> int:
        """Insert a failure analysis record"""
        async with self.pool.acquire() as conn:
            return await conn.fetchval("""
                INSERT INTO failure_analysis
                (timestamp, signal_type, failure_count, avg_loss_pct, recommendation, metadata)
                VALUES ($1, $2, $3, $4, $5, $6)
                RETURNING id
            """, analysis_data['timestamp'], analysis_data.get('signal_type'),
                analysis_data['failure_count'], analysis_data['avg_loss_pct'],
                analysis_data['recommendation'], _dumps(analysis_data.get('metadata', {})))

    # ─── Pattern Record Operations (Brain) ───

    async def insert_pattern_record(self, data: Dict[str, Any]) -> int:
        async with self.pool.acquire() as conn:
            return await conn.fetchval("""
                INSERT INTO pattern_records (symbol, exchange, market_type, snapshot_data,
                    outcome_15m, outcome_1h, outcome_4h, outcome_1d)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
                RETURNING id
            """, data['symbol'], data.get('exchange'), data.get('market_type', 'spot'),
                _dumps(data['snapshot_data']),
                data.get('outcome_15m'), data.get('outcome_1h'),
                data.get('outcome_4h'), data.get('outcome_1d'))

    async def get_pattern_records(self, symbol: str = None, limit: int = 500) -> List[Dict[str, Any]]:
        async with self.pool.acquire() as conn:
            if symbol:
                rows = await conn.fetch("""
                    SELECT * FROM pattern_records WHERE symbol = $1
                    ORDER BY created_at DESC LIMIT $2
                """, symbol, limit)
            else:
                rows = await conn.fetch("""
                    SELECT * FROM pattern_records
                    ORDER BY created_at DESC LIMIT $1
                """, limit)
            result = []
            for row in rows:
                d = dict(row)
                if isinstance(d.get('snapshot_data'), str):
                    d['snapshot_data'] = json.loads(d['snapshot_data'])
                result.append(d)
            return result

    async def update_pattern_outcome(self, pattern_id: int, timeframe: str, value: float):
        col = f'outcome_{timeframe}'
        if col not in ('outcome_15m', 'outcome_1h', 'outcome_4h', 'outcome_1d'):
            return
        async with self.pool.acquire() as conn:
            await conn.execute(
                f"UPDATE pattern_records SET {col} = $1 WHERE id = $2", value, pattern_id)

    # ─── Chat Operations ───

    async def insert_chat_message(self, role: str, message: str, agent_name: str = None) -> int:
        async with self.pool.acquire() as conn:
            return await conn.fetchval("""
                INSERT INTO chat_messages (role, message, agent_name)
                VALUES ($1, $2, $3) RETURNING id
            """, role, message, agent_name)

    async def get_chat_messages(self, limit: int = 50) -> List[Dict[str, Any]]:
        async with self.pool.acquire() as conn:
            rows = await conn.fetch("""
                SELECT * FROM chat_messages ORDER BY created_at DESC LIMIT $1
            """, limit)
            return [dict(row) for row in reversed(rows)]

    # ─── User Watchlist Operations ───

    async def get_user_watchlist(self) -> List[Dict[str, Any]]:
        async with self.pool.acquire() as conn:
            rows = await conn.fetch("""
                SELECT * FROM user_watchlist WHERE active = TRUE ORDER BY symbol
            """)
            return [dict(row) for row in rows]

    async def add_user_watchlist(self, symbol: str, exchange: str = 'all',
                                  market_type: str = 'spot') -> Optional[int]:
        async with self.pool.acquire() as conn:
            return await conn.fetchval("""
                INSERT INTO user_watchlist (symbol, exchange, market_type)
                VALUES ($1, $2, $3)
                ON CONFLICT (symbol, exchange, market_type)
                DO UPDATE SET active = TRUE
                RETURNING id
            """, symbol.upper(), exchange.lower(), market_type.lower())

    async def remove_user_watchlist(self, symbol: str, exchange: str = 'all',
                                     market_type: str = 'spot') -> bool:
        async with self.pool.acquire() as conn:
            result = await conn.execute("""
                UPDATE user_watchlist SET active = FALSE
                WHERE symbol = $1 AND exchange = $2 AND market_type = $3
            """, symbol.upper(), exchange.lower(), market_type.lower())
            return 'UPDATE' in result

    # ─── Brain Learning Log ───

    async def insert_learning_log(self, signal_type: str, was_correct: bool,
                                   pnl_pct: float, context: Dict = None) -> int:
        async with self.pool.acquire() as conn:
            return await conn.fetchval("""
                INSERT INTO brain_learning_log (signal_type, was_correct, pnl_pct, context)
                VALUES ($1, $2, $3, $4) RETURNING id
            """, signal_type, was_correct, pnl_pct, _dumps(context or {}))

    async def get_learning_stats(self) -> Dict[str, Any]:
        async with self.pool.acquire() as conn:
            total = await conn.fetchval("SELECT COUNT(*) FROM brain_learning_log")
            correct = await conn.fetchval("SELECT COUNT(*) FROM brain_learning_log WHERE was_correct = TRUE")
            avg_pnl = await conn.fetchval("SELECT AVG(pnl_pct) FROM brain_learning_log")
            return {
                'total': total or 0,
                'correct': correct or 0,
                'accuracy': (correct / total * 100) if total else 0,
                'avg_pnl': float(avg_pnl) if avg_pnl else 0,
            }

    async def get_symbol_learning_profile(self, symbol: str, lookback_days: int = 21) -> Dict[str, Any]:
        async with self.pool.acquire() as conn:
            return await self._get_symbol_learning_profile_conn(conn, symbol, lookback_days=lookback_days)

    async def get_learning_candidates(
        self,
        min_samples: int = 2,
        limit: int = 20,
        lookback_days: int = 21,
    ) -> List[Dict[str, Any]]:
        async with self.pool.acquire() as conn:
            cutoff = datetime.utcnow() - timedelta(days=max(1, int(lookback_days)))
            rows = await conn.fetch(
                """
                SELECT UPPER(COALESCE(context->>'symbol', '')) AS symbol,
                       COUNT(*)::int AS total,
                       COUNT(*) FILTER (WHERE was_correct = TRUE)::int AS correct,
                       COALESCE(AVG(pnl_pct), 0)::double precision AS avg_pnl
                FROM brain_learning_log
                WHERE COALESCE(context->>'symbol', '') <> ''
                  AND created_at >= $1
                GROUP BY 1
                HAVING COUNT(*) >= $2
                ORDER BY COUNT(*) DESC, AVG(pnl_pct) DESC
                LIMIT $3
                """,
                cutoff,
                max(1, int(min_samples)),
                max(1, int(limit)),
            )
            profiles: List[Dict[str, Any]] = []
            for row in rows:
                symbol = str(row.get('symbol') or '').upper().strip()
                if not symbol:
                    continue
                profile = await self._get_symbol_learning_profile_conn(conn, symbol, lookback_days=lookback_days)
                if int(profile.get('total', 0) or 0) > 0:
                    profiles.append(profile)
            profiles.sort(
                key=lambda item: (
                    float(item.get('score', 0.0) or 0.0),
                    float(item.get('avg_pnl', 0.0) or 0.0),
                    int(item.get('total', 0) or 0),
                ),
                reverse=True,
            )
            return profiles[: max(1, int(limit))]

    # ─── Enhanced Analytics ───

    async def get_trades_for_snapshot(self, symbol: str, minutes: int = 15,
                                       market_type: str = None) -> List[Dict[str, Any]]:
        """Son N dakikadaki trade'leri getir (Brain snapshot için). Max 200 kayıt."""
        async with self.pool.acquire() as conn:
            cutoff = datetime.utcnow() - timedelta(minutes=minutes)
            if market_type:
                rows = await conn.fetch("""
                    SELECT * FROM trades
                    WHERE symbol = $1 AND market_type = $2 AND timestamp >= $3
                    ORDER BY timestamp DESC LIMIT 200
                """, symbol, market_type, cutoff)
            else:
                rows = await conn.fetch("""
                    SELECT * FROM trades
                    WHERE symbol = $1 AND timestamp >= $2
                    ORDER BY timestamp DESC LIMIT 200
                """, symbol, cutoff)
            result = [dict(row) for row in rows]
            result.reverse()  # ASC order
            return result

    async def get_price_at_time(self, symbol: str, target_time: datetime) -> Optional[float]:
        """Belirli bir zamandaki fiyatı getir (en yakın trade)"""
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow("""
                SELECT price FROM trades
                WHERE symbol = $1 AND timestamp <= $2
                ORDER BY timestamp DESC LIMIT 1
            """, symbol, target_time)
            return float(row['price']) if row else None

    async def get_brain_status_data(self) -> Dict[str, Any]:
        """Brain dashboard verilerini getir"""
        async with self.pool.acquire() as conn:
            pattern_count = await conn.fetchval("SELECT COUNT(*) FROM pattern_records")
            learning = await self.get_learning_stats()
            recent_patterns = await conn.fetch("""
                SELECT symbol, outcome_15m, outcome_1h, outcome_4h, outcome_1d, created_at
                FROM pattern_records ORDER BY created_at DESC LIMIT 10
            """)
            return {
                'pattern_count': pattern_count or 0,
                'learning': learning,
                'recent_patterns': [dict(r) for r in recent_patterns],
            }

    # ─── Agent Heartbeat Operations ───

    async def update_heartbeat(self, agent_name: str, status: str = 'running',
                                 metadata: Dict = None):
        """Agent heartbeat güncelle"""
        async with self.pool.acquire() as conn:
            await conn.execute("""
                INSERT INTO agent_heartbeat (agent_name, status, last_heartbeat, metadata)
                VALUES ($1, $2, CURRENT_TIMESTAMP, $3)
                ON CONFLICT (agent_name)
                DO UPDATE SET status = $2, last_heartbeat = CURRENT_TIMESTAMP,
                              metadata = COALESCE($3, agent_heartbeat.metadata)
            """, agent_name, status, _dumps(metadata or {}))

    async def get_agent_heartbeats(self) -> List[Dict[str, Any]]:
        """Tüm agent heartbeat'lerini getir"""
        async with self.pool.acquire() as conn:
            rows = await conn.fetch("""
                SELECT agent_name, status, last_heartbeat, metadata
                FROM agent_heartbeat ORDER BY agent_name
            """)
            return [dict(row) for row in rows]

    # ─── Bot State Operations (StateTracker) ───

    async def get_bot_state(self, state_key: str = 'main') -> Optional[Dict[str, Any]]:
        """Bot state'ini getir"""
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow("""
                SELECT state_value FROM bot_state WHERE state_key = $1
            """, state_key)
            if row:
                val = row['state_value']
                return json.loads(val) if isinstance(val, str) else val
            return None

    async def save_bot_state(self, state_key: str, state_value: Dict[str, Any]):
        """Bot state'ini kaydet/güncelle"""
        async with self.pool.acquire() as conn:
            await conn.execute("""
                INSERT INTO bot_state (state_key, state_value, updated_at)
                VALUES ($1, $2, CURRENT_TIMESTAMP)
                ON CONFLICT (state_key)
                DO UPDATE SET state_value = $2, updated_at = CURRENT_TIMESTAMP
            """, state_key, _dumps(state_value))

    async def insert_state_history(self, data: Dict[str, Any]) -> int:
        """State snapshot'ı history'ye kaydet"""
        async with self.pool.acquire() as conn:
            return await conn.fetchval("""
                INSERT INTO state_history
                (timestamp, mode, cumulative_pnl, daily_pnl, daily_trade_count,
                 current_drawdown, win_rate, active_positions, total_trades, metadata)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
                RETURNING id
            """, data.get('timestamp', datetime.utcnow()),
                data.get('mode', 'BOOTSTRAP'),
                data.get('cumulative_pnl', 0),
                data.get('daily_pnl', 0),
                data.get('daily_trade_count', 0),
                data.get('current_drawdown', 0),
                data.get('win_rate', 0),
                data.get('active_positions', 0),
                data.get('total_trades', 0),
                _dumps(data.get('metadata', {})))

    async def get_state_history(self, hours: int = 24, limit: int = 100) -> List[Dict[str, Any]]:
        """State history getir"""
        async with self.pool.acquire() as conn:
            cutoff = datetime.utcnow() - timedelta(hours=hours)
            rows = await conn.fetch("""
                SELECT * FROM state_history
                WHERE timestamp >= $1
                ORDER BY timestamp DESC LIMIT $2
            """, cutoff, limit)
            return [dict(row) for row in rows]

    # ─── RCA Results Operations ───

    async def insert_rca_result(self, data: Dict[str, Any]) -> int:
        """RCA analiz sonucunu kaydet"""
        async with self.pool.acquire() as conn:
            return await conn.fetchval("""
                INSERT INTO rca_results
                (simulation_id, failure_type, confidence, explanation, recommendations, context)
                VALUES ($1, $2, $3, $4, $5, $6)
                RETURNING id
            """, data.get('simulation_id'),
                data['failure_type'],
                data.get('confidence', 0),
                data.get('explanation'),
                _dumps(data.get('recommendations', [])),
                _dumps(data.get('context', {})))

    async def get_rca_stats(self, limit: int = 50) -> Dict[str, Any]:
        """RCA istatistiklerini getir"""
        async with self.pool.acquire() as conn:
            rows = await conn.fetch("""
                SELECT failure_type, COUNT(*) as count
                FROM rca_results
                GROUP BY failure_type
                ORDER BY count DESC
            """)
            total = await conn.fetchval("SELECT COUNT(*) FROM rca_results")
            return {
                'total': total or 0,
                'distribution': {row['failure_type']: row['count'] for row in rows},
            }

    # ─── Correction Notes Operations (RCA → Strategist) ───

    async def insert_correction_note(self, data: Dict[str, Any]) -> int:
        """RCA'dan gelen düzeltme notunu kaydet"""
        async with self.pool.acquire() as conn:
            return await conn.fetchval("""
                INSERT INTO correction_notes
                (signal_type, failure_type, adjustment_key, adjustment_value,
                 reason, simulation_id)
                VALUES ($1, $2, $3, $4, $5, $6)
                RETURNING id
            """, data['signal_type'], data['failure_type'],
                data['adjustment_key'], data['adjustment_value'],
                data.get('reason'), data.get('simulation_id'))

    async def get_pending_corrections(self) -> List[Dict[str, Any]]:
        """Henüz uygulanmamış düzeltme notlarını getir"""
        async with self.pool.acquire() as conn:
            rows = await conn.fetch("""
                SELECT * FROM correction_notes
                WHERE applied = FALSE
                ORDER BY created_at DESC
                LIMIT 50
            """)
            return [dict(row) for row in rows]

    async def mark_correction_applied(self, correction_id: int):
        """Düzeltmeyi uygulandı olarak işaretle"""
        async with self.pool.acquire() as conn:
            await conn.execute("""
                UPDATE correction_notes SET applied = TRUE WHERE id = $1
            """, correction_id)

    async def get_correction_history(self, limit: int = 100) -> List[Dict[str, Any]]:
        """Tüm düzeltme geçmişini getir"""
        async with self.pool.acquire() as conn:
            rows = await conn.fetch("""
                SELECT * FROM correction_notes
                ORDER BY created_at DESC LIMIT $1
            """, limit)
            return [dict(row) for row in rows]

    # ─── Historical Signatures Operations ───

    async def insert_historical_signature(self, data: Dict[str, Any]) -> int:
        """Büyük fiyat hareketi öncesi pattern'ı kaydet"""
        safe_change_pct = float(max(min(float(data.get('change_pct', 0) or 0), 9999.9999), -9999.9999))
        async with self.pool.acquire() as conn:
            return await conn.fetchval("""
                INSERT INTO historical_signatures
                (symbol, market_type, timeframe, direction, change_pct,
                 pre_move_vector, pre_move_indicators, volume_profile, movement_id)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
                RETURNING id
            """, data['symbol'], data.get('market_type', 'spot'),
                data['timeframe'], data['direction'], safe_change_pct,
                _dumps(data['pre_move_vector']),
                _dumps(data.get('pre_move_indicators', {})),
                _dumps(data.get('volume_profile', {})),
                data.get('movement_id'))

    async def get_historical_signatures(self, symbol: str = None,
                                          timeframe: str = None,
                                          limit: int = 200,
                                          lookback_hours: int = None) -> List[Dict[str, Any]]:
        """Historical signature'ları getir"""
        async with self.pool.acquire() as conn:
            cutoff = None
            if lookback_hours:
                cutoff = datetime.utcnow() - timedelta(hours=max(1, int(lookback_hours)))
            if symbol and timeframe:
                if cutoff:
                    rows = await conn.fetch("""
                        SELECT * FROM historical_signatures
                        WHERE symbol = $1 AND timeframe = $2 AND created_at >= $3
                        ORDER BY created_at DESC LIMIT $4
                    """, symbol, timeframe, cutoff, limit)
                else:
                    rows = await conn.fetch("""
                        SELECT * FROM historical_signatures
                        WHERE symbol = $1 AND timeframe = $2
                        ORDER BY created_at DESC LIMIT $3
                    """, symbol, timeframe, limit)
            elif symbol:
                if cutoff:
                    rows = await conn.fetch("""
                        SELECT * FROM historical_signatures
                        WHERE symbol = $1 AND created_at >= $2
                        ORDER BY created_at DESC LIMIT $3
                    """, symbol, cutoff, limit)
                else:
                    rows = await conn.fetch("""
                        SELECT * FROM historical_signatures
                        WHERE symbol = $1
                        ORDER BY created_at DESC LIMIT $2
                    """, symbol, limit)
            else:
                if cutoff:
                    rows = await conn.fetch("""
                        SELECT * FROM historical_signatures
                        WHERE created_at >= $1
                        ORDER BY created_at DESC LIMIT $2
                    """, cutoff, limit)
                else:
                    rows = await conn.fetch("""
                        SELECT * FROM historical_signatures
                        ORDER BY created_at DESC LIMIT $1
                    """, limit)
            result = []
            for row in rows:
                d = dict(row)
                if isinstance(d.get('pre_move_vector'), str):
                    d['pre_move_vector'] = json.loads(d['pre_move_vector'])
                if isinstance(d.get('pre_move_indicators'), str):
                    d['pre_move_indicators'] = json.loads(d['pre_move_indicators'])
                if isinstance(d.get('volume_profile'), str):
                    d['volume_profile'] = json.loads(d['volume_profile'])
                result.append(d)
            return result

    async def count_historical_signatures(self) -> int:
        """Historical signature sayisini don."""
        async with self.pool.acquire() as conn:
            val = await conn.fetchval("SELECT COUNT(*)::int FROM historical_signatures")
            return int(val or 0)

    async def backfill_historical_signatures_from_movements(
        self,
        min_abs_change: float = 0.005,
        limit: int = 600,
        lookback_hours: int = None,
    ) -> int:
        """
        historical_signatures bosken, son price_movements kayitlarindan
        pattern-matcher icin bootstrap veri olustur.
        """
        async with self.pool.acquire() as conn:
            cutoff = datetime.utcnow() - timedelta(hours=max(1, int(lookback_hours))) if lookback_hours else None
            rows = await conn.fetch(
                """
                WITH candidates AS (
                    SELECT
                        pm.id AS movement_id,
                        pm.symbol,
                        pm.market_type,
                        COALESCE(pm.t10_data->>'timeframe', '15m') AS timeframe,
                        COALESCE(pm.direction, 'long') AS direction,
                        (pm.change_pct)::double precision AS change_pct,
                        COALESCE(pm.t10_data->'price_profile', '[]'::jsonb) AS pre_move_vector,
                        jsonb_build_object(
                            'trend', 'unknown',
                            'trend_strength', 0,
                            'source', 'movement_backfill'
                        ) AS pre_move_indicators,
                        jsonb_build_object(
                            'total', COALESCE(pm.volume::double precision, 0),
                            'buy_ratio', CASE
                                WHEN COALESCE(pm.volume::double precision, 0) > 0
                                    THEN COALESCE(pm.buy_volume::double precision, 0)
                                         / NULLIF(pm.volume::double precision, 0)
                                ELSE 0
                            END,
                            'trade_count', COALESCE((pm.t10_data->>'trade_count')::int, 0)
                        ) AS volume_profile,
                        pm.end_time
                    FROM price_movements pm
                                        WHERE ($3::timestamp IS NULL OR pm.end_time >= $3)
                      AND ABS((pm.change_pct)::double precision) >= $1
                      AND jsonb_typeof(COALESCE(pm.t10_data->'price_profile', '[]'::jsonb)) = 'array'
                      AND NOT EXISTS (
                          SELECT 1 FROM historical_signatures hs
                          WHERE hs.movement_id = pm.id
                      )
                    ORDER BY pm.end_time DESC
                    LIMIT $2
                )
                INSERT INTO historical_signatures
                    (symbol, market_type, timeframe, direction, change_pct,
                     pre_move_vector, pre_move_indicators, volume_profile, movement_id)
                SELECT
                    symbol, market_type, timeframe, direction, change_pct,
                    pre_move_vector, pre_move_indicators, volume_profile, movement_id
                FROM candidates
                RETURNING id
                """,
                min_abs_change,
                int(limit),
                cutoff,
            )
            return len(rows)

    async def get_trades_in_range(self, symbol: str, start_time: datetime,
                                   end_time: datetime,
                                   market_type: str = None) -> List[Dict[str, Any]]:
        """Belirli zaman aralığındaki trade'leri getir (max 300)"""
        async with self.pool.acquire() as conn:
            if market_type:
                rows = await conn.fetch("""
                    SELECT * FROM trades
                    WHERE symbol = $1 AND market_type = $2
                      AND timestamp >= $3 AND timestamp <= $4
                    ORDER BY timestamp ASC LIMIT 300
                """, symbol, market_type, start_time, end_time)
            else:
                rows = await conn.fetch("""
                    SELECT * FROM trades
                    WHERE symbol = $1 AND timestamp >= $2 AND timestamp <= $3
                    ORDER BY timestamp ASC LIMIT 300
                """, symbol, start_time, end_time)
            return [dict(row) for row in rows]

    # Pattern match result operations
    async def insert_pattern_match_result(self, data: Dict[str, Any]) -> int:
        """Pattern eşleşme sonucunu kaydet"""
        async with self.pool.acquire() as conn:
            return await conn.fetchval("""
                INSERT INTO pattern_match_results
                (symbol, timeframe, matched_signature_id, similarity,
                 euclidean_distance, matched_direction, matched_change_pct,
                 predicted_direction, predicted_magnitude, current_vector,
                 confidence, brain_decision, brain_reasoning)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13)
                RETURNING id
            """, data['symbol'], data['timeframe'],
                _coerce_optional_int(data.get('matched_signature_id')),
                data['similarity'], data['euclidean_distance'],
                data.get('matched_direction'), data.get('matched_change_pct'),
                data.get('predicted_direction'), data.get('predicted_magnitude'),
                _dumps(data.get('current_vector', [])),
                data.get('confidence', 0),
                data.get('brain_decision'), data.get('brain_reasoning'))

    async def get_recent_pattern_matches(self, symbol: str = None,
                                          limit: int = 50) -> List[Dict[str, Any]]:
        """Son pattern eşleşme sonuçlarını getir"""
        async with self.pool.acquire() as conn:
            if symbol:
                rows = await conn.fetch("""
                    SELECT pmr.*, hs.change_pct as hist_change_pct,
                           hs.direction as hist_direction
                    FROM pattern_match_results pmr
                    LEFT JOIN historical_signatures hs ON pmr.matched_signature_id = hs.id
                    WHERE pmr.symbol = $1
                    ORDER BY pmr.created_at DESC LIMIT $2
                """, symbol, limit)
            else:
                rows = await conn.fetch("""
                    SELECT pmr.*, hs.change_pct as hist_change_pct,
                           hs.direction as hist_direction
                    FROM pattern_match_results pmr
                    LEFT JOIN historical_signatures hs ON pmr.matched_signature_id = hs.id
                    ORDER BY pmr.created_at DESC LIMIT $1
                """, limit)
            result = []
            for row in rows:
                d = dict(row)
                if isinstance(d.get('current_vector'), str):
                    try:
                        d['current_vector'] = json.loads(d['current_vector'])
                    except Exception:
                        pass
                # Normalize Decimal/datetime/etc. for JSON endpoints.
                result.append(json.loads(_dumps(d)))
            return result

    async def update_pattern_match_outcome(self, match_id: int, outcome_pct: float):
        """Pattern eşleşme sonucunun gerçek sonucunu güncelle"""
        async with self.pool.acquire() as conn:
            await conn.execute("""
                UPDATE pattern_match_results SET outcome_pct = $1
                WHERE id = $2
            """, outcome_pct, match_id)

    # Generic query helpers for chat interface and other uses
    async def execute(self, query: str, *args) -> None:
        """Execute a query without returning results"""
        async with self.pool.acquire() as conn:
            await conn.execute(query, *args)

    async def fetch(self, query: str, *args) -> List[Dict[str, Any]]:
        """Fetch all rows as dictionaries"""
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(query, *args)
            result = []
            for row in rows:
                d = dict(row)
                # Normalize special types for JSON
                result.append(json.loads(_dumps(d)))
            return result

    async def fetchone(self, query: str, *args) -> Optional[Dict[str, Any]]:
        """Fetch single row as dictionary"""
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(query, *args)
            if row:
                d = dict(row)
                return json.loads(_dumps(d))
            return None
