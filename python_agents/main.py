#!/usr/bin/env python3
import asyncio
import logging
import os
import json
import time
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Any, Dict, Optional
from dotenv import load_dotenv

from config import Config
from database import Database

# ══ KATMAN 2: Biliş/Hafıza ══
from brain import BrainModule

# ══ KATMAN 3: Karar Çekirdeği ══
from gemma_decision_core import get_decision_core

# ══ KATMAN 5: Arayüz ══
from chat_engine import ChatEngine

# ══ KATMAN 1: Veri Giriş ══
from scout_agent import ScoutAgent

# ══ KATMAN 4: Aksiyon ══
from strategist_agent import StrategistAgent
from ghost_simulator_agent import GhostSimulatorAgent
from auditor_agent import AuditorAgent

# ══ KATMAN 2: Pattern Eşleşme ══
from pattern_matcher_agent import PatternMatcherAgent
from mamis import MAMISOrchestrator
from efom import EvolutionaryFeedbackOptimizationModule, apply_efom_runtime_overrides

from state_tracker import StateTracker
from risk_manager import RiskManager
from rca_engine import RCAEngine
from llm_client import get_llm_client
from llm_bridge import get_llm_bridge
from directive_store import get_directive_store
from task_queue import get_task_queue
from event_bus import get_event_bus, EventBus, Event, EventType
from resource_monitor import ResourceMonitor
from cleanup_module import CleanupModule
from code_operator import get_code_operator
from qwen_models import CommunicationLogEntry, DirectivePayload, decision_command_json_schema
from redis_event_bus import get_redis_bridge
from market_activity_tracker import get_market_tracker
from vector_memory import get_vector_store

# Setup logging
LOG_DIR = os.path.dirname(os.path.abspath(__file__))
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
    handlers=[
        logging.FileHandler(os.path.join(LOG_DIR, 'agents.log')),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

load_dotenv()

class AgentOrchestrator:
    """
    5-Katmanlı Gemma-Merkezli Mimari Orkestratörü
    ══════════════════════════════════════════════
    Katman 1 (Giriş)   : ScoutAgent — veri toplama, indikatörler
    Katman 2 (Biliş)   : PatternMatcher + Brain + IntelligenceCore
    Katman 3 (Karar)   : GemmaDecisionCore — Gemma 4 nihai karar
    Katman 4 (Aksiyon) : Strategist + GhostSimulator + RiskManager
    Katman 5 (Arayüz)  : ChatEngine — Gemma doğal dil konuşma

    Ajanlar ÖNERİ sunar, Gemma NİHAİ KARAR verir.
    """
    def __init__(self):
        self.db = Database()
        self.brain = None
        self.decision_core = None  # Katman 3: Gemma karar merkezi
        self.chat_engine = None
        self.scout = None
        self.strategist = None
        self.ghost_simulator = None
        self.auditor = None
        self.pattern_matcher = None
        self.mamis = None
        self.efom = None
        self.state_tracker = None
        self.risk_manager = None
        self.rca_engine = None
        self.llm_client = None
        self.llm_bridge = None
        self.task_queue = None
        self.directive_store = None
        self.code_operator = None
        self.event_bus: EventBus = get_event_bus()
        self.redis_bridge = get_redis_bridge(self.event_bus)
        self.vector_store = get_vector_store()
        self.cleanup_module = CleanupModule()
        self.resource_monitor = ResourceMonitor()
        self.running = False
        self._agent_restart_counts: dict = {}
        self._last_signal_cleanup_ts = 0.0
        self._max_restarts = 50
        self._system_mode = "initializing"  # initializing | healthy | degraded
        self._llm_available = False
        self._last_known_llm_model: str = os.getenv("QUENBOT_LLM_MODEL", "quenbot-brain")
        self._start_time = time.time()
        self._last_resource_snapshot = None
        self._resource_warnings: list[dict] = []
        # Pattern-match LLM evaluation throttles to keep chat responsive
        self._pattern_eval_min_similarity = float(os.getenv("QUENBOT_PATTERN_EVAL_MIN_SIM", "0.62"))
        self._pattern_eval_min_interval = float(os.getenv("QUENBOT_PATTERN_EVAL_MIN_INTERVAL", "180"))
        self._pattern_signal_min_conf = float(os.getenv("QUENBOT_PATTERN_SIGNAL_MIN_CONF", "0.68"))
        self._pattern_signal_min_quality = float(os.getenv("QUENBOT_PATTERN_SIGNAL_MIN_QUALITY", "0.74"))
        self._pattern_signal_window_seconds = int(os.getenv("QUENBOT_PATTERN_SIGNAL_WINDOW_SECONDS", "900"))
        self._target_card_min_conf = float(os.getenv("QUENBOT_TARGET_CARD_MIN_CONF", "0.62"))
        self._target_card_min_quality = float(os.getenv("QUENBOT_TARGET_CARD_MIN_QUALITY", "0.68"))
        self._mamis_target_card_min_conf = float(os.getenv("QUENBOT_MAMIS_TARGET_CARD_MIN_CONF", "0.72"))
        self._mamis_target_card_min_volatility = float(os.getenv("QUENBOT_MAMIS_TARGET_CARD_MIN_VOLATILITY", "0.0035"))
        self._last_pattern_signal_window: dict[str, int] = {}
        self._last_pattern_eval_at: dict[str, float] = {}
        self._pattern_eval_semaphore = asyncio.Semaphore(1)
        self._historical_warmup_task = None
        # Thread pool for CPU-bound work (pattern matching, similarity calc)
        self._thread_pool = ThreadPoolExecutor(
            max_workers=6, thread_name_prefix="quenbot-cpu"
        )

    @staticmethod
    def _normalize_target_pct(value: float) -> float:
        numeric = abs(float(value or 0.0))
        if numeric > 0.5:
            numeric /= 100.0
        return numeric

    def _signal_quality_score(self, confidence: float, target_pct: float) -> float:
        c = min(max(float(confidence), 0.0), 1.0)
        tp = self._normalize_target_pct(target_pct)
        ideal = 0.025
        target_component = 1.0 - min(abs(tp - ideal) / 0.03, 1.0)
        return min(max(c * 0.8 + target_component * 0.2, 0.0), 1.0)

    def _mark_target_card_metadata(
        self,
        metadata: dict | None,
        *,
        source: str,
        confidence: float,
        target_pct: float,
        approved: bool,
        dashboard_candidate: bool | None = None,
    ) -> dict:
        meta = dict(metadata or {})
        quality = float(meta.get("quality_score", self._signal_quality_score(confidence, target_pct)) or 0.0)
        eligible = (
            approved
            and self._normalize_target_pct(target_pct) >= 0.02
            and float(confidence) >= self._target_card_min_conf
            and quality >= self._target_card_min_quality
        )
        meta.setdefault("source", source)
        meta["quality_score"] = round(quality, 4)
        meta["strategy_approved"] = bool(meta.get("strategy_approved", False) or approved)
        meta["dashboard_candidate"] = bool(eligible if dashboard_candidate is None else dashboard_candidate)
        meta["target_candidate"] = bool(meta["dashboard_candidate"])
        return meta

    def _is_mamis_target_candidate(self, confidence: float, target_pct: float, estimated_volatility: float) -> bool:
        quality = self._signal_quality_score(confidence, target_pct)
        return (
            self._normalize_target_pct(target_pct) >= 0.02
            and float(confidence) >= self._mamis_target_card_min_conf
            and quality >= max(self._target_card_min_quality, 0.74)
            and float(estimated_volatility or 0.0) >= self._mamis_target_card_min_volatility
        )

    async def _handle_redis_message(self, channel: str, payload: dict):
        await self.event_bus.publish(Event(
            type=EventType.REDIS_MESSAGE,
            source="redis_bridge",
            data={"channel": channel, "payload": payload},
        ))

        if channel == "directives" and self.directive_store and payload.get("directive"):
            await self.directive_store.set_master_directive(payload.get("directive", ""))

    async def _bootstrap_vector_memory_from_signatures(self, limit: int) -> dict:
        signatures = await self.db.get_historical_signatures(
            limit=limit,
            lookback_hours=Config.HISTORICAL_LOOKBACK_HOURS,
        )
        upserted = 0
        for signature in signatures:
            try:
                vector = signature.get("pre_move_vector") or []
                if len(vector) < 8:
                    continue
                base_price = 100.0
                prices = [base_price * (1.0 + float(point)) for point in vector]
                volume_profile = signature.get("volume_profile") or {}
                trade_count = int(volume_profile.get("trade_count") or len(prices) or 1)
                total_volume = float(volume_profile.get("total") or trade_count)
                per_trade_volume = total_volume / max(trade_count, 1)
                volumes = [per_trade_volume] * len(prices)
                created_at = signature.get("created_at") or datetime.now(timezone.utc)
                snapshot = self.vector_store.build_feature_snapshot(
                    symbol=str(signature.get("symbol", "")).upper(),
                    prices=prices,
                    volumes=volumes,
                    timeframe=str(signature.get("timeframe", "15m")),
                    market_type=str(signature.get("market_type", "spot")),
                    exchange="historical",
                    metadata={
                        "direction": signature.get("direction", "neutral"),
                        "magnitude": float(signature.get("change_pct", 0) or 0),
                        "signature_id": signature.get("id"),
                        "buy_ratio": float(volume_profile.get("buy_ratio", 0.5) or 0.5),
                        "source": "historical_signature_bootstrap",
                    },
                    observed_at=created_at if isinstance(created_at, datetime) else datetime.now(timezone.utc),
                )
                self.vector_store.upsert_pattern_snapshot(
                    snapshot,
                    reference_id=f"sig:{signature.get('id')}",
                    direction=str(signature.get("direction", "neutral")),
                    magnitude=float(signature.get("change_pct", 0) or 0),
                )
                upserted += 1
            except Exception:
                continue
        return {"loaded": len(signatures), "upserted": upserted}

    async def _warm_historical_context(self) -> None:
        try:
            hs_count = await self.db.count_historical_signatures()
            inserted = 0
            if hs_count < Config.SIGNATURE_BACKFILL_LIMIT:
                inserted = await self.db.backfill_historical_signatures_from_movements(
                    min_abs_change=0.005,
                    limit=Config.SIGNATURE_BACKFILL_LIMIT,
                    lookback_hours=Config.HISTORICAL_LOOKBACK_HOURS,
                )
            vector_bootstrap = await self._bootstrap_vector_memory_from_signatures(Config.SIGNATURE_CACHE_LIMIT)
            logger.info(
                f"📚 Historical warmup completed: existing={hs_count} inserted={inserted} vector_upserted={vector_bootstrap['upserted']}"
            )
        except Exception as e:
            logger.warning(f"Historical warmup background task failed: {e}")

    async def initialize(self):
        """Initialize all components with startup status report"""
        logger.info("=" * 80)
        logger.info("🤖 QUENBOT — Gemma-Merkezli 5-Katmanlı Otonom Trading Zekası")
        logger.info("=" * 80)
        logger.info("  Katman 1: Veri Giriş    → ScoutAgent")
        logger.info("  Katman 2: Biliş/Hafıza  → PatternMatcher + Brain")
        logger.info("  Katman 3: Karar Çekirdeği → GemmaDecisionCore (Gemma 4)")
        logger.info("  Katman 4: Aksiyon       → Strategist + Ghost + Risk")
        logger.info("  Katman 5: Arayüz        → ChatEngine (Gemma doğal dil)")
        logger.info("=" * 80)

        startup_report = {
            "start_time": datetime.now(timezone.utc).isoformat(),
            "components": {},
        }

        # 1. Database
        await self.db.connect()
        logger.info("✓ Database initialized")
        startup_report["components"]["database"] = {"status": "ok"}

        efom_override_report = apply_efom_runtime_overrides()
        startup_report["components"]["efom_runtime"] = {
            "status": "ok",
            **efom_override_report,
        }

        # 1.1 Lightweight historical bootstrap on critical path; full archive warmup runs in background.
        try:
            hs_count = await self.db.count_historical_signatures()
            inserted = 0
            if hs_count == 0:
                inserted = await self.db.backfill_historical_signatures_from_movements(
                    min_abs_change=0.005,
                    limit=min(500, Config.SIGNATURE_BACKFILL_LIMIT),
                    lookback_hours=Config.HISTORICAL_LOOKBACK_HOURS,
                )
            vector_bootstrap = await self._bootstrap_vector_memory_from_signatures(min(500, Config.SIGNATURE_CACHE_LIMIT))
            logger.info(f"🔁 Historical signature backfill: existing={hs_count} inserted={inserted} vector_upserted={vector_bootstrap['upserted']}")
            startup_report["components"]["historical_signature_backfill"] = {
                "status": "ok",
                "existing": hs_count,
                "inserted": inserted,
                "vector_bootstrap": vector_bootstrap,
                "lookback_hours": Config.HISTORICAL_LOOKBACK_HOURS,
                "background_warmup": True,
            }
        except Exception as e:
            logger.warning(f"Historical signature backfill skipped: {e}")
            startup_report["components"]["historical_signature_backfill"] = {
                "status": "error",
                "reason": str(e),
            }

        # 2. Brain
        self.brain = BrainModule(self.db)
        await self.brain.initialize()
        brain_info = self.brain.get_brain_status()
        logger.info(f"🧠 Brain initialized ({brain_info['total_patterns']} patterns)")
        startup_report["components"]["brain"] = {"status": "ok", "patterns": brain_info["total_patterns"]}

        # 3. StateTracker
        self.state_tracker = StateTracker(self.db)
        await self.state_tracker.load_state()
        mode = self.state_tracker.get_mode()
        trades = self.state_tracker.state['total_trades']
        logger.info(f"📊 StateTracker initialized (mode={mode}, trades={trades})")
        startup_report["components"]["state_tracker"] = {"status": "ok", "mode": mode, "trades": trades}

        # 4. RiskManager
        self.risk_manager = RiskManager(self.state_tracker)
        logger.info(f"🛡 RiskManager initialized (max_daily={self.risk_manager.MAX_DAILY_TRADES})")
        startup_report["components"]["risk_manager"] = {"status": "ok"}

        # 5. RCA Engine
        self.rca_engine = RCAEngine(self.db)
        logger.info("🔍 RCA Engine initialized")
        startup_report["components"]["rca_engine"] = {"status": "ok"}

        # 5.5 GemmaDecisionCore — Katman 3: Merkezi karar motoru
        self.decision_core = get_decision_core(
            brain=self.brain,
            risk_manager=self.risk_manager,
            state_tracker=self.state_tracker,
            redis_bridge=self.redis_bridge,
        )
        logger.info("⚡ GemmaDecisionCore initialized (Katman 3 — Gemma nihai karar)")
        startup_report["components"]["decision_core"] = {"status": "ok"}
        logger.info("🔍 RCA Engine initialized")
        startup_report["components"]["rca_engine"] = {"status": "ok"}

        # 5.6 Vector memory + Redis bridge + cleanup report
        startup_report["components"]["vector_memory"] = {"status": "ok", **self.vector_store.get_stats()}
        redis_ok = await self.redis_bridge.connect()
        if redis_ok:
            self.event_bus.register_mirror(self.redis_bridge.mirror_event)
            await self.redis_bridge.start_listener(self._handle_redis_message)
            startup_report["components"]["redis"] = {"status": "ok", **self.redis_bridge.get_stats()}
        else:
            startup_report["components"]["redis"] = {"status": "degraded", **self.redis_bridge.get_stats()}

        cleanup_report = self.cleanup_module.cleanup(dry_run=True)
        startup_report["components"]["cleanup"] = {"status": "ok", **cleanup_report}
        await self.event_bus.publish(Event(
            type=EventType.CLEANUP_COMPLETED,
            source="cleanup_module",
            data=cleanup_report,
        ))

        # 6. LLM — degraded mode if unavailable
        try:
            self.llm_client = get_llm_client()
            self.task_queue = get_task_queue()
            await self.task_queue.start()
            self.directive_store = get_directive_store()
            self.code_operator = get_code_operator()
            await self.code_operator.start()
            self.llm_bridge = get_llm_bridge()

            llm_healthy = await self.llm_client.health_check()
            if llm_healthy:
                self._llm_available = True
                logger.info(f"🧠 LLM connected (model: {self.llm_client.model})")
                asyncio.create_task(self._warmup_llm())
                startup_report["components"]["llm"] = {"status": "ok", "model": self.llm_client.model}
                startup_report["components"]["code_operator"] = {"status": "ok", "model": self.code_operator._client.model if self.code_operator else None}
            else:
                logger.info("🧠 LLM backend reachable, checking for models...")
                model_ok = await self.llm_client.ensure_model()
                if model_ok:
                    self._llm_available = True
                    logger.info(f"🧠 LLM model ready: {self.llm_client.model}")
                    asyncio.create_task(self._warmup_llm())
                    startup_report["components"]["llm"] = {"status": "ok", "model": self.llm_client.model}
                    startup_report["components"]["code_operator"] = {"status": "ok", "model": self.code_operator._client.model if self.code_operator else None}
                else:
                    self._llm_available = False
                    logger.warning("⚠ No LLM model available — DEGRADED MODE (rule-based logic)")
                    startup_report["components"]["llm"] = {"status": "degraded", "reason": "no model"}
                    startup_report["components"]["code_operator"] = {"status": "degraded", "reason": "llm unavailable"}
        except Exception as e:
            self._llm_available = False
            logger.warning(f"⚠ LLM initialization failed — DEGRADED MODE: {e}")
            self.llm_bridge = None
            startup_report["components"]["llm"] = {"status": "degraded", "reason": str(e)}
            startup_report["components"]["code_operator"] = {"status": "degraded", "reason": str(e)}

        # Set system mode
        self._system_mode = "healthy" if self._llm_available else "degraded"

        # 7. Resource snapshot
        snap = self.resource_monitor.snapshot()
        self._last_resource_snapshot = snap
        startup_report["resources"] = snap.to_dict()
        logger.info(f"💻 Resources: CPU={snap.cpu_percent:.0f}% RAM={snap.ram_percent:.0f}% "
                     f"({snap.ram_used_mb:.0f}/{snap.ram_total_mb:.0f}MB) "
                     f"Disk={snap.disk_percent:.0f}%")

        # 8. Initialize agents
        self.scout = ScoutAgent(self.db, brain=self.brain)
        self.strategist = StrategistAgent(self.db, brain=self.brain,
                                           state_tracker=self.state_tracker,
                                           risk_manager=self.risk_manager)
        self.ghost_simulator = GhostSimulatorAgent(self.db, brain=self.brain,
                                                     state_tracker=self.state_tracker,
                                                     risk_manager=self.risk_manager,
                                                     rca_engine=self.rca_engine,
                                                     decision_core=self.decision_core)
        self.auditor = AuditorAgent(self.db, brain=self.brain, rca_engine=self.rca_engine)
        self.pattern_matcher = PatternMatcherAgent(self.db, brain=self.brain)
        self.mamis = MAMISOrchestrator()

        # Parallel agent initialization — utilize multiple cores
        await asyncio.gather(
            self.scout.initialize(),
            self.strategist.initialize(),
            self.ghost_simulator.initialize(),
            self.auditor.initialize(),
            self.pattern_matcher.initialize(),
            self.mamis.initialize(),
        )

        try:
            self.efom = EvolutionaryFeedbackOptimizationModule(
                self.db,
                self.event_bus,
                llm_client=self.llm_client,
            )
            await self.efom.initialize()
            startup_report["components"]["efom"] = {
                "status": "ok",
                **(await self.efom.health_check()),
            }
        except Exception as e:
            self.efom = None
            logger.warning(f"⚠ EFOM initialization failed — observer disabled: {e}")
            startup_report["components"]["efom"] = {"status": "degraded", "reason": str(e)}

        # Chat engine
        self.chat_engine = ChatEngine(self.db, self.brain)
        self.chat_engine.register_agent('Scout', self.scout)
        self.chat_engine.register_agent('Strategist', self.strategist)
        self.chat_engine.register_agent('Ghost', self.ghost_simulator)
        self.chat_engine.register_agent('Auditor', self.auditor)
        self.chat_engine.register_agent('PatternMatcher', self.pattern_matcher)
        self.chat_engine.register_agent('MAMIS', self.mamis)
        self.chat_engine.state_tracker = self.state_tracker
        self.chat_engine.risk_manager = self.risk_manager
        self.chat_engine.rca_engine = self.rca_engine

        # 9. MarketActivityTracker — Low-Power Watch mode
        self.market_tracker = get_market_tracker()
        await self.market_tracker.initialize()
        logger.info("📊 MarketActivityTracker initialized — Low-Power Watch mode active")
        startup_report["components"]["market_activity_tracker"] = {"status": "ok"}

        # 10. Wire event bus subscriptions
        self._setup_event_subscriptions()
        await self._bootstrap_learning_watchlist()

        if not self._historical_warmup_task or self._historical_warmup_task.done():
            self._historical_warmup_task = asyncio.create_task(self._warm_historical_context())

        logger.info("✓ All agents initialized with Brain + StateTracker + RiskManager + MAMIS")
        logger.info(f"✓ Monitoring {len(Config.WATCHLIST)} symbols: {Config.WATCHLIST}")
        logger.info(f"✓ System mode: {self._system_mode.upper()}")

        # 10. Startup report to DB
        startup_report["system_mode"] = self._system_mode
        startup_report["symbols"] = Config.WATCHLIST
        try:
            await self.db.update_heartbeat('system', 'running', {
                **startup_report,
                "system_mode": self._system_mode,
            })
        except Exception as e:
            logger.debug(f"Startup report save: {e}")

        # Print startup status report
        logger.info("=" * 80)
        logger.info("📋 QUENBOT — SİSTEM BAŞLANGIÇ RAPORU")
        logger.info("-" * 80)
        logger.info(f"  ⚙️  Mod          : {self._system_mode.upper()}")
        logger.info(f"  🧠 Gemma        : {'✓ ' + self.llm_client.model + ' (AKTİF — NİHAİ KARAR OTORİTESİ)' if self._llm_available else '✗ Yok (KURAL TABANLI)'}")
        logger.info(f"  🎯 DecisionCore : Gemma-merkezli karar motoru aktif")
        logger.info(f"  📚 Brain        : {brain_info['total_patterns']} pattern | %{brain_info['accuracy']*100:.1f} doğruluk")
        logger.info(f"  📊 State        : mode={mode} | trades={trades}")
        logger.info(f"  🛡  Risk         : max_daily={self.risk_manager.MAX_DAILY_TRADES}")
        logger.info(f"  💻 RAM          : {snap.ram_used_mb:.0f}/{snap.ram_total_mb:.0f} MB (%{snap.ram_percent:.0f})")
        logger.info(f"  💻 CPU          : %{snap.cpu_percent:.0f} | Load: {snap.load_avg_1m:.1f}")
        logger.info(f"  💻 Disk         : %{snap.disk_percent:.0f}")
        logger.info(f"  📡 Semboller    : {len(Config.WATCHLIST)} adet — {', '.join(Config.WATCHLIST[:5])}...")
        logger.info("-" * 80)
        logger.info("  Akış: Scout→PatternMatcher→Brain→GemmaDecisionCore→Strategist→Ghost")
        logger.info("  Chat: Kullanıcı→Gemma (doğrudan, aracısız)")
        logger.info("=" * 80)

    def _setup_event_subscriptions(self):
        """Wire inter-agent event subscriptions."""
        bus = self.event_bus

        # Scout anomaly → Strategist should re-analyze
        bus.subscribe(EventType.SCOUT_ANOMALY, self._on_scout_anomaly)

        # Signal generated → RiskManager gate → Ghost Simulator
        bus.subscribe(EventType.SIGNAL_GENERATED, self._on_signal_generated)

        # Risk approved → open simulation
        bus.subscribe(EventType.RISK_APPROVED, self._on_risk_approved)

        # Simulation closed → StateTracker + Auditor
        bus.subscribe(EventType.SIM_CLOSED, self._on_sim_closed)

        # Decision core learning → orchestrator watchlist promotion
        bus.subscribe(EventType.EXPERIENCE_RECORDED, self._on_learning_experience)

        # Resource warnings → log + DB
        bus.subscribe(EventType.RESOURCE_WARNING, self._on_resource_warning)

        # LLM status changes
        bus.subscribe(EventType.LLM_STATUS_CHANGE, self._on_llm_status_change)

        # Pattern match → Brain evaluation → Signal generation
        bus.subscribe(EventType.PATTERN_MATCH, self._on_pattern_match)
        bus.subscribe(EventType.PATTERN_DETECTED, self._on_pattern_match)

        # MAMIS microstructure signal → standard risk gate
        bus.subscribe(EventType.MICROSTRUCTURE_SIGNAL, self._on_mamis_signal)

        logger.info("✓ Event bus subscriptions wired")

    async def _refresh_watchlist_runtime(self):
        try:
            if self.scout and hasattr(self.scout, "_refresh_watchlist"):
                await self.scout._refresh_watchlist()
        except Exception as e:
            logger.warning(f"Watchlist runtime refresh failed: {e}")

    async def _update_learning_watchlist_state(
        self,
        symbol: str,
        profile: Dict[str, Any],
        outcome: Optional[Dict[str, Any]] = None,
    ) -> None:
        normalized_symbol = str(symbol or '').upper().strip()
        if not normalized_symbol:
            return

        current = await self.db.get_bot_state('learning_watchlist') or {'candidates': []}
        candidates = [
            item for item in list(current.get('candidates') or [])
            if str(item.get('symbol', '')).upper() != normalized_symbol
        ]
        entry = {
            'symbol': normalized_symbol,
            'score': float(profile.get('score', 0.0) or 0.0),
            'accuracy': float(profile.get('accuracy', 0.0) or 0.0),
            'avg_pnl': float(profile.get('avg_pnl', 0.0) or 0.0),
            'total': int(profile.get('total', 0) or 0),
            'correct': int(profile.get('correct', 0) or 0),
            'status': str(profile.get('status', 'cold')),
            'last_learning_at': profile.get('last_learning_at'),
            'recent_reasons': list(profile.get('recent_reasons') or [])[:3],
            'last_outcome': outcome or {},
            'updated_at': datetime.now(timezone.utc).isoformat(),
        }
        candidates.append(entry)
        candidates.sort(
            key=lambda item: (
                float(item.get('score', 0.0) or 0.0),
                float(item.get('avg_pnl', 0.0) or 0.0),
                int(item.get('total', 0) or 0),
            ),
            reverse=True,
        )
        await self.db.save_bot_state('learning_watchlist', {
            'updated_at': datetime.now(timezone.utc).isoformat(),
            'candidates': candidates[:25],
        })

    async def _promote_learning_symbol(
        self,
        symbol: str,
        profile: Dict[str, Any],
        outcome: Optional[Dict[str, Any]] = None,
    ) -> None:
        normalized_symbol = str(symbol or '').upper().strip()
        if not normalized_symbol:
            return

        await self._update_learning_watchlist_state(normalized_symbol, profile, outcome)
        if str(profile.get('status', 'cold')) != 'promote':
            return

        await self.db.add_user_watchlist(normalized_symbol, 'all', 'spot')
        await self.db.add_user_watchlist(normalized_symbol, 'all', 'futures')
        await self._refresh_watchlist_runtime()
        await self.db.update_heartbeat('learning_orchestrator', 'running', {
            'symbol': normalized_symbol,
            'score': float(profile.get('score', 0.0) or 0.0),
            'accuracy': float(profile.get('accuracy', 0.0) or 0.0),
            'avg_pnl': float(profile.get('avg_pnl', 0.0) or 0.0),
            'action': 'watchlist_promote',
            'updated_at': datetime.now(timezone.utc).isoformat(),
        })
        logger.info(
            f"🧠 Learning watchlist promote: {normalized_symbol} "
            f"score={float(profile.get('score', 0.0) or 0.0):.2f} "
            f"acc={float(profile.get('accuracy', 0.0) or 0.0):.0%}"
        )

    async def _bootstrap_learning_watchlist(self) -> None:
        try:
            candidates = await self.db.get_learning_candidates(min_samples=2, limit=12)
            promoted = 0
            for profile in candidates:
                symbol = str(profile.get('symbol', '')).upper().strip()
                if not symbol:
                    continue
                await self._update_learning_watchlist_state(symbol, profile)
                if str(profile.get('status', 'cold')) == 'promote':
                    await self.db.add_user_watchlist(symbol, 'all', 'spot')
                    await self.db.add_user_watchlist(symbol, 'all', 'futures')
                    promoted += 1
            if candidates:
                await self._refresh_watchlist_runtime()
                logger.info(
                    f"🧠 Bootstrapped {len(candidates)} learning candidates "
                    f"({promoted} promoted to active watchlist)"
                )
        except Exception as e:
            logger.warning(f"Learning watchlist bootstrap skipped: {e}")

    async def _warmup_llm(self):
        """Prime the active model to reduce first-response latency for chat."""
        if not self.llm_client:
            return
        try:
            await self.llm_client.generate(
                prompt="Kisa bir hazirlik kontrolu yap ve sadece hazirim yaz.",
                system="Sadece tek kelime cevap ver: hazirim",
                temperature=0.0,
                json_mode=False,
                timeout_override=20,
                model_override=self.llm_client.model,
            )
            logger.info("✓ LLM warmup completed")
        except Exception as e:
            logger.debug(f"LLM warmup skipped: {e}")
        # Also warm up the dedicated chat model in background if different.
        asyncio.create_task(self._warmup_chat_model())

    async def _warmup_chat_model(self):
        """Pre-load the dedicated chat model so first user message gets fast response."""
        chat_model = os.getenv("QUENBOT_CHAT_MODEL", "").strip()
        if not chat_model or chat_model == self.llm_client.model if self.llm_client else True:
            return  # same model — already warmed
        try:
            from llm_client import LLMClient
            tmp = LLMClient(model=chat_model, timeout=30, max_tokens=8, max_retries=0)
            resp = await tmp.generate(
                prompt="hazirim",
                system="Tek kelime: hazirim",
                temperature=0.0,
                json_mode=False,
                timeout_override=28,
                max_tokens_override=8,
            )
            await tmp.close()
            logger.info(f"✓ Chat model warmup done: {chat_model} (success={resp.success})")
        except Exception as e:
            logger.debug(f"Chat model warmup skipped: {e}")

    # ─── Event Handlers ───

    async def _on_scout_anomaly(self, event: Event):
        """Scout detected anomaly → notify strategist for priority re-analysis."""
        symbol = event.data.get("symbol", "")
        logger.info(f"📡 Event: Scout anomaly on {symbol} → Strategist notified")
        # The strategist will pick this up on its next cycle via shared state
        try:
            await self.db.update_heartbeat('event_bus',
                'running', {"last_event": "scout_anomaly", "symbol": symbol})
        except Exception:
            pass

    async def _on_signal_generated(self, event: Event):
        """Strategist generated signal → pass through RiskManager gate."""
        signal = event.data
        symbol = signal.get("symbol", "")
        direction = signal.get("direction", "")

        if self.risk_manager:
            approved, reason = self.risk_manager.check_signal(signal)
            if approved:
                logger.info(f"✅ Risk approved: {symbol} {direction}")
                await self.event_bus.publish(Event(
                    type=EventType.RISK_APPROVED,
                    source="risk_manager",
                    data=signal,
                ))
            else:
                if self.risk_manager.should_log_rejection(symbol, reason):
                    logger.info(f"🚫 Risk rejected: {symbol} {direction} — {reason}")
                await self.event_bus.publish(Event(
                    type=EventType.RISK_REJECTED,
                    source="risk_manager",
                    data={"symbol": symbol, "reason": reason},
                ))

    async def _on_risk_approved(self, event: Event):
        """Risk-approved signal → Ghost Simulator opens position."""
        signal = event.data
        logger.debug(f"👻 Signal forwarded to Ghost Simulator: {signal.get('symbol')}")
        # Ghost simulator picks up approved signals from DB in its cycle

    async def _on_sim_closed(self, event: Event):
        """Simulation closed → orchestrator receives realized outcome and persists summary state."""
        sim_result = event.data
        symbol = str(sim_result.get('symbol', '')).upper()
        summary = {
            'simulation_id': sim_result.get('id'),
            'signal_id': sim_result.get('signal_id'),
            'symbol': symbol,
            'side': sim_result.get('side'),
            'pnl_pct': float(sim_result.get('pnl_pct', 0) or 0.0),
            'target_hit': bool(sim_result.get('target_hit', False)),
            'was_correct': bool(sim_result.get('was_correct', False)),
            'reason': sim_result.get('reason'),
            'closed_at': sim_result.get('closed_at') or datetime.now(timezone.utc).isoformat(),
            'loss_analysis': sim_result.get('loss_analysis'),
        }
        await self.db.save_bot_state('last_simulation_feedback', summary)
        await self.db.update_heartbeat('orchestrator_feedback', 'running', summary)
        logger.info(
            f"🎼 Orchestrator feedback: {symbol or '?'} "
            f"target={'hit' if summary['target_hit'] else 'miss'} pnl={summary['pnl_pct']:.2f}%"
        )

    async def _on_learning_experience(self, event: Event):
        """Decision-core experience → keep learned symbols in orchestrator memory and promote strong symbols."""
        data = event.data or {}
        symbol = str(data.get('symbol', '')).upper().strip()
        if not symbol:
            return

        profile = await self.db.get_symbol_learning_profile(symbol)
        outcome = {
            'outcome': data.get('outcome'),
            'pnl_pct': float(data.get('pnl_pct', 0.0) or 0.0),
            'confidence': float(data.get('confidence', 0.0) or 0.0),
            'reasoning': str(data.get('reasoning', '') or '')[:180],
        }
        await self._promote_learning_symbol(symbol, profile, outcome)

    async def _on_resource_warning(self, event: Event):
        """Resource threshold exceeded → log and save."""
        for w in event.data.get("warnings", []):
            if w["level"] == "critical":
                logger.critical(f"🚨 {w['message']}")
            else:
                logger.warning(f"⚠ {w['message']}")
        if self.decision_core and event.data.get("warnings"):
            first = event.data["warnings"][0]
            await self.decision_core.record_error_observation(
                source="resource_monitor",
                error_type=str(first.get("component", "resource_warning")),
                message=str(first.get("message", "resource warning")),
                context=event.data,
            )

    async def _on_llm_status_change(self, event: Event):
        """LLM availability changed."""
        was_available = self._llm_available
        now_available = event.data.get("available", False)
        self._llm_available = now_available

        if was_available and not now_available:
            self._system_mode = "degraded"
            logger.warning("⚠ LLM went offline — switching to DEGRADED mode (rule-based)")
        elif not was_available and now_available:
            self._system_mode = "healthy"
            model = event.data.get('model', '?')
            if model and model != '?':
                self._last_known_llm_model = model
            logger.info(f"✓ LLM back online — switching to HEALTHY mode (model: {model})")

    async def _on_pattern_match(self, event: Event):
        """PatternMatcher found high-similarity match → Brain evaluates → Signal pipeline."""
        match_data = event.data
        symbol = match_data.get('symbol', '')
        similarity = match_data.get('similarity', 0)
        match_id = match_data.get('match_id')
        now = time.monotonic()

        # Fast gate: ignore low-value pattern matches to avoid LLM saturation.
        if similarity < self._pattern_eval_min_similarity:
            return

        # Per-symbol cooldown to reduce repetitive evaluations for near-identical bursts.
        last_eval = self._last_pattern_eval_at.get(symbol, 0.0)
        if now - last_eval < self._pattern_eval_min_interval:
            return

        # Global single-flight to keep chat and critical calls responsive under load.
        if self._pattern_eval_semaphore.locked():
            return

        logger.info(f"🎯 Pattern Match event: {symbol} similarity={similarity:.4f}")

        # Brain (Gemma) merkezi karar otoritesi olarak değerlendirir
        if self.brain:
            try:
                self._last_pattern_eval_at[symbol] = now
                async with self._pattern_eval_semaphore:
                    decision = await self.brain.evaluate_pattern_match(match_data)

                # DB'ye Brain kararını kaydet
                if match_id or symbol:
                    try:
                        async with self.db.pool.acquire() as conn:
                            if match_id:
                                await conn.execute("""
                                    UPDATE pattern_match_results
                                    SET brain_decision = $1, brain_reasoning = $2
                                    WHERE id = $3
                                """, 'approved' if decision['approved'] else 'rejected',
                                    decision.get('reasoning', '')[:500],
                                    match_id)
                            else:
                                await conn.execute("""
                                    UPDATE pattern_match_results
                                    SET brain_decision = $1, brain_reasoning = $2
                                    WHERE id = (
                                        SELECT id FROM pattern_match_results
                                        WHERE symbol = $3
                                        ORDER BY created_at DESC
                                        LIMIT 1
                                    )
                                """, 'approved' if decision['approved'] else 'rejected',
                                    decision.get('reasoning', '')[:500],
                                    symbol)
                    except Exception:
                        pass

                if decision.get('approved'):
                    raw_direction = decision.get('direction', 'neutral')
                    trade_direction = 'long' if raw_direction in ('up', 'long') else 'short'
                    conf = float(decision.get('confidence', 0) or 0)
                    quality = min(max(conf * 0.7 + float(similarity) * 0.3, 0.0), 1.0)
                    current_price = float(match_data.get('current_price') or 0)
                    predicted_magnitude = abs(float(decision.get('magnitude', 0) or match_data.get('predicted_magnitude', 0) or 0))
                    if predicted_magnitude > 0.5:
                        predicted_magnitude /= 100.0
                    target_pct = max(predicted_magnitude, 0.02)
                    data_density = min(max(float(match_data.get('data_density', match_data.get('match_count', 1) / 5.0) or 0.4), 0.0), 1.0)
                    horizon_strength = min(max(conf * 0.7 + data_density * 0.3, 0.0), 1.0)
                    target_horizons = [{
                        'label': '15m',
                        'eta_minutes': 15,
                        'target_pct': round(target_pct, 6),
                        'target_price': round(current_price * (1.0 + target_pct if trade_direction == 'long' else 1.0 - target_pct), 8),
                        'strength': round(horizon_strength, 4),
                    }]
                    for label, eta_minutes, multiplier, required_strength in [
                        ('1h', 60, 1.2, 0.35),
                        ('2h', 120, 1.45, 0.45),
                        ('4h', 240, 1.8, 0.58),
                        ('8h', 480, 2.1, 0.72),
                        ('12h', 720, 2.35, 0.82),
                    ]:
                        if horizon_strength < required_strength:
                            continue
                        scaled_target = min(max(target_pct * multiplier, 0.02), 0.18)
                        target_horizons.append({
                            'label': label,
                            'eta_minutes': eta_minutes,
                            'target_pct': round(scaled_target, 6),
                            'target_price': round(current_price * (1.0 + scaled_target if trade_direction == 'long' else 1.0 - scaled_target), 8),
                            'strength': round(horizon_strength, 4),
                        })
                    selected_horizon = target_horizons[-1]

                    if target_pct < 0.02:
                        logger.info(f"🚫 Pattern magnitude veto: {symbol} target={target_pct:.4f}")
                        return

                    # Second quality layer: low-confidence/low-quality pattern signals are vetoed.
                    if conf < self._pattern_signal_min_conf or quality < self._pattern_signal_min_quality:
                        logger.info(
                            f"🚫 Pattern quality veto: {symbol} conf={conf:.2f} quality={quality:.2f}"
                        )
                        return

                    pattern_window = int(time.time() // max(self._pattern_signal_window_seconds, 1))
                    if self._last_pattern_signal_window.get(symbol) == pattern_window:
                        return
                    self._last_pattern_signal_window[symbol] = pattern_window

                    envelope = self.decision_core.build_command_envelope_from_dict(match_data, decision) if self.decision_core else None

                    # Brain onayladı → Sinyal üret ve pipeline'a gönder
                    signal_metadata = self._mark_target_card_metadata(
                        {
                            'raw_direction': raw_direction,
                            'similarity': similarity,
                            'quality_score': quality,
                            'euclidean_distance': match_data.get('euclidean_distance'),
                            'predicted_magnitude': decision['magnitude'],
                            'target_pct': selected_horizon['target_pct'],
                            'target_price': selected_horizon['target_price'],
                            'estimated_duration_to_target_minutes': selected_horizon['eta_minutes'],
                            'target_horizons': target_horizons,
                            'selected_horizon': selected_horizon['label'],
                            'data_density': data_density,
                            'match_count': match_data.get('match_count', 0),
                            'brain_reasoning': decision.get('reasoning', ''),
                            'llm_analysis': decision.get('llm_analysis'),
                            'command_envelope': envelope.model_dump(mode='json') if envelope else None,
                            'event_type': match_data.get('event_type', 'EVENT_PATTERN_DETECTED'),
                        },
                        source='pattern_matcher',
                        confidence=conf,
                        target_pct=selected_horizon['target_pct'],
                        approved=True,
                        dashboard_candidate=True,
                    )

                    signal_data = {
                        'symbol': symbol,
                        'direction': trade_direction,
                        'signal_type': f"pattern_match_{trade_direction}",
                        'confidence': conf,
                        'price': current_price,
                        'source': 'pattern_matcher',
                        'metadata': signal_metadata,
                    }

                    # Insert signal to DB
                    try:
                        await self.db.insert_signal({
                            'market_type': 'spot',
                            'symbol': symbol,
                            'signal_type': signal_data['signal_type'],
                            'direction': signal_data['direction'],
                            'confidence': signal_data['confidence'],
                            'price': signal_data.get('price', 0),
                            'timestamp': datetime.now(timezone.utc),
                            'metadata': signal_data['metadata'],
                        })
                    except Exception as e:
                        logger.debug(f"Signal insert error: {e}")

                    # Signal → RiskManager gate
                    await self.event_bus.publish(Event(
                        type=EventType.SIGNAL_GENERATED,
                        source="pattern_matcher",
                        data=signal_data,
                    ))

                    if self.redis_bridge:
                        await self.redis_bridge.publish_command(CommunicationLogEntry(
                            channel="commands",
                            source="pattern_matcher",
                            kind="command",
                            summary=f"{symbol} icin {trade_direction} paper-trade sinyali risk katmanina iletildi",
                            payload=signal_data,
                        ))

                    logger.info(f"✅ Brain approved pattern match signal: "
                                f"{symbol} {decision['direction']} "
                                f"(confidence={decision['confidence']:.2%})")
                else:
                    logger.info(f"🚫 Brain rejected pattern match: {symbol} — "
                                f"{decision.get('reasoning', '')[:100]}")

            except Exception as e:
                logger.error(f"Pattern match brain evaluation error: {e}")

    async def _on_mamis_signal(self, event: Event):
        """MAMIS microstructure signal → persist and send through the same risk gate."""
        signal = event.data or {}
        direction = str(signal.get("signal_direction", "neutral")).lower()
        symbol = str(signal.get("symbol", "")).upper()
        if direction not in {"long", "short"} or not symbol:
            return

        confidence = float(signal.get("confidence_score", 0) or 0)
        estimated_volatility = float(signal.get("estimated_volatility", 0) or 0)
        latest_price = float(signal.get("metadata", {}).get("latest_price", 0) or 0)
        if latest_price <= 0:
            try:
                recent = await self.db.get_recent_trades(symbol, limit=1)
                if recent:
                    latest_price = float(recent[0].get("price", 0) or 0)
            except Exception:
                latest_price = 0.0

        target_pct = max(0.02, min(0.08, estimated_volatility * 6.0 + 0.02))
        mamis_target_candidate = self._is_mamis_target_candidate(confidence, target_pct, estimated_volatility)
        if not mamis_target_candidate:
            logger.info(
                f"🚫 MAMIS veto: {symbol} conf={confidence:.2f} vol={estimated_volatility:.4f} target={target_pct:.4f}"
            )
            return

        logger.info(
            f"🧭 MAMIS context accepted for strategist fusion: {symbol} dir={direction} conf={confidence:.2f} vol={estimated_volatility:.4f}"
        )

    async def start(self):
        """Start all agents with crash resilience — one agent failing does NOT kill the system"""
        self.running = True
        logger.info("🚀 Starting agent system with crash resilience...")

        tasks = [
            self._resilient_task("Scout", self.scout.start),
            self._resilient_task("Strategist", self.strategist.start),
            self._resilient_task("GhostSimulator", self.ghost_simulator.start),
            self._resilient_task("Auditor", self.auditor.start),
            self._resilient_task("PatternMatcher", self.pattern_matcher.start),
            self._resilient_task("MAMIS", self.mamis.start),
            self._resilient_task("HealthMonitor", self._health_monitor),
            self._resilient_task("DirectiveAPI", self._directive_api_server),
            self._resilient_task("SelfCorrection", self._self_correction_loop),
        ]

        if self.efom:
            tasks.append(self._resilient_task("EFOM", self.efom.start))

        if os.getenv("QUENBOT_ENABLE_CHAT_POLLER", "0").lower() in {"1", "true", "yes", "on"}:
            tasks.append(self._resilient_task("ChatProcessor", self._chat_processor))

        try:
            await asyncio.gather(*tasks)
        except KeyboardInterrupt:
            logger.info("Received shutdown signal")
        finally:
            await self.stop()

    async def _resilient_task(self, name: str, coro_func):
        """Wrap an agent task with auto-restart on failure.
        If the agent crashes, wait with exponential backoff and restart it.
        The orchestrator stays alive regardless."""
        self._agent_restart_counts[name] = 0
        while self.running:
            try:
                logger.info(f"▶ Starting {name}...")
                await coro_func()
            except asyncio.CancelledError:
                logger.info(f"⏹ {name} cancelled")
                break
            except Exception as e:
                self._agent_restart_counts[name] += 1
                count = self._agent_restart_counts[name]
                if count > self._max_restarts:
                    logger.critical(f"💀 {name} exceeded {self._max_restarts} restarts, giving up: {e}")
                    break
                backoff = min(5 * (2 ** min(count - 1, 5)), 300)  # 5s → 10s → 20s → ... max 300s
                logger.error(f"💥 {name} crashed (attempt #{count}): {e} — restarting in {backoff}s")
                await asyncio.sleep(backoff)
            else:
                # Clean exit (agent returned normally)
                if self.running:
                    logger.warning(f"⚠ {name} exited unexpectedly, restarting in 5s...")
                    await asyncio.sleep(5)
                else:
                    break

    async def stop(self):
        """Stop all agents gracefully"""
        self.running = False
        logger.info("🛑 Shutting down agent system...")

        try:
            if self.scout:
                await self.scout.stop()
            if self.strategist:
                await self.strategist.stop()
            if self.ghost_simulator:
                await self.ghost_simulator.stop()
            if self.auditor:
                await self.auditor.stop()
            if self.pattern_matcher:
                await self.pattern_matcher.stop()
            if self.mamis:
                await self.mamis.stop()
            if self.efom:
                await self.efom.stop()
            if self.task_queue:
                await self.task_queue.stop()
            if self.code_operator:
                await self.code_operator.stop()
            if self.redis_bridge:
                await self.redis_bridge.close()
            if self.llm_client:
                await self.llm_client.close()
            self._thread_pool.shutdown(wait=False)
            if self.db:
                await self.db.disconnect()
            logger.info("✓ All agents stopped")
        except Exception as e:
            logger.error(f"Error during shutdown: {e}")

    async def _self_correction_loop(self):
        """Periodic self-correction: monitor win rate, auto-revise strategy if <50%"""
        from adaptive_strategy import AdaptiveStrategyEvolver
        evolver = AdaptiveStrategyEvolver(self.db)
        logger.info("🔄 Self-Correction loop started (check every 5 min)")
        while self.running:
            try:
                await asyncio.sleep(300)  # 5 dakikada bir kontrol
                # Son 24 saatteki performansı kontrol et
                result = await self.db.fetch("""
                    SELECT
                        COUNT(*)::int AS total,
                        SUM(CASE WHEN pnl > 0 THEN 1 ELSE 0 END)::int AS wins,
                        ROUND(AVG(pnl_pct)::numeric, 3) AS avg_pnl
                    FROM simulations
                    WHERE status = 'closed' AND exit_time > NOW() - INTERVAL '24 hours'
                """)
                if not result or not result[0]:
                    continue
                row = result[0]
                total = int(row.get('total') or 0)
                wins = int(row.get('wins') or 0)
                avg_pnl = float(row.get('avg_pnl') or 0)
                if total < 5:
                    continue  # Yeterli veri yok
                win_rate = (wins / total) * 100

                # Bot state'e güncel performansı yaz
                await self.db.execute("""
                    INSERT INTO bot_state (state_key, state_value, updated_at)
                    VALUES ('self_correction_status', $1, NOW())
                    ON CONFLICT (state_key) DO UPDATE SET state_value = EXCLUDED.state_value, updated_at = NOW()
                """, json.dumps({
                    "win_rate": round(win_rate, 1),
                    "total_trades": total,
                    "wins": wins,
                    "avg_pnl_pct": float(avg_pnl),
                    "checked_at": datetime.now(timezone.utc).isoformat(),
                }))

                if win_rate < 50:
                    logger.warning(f"⚠️ Win rate {win_rate:.1f}% < 50%! Strateji revizyonu başlatılıyor...")
                    # Adaptive strategy revision
                    regime = self.state_tracker.state.get("market_regime", "SIDEWAYS") if self.state_tracker else "SIDEWAYS"
                    adaptation = await evolver.evaluate_and_evolve(regime)

                    # LLM-based strategy revision if available
                    revision_text = None
                    if self._llm_available and self.llm_bridge:
                        try:
                            prompt = (
                                f"Son 24 saatte {total} işlem yapıldı. Win rate: %{win_rate:.1f}, "
                                f"ortalama PnL: %{avg_pnl:.2f}. Performans kötü. "
                                f"Mevcut rejim: {regime}. "
                                f"Stratejiyi nasıl revize etmeliyiz? Kısa ve net öneriler ver."
                            )
                            resp = await self.llm_bridge.ask(prompt, timeout=30)
                            revision_text = resp[:500] if resp else None
                        except Exception:
                            pass

                    event_data = {
                        "type": "strategy_revised",
                        "win_rate": round(win_rate, 1),
                        "total_trades": total,
                        "regime": regime,
                        "adaptation": adaptation,
                        "llm_recommendation": revision_text,
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                    }
                    # Bot state'e strateji güncelleme kaydı yaz
                    await self.db.execute("""
                        INSERT INTO bot_state (state_key, state_value, updated_at)
                        VALUES ('last_strategy_update', $1, NOW())
                        ON CONFLICT (state_key) DO UPDATE SET state_value = EXCLUDED.state_value, updated_at = NOW()
                    """, json.dumps(event_data))

                    logger.info(f"✅ Strateji revize edildi: {event_data}")
                else:
                    logger.debug(f"📊 Self-correction check OK: win_rate={win_rate:.1f}%")

            except Exception as e:
                logger.error(f"Self-correction loop error: {e}")
                await asyncio.sleep(60)

    async def _chat_processor(self):
        """Chat mesajlarını kontrol et ve cevapla — DB polling azaltıldı"""
        last_processed_id = 0
        while self.running:
            try:
                await asyncio.sleep(3)  # 3s polling — DB yükünü %83 azaltır (500ms → 3s)
                messages = await self.db.get_chat_messages(limit=10)
                for msg in messages:
                    if msg['role'] == 'user' and msg['id'] > last_processed_id:
                        response = await self.chat_engine.respond(msg['message'])
                        assistant_name = self.chat_engine.get_assistant_identity()['name'] if self.chat_engine else 'Qwen Command'
                        await self.db.insert_chat_message('assistant', response, assistant_name)
                        last_processed_id = msg['id']
                        logger.info(f"💬 Chat: '{msg['message'][:50]}' → answered")
            except Exception as e:
                logger.debug(f"Chat processor: {e}")

    async def _health_monitor(self):
        """Monitor health of all agents, resources, and send heartbeats"""
        while self.running:
            try:
                await asyncio.sleep(30)

                # ─── Agent health checks (parallel) ───
                scout_health, strategist_health, ghost_health, auditor_health, pm_health, mamis_health, efom_health = await asyncio.gather(
                    self.scout.health_check(),
                    self.strategist.health_check(),
                    self.ghost_simulator.health_check(),
                    self.auditor.health_check(),
                    self.pattern_matcher.health_check(),
                    self.mamis.health_check(),
                    self.efom.health_check() if self.efom else asyncio.sleep(0, result={"healthy": False, "status": "disabled"}),
                )
                brain_status = self.brain.get_brain_status()
                decision_core_stats = self.decision_core.get_stats() if self.decision_core else {}

                await asyncio.gather(
                    self.db.update_heartbeat('scout',
                        'running' if scout_health.get('healthy') else 'error', scout_health),
                    self.db.update_heartbeat('strategist',
                        'running' if strategist_health.get('healthy') else 'error', strategist_health),
                    self.db.update_heartbeat('ghost_simulator',
                        'running' if ghost_health.get('healthy') else 'error', ghost_health),
                    self.db.update_heartbeat('auditor',
                        'running' if auditor_health.get('healthy') else 'error', auditor_health),
                    self.db.update_heartbeat('pattern_matcher',
                        'running' if pm_health.get('healthy') else 'error', pm_health),
                    self.db.update_heartbeat('mamis',
                        'running' if mamis_health.get('healthy') else 'error', mamis_health),
                    self.db.update_heartbeat('efom',
                        'running' if efom_health.get('healthy') else 'degraded', efom_health),
                    self.db.update_heartbeat('brain', 'running', brain_status),
                    self.db.update_heartbeat('decision_core',
                        'running' if self.decision_core else 'degraded', {
                            **decision_core_stats,
                            'active_model': getattr(self.decision_core, '_decision_model', None) if self.decision_core else None,
                        }),
                    self.db.update_heartbeat('chat_engine', 'running', {
                        'registered_agents': list(self.chat_engine.agents.keys())
                    }),
                )

                # ─── LLM health + degraded mode tracking ───
                llm_was_available = self._llm_available
                if self.llm_client:
                    llm_available = await self.llm_client.health_check()
                    self._llm_available = llm_available
                    llm_stats = self.llm_bridge.get_stats() if self.llm_bridge else {}
                    await self.db.update_heartbeat('llm_brain',
                        'running' if llm_available else 'degraded', {
                            **llm_stats,
                            "active_model": self.llm_client.model if llm_available else None,
                        })

                    # Detect status change
                    if llm_was_available != llm_available:
                        await self.event_bus.publish(Event(
                            type=EventType.LLM_STATUS_CHANGE,
                            source="health_monitor",
                            data={"available": llm_available, "model": self.llm_client.model},
                        ))
                else:
                    await self.db.update_heartbeat('llm_brain', 'degraded', {
                        "reason": "LLM client not initialized"
                    })

                self._system_mode = "healthy" if self._llm_available else "degraded"

                # Keep system heartbeat fresh so dashboard agent counter does not mark it stale.
                await self.db.update_heartbeat('system', 'running', {
                    "mode": self._system_mode,
                    "llm_available": self._llm_available,
                    "uptime_seconds": int(time.time() - self._start_time),
                })

                # ─── Resource monitoring ───
                snap = self.resource_monitor.snapshot()
                self._last_resource_snapshot = snap

                component_breakdown = {
                    "scout": {
                        "healthy": scout_health.get("healthy", False),
                        "activity_score": float(scout_health.get("trade_counter", 0)) / 5000.0,
                        "active_connections": scout_health.get("active_connections", 0),
                    },
                    "strategist": {
                        "healthy": strategist_health.get("healthy", False),
                        "activity_score": float(strategist_health.get("analysis_count", 0)) / 300.0,
                        "signals_generated": strategist_health.get("signals_generated", 0),
                    },
                    "ghost": {
                        "healthy": ghost_health.get("healthy", False),
                        "activity_score": float(ghost_health.get("active_simulations", 0)) / 20.0,
                        "active_simulations": ghost_health.get("active_simulations", 0),
                    },
                    "auditor": {
                        "healthy": auditor_health.get("healthy", False),
                        "activity_score": float(auditor_health.get("audit_count", 0)) / 100.0,
                        "audit_count": auditor_health.get("audit_count", 0),
                    },
                    "pattern_matcher": {
                        "healthy": pm_health.get("healthy", False),
                        "activity_score": float(pm_health.get("scan_count", 0)) / 500.0 +
                                         float(pm_health.get("match_count", 0)) / 100.0,
                        "scan_count": pm_health.get("scan_count", 0),
                        "match_count": pm_health.get("match_count", 0),
                        "best_similarity": pm_health.get("best_similarity", 0),
                    },
                    "mamis": {
                        "healthy": mamis_health.get("healthy", False),
                        "activity_score": float(mamis_health.get("sentinel", {}).get("bars_completed", 0)) / 300.0 +
                                         float(mamis_health.get("strategist", {}).get("signals", 0)) / 50.0,
                        "bars_completed": mamis_health.get("sentinel", {}).get("bars_completed", 0),
                        "alerts": mamis_health.get("sentinel", {}).get("anomalies", 0),
                        "signals": mamis_health.get("strategist", {}).get("signals", 0),
                    },
                    "efom": {
                        "healthy": efom_health.get("healthy", False),
                        "activity_score": float(efom_health.get("logged_trades", 0)) / 200.0 +
                                         float(efom_health.get("optimizations_run", 0)) / 20.0,
                        "logged_trades": efom_health.get("logged_trades", 0),
                        "optimizations_run": efom_health.get("optimizations_run", 0),
                    },
                }

                warnings = self.resource_monitor.check_warnings(
                    snap, component_breakdown=component_breakdown)
                self._resource_warnings = warnings

                resource_data = snap.to_dict()
                resource_data["warnings"] = warnings
                resource_data["system_mode"] = self._system_mode
                resource_data["uptime_seconds"] = int(time.time() - self._start_time)
                resource_data["event_bus"] = self.event_bus.get_stats()
                resource_data["agent_restarts"] = dict(self._agent_restart_counts)
                resource_data["agent_breakdown"] = component_breakdown
                resource_data["market_activity"] = self.market_tracker.get_stats() if hasattr(self, 'market_tracker') else {}
                await self.db.update_heartbeat('system_resources', 'running', resource_data)

                if warnings:
                    await self.event_bus.publish(Event(
                        type=EventType.RESOURCE_WARNING,
                        source="health_monitor",
                        data={"warnings": warnings},
                    ))

                # ─── Brain refresh ───
                await self.brain.refresh_patterns()

                # ─── StateTracker persist ───
                if self.state_tracker:
                    self.state_tracker.update_mode()
                    await self.state_tracker.save_state()
                    await self.state_tracker.snapshot_history()

                if time.time() - self._last_signal_cleanup_ts >= 900:
                    cleanup_report = await self.db.cleanup_stale_signals(ttl_hours=24)
                    self._last_signal_cleanup_ts = time.time()
                    if cleanup_report.get('deleted_count') or cleanup_report.get('expired_count'):
                        logger.info(
                            "🧹 Signal TTL cleanup: deleted=%s expired=%s",
                            cleanup_report.get('deleted_count', 0),
                            cleanup_report.get('expired_count', 0),
                        )

                # ─── Health report event ───
                await self.event_bus.publish(Event(
                    type=EventType.HEALTH_REPORT,
                    source="health_monitor",
                    data={
                        "system_mode": self._system_mode,
                        "llm_available": self._llm_available,
                        "agents": {
                            "scout": scout_health.get("healthy", False),
                            "strategist": strategist_health.get("healthy", False),
                            "ghost": ghost_health.get("healthy", False),
                            "auditor": auditor_health.get("healthy", False),
                            "pattern_matcher": pm_health.get("healthy", False),
                        },
                        "ram_percent": snap.ram_percent,
                        "cpu_percent": snap.cpu_percent,
                    },
                ))

                # ─── Periodic logging (every ~2 min) ───
                if int(asyncio.get_event_loop().time()) % 120 < 35:
                    logger.info(f"📊 HEALTH CHECK [{self._system_mode.upper()}]")
                    logger.info(f"  🧠 Brain: {brain_status['total_patterns']} patterns | "
                                 f"Accuracy: {brain_status['accuracy']:.1%}")
                    logger.info(f"  Scout: {'✓' if scout_health.get('healthy') else '✗'} "
                                 f"({scout_health.get('active_connections', 0)} conn | "
                                 f"{scout_health.get('trade_counter', 0)} trades)")
                    logger.info(f"  Strategist: {'✓' if strategist_health.get('healthy') else '✗'} "
                                 f"({strategist_health.get('signals_generated', 0)} signals)")
                    logger.info(f"  Ghost: {'✓' if ghost_health.get('healthy') else '✗'} "
                                 f"({ghost_health.get('active_simulations', 0)} active | "
                                 f"Win: {ghost_health.get('win_rate', 0):.0f}%)")
                    logger.info(f"  Auditor: {'✓' if auditor_health.get('healthy') else '✗'} "
                                 f"(#{auditor_health.get('audit_count', 0)})")
                    logger.info(f"  PatternMatcher: {'✓' if pm_health.get('healthy') else '✗'} "
                                 f"(scans={pm_health.get('scan_count', 0)} | "
                                 f"matches={pm_health.get('match_count', 0)} | "
                                 f"best={pm_health.get('best_similarity', 0):.4f})")
                    logger.info(f"  LLM: {'✓ ' + self.llm_client.model if self._llm_available else '✗ Kapalı (Degraded)'}")
                    logger.info(f"  💻 CPU={snap.cpu_percent:.0f}% RAM={snap.ram_percent:.0f}% "
                                 f"({snap.ram_used_mb:.0f}/{snap.ram_total_mb:.0f}MB) "
                                 f"Disk={snap.disk_percent:.0f}%")
                    if self.state_tracker:
                        st = self.state_tracker.state
                        logger.info(f"  📊 State: mode={self.state_tracker.get_mode()} | "
                                     f"trades={st['total_trades']} | "
                                     f"PnL={st['cumulative_pnl']:.2f}% | "
                                     f"DD={st['current_drawdown']:.2f}%")
                    if warnings:
                        for w in warnings:
                            logger.warning(f"  ⚠ {w['component']}: {w['message']}")

            except Exception as e:
                logger.error(f"Health monitoring error: {e}")

    async def _directive_api_server(self):
        """Lightweight HTTP server for directive management (Master Control).
        Listens on port 3002 for directive CRUD + system status + resources."""
        from aiohttp import web

        control_token = os.getenv("QUENBOT_CONTROL_TOKEN", "").strip()

        def _is_control_authorized(request) -> bool:
            if not control_token:
                return True
            return request.headers.get("X-Control-Token", "") == control_token

        async def _refresh_watchlist_runtime():
            try:
                if self.scout and hasattr(self.scout, "_refresh_watchlist"):
                    await self.scout._refresh_watchlist()
            except Exception as e:
                logger.warning(f"Watchlist runtime refresh failed: {e}")

        async def execute_control(request):
            """Runtime control plane for Qwen: directives, watchlist, risk params, model switch."""
            if not _is_control_authorized(request):
                return web.json_response({"error": "Unauthorized"}, status=403)

            try:
                body = await request.json()
                action = str(body.get("action", "")).strip().lower()
                params = body.get("params", {}) or {}

                if not action:
                    return web.json_response({"error": "action required"}, status=400)

                if action == "set_master_directive":
                    text = str(params.get("text", "")).strip()
                    if not text:
                        return web.json_response({"error": "params.text required"}, status=400)
                    await get_directive_store().set_master_directive(text)
                    result = {"ok": True, "action": action, "master_directive": text[:200]}

                elif action == "set_agent_override":
                    agent = str(params.get("agent", "")).strip()
                    text = str(params.get("text", "")).strip()
                    if not agent or not text:
                        return web.json_response({"error": "params.agent and params.text required"}, status=400)
                    await get_directive_store().set_agent_override(agent, text)
                    result = {"ok": True, "action": action, "agent": agent}

                elif action == "watchlist_replace":
                    symbols = [str(s).upper() for s in (params.get("symbols") or []) if str(s).strip()]
                    if not symbols:
                        return web.json_response({"error": "params.symbols required"}, status=400)
                    # Disable current active entries then insert new list for both spot/futures.
                    current = await self.db.get_user_watchlist()
                    for row in current:
                        await self.db.remove_user_watchlist(row["symbol"], row.get("exchange", "all"), row.get("market_type", "spot"))
                    for sym in symbols:
                        await self.db.add_user_watchlist(sym, "all", "spot")
                        await self.db.add_user_watchlist(sym, "all", "futures")
                    await _refresh_watchlist_runtime()
                    result = {"ok": True, "action": action, "watchlist": symbols, "count": len(symbols)}

                elif action == "watchlist_add":
                    symbol = str(params.get("symbol", "")).upper().strip()
                    if not symbol:
                        return web.json_response({"error": "params.symbol required"}, status=400)
                    await self.db.add_user_watchlist(symbol, "all", "spot")
                    await self.db.add_user_watchlist(symbol, "all", "futures")
                    await _refresh_watchlist_runtime()
                    result = {"ok": True, "action": action, "symbol": symbol}

                elif action == "watchlist_remove":
                    symbol = str(params.get("symbol", "")).upper().strip()
                    if not symbol:
                        return web.json_response({"error": "params.symbol required"}, status=400)
                    await self.db.remove_user_watchlist(symbol, "all", "spot")
                    await self.db.remove_user_watchlist(symbol, "all", "futures")
                    await _refresh_watchlist_runtime()
                    result = {"ok": True, "action": action, "symbol": symbol}

                elif action == "set_risk_limits":
                    if "max_daily_trades" in params:
                        Config.RISK_MAX_DAILY_TRADES = int(params["max_daily_trades"])
                    if "max_open_positions" in params:
                        Config.RISK_MAX_OPEN_POSITIONS = int(params["max_open_positions"])
                    if "max_daily_loss_pct" in params:
                        Config.RISK_MAX_DAILY_LOSS_PCT = float(params["max_daily_loss_pct"])
                    if "max_drawdown_pct" in params:
                        Config.RISK_MAX_DRAWDOWN_PCT = float(params["max_drawdown_pct"])
                    result = {
                        "ok": True,
                        "action": action,
                        "risk": {
                            "max_daily_trades": Config.RISK_MAX_DAILY_TRADES,
                            "max_open_positions": Config.RISK_MAX_OPEN_POSITIONS,
                            "max_daily_loss_pct": Config.RISK_MAX_DAILY_LOSS_PCT,
                            "max_drawdown_pct": Config.RISK_MAX_DRAWDOWN_PCT,
                        },
                    }

                elif action == "set_llm_model":
                    model = str(params.get("model", "")).strip()
                    if not model:
                        return web.json_response({"error": "params.model required"}, status=400)
                    if self.llm_client:
                        self.llm_client.model = model
                    if self.chat_engine and hasattr(self.chat_engine, "_chat_client"):
                        self.chat_engine._chat_client = None
                    self._last_known_llm_model = model
                    result = {"ok": True, "action": action, "model": model}

                elif action == "status":
                    current_watchlist = []
                    try:
                        current_watchlist = self.scout.get_watchlist() if self.scout else []
                    except Exception:
                        current_watchlist = []
                    result = {
                        "ok": True,
                        "action": action,
                        "system_mode": self._system_mode,
                        "llm_model": self._last_known_llm_model,
                        "watchlist": current_watchlist,
                        "risk": {
                            "max_daily_trades": Config.RISK_MAX_DAILY_TRADES,
                            "max_open_positions": Config.RISK_MAX_OPEN_POSITIONS,
                            "max_daily_loss_pct": Config.RISK_MAX_DAILY_LOSS_PCT,
                            "max_drawdown_pct": Config.RISK_MAX_DRAWDOWN_PCT,
                        },
                    }

                else:
                    return web.json_response({"error": f"unknown action: {action}"}, status=400)

                await self.event_bus.publish(Event(
                    type=EventType.COMMAND_ROUTED,
                    source="control_api",
                    data={"action": action, "params": params},
                ))

                await self.event_bus.publish(Event(
                    type=EventType.COMMAND_EXECUTED,
                    source="control_api",
                    data={"action": action, "ok": True},
                ))

                await self.db.insert_chat_message(
                    "system",
                    json.dumps({"control_action": action, "params": params}, ensure_ascii=False)[:1000],
                    "ControlAPI",
                )

                return web.json_response(result)
            except Exception as e:
                logger.error(f"Control action error: {e}")
                await self.event_bus.publish(Event(
                    type=EventType.COMMAND_FAILED,
                    source="control_api",
                    data={"error": str(e)[:300]},
                ))
                return web.json_response({"error": str(e)}, status=500)

        async def get_directives(request):
            store = get_directive_store()
            data = await store.get_all()
            return web.json_response(data)

        async def set_directive(request):
            try:
                body = await request.json()
                store = get_directive_store()

                if "master_directive" in body:
                    await store.set_master_directive(body["master_directive"])
                if "agent_overrides" in body:
                    for agent, text in body["agent_overrides"].items():
                        await store.set_agent_override(agent, text)

                # Publish directive event
                await self.event_bus.publish(Event(
                    type=EventType.DIRECTIVE_UPDATED,
                    source="api",
                    data=body,
                ))
                if self.redis_bridge:
                    directive_text = body.get("master_directive") or json.dumps(body, ensure_ascii=False)
                    directive_payload = DirectivePayload(
                        directive=str(directive_text),
                        requested_by="dashboard",
                        symbols=body.get("symbols", []),
                        metadata=body,
                    )
                    await self.redis_bridge.publish_directive(directive_payload.model_dump(mode="json"))

                data = await store.get_all()
                return web.json_response({"status": "ok", **data})
            except Exception as e:
                return web.json_response({"error": str(e)}, status=400)

        async def clear_directives(request):
            store = get_directive_store()
            await store.clear()
            return web.json_response({"status": "cleared"})

        async def get_llm_status(request):
            try:
                healthy = False
                models = []
                client_stats = {}

                if self.llm_client:
                    healthy = await self.llm_client.health_check()
                    models = await self.llm_client.list_models()
                    client_stats = self.llm_client.get_stats()

                bridge_stats = self.llm_bridge.get_stats() if self.llm_bridge else {}

                return web.json_response({
                    "healthy": healthy,
                    "active_model": self.llm_client.model if self.llm_client else None,
                    "available_models": models,
                    "system_mode": self._system_mode,
                    "call_count": client_stats.get("total_calls", 0),
                    "llm_stats": client_stats,
                    "decision_command_schema": decision_command_json_schema(),
                    **bridge_stats,
                })
            except Exception as e:
                return web.json_response({
                    "healthy": False,
                    "system_mode": self._system_mode,
                    "error": str(e),
                })

        async def get_queue_status(request):
            if self.task_queue:
                return web.json_response(self.task_queue.get_stats())
            return web.json_response({"error": "Queue not initialized"})

        async def get_system_resources(request):
            """System resource snapshot + warnings — mobile-friendly compact JSON."""
            snap = self.resource_monitor.snapshot()
            self._last_resource_snapshot = snap

            agent_breakdown = {}
            try:
                scout_h, strategist_h, ghost_h, auditor_h, pm_h = await asyncio.gather(
                    self.scout.health_check(),
                    self.strategist.health_check(),
                    self.ghost_simulator.health_check(),
                    self.auditor.health_check(),
                    self.pattern_matcher.health_check(),
                )
                agent_breakdown = {
                    "scout": {
                        "healthy": scout_h.get("healthy", False),
                        "activity_score": float(scout_h.get("trade_counter", 0)) / 5000.0,
                        "active_connections": scout_h.get("active_connections", 0),
                    },
                    "strategist": {
                        "healthy": strategist_h.get("healthy", False),
                        "activity_score": float(strategist_h.get("analysis_count", 0)) / 300.0,
                        "signals_generated": strategist_h.get("signals_generated", 0),
                    },
                    "ghost": {
                        "healthy": ghost_h.get("healthy", False),
                        "activity_score": float(ghost_h.get("active_simulations", 0)) / 20.0,
                        "active_simulations": ghost_h.get("active_simulations", 0),
                    },
                    "auditor": {
                        "healthy": auditor_h.get("healthy", False),
                        "activity_score": float(auditor_h.get("audit_count", 0)) / 100.0,
                        "audit_count": auditor_h.get("audit_count", 0),
                    },
                    "pattern_matcher": {
                        "healthy": pm_h.get("healthy", False),
                        "activity_score": float(pm_h.get("scan_count", 0)) / 500.0 +
                                         float(pm_h.get("match_count", 0)) / 100.0,
                        "scan_count": pm_h.get("scan_count", 0),
                        "match_count": pm_h.get("match_count", 0),
                        "best_similarity": pm_h.get("best_similarity", 0),
                    },
                }
            except Exception:
                agent_breakdown = {}

            warnings = self.resource_monitor.check_warnings(
                snap, component_breakdown=agent_breakdown)
            self._resource_warnings = warnings

            # Compact response for mobile
            is_mobile = request.query.get("compact") == "1"
            if is_mobile:
                return web.json_response({
                    "cpu": round(snap.cpu_percent, 0),
                    "ram": round(snap.ram_percent, 0),
                    "ram_mb": f"{snap.ram_used_mb:.0f}/{snap.ram_total_mb:.0f}",
                    "disk": round(snap.disk_percent, 0),
                    "mode": self._system_mode,
                    "llm": self._llm_available,
                    "warnings": len(warnings),
                    "uptime": int(time.time() - self._start_time),
                })

            return web.json_response({
                **snap.to_dict(),
                "warnings": warnings,
                "system_mode": self._system_mode,
                "llm_available": self._llm_available,
                "llm_model": self.llm_client.model if self.llm_client and self._llm_available else None,
                "uptime_seconds": int(time.time() - self._start_time),
                "agent_restarts": dict(self._agent_restart_counts),
                "event_bus": self.event_bus.get_stats(),
                "redis": self.redis_bridge.get_stats() if self.redis_bridge else {},
                "vector_memory": self.vector_store.get_stats(),
                "agent_breakdown": agent_breakdown,
                "resource_history": self.resource_monitor.get_history(),
            })

        async def get_system_summary(request):
            """Compact system summary for mobile dashboard — single endpoint."""
            snap = self._last_resource_snapshot
            resource = snap.to_dict() if snap else {}
            warnings = self._resource_warnings

            # Gather all status in one call
            llm_healthy = self._llm_available
            # Always surface the last known model name (even when temporarily unreachable)
            if self.llm_client and self.llm_client.model:
                self._last_known_llm_model = self.llm_client.model
            current_model = self._last_known_llm_model
            st = self.state_tracker.state if self.state_tracker else {}
            brain = self.brain.get_brain_status() if self.brain else {}
            decision_core = self.decision_core.get_stats() if self.decision_core else {}
            pm = {}
            mamis = {}
            efom = {}
            try:
                pm = await self.pattern_matcher.health_check()
            except Exception:
                pm = {}
            try:
                mamis = await self.mamis.health_check()
            except Exception:
                mamis = {}
            try:
                efom = await self.efom.health_check() if self.efom else {}
            except Exception:
                efom = {}

            return web.json_response({
                "mode": "active" if self.running else self._system_mode,
                "health": self._system_mode,
                "llm": {
                    "ok": llm_healthy,
                    "model": current_model,
                },
                "llm_stats": self.llm_client.get_stats() if self.llm_client else {},
                "resources": {
                    "cpu": resource.get("cpu_percent", 0),
                    "ram": resource.get("ram_percent", 0),
                    "ram_mb": f"{resource.get('ram_used_mb', 0):.0f}/{resource.get('ram_total_mb', 0):.0f}",
                    "disk": resource.get("disk_percent", 0),
                },
                "state": {
                    "mode": self.state_tracker.get_mode() if self.state_tracker else "?",
                    "trades": st.get("total_trades", 0),
                    "pnl": round(st.get("cumulative_pnl", 0), 2),
                },
                "brain": {
                    "patterns": brain.get("total_patterns", 0),
                    "accuracy": round(brain.get("accuracy", 0) * 100, 1),
                    "pattern_match": brain.get("pattern_match", {}),
                    "learning_weights": brain.get("learning_weights", {}),
                },
                "decision_core": {
                    "ok": bool(self.decision_core),
                    "model": getattr(self.decision_core, '_decision_model', self._last_known_llm_model),
                    "approval_rate": round(float(decision_core.get("approval_rate", 0)) * 100, 1),
                    "total_requests": decision_core.get("total_requests", 0),
                    "gemma_calls": decision_core.get("gemma_calls", 0),
                    "fallback_calls": decision_core.get("fallback_calls", 0),
                    "avg_latency_ms": round(float(decision_core.get("avg_latency_ms", 0)), 1),
                },
                "code_operator": await self.code_operator.get_status() if self.code_operator else {"enabled": False},
                "vector_memory": self.vector_store.get_stats(),
                "redis": self.redis_bridge.get_stats() if self.redis_bridge else {},
                "cleanup": self.cleanup_module.scan(),
                "pattern_matcher": {
                    "ok": pm.get("healthy", False),
                    "scans": pm.get("scan_count", 0),
                    "matches": pm.get("match_count", 0),
                    "best_similarity": pm.get("best_similarity", 0),
                },
                "mamis": {
                    "ok": mamis.get("healthy", False),
                    "bars": mamis.get("sentinel", {}).get("bars_completed", 0),
                    "alerts": mamis.get("sentinel", {}).get("anomalies", 0),
                    "classifications": mamis.get("forensic", {}).get("classified", 0),
                    "signals": mamis.get("strategist", {}).get("signals", 0),
                    "last_pattern": mamis.get("forensic", {}).get("last_pattern"),
                },
                "efom": {
                    "ok": efom.get("healthy", False),
                    "logged_trades": efom.get("logged_trades", 0),
                    "optimizations_run": efom.get("optimizations_run", 0),
                    "config_path": efom.get("config_path"),
                },
                "warnings": [{"level": w["level"], "comp": w["component"],
                              "msg": w["message"][:120]} for w in warnings],
                "uptime": int(time.time() - self._start_time),
            })

        async def get_event_log(request):
            """Recent event bus activity."""
            stats = self.event_bus.get_stats()
            return web.json_response(stats)

        async def get_mamis_status(request):
            """Live MAMIS microstructure dashboard payload."""
            try:
                health = await self.mamis.health_check() if self.mamis else {}
                payload = self.mamis.get_dashboard_payload() if self.mamis else {}
                return web.json_response({
                    "health": health,
                    **payload,
                })
            except Exception as e:
                return web.json_response({"error": str(e)}, status=500)

        async def get_code_status(request):
            if not self.code_operator:
                return web.json_response({"enabled": False})
            return web.json_response(await self.code_operator.get_status())

        async def list_code_tasks(request):
            if not self.code_operator:
                return web.json_response({"items": []})
            limit = int(request.query.get("limit", "20"))
            return web.json_response({"items": await self.code_operator.list_tasks(limit=limit)})

        async def create_code_task(request):
            if not self.code_operator:
                return web.json_response({"error": "Code operator unavailable"}, status=503)
            body = await request.json()
            prompt = str(body.get("prompt", "")).strip()
            if not prompt:
                return web.json_response({"error": "prompt required"}, status=400)
            task = await self.code_operator.submit_task(
                prompt,
                requested_by=str(body.get("requested_by", "dashboard")),
                mode=str(body.get("mode", "preview")),
                source=str(body.get("source", "dashboard")),
            )
            return web.json_response(task)

        async def apply_code_task(request):
            if not self.code_operator:
                return web.json_response({"error": "Code operator unavailable"}, status=503)
            task_id = request.match_info.get("task_id", "")
            task = await self.code_operator.apply_task(task_id)
            if not task:
                return web.json_response({"error": "task not found or not previewable"}, status=404)
            return web.json_response({"ok": True, "task_id": task_id})

        async def apply_routed_action(action: dict):
            """Apply a parsed command action to the running system."""
            action_type = str(action.get("type", "")).strip().lower()

            if action_type in {"watchlist_add", "watchlist_remove"}:
                raw_symbols = action.get("symbols", []) or []
                symbols = []
                for item in raw_symbols[:20]:
                    sym = re.sub(r"[^A-Za-z]", "", str(item or "").upper())
                    if not sym:
                        continue
                    if not sym.endswith("USDT"):
                        sym = sym + "USDT"
                    base = sym[:-4]
                    if 2 <= len(base) <= 10 and sym not in symbols:
                        symbols.append(sym)

                if not symbols:
                    return None

                if action_type == "watchlist_add":
                    for sym in symbols:
                        await self.db.add_user_watchlist(sym, "all", "spot")
                        await self.db.add_user_watchlist(sym, "all", "futures")
                    await _refresh_watchlist_runtime()
                    return {"type": "watchlist_add", "symbols": symbols}

                for sym in symbols:
                    await self.db.remove_user_watchlist(sym, "all", "spot")
                    await self.db.remove_user_watchlist(sym, "all", "futures")
                await _refresh_watchlist_runtime()
                return {"type": "watchlist_remove", "symbols": symbols}

            if action_type == "risk_update":
                changes = action.get("changes", {}) or {}
                applied = {}

                if "max_daily_trades" in changes:
                    Config.RISK_MAX_DAILY_TRADES = int(changes["max_daily_trades"])
                    applied["max_daily_trades"] = int(changes["max_daily_trades"])
                if "max_open_positions" in changes:
                    Config.RISK_MAX_OPEN_POSITIONS = int(changes["max_open_positions"])
                    applied["max_open_positions"] = int(changes["max_open_positions"])
                if "max_daily_loss_pct" in changes:
                    Config.RISK_MAX_DAILY_LOSS_PCT = float(changes["max_daily_loss_pct"])
                    applied["max_daily_loss_pct"] = float(changes["max_daily_loss_pct"])
                if "max_drawdown_pct" in changes:
                    Config.RISK_MAX_DRAWDOWN_PCT = float(changes["max_drawdown_pct"])
                    applied["max_drawdown_pct"] = float(changes["max_drawdown_pct"])

                if applied:
                    return {"type": "risk_update", "changes": applied}
                return None

            if action_type == "master_directive_update":
                directive_text = str(action.get("text", "")).strip()
                if directive_text:
                    await get_directive_store().set_master_directive(directive_text)
                    return {"type": "master_directive_update", "text": directive_text[:200]}
                return None

            if action_type == "system_mode_update":
                target_mode = str(action.get("mode", "")).strip().upper()
                if not self.state_tracker or not target_mode:
                    return None
                await self.state_tracker.set_mode(target_mode)
                return {
                    "type": "system_mode_update",
                    "mode": self.state_tracker.get_mode(),
                    "forced": bool(self.state_tracker.state.get("forced_mode")),
                }

            if action_type == "cleanup_run":
                dry_run = bool(action.get("dry_run", True))
                report = self.cleanup_module.cleanup(dry_run=dry_run)
                await self.event_bus.publish(Event(
                    type=EventType.CLEANUP_COMPLETED,
                    source="cleanup_module",
                    data=report,
                ))
                return {
                    "type": "cleanup_run",
                    "dry_run": dry_run,
                    "stale_count": len(report.get("stale_manifests", [])),
                    "deleted_count": len(report.get("deleted", [])),
                    "active_models": report.get("active_models", []),
                }

            if action_type == "system_diagnostic":
                summary = await self.db.get_dashboard_summary()
                scout_health, strategist_health, ghost_health, auditor_health, pm_health = await asyncio.gather(
                    self.scout.health_check(),
                    self.strategist.health_check(),
                    self.ghost_simulator.health_check(),
                    self.auditor.health_check(),
                    self.pattern_matcher.health_check(),
                    return_exceptions=True,
                )
                components = {
                    "scout": getattr(scout_health, "get", lambda *_: False)("healthy", False) if not isinstance(scout_health, Exception) else False,
                    "strategist": getattr(strategist_health, "get", lambda *_: False)("healthy", False) if not isinstance(strategist_health, Exception) else False,
                    "ghost": getattr(ghost_health, "get", lambda *_: False)("healthy", False) if not isinstance(ghost_health, Exception) else False,
                    "auditor": getattr(auditor_health, "get", lambda *_: False)("healthy", False) if not isinstance(auditor_health, Exception) else False,
                    "pattern_matcher": getattr(pm_health, "get", lambda *_: False)("healthy", False) if not isinstance(pm_health, Exception) else False,
                }
                return {
                    "type": "system_diagnostic",
                    "system_mode": self.state_tracker.get_mode() if self.state_tracker else self._system_mode,
                    "llm_model": self._last_known_llm_model,
                    "llm_ok": self._llm_available,
                    "components": components,
                    "summary": {
                        "active_signals": summary.get("active_signals", 0),
                        "open_simulations": summary.get("open_simulations", 0),
                        "total_pnl": summary.get("total_pnl", 0),
                        "win_rate": summary.get("win_rate", 0),
                    },
                }

            if action_type == "symbol_analysis":
                symbol = re.sub(r"[^A-Za-z]", "", str(action.get("symbol", "")).upper())
                if not symbol:
                    return None
                if not symbol.endswith("USDT"):
                    symbol = symbol + "USDT"

                recent_trades = await self.db.get_recent_trades(symbol, limit=5)
                recent_movements = await self.db.get_recent_movements(symbol, hours=24)
                pattern_analysis = await self.pattern_matcher.deep_analyze_symbol(symbol) if self.pattern_matcher else {}
                latest_price = float(recent_trades[0]["price"]) if recent_trades else 0.0
                overall = pattern_analysis.get("overall_signal", {}) if isinstance(pattern_analysis, dict) else {}

                return {
                    "type": "symbol_analysis",
                    "symbol": symbol,
                    "latest_price": latest_price,
                    "recent_trade_count": len(recent_trades),
                    "recent_movement_count": len(recent_movements),
                    "overall_signal": overall,
                    "timeframes": pattern_analysis.get("timeframes", {}) if isinstance(pattern_analysis, dict) else {},
                }

            if action_type == "code_change_request":
                if not self.code_operator:
                    return None
                request_prompt = str(action.get("prompt", "")).strip()
                if not request_prompt:
                    return None
                mode = str(action.get("mode", "preview")).strip().lower()
                task = await self.code_operator.submit_task(
                    request_prompt,
                    requested_by="chat",
                    mode="apply" if mode == "apply" else "preview",
                    source="chat",
                )
                return {
                    "type": "code_change_request",
                    "task_id": task.get("id"),
                    "mode": task.get("mode"),
                    "summary": "Kod operatoru istegi siraya alindi. Gorev detaylari panelde gorunur olacak.",
                    "status": task.get("status"),
                    "selected_files": task.get("selected_files", []),
                    "clarification": task.get("clarification"),
                }

            return None

        async def route_nl_command(message: str):
            """Parse user natural-language command and route executable actions to agents/system."""
            text = (message or "").strip()
            lower = text.lower()
            draft_actions = []

            await self.event_bus.publish(Event(
                type=EventType.COMMAND_RECEIVED,
                source="chat_api",
                data={"message": text[:400]},
            ))

            # Watchlist commands
            if any(k in lower for k in ["watchlist", "izleme", "takip listesi", "coin"]):
                raw_symbols = re.findall(r"\b[A-Za-z]{2,10}(?:USDT)?\b", text)
                symbols = []
                stopwords = {
                    "WATCHLIST", "WATCHLISTE", "IZLEME", "TAKIP", "LISTESI", "LİSTESİ", "COIN",
                    "EKLE", "ADD", "SIL", "REMOVE", "CIKAR", "ÇIKAR", "KALDIR", "VE", "ILE", "İLE",
                    "LONG", "SHORT", "SPOT", "FUTURES", "RISK",
                }
                for s in raw_symbols:
                    su = s.upper()
                    if su in stopwords:
                        continue
                    if su.endswith("USDT"):
                        base = su[:-4]
                    else:
                        base = su
                    # Ignore obvious natural-language words; keep likely symbols (2-6 chars).
                    if len(base) < 2 or len(base) > 6:
                        continue
                    if not su.endswith("USDT"):
                        su = su + "USDT"
                    symbols.append(su)

                if any(k in lower for k in ["ekle", "add", "takibe al"]):
                    if symbols:
                        draft_actions.append({"type": "watchlist_add", "symbols": symbols[:20]})

                if any(k in lower for k in ["sil", "remove", "çıkar", "kaldır"]):
                    if symbols:
                        draft_actions.append({"type": "watchlist_remove", "symbols": symbols[:20]})

            # Risk commands
            if "risk" in lower:
                risk_changes = {}

                m_trades = re.search(r"(max|en fazla)?\s*(\d+)\s*(işlem|trade)", lower)
                if m_trades:
                    risk_changes["max_daily_trades"] = int(m_trades.group(2))

                m_open = re.search(r"(max|en fazla)?\s*(\d+)\s*(açık pozisyon|open position|pozisyon)", lower)
                if m_open:
                    risk_changes["max_open_positions"] = int(m_open.group(2))

                m_loss = re.search(r"(-?\d+(?:\.\d+)?)\s*%\s*(günlük zarar|daily loss)", lower)
                if m_loss:
                    risk_changes["max_daily_loss_pct"] = float(m_loss.group(1))

                m_dd = re.search(r"(-?\d+(?:\.\d+)?)\s*%\s*(drawdown|max drawdown)", lower)
                if m_dd:
                    risk_changes["max_drawdown_pct"] = float(m_dd.group(1))

                if risk_changes:
                    draft_actions.append({"type": "risk_update", "changes": risk_changes})

            # Directive commands
            if any(k in lower for k in ["directive", "direktif", "kural", "talimat"]) and ":" in text:
                directive_text = text.split(":", 1)[1].strip()
                if directive_text:
                    draft_actions.append({"type": "master_directive_update", "text": directive_text[:200]})

            if any(k in lower for k in ["diagnostik", "diagnostic", "teşhis", "teshis", "durum raporu", "sistem raporu"]):
                draft_actions.append({"type": "system_diagnostic"})

            if any(k in lower for k in ["temizle", "cleanup", "temizlik"]) and any(k in lower for k in ["model", "cache", "manifest", "artık", "artik", "stale"]):
                destructive = any(k in lower for k in ["çalıştır", "calistir", "uygula", "sil", "gerçek", "gercek"])
                draft_actions.append({"type": "cleanup_run", "dry_run": not destructive})

            mode_map = {
                "bootstrap": "BOOTSTRAP",
                "learning": "LEARNING",
                "warmup": "WARMUP",
                "production": "PRODUCTION",
                "prod": "PRODUCTION",
                "auto": "AUTO",
                "otomatik": "AUTO",
            }
            if any(k in lower for k in ["mod", "mode"]):
                for needle, mode_name in mode_map.items():
                    if needle in lower:
                        draft_actions.append({"type": "system_mode_update", "mode": mode_name})
                        break

            if any(k in lower for k in ["analiz", "incele", "degerlendir", "değerlendir", "yorumla"]):
                raw_symbols = re.findall(r"\b[A-Za-z]{2,10}(?:USDT)?\b", text)
                for raw_symbol in raw_symbols[:1]:
                    candidate = raw_symbol.upper()
                    if candidate in {"ANALIZ", "ANALİZ", "INCELE", "DEGERLENDIR", "DEĞERLENDIR", "YORUMLA", "COIN", "SEMBOL", "SİMBOL"}:
                        continue
                    draft_actions.append({"type": "symbol_analysis", "symbol": candidate})
                    break

            if any(k in lower for k in ["kod", "code", "dosya", "file", "dashboard", "api", "bug", "fix", "düzelt", "duzelt", "refactor", "component", "bileşen", "bilesen"]):
                apply_now = any(k in lower for k in ["uygula", "calistir", "hemen yap", "apply"])
                draft_actions.append({
                    "type": "code_change_request",
                    "prompt": text[:1500],
                    "mode": "apply" if apply_now else "preview",
                })

            if not draft_actions and self.chat_engine:
                try:
                    llm_interpretation = await self.chat_engine.interpret_direct_command(text)
                    for candidate in llm_interpretation.get("actions", []):
                        draft_actions.append(candidate)
                except Exception as e:
                    logger.debug(f"LLM command interpretation skipped: {e}")

            actions = []
            for action in draft_actions:
                applied = await apply_routed_action(action)
                if applied:
                    actions.append(applied)

            if actions:
                for action in actions:
                    await self.event_bus.publish(Event(
                        type=EventType.COMMAND_ROUTED,
                        source="qwen_router",
                        data=action,
                    ))
                await self.event_bus.publish(Event(
                    type=EventType.COMMAND_EXECUTED,
                    source="qwen_router",
                    data={"actions": actions, "count": len(actions)},
                ))
                return actions

            await self.event_bus.publish(Event(
                type=EventType.COMMAND_FAILED,
                source="qwen_router",
                data={"reason": "no_routable_action", "message": text[:200]},
            ))
            return []

        async def post_chat(request):
            """Qwen chat and direct command interface."""
            try:
                data = await request.json()
                message = data.get("message", "").strip()
                if not message:
                    return web.json_response({"error": "Message required"}, status=400)
                
                if not self.chat_engine:
                    return web.json_response({"error": "Chat engine not initialized"}, status=500)

                routed_actions = await route_nl_command(message)
                assistant = self.chat_engine.get_assistant_identity()
                response = (
                    self.chat_engine.build_command_response(routed_actions)
                    if routed_actions else
                    await self.chat_engine.respond(message, routed_actions=routed_actions)
                )

                payload = {
                    "success": True,
                    "message": response,
                    "assistant": assistant,
                    "routed_actions": routed_actions,
                    "timestamp": datetime.now(timezone.utc).isoformat()
                }

                async def persist_chat_log():
                    try:
                        await self.db.insert_chat_message('user', message, 'user')
                        await self.db.insert_chat_message('assistant', response, assistant['name'])
                    except Exception as db_error:
                        logger.warning(f"Chat log persistence skipped: {db_error}")

                asyncio.create_task(persist_chat_log())
                return web.json_response(payload)
            except Exception as e:
                logger.error(f"Chat error: {e}")
                return web.json_response({"error": str(e)}, status=500)

        async def get_pattern_matches(request):
            """Recent pattern match results for dashboard and mobile clients."""
            try:
                symbol = request.query.get("symbol")
                limit = int(request.query.get("limit", "50"))
                limit = max(1, min(limit, 200))

                rows = await self.db.get_recent_pattern_matches(symbol=symbol, limit=limit)

                return web.json_response({
                    "count": len(rows),
                    "symbol": symbol,
                    "items": rows,
                })
            except Exception as e:
                return web.json_response({"error": str(e)}, status=400)

        # Kill stale port binding before starting
        import socket
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.close()

        app = web.Application()
        app.router.add_get("/api/directives", get_directives)
        app.router.add_post("/api/directives", set_directive)
        app.router.add_delete("/api/directives", clear_directives)
        app.router.add_post("/api/chat", post_chat)
        app.router.add_get("/api/llm/status", get_llm_status)
        app.router.add_get("/api/llm/queue", get_queue_status)
        app.router.add_get("/api/system/resources", get_system_resources)
        app.router.add_get("/api/system/summary", get_system_summary)
        app.router.add_get("/api/system/events", get_event_log)
        app.router.add_get("/api/mamis/status", get_mamis_status)
        app.router.add_get("/api/pattern/matches", get_pattern_matches)
        app.router.add_get("/api/code/status", get_code_status)
        app.router.add_get("/api/code/tasks", list_code_tasks)
        app.router.add_post("/api/code/tasks", create_code_task)
        app.router.add_post("/api/code/tasks/{task_id}/apply", apply_code_task)
        app.router.add_post("/api/control/execute", execute_control)

        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "0.0.0.0", 3002)
        await site.start()
        logger.info("📡 Directive API server running on port 3002")

        # Keep alive
        while self.running:
            await asyncio.sleep(5)

        await runner.cleanup()

async def main():
    orchestrator = AgentOrchestrator()
    try:
        await orchestrator.initialize()
        await orchestrator.start()
    except Exception as e:
        logger.error(f"Orchestrator failed: {e}")
        raise
    finally:
        await orchestrator.stop()

if __name__ == "__main__":
    asyncio.run(main())
