#!/usr/bin/env python3
import asyncio
import logging
import os
import json
import time
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
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
from storage_manager import init_storage_manager, get_storage_manager
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


def _safe_call(fn, default=None):
    """Invoke a zero-arg callable, swallow exceptions, return default on failure."""
    try:
        return fn()
    except Exception as exc:
        logger.warning(f"_safe_call: {getattr(fn, '__qualname__', fn)} failed: {exc}")
        return default


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
        self.storage_manager = None  # Akıllı depolama yöneticisi
        self.running = False
        self._agent_restart_counts: dict = {}
        self._last_signal_cleanup_ts = 0.0
        self._max_restarts = 50
        self._system_mode = "initializing"  # initializing | healthy | degraded
        self._llm_available = False
        self._last_known_llm_model: str = os.getenv("QUENBOT_LLM_MODEL", "gemma-3-12b-it")
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
        # MAMIS veto per-symbol cooldown — aynı sembol için tekrarlayan veto spam'ini önle
        self._mamis_veto_cooldown_seconds = float(os.getenv("QUENBOT_MAMIS_VETO_COOLDOWN_SECONDS", "5.0"))
        self._mamis_last_veto_at: dict[str, float] = {}
        # Günlük sinyal limiti - her coin için günde max 4 sinyal
        self._max_daily_signals_per_symbol = int(os.getenv("QUENBOT_MAX_DAILY_SIGNALS_PER_SYMBOL", "4"))
        self._daily_signal_timestamps: dict[str, list[float]] = {}  # {symbol: [timestamp1, timestamp2, ...]}
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
        eta_minutes = int(meta.get("estimated_duration_to_target_minutes", 60) or 60)
        horizon_ok = 60 <= eta_minutes <= 1440
        eligible = (
            approved
            and self._normalize_target_pct(target_pct) >= 0.02
            and horizon_ok
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

        # 5.7 StorageManager — Akıllı Veri Yönetimi ve 70GB Pruning
        try:
            self.storage_manager = await init_storage_manager(self.db.pool)
            await self.storage_manager.start()
            storage_status = self.storage_manager.get_status()
            logger.info(f"📦 StorageManager initialized (threshold={storage_status['threshold_gb']}GB)")
            startup_report["components"]["storage_manager"] = {"status": "ok", **storage_status}
        except Exception as e:
            logger.warning(f"⚠ StorageManager initialization failed: {e}")
            startup_report["components"]["storage_manager"] = {"status": "degraded", "reason": str(e)}

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

        # 10.b Enhanced intelligence stack (microstructure, HMM, fingerprint,
        #      meta-labeler, bandit, conformal, drift, loss autopsy).
        await self._bootstrap_enhanced_intelligence()

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

    async def _bootstrap_enhanced_intelligence(self) -> None:
        """Microstructure + HMM rejim + iceberg/spoof + meta-labeler + bandit + conformal + drift + otopsi."""
        try:
            from microstructure import get_microstructure_engine
            from hmm_regime import get_hmm_detector
            from iceberg_detector import get_iceberg_detector
            from meta_labeler import get_meta_labeler
            from thompson_bandit import get_thompson_bandit
            from conformal import get_conformal
            from alpha_drift_monitor import get_drift_monitor
            from loss_autopsy import get_loss_autopsy
        except Exception as e:
            logger.warning(f"Enhanced intelligence imports failed: {e}")
            return

        self.micro_engine = get_microstructure_engine(self.event_bus)
        self.hmm_detector = get_hmm_detector(self.event_bus)
        self.iceberg = get_iceberg_detector(self.event_bus)
        self.meta_labeler = get_meta_labeler()
        self.bandit = get_thompson_bandit()
        self.conformal = get_conformal(alpha=0.1)
        self.drift_monitor = get_drift_monitor(self.event_bus)
        self.loss_autopsy = get_loss_autopsy(self.db)

        await self.bandit.load(self.db)

        # wire event subscriptions
        bus = self.event_bus
        bus.subscribe(EventType.ORDER_BOOK_UPDATE, self.micro_engine.on_order_book)
        bus.subscribe(EventType.ORDER_BOOK_UPDATE, self.iceberg.on_order_book)
        bus.subscribe(EventType.SCOUT_PRICE_UPDATE, self.micro_engine.on_trade)
        bus.subscribe(EventType.SCOUT_PRICE_UPDATE, self.hmm_detector.on_trade)

        logger.info(
            "🧬 Enhanced intelligence online: microstructure, HMM, fingerprint, "
            "meta-labeler, bandit, conformal, drift, loss-autopsy"
        )

        # ── Intel Upgrade (Phase 1): feature_store + OFI + multi-horizon + confluence ──
        await self._bootstrap_intel_upgrade()

        # Cold-start meta-labeler fit in background (no-op if insufficient samples)
        asyncio.create_task(self._refit_meta_labeler())
        # Periodic retrain every 10 minutes so the labeler leaves "pending"
        # as soon as enough history accumulates.
        asyncio.create_task(self._meta_labeler_trainer_loop())

    async def _bootstrap_intel_upgrade(self) -> None:
        """Phase 1 pre-move detection stack. Flag'lerle guarded, additive.

        Sırayla:
          1) FeatureStore (Parquet + DuckDB) — diğer modüller bunu kullanır
          2) OrderFlowImbalance (L1 orderbook subscribe)
          3) MultiHorizonSignatures (trade stream subscribe)
          4) ConfluenceEngine + periyodik 1Hz publish task
        """
        self.feature_store = None
        self.ofi_engine = None
        self.multi_horizon_engine = None
        self.confluence_engine = None
        self._confluence_publisher_task = None

        if not getattr(Config, "FEATURE_STORE_ENABLED", False):
            logger.info("⏭️  Intel upgrade devre dışı (FEATURE_STORE_ENABLED=False)")
            return

        try:
            from feature_store import get_feature_store
            self.feature_store = get_feature_store(
                root=Config.FEATURE_STORE_PATH,
                flush_seconds=Config.FEATURE_STORE_FLUSH_SECONDS,
                flush_rows=Config.FEATURE_STORE_FLUSH_ROWS,
                queue_max=Config.FEATURE_STORE_QUEUE_MAX,
                enable_write=Config.FEATURE_STORE_WRITE,
            )
            await self.feature_store.start()
            logger.info("📦 FeatureStore aktif (path=%s)", Config.FEATURE_STORE_PATH)
        except Exception as e:
            logger.warning("FeatureStore bootstrap başarısız: %s", e)
            self.feature_store = None

        bus = self.event_bus

        if getattr(Config, "OFI_ENABLED", False):
            try:
                from order_flow_imbalance import get_ofi_engine
                self.ofi_engine = get_ofi_engine(
                    event_bus=bus,
                    feature_store=self.feature_store,
                    publish_hz=Config.OFI_PUBLISH_HZ,
                )
                bus.subscribe(EventType.ORDER_BOOK_UPDATE, self.ofi_engine.on_order_book)
                logger.info("🌊 OrderFlowImbalance engine online (Cont-Kukanov-Stoikov + Hurst)")
            except Exception as e:
                logger.warning("OFI bootstrap başarısız: %s", e)

        if getattr(Config, "MULTI_HORIZON_SIGNATURES_ENABLED", False):
            try:
                from multi_horizon_signatures import get_multi_horizon_engine
                self.multi_horizon_engine = get_multi_horizon_engine(
                    event_bus=bus,
                    feature_store=self.feature_store,
                    horizons_sec=tuple(Config.MULTI_HORIZON_WINDOWS_SEC),
                    publish_hz=Config.MULTI_HORIZON_PUBLISH_HZ,
                )
                bus.subscribe(EventType.SCOUT_PRICE_UPDATE, self.multi_horizon_engine.on_trade)
                logger.info("🧭 MultiHorizonSignatures online (ufuklar: %s)",
                            Config.MULTI_HORIZON_WINDOWS_SEC)
            except Exception as e:
                logger.warning("MultiHorizon bootstrap başarısız: %s", e)

        if getattr(Config, "CONFLUENCE_ENABLED", False):
            try:
                from confluence_engine import get_confluence_engine
                self.confluence_engine = get_confluence_engine(
                    event_bus=bus,
                    feature_store=self.feature_store,
                    weights_path=Config.CONFLUENCE_WEIGHTS_PATH,
                    publish_hz=Config.CONFLUENCE_PUBLISH_HZ,
                )
                self._confluence_publisher_task = asyncio.create_task(
                    self._confluence_publisher_loop()
                )
                logger.info("🎯 ConfluenceEngine online (Bayesian fusion, %.1f Hz)",
                            Config.CONFLUENCE_PUBLISH_HZ)
            except Exception as e:
                logger.warning("Confluence bootstrap başarısız: %s", e)

        # ── Phase 2: Cross-Asset Graph ────────────────────────────
        self.cross_asset_engine = None
        self._cross_asset_rebuild_task = None
        if getattr(Config, "CROSS_ASSET_ENABLED", False):
            try:
                from cross_asset_graph import get_cross_asset_engine
                symbols_init = list(getattr(Config, "WATCHLIST", []) or []) or list(
                    getattr(Config, "TRADING_PAIRS", []) or []
                )
                self.cross_asset_engine = get_cross_asset_engine(
                    event_bus=bus,
                    feature_store=self.feature_store,
                    symbols=symbols_init,
                    step_sec=Config.CROSS_ASSET_LAG_STEP_SEC,
                    history_sec=Config.CROSS_ASSET_HISTORY_SEC,
                    max_lag_sec=Config.CROSS_ASSET_MAX_LAG_SEC,
                    min_samples=Config.CROSS_ASSET_MIN_SAMPLES,
                    min_edge=Config.CROSS_ASSET_MIN_EDGE_STRENGTH,
                    rebuild_interval_sec=int(Config.CROSS_ASSET_REBUILD_INTERVAL_MIN) * 60,
                    alert_cooldown_sec=Config.CROSS_ASSET_ALERT_COOLDOWN_SEC,
                    leader_min_bps=Config.CROSS_ASSET_LEADER_MIN_MOVE_BPS,
                    graph_path=Config.CROSS_ASSET_GRAPH_PATH,
                )
                bus.subscribe(EventType.SCOUT_PRICE_UPDATE, self.cross_asset_engine.on_price_update)
                self._cross_asset_rebuild_task = asyncio.create_task(
                    self.cross_asset_engine.rebuild_loop()
                )
                logger.info(
                    "🕸️  CrossAssetGraph online (step=%ds, hist=%ds, rebuild=%dm)",
                    Config.CROSS_ASSET_LAG_STEP_SEC,
                    Config.CROSS_ASSET_HISTORY_SEC,
                    Config.CROSS_ASSET_REBUILD_INTERVAL_MIN,
                )
            except Exception as e:
                logger.warning("CrossAssetGraph bootstrap başarısız: %s", e)

        # ── Phase 3: Fast Brain + Decision Router ─────────────────
        self.fast_brain_engine = None
        self.decision_router = None
        if getattr(Config, "FAST_BRAIN_ENABLED", False):
            try:
                from fast_brain import get_fast_brain_engine
                self.fast_brain_engine = get_fast_brain_engine(
                    model_path=Config.FAST_BRAIN_MODEL_PATH,
                    calibration_path=Config.FAST_BRAIN_CALIBRATION_PATH,
                    t_high=Config.FAST_BRAIN_T_HIGH,
                    t_low=Config.FAST_BRAIN_T_LOW,
                    min_features=Config.FAST_BRAIN_MIN_FEATURES,
                    allow_confluence_fallback=Config.FAST_BRAIN_ALLOW_CONFLUENCE_FALLBACK,
                    event_bus=bus,
                )
                if self.fast_brain_engine.enabled:
                    logger.info(
                        "🧠 FastBrain online (t_high=%.2f, t_low=%.2f, model=%s)",
                        Config.FAST_BRAIN_T_HIGH, Config.FAST_BRAIN_T_LOW,
                        Config.FAST_BRAIN_MODEL_PATH,
                    )
                else:
                    logger.info("🧠 FastBrain flag=ON ama model dosyası yok — dormant")
            except Exception as e:
                logger.warning("FastBrain bootstrap başarısız: %s", e)

        if getattr(Config, "DECISION_ROUTER_ENABLED", False):
            try:
                from decision_router import get_decision_router
                self.decision_router = get_decision_router(
                    shadow=bool(getattr(Config, "DECISION_ROUTER_SHADOW", True)),
                    log_path=Config.DECISION_ROUTER_LOG_PATH,
                    max_log_rows=Config.DECISION_ROUTER_MAX_LOG_ROWS,
                    t_high=Config.FAST_BRAIN_T_HIGH,
                    t_low=Config.FAST_BRAIN_T_LOW,
                    event_bus=bus,
                )
                logger.info(
                    "🧭 DecisionRouter online (shadow=%s, log=%s)",
                    self.decision_router.shadow, Config.DECISION_ROUTER_LOG_PATH,
                )
            except Exception as e:
                logger.warning("DecisionRouter bootstrap başarısız: %s", e)

        # GemmaDecisionCore'a Phase 3 referanslarını ilet (shadow hook için)
        try:
            if getattr(self, "decision_core", None) is not None:
                self.decision_core.fast_brain_engine = self.fast_brain_engine
                self.decision_core.decision_router = self.decision_router
        except Exception as e:
            logger.debug("decision_core Phase 3 wiring skip: %s", e)

        # ── Phase 4: Online Learning Evaluator ────────────────────
        self.online_learning = None
        self._online_learning_task = None
        if getattr(Config, "ONLINE_LEARNING_ENABLED", False):
            try:
                # Phase 4 Finalization — DB-backed counterfactual store.
                # create_counterfactual_table idempotent, hata halinde
                # JSONL-only moda gracefully dusulur.
                db_ref = getattr(self, "db", None)
                persist_db = bool(getattr(Config, "ONLINE_LEARNING_PERSIST_DB", False)) and db_ref is not None
                if persist_db:
                    try:
                        await db_ref.create_counterfactual_table()
                    except Exception as exc:
                        logger.warning("counterfactual table bootstrap skipped: %s", exc)
                        persist_db = False
                from online_learning import get_online_learning_evaluator
                self.online_learning = get_online_learning_evaluator(
                    log_path=Config.DECISION_ROUTER_LOG_PATH,
                    horizon_min=Config.ONLINE_LEARNING_HORIZON_MIN,
                    interval_min=Config.ONLINE_LEARNING_INTERVAL_MIN,
                    min_samples=Config.ONLINE_LEARNING_MIN_SAMPLES,
                    state_path=Config.ONLINE_LEARNING_STATE_PATH,
                    event_bus=self.event_bus,
                    database=db_ref if persist_db else None,
                    db_offset_path=getattr(Config, "ONLINE_LEARNING_DB_OFFSET_PATH",
                                           "python_agents/.online_learning_db_offset.json"),
                    persist_db=persist_db,
                )
                self._online_learning_task = self.online_learning.start()
                logger.info(
                    "📈 OnlineLearning online (interval=%dm, horizon=%dm, db_persist=%s)",
                    Config.ONLINE_LEARNING_INTERVAL_MIN,
                    Config.ONLINE_LEARNING_HORIZON_MIN,
                    persist_db,
                )
            except Exception as e:
                logger.warning("OnlineLearning bootstrap başarısız: %s", e)

        # ── Phase 5 Finalization: Safety Net ─────────────────────
        # SAFETY_NET_ENABLED default OFF. Flag acikken bile sadece izleyici
        # olarak baslar; sentinel varsa bootta fast_brain disable kalir.
        self.safety_net = None
        self._safety_net_task = None
        if getattr(Config, "SAFETY_NET_ENABLED", False):
            try:
                from safety_net import get_safety_net
                self.safety_net = get_safety_net(
                    event_bus=self.event_bus,
                    config=Config,
                    database=getattr(self, "db", None),
                    feature_store=getattr(self, "feature_store", None),
                    baseline_path=getattr(Config, "SAFETY_NET_BASELINE_PATH",
                                          "python_agents/.safety_net_baseline.json"),
                    trip_sentinel_path=getattr(Config, "SAFETY_NET_TRIP_SENTINEL",
                                               "python_agents/.safety_net_trip.json"),
                    brier_tol=getattr(Config, "SAFETY_NET_BRIER_TOL", 1.25),
                    hitrate_tol=getattr(Config, "SAFETY_NET_HITRATE_TOL", 0.80),
                    degradation_window_min=getattr(Config, "SAFETY_NET_DEGRADATION_WINDOW_MIN", 120),
                    drift_sigma=getattr(Config, "SAFETY_NET_CONFLUENCE_DRIFT_SIGMA", 3.0),
                    fs_failure_tol=getattr(Config, "SAFETY_NET_FS_FAILURE_TOL", 0.05),
                    bg_interval_sec=getattr(Config, "SAFETY_NET_BG_INTERVAL_SEC", 30),
                )
                self._safety_net_task = self.safety_net.start()
                logger.info("🛡️ SafetyNet online (brier_tol=%.2f hitrate_tol=%.2f)",
                            Config.SAFETY_NET_BRIER_TOL, Config.SAFETY_NET_HITRATE_TOL)
            except Exception as e:
                logger.warning("SafetyNet bootstrap başarısız: %s", e)

        # ── Phase 5: Metrics Exporter ─────────────────────────────
        self.metrics_exporter = None
        if getattr(Config, "METRICS_EXPORTER_ENABLED", False):
            try:
                from metrics_exporter import get_metrics_exporter
                self.metrics_exporter = get_metrics_exporter(
                    port=Config.METRICS_EXPORTER_PORT,
                )
                # register all intel modules that expose metrics()
                for name, obj in [
                    ("feature_store", getattr(self, "feature_store", None)),
                    ("ofi", getattr(self, "ofi_engine", None)),
                    ("multi_horizon", getattr(self, "multi_horizon_engine", None)),
                    ("confluence", getattr(self, "confluence_engine", None)),
                    ("cross_asset", getattr(self, "cross_asset_engine", None)),
                    ("fast_brain", getattr(self, "fast_brain_engine", None)),
                    ("decision_router", getattr(self, "decision_router", None)),
                    ("online_learning", getattr(self, "online_learning", None)),
                ]:
                    if obj is not None and hasattr(obj, "metrics"):
                        self.metrics_exporter.register(name, obj.metrics)
                # safety_net is registered separately because it may boot
                # under different flags; treat its absence as soft-miss.
                sn = getattr(self, "safety_net", None)
                if sn is not None and hasattr(sn, "metrics"):
                    try:
                        self.metrics_exporter.register("safety_net", sn.metrics)
                    except Exception:
                        pass
                await self.metrics_exporter.start()
            except Exception as e:
                logger.warning("MetricsExporter bootstrap başarısız: %s", e)

        # ── Phase 6: Oracle Stack ─────────────────────────────────
        # Hepsi DEFAULT-OFF (ORACLE_BUS_ENABLED hariç; o read-only registry).
        # Flag açılmadan singleton hiç oluşturulmaz; davranışsal hiçbir yol
        # değişmez. PR1 bu turda §9 (bus) + §1 (BOCPD); §2-§8 sonraki turda.
        self.oracle_signal_bus = None
        self._oracle_detectors: list = []
        try:
            if getattr(Config, "ORACLE_BUS_ENABLED", True):
                from oracle_signal_bus import get_oracle_signal_bus
                self.oracle_signal_bus = get_oracle_signal_bus(event_bus=self.event_bus)
                logger.info(
                    "🔮 OracleSignalBus ready (channels=%d)",
                    len(self.oracle_signal_bus.registered_channels()),
                )
        except Exception as e:
            logger.warning("OracleSignalBus bootstrap başarısız: %s", e)

        # §1 BOCPD — Bayesian Online Changepoint Detection (default OFF)
        if getattr(Config, "BOCPD_ENABLED", False):
            try:
                from bocpd_detector import get_bocpd_detector
                bocpd = get_bocpd_detector(
                    event_bus=self.event_bus,
                    feature_store=self.feature_store,
                    signal_bus=self.oracle_signal_bus,
                    hazard_lambda_sec=Config.BOCPD_HAZARD_LAMBDA_SEC,
                    min_streams=Config.BOCPD_MIN_STREAMS,
                    consensus_window_sec=Config.BOCPD_CONSENSUS_WINDOW_SEC,
                    cp_threshold=Config.BOCPD_CP_THRESHOLD,
                    run_length_truncation=Config.BOCPD_RUN_LENGTH_TRUNCATION,
                    publish_hz=Config.BOCPD_PUBLISH_HZ,
                )
                await bocpd.initialize()
                if self.oracle_signal_bus is not None:
                    self.oracle_signal_bus.register_channel(
                        bocpd.ORACLE_CHANNEL_NAME, "bocpd_detector",
                    )
                self._oracle_detectors.append(("bocpd", bocpd))
                logger.info("🧭 BOCPD detector online (λ=%.0fs)", Config.BOCPD_HAZARD_LAMBDA_SEC)
            except Exception as e:
                logger.warning("BOCPDDetector bootstrap başarısız: %s", e)

        # §2 Hawkes Kernel Fitter (default OFF)
        if getattr(Config, "HAWKES_ENABLED", False):
            try:
                from hawkes_kernel_fitter import get_hawkes_fitter
                det = get_hawkes_fitter(
                    event_bus=self.event_bus,
                    feature_store=self.feature_store,
                    signal_bus=self.oracle_signal_bus,
                    publish_hz=Config.HAWKES_PUBLISH_HZ,
                )
                await det.initialize()
                if self.oracle_signal_bus is not None:
                    self.oracle_signal_bus.register_channel(det.ORACLE_CHANNEL_NAME, "hawkes_kernel_fitter")
                self._oracle_detectors.append(("hawkes", det))
                logger.info("🧭 Hawkes detector online")
            except Exception as e:
                logger.warning("HawkesKernelFitter bootstrap başarısız: %s", e)

        # §3 LOB Thermodynamics (default OFF)
        if getattr(Config, "LOB_THERMO_ENABLED", False):
            try:
                from lob_thermodynamics import get_lob_thermodynamics
                det = get_lob_thermodynamics(
                    event_bus=self.event_bus,
                    feature_store=self.feature_store,
                    signal_bus=self.oracle_signal_bus,
                    publish_hz=Config.LOB_THERMO_PUBLISH_HZ,
                )
                await det.initialize()
                if self.oracle_signal_bus is not None:
                    self.oracle_signal_bus.register_channel(det.ORACLE_CHANNEL_NAME, "lob_thermodynamics")
                self._oracle_detectors.append(("lob_thermo", det))
                logger.info("🧭 LOB Thermodynamics detector online")
            except Exception as e:
                logger.warning("LOBThermodynamics bootstrap başarısız: %s", e)

        # §4 Wasserstein Drift (default OFF)
        if getattr(Config, "WASSERSTEIN_ENABLED", False):
            try:
                from wasserstein_drift import get_wasserstein_drift
                det = get_wasserstein_drift(
                    event_bus=self.event_bus,
                    feature_store=self.feature_store,
                    signal_bus=self.oracle_signal_bus,
                    publish_hz=Config.WASSERSTEIN_PUBLISH_HZ,
                )
                await det.initialize()
                if self.oracle_signal_bus is not None:
                    self.oracle_signal_bus.register_channel(det.ORACLE_CHANNEL_NAME, "wasserstein_drift")
                self._oracle_detectors.append(("wasserstein", det))
                logger.info("🧭 Wasserstein detector online")
            except Exception as e:
                logger.warning("WassersteinDrift bootstrap başarısız: %s", e)

        # §5 Path Signature Engine (default OFF)
        if getattr(Config, "PATH_SIGNATURE_ENABLED", False):
            try:
                from path_signature_engine import get_path_signature
                det = get_path_signature(
                    event_bus=self.event_bus,
                    feature_store=self.feature_store,
                    signal_bus=self.oracle_signal_bus,
                    publish_hz=Config.PATH_SIG_PUBLISH_HZ,
                )
                await det.initialize()
                if self.oracle_signal_bus is not None:
                    self.oracle_signal_bus.register_channel(det.ORACLE_CHANNEL_NAME, "path_signature_engine")
                self._oracle_detectors.append(("path_signature", det))
                logger.info("🧭 Path Signature detector online")
            except Exception as e:
                logger.warning("PathSignatureEngine bootstrap başarısız: %s", e)

        # §6 Mirror Flow Analyzer (default OFF)
        if getattr(Config, "MIRROR_FLOW_ENABLED", False):
            try:
                from mirror_flow_analyzer import get_mirror_flow
                det = get_mirror_flow(
                    event_bus=self.event_bus,
                    feature_store=self.feature_store,
                    signal_bus=self.oracle_signal_bus,
                )
                await det.initialize()
                if self.oracle_signal_bus is not None:
                    self.oracle_signal_bus.register_channel(det.ORACLE_CHANNEL_NAME, "mirror_flow_analyzer")
                self._oracle_detectors.append(("mirror_flow", det))
                logger.info("🧭 Mirror Flow detector online")
            except Exception as e:
                logger.warning("MirrorFlowAnalyzer bootstrap başarısız: %s", e)

        # §7 Topological LOB (default OFF)
        if getattr(Config, "TDA_ENABLED", False):
            try:
                from topological_lob_analyzer import get_topology
                det = get_topology(
                    event_bus=self.event_bus,
                    feature_store=self.feature_store,
                    signal_bus=self.oracle_signal_bus,
                )
                await det.initialize()
                if self.oracle_signal_bus is not None:
                    self.oracle_signal_bus.register_channel(det.ORACLE_CHANNEL_NAME, "topological_lob_analyzer")
                self._oracle_detectors.append(("topology", det))
                logger.info("🧭 Topological LOB detector online")
            except Exception as e:
                logger.warning("TopologicalLOBAnalyzer bootstrap başarısız: %s", e)

        # §8 Causal On-Chain Bridge (default OFF)
        if getattr(Config, "ONCHAIN_ENABLED", False):
            try:
                from onchain_client import get_onchain_client
                from causal_onchain_bridge import get_causal_onchain
                oc = get_onchain_client()
                await oc.start()
                det = get_causal_onchain(
                    event_bus=self.event_bus,
                    feature_store=self.feature_store,
                    signal_bus=self.oracle_signal_bus,
                    onchain_client=oc,
                )
                await det.initialize()
                if self.oracle_signal_bus is not None:
                    self.oracle_signal_bus.register_channel(det.ORACLE_CHANNEL_NAME, "causal_onchain_bridge")
                self._oracle_detectors.append(("ccm", det))
                logger.info("🧭 Causal On-Chain detector online")
            except Exception as e:
                logger.warning("CausalOnChainBridge bootstrap başarısız: %s", e)

        # §10 Factor Graph Fusion — 12 kanal → IFI (default OFF)
        self.factor_graph = None
        if getattr(Config, "FACTOR_GRAPH_ENABLED", False):
            try:
                from factor_graph_fusion import get_factor_graph
                fg = get_factor_graph(
                    event_bus=self.event_bus,
                    feature_store=self.feature_store,
                    signal_bus=self.oracle_signal_bus,
                    bp_iters=Config.FG_BP_ITER,
                    damping=Config.FG_DAMPING,
                    publish_hz=Config.FG_PUBLISH_HZ,
                )
                await fg.initialize()
                if self.oracle_signal_bus is not None:
                    self.oracle_signal_bus.register_channel(fg.ORACLE_CHANNEL_NAME, "factor_graph_fusion")
                self.factor_graph = fg
                # Publisher loop: her publish_hz aralığında aktif watchlist için fuse
                async def _fg_loop():
                    interval = 1.0 / max(0.001, float(Config.FG_PUBLISH_HZ))
                    while True:
                        try:
                            symbols = list(getattr(Config, "WATCHLIST", []) or []) or list(Config.TRADING_PAIRS)
                            for sym in symbols:
                                try:
                                    fg.maybe_publish(sym)
                                except Exception as e:
                                    logger.debug("fg publish %s skip: %s", sym, e)
                            await asyncio.sleep(interval)
                        except asyncio.CancelledError:
                            break
                        except Exception as e:
                            logger.debug("fg loop hiccup: %s", e)
                            await asyncio.sleep(1.0)
                asyncio.create_task(_fg_loop())
                logger.info("🧭 FactorGraphFusion online (iters=%d, damping=%.2f, hz=%.2f)",
                            Config.FG_BP_ITER, Config.FG_DAMPING, Config.FG_PUBLISH_HZ)
            except Exception as e:
                logger.warning("FactorGraphFusion bootstrap başarısız: %s", e)

        # §11 Qwen Oracle Brain — merkezi orkestrasyon (default OFF, shadow)
        self.oracle_brain = None
        self.oracle_rag = None
        if getattr(Config, "ORACLE_BRAIN_ENABLED", False):
            try:
                from qwen_oracle_rag import get_oracle_rag
                from qwen_oracle_brain import get_oracle_brain
                rag = get_oracle_rag(
                    collection_name=Config.ORACLE_BRAIN_REASONING_CHROMA_COLLECTION,
                    top_k=Config.ORACLE_BRAIN_RAG_TOP_K,
                )
                self.oracle_rag = rag
                brain = get_oracle_brain(
                    event_bus=self.event_bus,
                    feature_store=self.feature_store,
                    signal_bus=self.oracle_signal_bus,
                    factor_graph=self.factor_graph,
                    confluence_engine=getattr(self, "confluence_engine", None),
                    llm_bridge=getattr(self, "agent_llm_bridge", None) or getattr(self, "llm_bridge", None),
                    rag=rag,
                    symbols=list(getattr(Config, "WATCHLIST", []) or []) or list(Config.TRADING_PAIRS),
                    shadow=Config.ORACLE_BRAIN_SHADOW,
                    learn_interval_sec=Config.ORACLE_BRAIN_LEARN_INTERVAL_MIN * 60.0,
                    teach_interval_sec=Config.ORACLE_BRAIN_TEACH_INTERVAL_MIN * 60.0,
                    daily_report_hour=Config.ORACLE_BRAIN_DAILY_REPORT_HOUR,
                )
                # safety_net bağla
                try:
                    brain._safety_net = getattr(self, "safety_net", None)
                except Exception:
                    pass
                await brain.initialize()
                await brain.start()
                self.oracle_brain = brain
                logger.info("🧭 QwenOracleBrain online (shadow=%s, symbols=%d)",
                            Config.ORACLE_BRAIN_SHADOW, len(brain.symbols))
            except Exception as e:
                logger.warning("QwenOracleBrain bootstrap başarısız: %s", e)

        # §12 Runtime Supervisor (default OFF). Health aggregator + heartbeat
        # dosyası yazar; dış watchdog.sh'a tüketiciliğini verir.
        self.runtime_supervisor = None
        if getattr(Config, "RUNTIME_SUPERVISOR_ENABLED", False):
            try:
                from runtime_supervisor import get_runtime_supervisor
                sup = get_runtime_supervisor(
                    status_path=Config.RUNTIME_STATUS_PATH,
                    heartbeat_path=Config.WATCHDOG_HEARTBEAT_PATH if getattr(Config, "WATCHDOG_ENABLED", False) else None,
                    interval_sec=Config.RUNTIME_HEALTH_CHECK_INTERVAL_SEC,
                    max_restart_attempts=Config.RUNTIME_MAX_RESTART_ATTEMPTS,
                )
                # Register all major components (lambda ensures late-binding)
                sup.register("feature_store", lambda: getattr(self, "feature_store", None))
                sup.register("confluence", lambda: getattr(self, "confluence_engine", None))
                sup.register("decision_router", lambda: getattr(self, "decision_router", None))
                sup.register("safety_net", lambda: getattr(self, "safety_net", None))
                sup.register("oracle_signal_bus", lambda: getattr(self, "oracle_signal_bus", None))
                sup.register("factor_graph", lambda: getattr(self, "factor_graph", None))
                sup.register("oracle_brain", lambda: getattr(self, "oracle_brain", None))
                for name, det in list(getattr(self, "_oracle_detectors", []) or []):
                    sup.register(f"detector_{name}", (lambda d=det: d))
                await sup.start()
                self.runtime_supervisor = sup
                logger.info("🧭 RuntimeSupervisor online (interval=%ds)",
                            Config.RUNTIME_HEALTH_CHECK_INTERVAL_SEC)
            except Exception as e:
                logger.warning("RuntimeSupervisor bootstrap başarısız: %s", e)

    async def _confluence_publisher_loop(self) -> None:
        """Aktif watchlist için periyodik confluence score publish."""
        if not self.confluence_engine:
            return
        hz = max(0.1, float(getattr(Config, "CONFLUENCE_PUBLISH_HZ", 1.0)))
        interval = 1.0 / hz
        while True:
            try:
                symbols = list(getattr(Config, "WATCHLIST", []) or []) or list(Config.TRADING_PAIRS)
                for sym in symbols:
                    try:
                        await self.confluence_engine.maybe_publish(sym)
                    except Exception as e:
                        logger.debug("confluence publish %s skip: %s", sym, e)
                    # yay yayılım: iki sembol arası küçük bekleme
                    await asyncio.sleep(max(0.005, interval / max(len(symbols), 1)))
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.debug("confluence publisher hiccup: %s", e)
                await asyncio.sleep(1.0)

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
                        'label': '1h',
                        'eta_minutes': 60,
                        'target_pct': round(target_pct, 6),
                        'target_price': round(current_price * (1.0 + target_pct if trade_direction == 'long' else 1.0 - target_pct), 8),
                        'strength': round(horizon_strength, 4),
                    }]
                    for label, eta_minutes, multiplier, required_strength in [
                        ('2h', 120, 1.35, 0.42),
                        ('4h', 240, 1.7, 0.54),
                        ('8h', 480, 2.0, 0.68),
                        ('12h', 720, 2.2, 0.78),
                        ('24h', 1440, 2.45, 0.88),
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

                    # ─── GÜNLÜK SİNYAL LİMİTİ ─── (max 4 sinyal/gün/coin)
                    now_ts = time.time()
                    one_day_ago = now_ts - 86400  # 24 saat
                    if symbol not in self._daily_signal_timestamps:
                        self._daily_signal_timestamps[symbol] = []
                    # Eski sinyalleri temizle (24 saatten önce)
                    self._daily_signal_timestamps[symbol] = [
                        ts for ts in self._daily_signal_timestamps[symbol] if ts > one_day_ago
                    ]
                    # Günlük limit kontrolü
                    if len(self._daily_signal_timestamps[symbol]) >= self._max_daily_signals_per_symbol:
                        logger.info(
                            f"🚫 Daily signal limit reached: {symbol} has {len(self._daily_signal_timestamps[symbol])}/{self._max_daily_signals_per_symbol} signals today"
                        )
                        return

                    pattern_window = int(time.time() // max(self._pattern_signal_window_seconds, 1))
                    if self._last_pattern_signal_window.get(symbol) == pattern_window:
                        return
                    self._last_pattern_signal_window[symbol] = pattern_window
                    # Günlük sayaca ekle
                    self._daily_signal_timestamps[symbol].append(now_ts)

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

        # Taban %2; üst sınır kullanıcı talebiyle kaldırıldı — volatiliteye bağlı hedef
        # %2-%50 arası (matematiksel emniyet), %2 altı sinyal olmaz.
        target_pct = max(0.02, min(0.50, estimated_volatility * 6.0 + 0.02))
        mamis_target_candidate = self._is_mamis_target_candidate(confidence, target_pct, estimated_volatility)
        if not mamis_target_candidate:
            # Per-symbol cooldown: aynı sembolü cooldown süresi geçmeden tekrar loglama
            now_mono = time.monotonic()
            last_veto = self._mamis_last_veto_at.get(symbol, 0.0)
            if now_mono - last_veto >= self._mamis_veto_cooldown_seconds:
                self._mamis_last_veto_at[symbol] = now_mono
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
            self._resilient_task("HorizonTracker", self._horizon_outcome_tracker),
            self._resilient_task("HeartbeatPulse", self._agent_heartbeat_pulse),
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
            if self.storage_manager:
                await self.storage_manager.stop()
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

    # ═══════════════════════════════════════════════════════════════════
    # HORIZON OUTCOME TRACKER — 15m/1h/4h hedef süre takibi
    # ═══════════════════════════════════════════════════════════════════
    async def _agent_heartbeat_pulse(self):
        """Independent, lightweight task that reads agent_heartbeat table and
        re-broadcasts AGENT_HEARTBEAT events to the event bus every 20s so the
        Inter-Agent Terminal always shows every agent, decoupled from the heavy
        _health_monitor loop."""
        logger.info("💓 Agent heartbeat pulse started — 20s interval")
        while self.running:
            try:
                await asyncio.sleep(20)
                rows = []
                try:
                    rows = await self.db.fetch(
                        "SELECT agent_name, status, metadata, "
                        "EXTRACT(EPOCH FROM (NOW() - last_heartbeat))::float AS age_seconds "
                        "FROM agent_heartbeat ORDER BY agent_name"
                    )
                except Exception:
                    rows = []
                for row in rows or []:
                    try:
                        agent_name = row.get("agent_name") if isinstance(row, dict) else row["agent_name"]
                        status = row.get("status") if isinstance(row, dict) else row["status"]
                        age = float(row.get("age_seconds") if isinstance(row, dict) else row["age_seconds"] or 0)
                        metadata = row.get("metadata") if isinstance(row, dict) else row["metadata"]
                        if isinstance(metadata, str):
                            try:
                                metadata = json.loads(metadata)
                            except Exception:
                                metadata = {}
                        metadata = metadata or {}
                        summary = {
                            k: v for k, v in metadata.items()
                            if isinstance(v, (int, float, bool, str)) or v is None
                        }
                        # Keep summary compact
                        if len(summary) > 8:
                            summary = dict(list(summary.items())[:8])
                        healthy = bool(status == "running" and age < 180)
                        await self.event_bus.publish(Event(
                            type=EventType.AGENT_HEARTBEAT,
                            source=str(agent_name),
                            data={
                                "agent": str(agent_name),
                                "status": str(status or "unknown"),
                                "age_seconds": round(age, 1),
                                "healthy": healthy,
                                "summary": summary,
                            },
                            priority=0,
                        ))
                    except Exception as e:
                        logger.debug(f"heartbeat pulse row skipped: {e}")
            except Exception as e:
                logger.debug(f"heartbeat pulse cycle error: {e}")

    async def _horizon_outcome_tracker(self):
        """
        Periyodik olarak aktif sinyallerin hedef zaman dilimlerini (15m/1h/4h) kontrol eder.
        Süresi dolan horizon'lar değerlendirilir (hit/missed), sonuçlar Brain'e öğrenme
        verisi olarak gönderilir. Tüm horizon'lar tamamlandığında sinyal tahtadan kaldırılır.
        """
        logger.info("🎯 Horizon outcome tracker started — monitoring 15m/1h/4h targets")
        while self.running:
            try:
                await asyncio.sleep(30)
                signals = await self.db.get_signals_for_horizon_check()
                if not signals:
                    continue

                tracker = get_market_tracker()
                now = datetime.utcnow()

                for signal in signals:
                    try:
                        await self._evaluate_signal_horizons(signal, tracker, now)
                    except Exception as e:
                        logger.debug(f"Horizon eval error signal #{signal.get('id')}: {e}")

            except Exception as e:
                logger.error(f"Horizon tracker cycle error: {e}")
                await asyncio.sleep(30)

    async def _evaluate_signal_horizons(self, signal: dict, tracker, now: datetime):
        """Tek bir sinyalin tüm horizon'larını değerlendir."""
        metadata = signal.get('metadata', {})
        if isinstance(metadata, str):
            try:
                metadata = json.loads(metadata)
            except (json.JSONDecodeError, TypeError):
                return

        horizons = metadata.get('target_horizons', [])
        if not horizons:
            return

        signal_time = signal.get('timestamp')
        if isinstance(signal_time, str):
            signal_time = datetime.fromisoformat(signal_time.replace('Z', '+00:00')).replace(tzinfo=None)
        if not signal_time:
            return

        entry_price = float(metadata.get('entry_price', 0) or signal.get('price', 0))
        if entry_price <= 0:
            return

        direction = metadata.get('position_bias', 'long')
        symbol = signal['symbol']
        current_price = tracker.get_price(symbol)
        if not current_price or current_price <= 0:
            return

        updated = False
        all_evaluated = True

        for h in horizons:
            if h.get('status', 'active') != 'active':
                continue  # already evaluated

            eta = int(h.get('eta_minutes', 15))
            deadline = signal_time + timedelta(minutes=eta)

            if now < deadline:
                all_evaluated = False
                continue

            # ── Horizon süresi doldu — değerlendir ──
            target_price = float(h.get('target_price', 0))
            if direction == 'long':
                actual_change_pct = (current_price - entry_price) / max(entry_price, 1e-8)
                hit = current_price >= target_price
            else:
                actual_change_pct = (entry_price - current_price) / max(entry_price, 1e-8)
                hit = current_price <= target_price

            h['status'] = 'hit' if hit else 'missed'
            h['evaluated_at'] = now.isoformat() + 'Z'
            h['actual_price'] = float(current_price)
            h['actual_change_pct'] = round(actual_change_pct, 6)
            updated = True

            # Brain'e per-horizon öğrenme verisi
            if self.brain:
                signal_type = f"{signal.get('signal_type', 'unknown')}_{h['label']}"
                self.brain.update_learning(signal_type, hit, actual_change_pct * 100)

            # Publish to event bus so inter-agent terminal reflects the resolution
            try:
                await self.event_bus.publish(Event(
                    type=EventType.HORIZON_RESOLVED,
                    source="horizon_tracker",
                    data={
                        'signal_id': signal.get('id'),
                        'symbol': symbol,
                        'direction': direction,
                        'label': h['label'],
                        'eta_minutes': int(h.get('eta_minutes', 0)),
                        'hit': bool(hit),
                        'target_price': float(target_price),
                        'actual_price': float(current_price),
                        'actual_change_pct': float(actual_change_pct),
                    },
                ))
            except Exception as _hz_pub_err:
                logger.debug(f"Horizon event publish skipped: {_hz_pub_err}")

            emoji = "✅" if hit else "❌"
            logger.info(
                f"🎯 Horizon {h['label']} {emoji} | {symbol} {direction} "
                f"| hedef=${target_price:,.2f} gerçek=${current_price:,.2f} "
                f"| değişim={actual_change_pct*100:+.2f}%"
            )

        if not updated:
            return

        patch = {'target_horizons': horizons}

        if all_evaluated:
            # Tüm horizon'lar değerlendirildi — sinyal tahtadan kaldırılır
            hits = sum(1 for h in horizons if h.get('status') == 'hit')
            total = len(horizons)
            patch['horizons_complete'] = True
            patch['horizon_summary'] = {
                'hits': hits,
                'total': total,
                'hit_rate': round(hits / max(total, 1), 2),
                'completed_at': now.isoformat() + 'Z',
            }
            await self.db.update_signal_status(signal['id'], 'expired')
            logger.info(
                f"🏁 Signal #{signal['id']} {symbol} tüm hedefler tamamlandı: "
                f"{hits}/{total} isabet — tahtadan kaldırıldı"
            )

            # Brain'e genel analiz + neden kar/zarar olduğunu analiz et
            await self._analyze_horizon_outcomes(signal, horizons)

        await self.db.update_signal_metadata(signal['id'], patch)

    async def _analyze_horizon_outcomes(self, signal: dict, horizons: list):
        """Brain'e horizon sonuçlarını raporla — neden kar/zarar olduğunu analiz et"""
        try:
            metadata = signal.get('metadata', {})
            if isinstance(metadata, str):
                metadata = json.loads(metadata)

            symbol = signal['symbol']
            direction = metadata.get('position_bias', 'long')
            hits = [h for h in horizons if h.get('status') == 'hit']
            misses = [h for h in horizons if h.get('status') == 'missed']
            was_correct = len(hits) > len(misses)
            best_actual = max((abs(h.get('actual_change_pct', 0)) for h in horizons), default=0) * 100

            # ─── Triple-barrier label on the PATH recorded by horizon tracker ───
            barrier_result = await self._compute_triple_barrier_for_signal(
                signal=signal, horizons=horizons, metadata=metadata, direction=direction,
            )

            # DB'ye öğrenme kaydı (triple-barrier enriched)
            try:
                await self.db.insert_triple_barrier_log(
                    signal_type=signal.get('signal_type', 'unknown'),
                    was_correct=was_correct,
                    pnl_pct=(best_actual if was_correct else -best_actual),
                    symbol=symbol,
                    direction=direction,
                    confidence=float(signal.get('confidence', 0) or 0),
                    barrier_hit=str(barrier_result.get('barrier_hit', 'timeout')),
                    barrier_time_s=float(barrier_result.get('barrier_time_s', 0.0)),
                    mfe_pct=float(barrier_result.get('mfe_pct', 0.0)),
                    mae_pct=float(barrier_result.get('mae_pct', 0.0)),
                    risk_adjusted_return=float(barrier_result.get('risk_adjusted_return', 0.0)),
                    context={
                        'symbol': symbol,
                        'direction': direction,
                        'entry_price': float(metadata.get('entry_price', 0)),
                        'signal_type': signal.get('signal_type', 'unknown'),
                        'horizons_hit': len(hits),
                        'horizons_missed': len(misses),
                        'horizon_details': horizons,
                        'barrier': barrier_result,
                        'entry_features': metadata.get('entry_features') or {},
                    },
                )
            except Exception as db_err:
                logger.debug(f"triple-barrier DB insert fallback: {db_err}")
                # fallback to legacy insert so nothing is lost
                await self.db.insert_learning_log(
                    signal_type=signal.get('signal_type', 'unknown'),
                    was_correct=was_correct,
                    pnl_pct=best_actual if was_correct else -best_actual,
                    context={'barrier': barrier_result, 'horizons': horizons, 'symbol': symbol},
                )

            # Bandit: strateji kolunu güncelle
            try:
                if hasattr(self, 'bandit') and self.bandit is not None:
                    self.bandit.record_outcome(
                        signal.get('signal_type', 'unknown'),
                        success=was_correct,
                        weight=1.0 + min(3.0, abs(best_actual) / 2.0),
                    )
                    await self.bandit.persist(self.db)
                    await self.event_bus.publish(Event(
                        type=EventType.BANDIT_UPDATED,
                        source='thompson_bandit',
                        data={'arm': signal.get('signal_type', 'unknown'),
                              'success': was_correct,
                              'ev': self.bandit.expected_value(signal.get('signal_type', 'unknown'))},
                    ))
            except Exception as be:
                logger.debug(f"bandit update skipped: {be}")

            # Conformal: confidence kalibrasyon kaydı
            try:
                if hasattr(self, 'conformal') and self.conformal is not None:
                    self.conformal.record(float(signal.get('confidence', 0) or 0),
                                          1 if was_correct else 0)
            except Exception as ce:
                logger.debug(f"conformal record skipped: {ce}")

            # Barrier event yayını (terminale düşsün)
            try:
                await self.event_bus.publish(Event(
                    type=EventType.BARRIER_LABELED,
                    source='triple_barrier',
                    data={
                        'signal_id': signal.get('id'),
                        'symbol': symbol,
                        'direction': direction,
                        'barrier_hit': barrier_result.get('barrier_hit'),
                        'mfe_pct': barrier_result.get('mfe_pct'),
                        'mae_pct': barrier_result.get('mae_pct'),
                        'final_return_pct': barrier_result.get('final_return_pct'),
                        'was_correct': was_correct,
                    },
                ))
            except Exception as _be2:
                logger.debug(f"barrier event skipped: {_be2}")

            # ─── Loss autopsy (yalnızca kayıp/timeout sinyaller) ───
            try:
                if hasattr(self, 'loss_autopsy') and self.loss_autopsy is not None and not was_correct:
                    current_price = float(horizons[-1].get('actual_price', 0) or metadata.get('entry_price', 0))
                    entry_features = metadata.get('entry_features') or {}
                    current_features = {}
                    try:
                        from enhanced_features import build_feature_snapshot
                        current_features = build_feature_snapshot(symbol)
                    except Exception:
                        current_features = {}
                    rec = await self.loss_autopsy.autopsy(
                        signal=signal,
                        barrier_result=barrier_result,
                        entry_context=entry_features or current_features,
                        current_context={'price': current_price, 'microstructure': current_features.get('microstructure'),
                                         'regime': current_features.get('regime'),
                                         'fingerprint': current_features.get('fingerprint')},
                    )
                    if rec is not None:
                        await self.event_bus.publish(Event(
                            type=EventType.LOSS_AUTOPSY,
                            source='loss_autopsy',
                            data={
                                'symbol': symbol, 'signal_id': signal.get('id'),
                                'loss_pct': rec.loss_pct,
                                'root_causes': rec.root_causes[:3],
                                'rule': rec.lesson_rule.get('avoid_if'),
                                'score': rec.score,
                            },
                        ))
            except Exception as ae:
                logger.debug(f"loss autopsy skipped: {ae}")

            # ─── Periyodik meta-labeler refit (her 25 tamamlanan sinyal) ───
            try:
                if hasattr(self, 'meta_labeler') and self.meta_labeler is not None:
                    total_seen = int(self.brain.prediction_accuracy.get('total', 0)) if self.brain else 0
                    if total_seen and total_seen % 25 == 0:
                        asyncio.create_task(self._refit_meta_labeler())
            except Exception as me:
                logger.debug(f"meta refit tick skipped: {me}")

            # LLM ile neden kar/zarar olduğunu analiz ettir
            bridge = get_llm_bridge()
            if bridge and await bridge.is_available():
                try:
                    analysis = await bridge.ghost_post_trade_analysis({
                        "symbol": symbol,
                        "side": direction,
                        "entry_price": float(metadata.get('entry_price', 0)),
                        "exit_price": float(horizons[-1].get('actual_price', 0)),
                        "pnl_pct": best_actual if was_correct else -best_actual,
                        "close_reason": "horizon_complete",
                        "metadata": {
                            'horizon_outcomes': horizons,
                            'confidence': float(signal.get('confidence', 0)),
                            'similarity': float(metadata.get('avg_similarity', 0) or metadata.get('similarity', 0)),
                            'barrier': barrier_result,
                        },
                        "holding_time_min": int(horizons[-1].get('eta_minutes', 240)),
                    })
                    if analysis and analysis.get("_parsed"):
                        lesson = analysis.get("lesson", "")
                        if lesson:
                            logger.info(f"🧠 Brain horizon analiz [{symbol}]: {lesson[:150]}")
                except Exception as e:
                    logger.debug(f"Horizon LLM analysis skipped: {e}")

        except Exception as e:
            logger.debug(f"Horizon analysis error: {e}")

    async def _compute_triple_barrier_for_signal(
        self, *, signal: dict, horizons: list, metadata: dict, direction: str,
    ) -> dict:
        """Horizon verilerinden triple-barrier etiketi üret (hızlı, approx path)."""
        try:
            from triple_barrier import compute_triple_barrier
            entry_price = float(metadata.get('entry_price', 0) or signal.get('price', 0))
            sig_ts = signal.get('timestamp')
            from datetime import datetime as _dt
            if isinstance(sig_ts, str):
                entry_ts = _dt.fromisoformat(sig_ts.replace('Z', '+00:00')).replace(tzinfo=None).timestamp()
            elif isinstance(sig_ts, _dt):
                entry_ts = sig_ts.timestamp()
            else:
                entry_ts = 0.0
            # path: horizon checkpointleri (eta_minutes sırasına göre)
            path = []
            for h in sorted(horizons, key=lambda x: int(x.get('eta_minutes', 0))):
                if h.get('actual_price') is None or h.get('evaluated_at') is None:
                    continue
                try:
                    eta_s = float(h.get('eta_minutes', 0)) * 60.0
                    path.append((entry_ts + eta_s, float(h['actual_price'])))
                except Exception:
                    continue
            target_pct = float(metadata.get('target_pct', 0.01) or 0.01)
            tp_pct = max(0.003, abs(target_pct))
            sl_pct = max(0.003, abs(target_pct) * 0.7)
            timeout_s = max(900.0, float(max((h.get('eta_minutes', 60) for h in horizons), default=60)) * 60.0)
            res = compute_triple_barrier(
                direction=direction, entry_price=entry_price, entry_ts=entry_ts,
                path=path, tp_pct=tp_pct, sl_pct=sl_pct, timeout_s=timeout_s,
            )
            return res.to_dict()
        except Exception as e:
            logger.debug(f"triple_barrier compute skipped: {e}")
            return {'barrier_hit': 'timeout', 'final_return_pct': 0.0, 'mfe_pct': 0.0,
                    'mae_pct': 0.0, 'risk_adjusted_return': 0.0, 'barrier_time_s': 0.0,
                    'confidence_factor': 0.0}

    async def _refit_meta_labeler(self) -> None:
        """Son 21 günün barrier-etiketli kayıtlarından meta-labeler'ı yeniden eğit."""
        try:
            rows = await self.db.fetch_meta_training_set(lookback_days=21, limit=2000)
            if not rows:
                return
            samples = []
            for r in rows:
                ctx = r.get('context') or {}
                if isinstance(ctx, str):
                    try: ctx = json.loads(ctx)
                    except Exception: ctx = {}
                entry_feats = (ctx.get('entry_features') or {})
                ms = entry_feats.get('microstructure') or {}
                reg = entry_feats.get('regime') or {}
                fv = {
                    'confidence': float(r.get('confidence', 0) or 0),
                    'obi': float(ms.get('obi', 0) or 0),
                    'vpin': float(ms.get('vpin', 0) or 0),
                    'kyle_lambda': float(ms.get('kyle_lambda', 0) or 0),
                    'aggressor_buy_ratio': float(ms.get('aggressor_buy_ratio', 0.5) or 0.5),
                    'spread_bps': float(ms.get('spread_bps', 0) or 0),
                    'trade_intensity': float(ms.get('trade_intensity', 0) or 0),
                    'regime_trend_prob': float(reg.get('trend_prob', 0) or 0),
                    'regime_vol_prob': float(reg.get('vol_prob', 0) or 0),
                    'hist_accuracy': 0.5,
                    'hist_avg_pnl': 0.0,
                }
                label = 1 if str(r.get('barrier_hit', '')) == 'tp' else (
                    1 if r.get('barrier_hit') is None and bool(r.get('was_correct')) else 0
                )
                samples.append((fv, label))
            res = self.meta_labeler.fit(samples)
            await self.event_bus.publish(Event(
                type=EventType.META_MODEL_REFIT, source='meta_labeler', data=res,
            ))
            logger.info(f"🧪 Meta-labeler refit: {res}")
        except Exception as e:
            logger.debug(f"meta refit skipped: {e}")

    async def _meta_labeler_trainer_loop(self) -> None:
        """Her 10 dakikada bir meta-labeler'ı yeniden dene."""
        while self.running:
            try:
                await asyncio.sleep(600)
                await self._refit_meta_labeler()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.debug(f"meta trainer loop: {e}")


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
                        assistant_name = self.chat_engine.get_assistant_identity()['name'] if self.chat_engine else 'SuperGemma Command'
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
                    self.db.update_heartbeat('orchestrator_feedback', 'running', {
                        'role': 'simulation_feedback_receiver',
                    }),
                    self.db.update_heartbeat('event_bus', 'running', {
                        **self.event_bus.get_stats(),
                        'role': 'central_event_dispatcher',
                    }),
                )

                # ─── Enhanced intelligence heartbeats (microstructure, HMM, fingerprint, bandit) ───
                try:
                    if hasattr(self, 'micro_engine') and self.micro_engine is not None:
                        ms_h = await self.micro_engine.health_check()
                        await self.db.update_heartbeat('microstructure',
                            'running' if ms_h.get('healthy') else 'degraded', ms_h)
                    if hasattr(self, 'hmm_detector') and self.hmm_detector is not None:
                        hm_h = await self.hmm_detector.health_check()
                        await self.db.update_heartbeat('regime_hmm',
                            'running' if hm_h.get('healthy') else 'degraded', hm_h)
                    if hasattr(self, 'iceberg') and self.iceberg is not None:
                        ib_h = await self.iceberg.health_check()
                        await self.db.update_heartbeat('fingerprint_detector',
                            'running' if ib_h.get('healthy') else 'degraded', ib_h)
                    if hasattr(self, 'loss_autopsy') and self.loss_autopsy is not None:
                        la_h = await self.loss_autopsy.health_check()
                        await self.db.update_heartbeat('loss_autopsy',
                            'running' if la_h.get('healthy') else 'degraded', la_h)
                    if hasattr(self, 'meta_labeler') and self.meta_labeler is not None:
                        ml_s = self.meta_labeler.status()
                        await self.db.update_heartbeat('meta_labeler',
                            'running' if ml_s.get('trained') else 'pending', ml_s)
                    if hasattr(self, 'bandit') and self.bandit is not None:
                        await self.db.update_heartbeat('thompson_bandit', 'running', {
                            'arms': len(self.bandit.arms),
                            'top_arm': max(self.bandit.arms.items(),
                                           key=lambda kv: kv[1]['alpha'] / (kv[1]['alpha']+kv[1]['beta']),
                                           default=('n/a', {'alpha':1,'beta':1}))[0] if self.bandit.arms else 'n/a',
                        })
                    if hasattr(self, 'conformal') and self.conformal is not None:
                        await self.db.update_heartbeat('conformal_calibrator', 'running',
                                                       self.conformal.snapshot())
                    if hasattr(self, 'drift_monitor') and self.drift_monitor is not None:
                        dm_r = await self.drift_monitor.tick()
                        dm_h = await self.drift_monitor.health_check()
                        await self.db.update_heartbeat('alpha_drift',
                            'running' if dm_h.get('healthy') else 'warning', {**dm_h, **dm_r})
                except Exception as _enh_hb_err:
                    logger.debug(f"enhanced intelligence heartbeat skipped: {_enh_hb_err}")

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

                # Keep learning_orchestrator heartbeat fresh so it doesn't appear stale
                # between promote events (which are sparse by nature).
                try:
                    lw_state = await self.db.get_bot_state('learning_watchlist') or {}
                    candidates = list(lw_state.get('candidates') or [])
                    promoted = sum(1 for c in candidates if str(c.get('status', '')) == 'promote')
                    await self.db.update_heartbeat('learning_orchestrator', 'running', {
                        'role': 'watchlist_curation',
                        'candidate_count': len(candidates),
                        'promoted_count': promoted,
                        'last_state_update': lw_state.get('updated_at'),
                    })
                except Exception as _lo_hb_err:
                    logger.debug(f"learning_orchestrator heartbeat skipped: {_lo_hb_err}")

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

                # ─── Broadcast per-agent heartbeats on event bus so all agents
                #     appear in the Inter-Agent Terminal, not just publishers. ───
                try:
                    agent_beats = [
                        ("scout", scout_health),
                        ("strategist", strategist_health),
                        ("ghost_simulator", ghost_health),
                        ("auditor", auditor_health),
                        ("pattern_matcher", pm_health),
                        ("mamis", mamis_health),
                        ("efom", efom_health),
                        ("brain", brain_status),
                        ("decision_core", decision_core_stats),
                        ("system_resources", {
                            "cpu_percent": resource_data.get("cpu_percent"),
                            "ram_percent": resource_data.get("ram_percent"),
                            "mode": self._system_mode,
                        }),
                    ]
                    for agent_name, beat in agent_beats:
                        summary = {
                            k: v for k, v in (beat or {}).items()
                            if isinstance(v, (int, float, bool, str)) or v is None
                        }
                        await self.event_bus.publish(Event(
                            type=EventType.AGENT_HEARTBEAT,
                            source=agent_name,
                            data={
                                "agent": agent_name,
                                "healthy": bool((beat or {}).get("healthy", True)),
                                "summary": summary,
                            },
                            priority=0,
                        ))
                except Exception as _hb_pub_err:
                    logger.debug(f"Heartbeat broadcast skipped: {_hb_pub_err}")

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
            """Runtime control plane for SuperGemma: directives, watchlist, risk params, model switch."""
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
                "vector_memory": _safe_call(self.vector_store.get_stats, default={"enabled": False, "healthy": False}),
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
                "vector_memory": _safe_call(self.vector_store.get_stats, default={"enabled": False, "healthy": False}),
                "redis": self.redis_bridge.get_stats() if self.redis_bridge else {},
                "cleanup": _safe_call(self.cleanup_module.scan, default={}),
                "storage_manager": self.storage_manager.get_status() if self.storage_manager else {"enabled": False},
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
            try:
                limit = int(request.rel_url.query.get("limit", "200"))
            except Exception:
                limit = 200
            include_spam = request.rel_url.query.get("include_spam", "0") in ("1", "true", "yes")
            stats = self.event_bus.get_stats(recent_limit=limit, include_spam=include_spam)
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

                m_trades = re.search(r"(max|en fazla)?\s*(\d+)\s*(i[sş]lem|trade)", lower)
                if m_trades:
                    risk_changes["max_daily_trades"] = int(m_trades.group(2))

                m_open = re.search(r"(max|en fazla)?\s*(\d+)\s*(a[çc][ıi]k\s*pozisyon|open\s*position|pozisyon)", lower)
                if m_open:
                    risk_changes["max_open_positions"] = int(m_open.group(2))

                m_loss = re.search(r"(-?\d+(?:\.\d+)?)\s*%\s*(g[üu]nl[üu]k\s*zarar|daily\s*loss)", lower)
                if m_loss:
                    risk_changes["max_daily_loss_pct"] = float(m_loss.group(1))

                m_dd = re.search(r"(-?\d+(?:\.\d+)?)\s*%\s*(drawdown|max\s*drawdown)", lower)
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
                # LLM yorumlayıcısını sadece imperatif/komut gibi görünen mesajlarda çalıştır.
                # Saf soru/sohbet mesajlarında ikinci LLM çağrısı atlanır → chat 2× hızlanır.
                command_hint = any(
                    k in lower for k in (
                        "yap", "ayarla", "değiştir", "degistir", "ekle", "sil", "kapat",
                        "aç ", "başlat", "baslat", "durdur", "güncelle", "guncelle",
                        "set ", "update", "change", "run ", "apply", "uygula", "çalıştır",
                        "calistir", "aktifleştir", "aktiflestir", "devre dışı", "devre disi",
                    )
                )
                is_question = text.endswith("?") or any(
                    q in lower for q in (
                        "nedir", "nasıl", "nasil", "neden", "kim ", "ne ", "hangi",
                        "nerede", "ne zaman", " mi ", " mı ", " mu ", " mü ",
                        "anlat", "söyle", "soyle", "açıkla", "acikla",
                    )
                )
                if command_hint and not is_question:
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
            """SuperGemma chat and direct command interface."""
            try:
                data = await request.json()
                message = data.get("message", "").strip()
                if not message:
                    return web.json_response({"error": "Message required"}, status=400)
                
                if not self.chat_engine:
                    return web.json_response({"error": "Chat engine not initialized"}, status=500)

                routed_actions = await route_nl_command(message)
                assistant = self.chat_engine.get_assistant_identity()
                # Doğal konuşma: aksiyon uygulansa bile Gemma kendi sözleriyle onaylar.
                # Robotik "Komut uygulandı:" metinleri artık kullanılmaz.
                response = await self.chat_engine.respond(message, routed_actions=routed_actions)

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

        # ─── Intel Upgrade (Phase 1) endpoints ───
        async def get_confluence_symbol(request):
            """Bir sembol için anlık confluence score + top contributors."""
            symbol = (request.match_info.get("symbol") or "").upper()
            if not symbol:
                return web.json_response({"error": "symbol required"}, status=400)
            if not getattr(self, "confluence_engine", None):
                return web.json_response({"error": "confluence disabled"}, status=503)
            try:
                # Fresh compute (aynı zamanda cache'e yazar)
                res = await self.confluence_engine.compute(symbol)
                return web.json_response(res.to_dict())
            except Exception as e:
                logger.error(f"confluence endpoint error: {e}")
                return web.json_response({"error": str(e)}, status=500)

        async def get_intel_summary(request):
            """Tüm Intel modüllerinin health özeti."""
            out: Dict[str, Any] = {}
            for name, obj in [
                ("feature_store", getattr(self, "feature_store", None)),
                ("ofi", getattr(self, "ofi_engine", None)),
                ("multi_horizon", getattr(self, "multi_horizon_engine", None)),
                ("confluence", getattr(self, "confluence_engine", None)),
                ("cross_asset", getattr(self, "cross_asset_engine", None)),
                ("fast_brain", getattr(self, "fast_brain_engine", None)),
                ("decision_router", getattr(self, "decision_router", None)),
                ("online_learning", getattr(self, "online_learning", None)),
                ("metrics_exporter", getattr(self, "metrics_exporter", None)),
                ("safety_net", getattr(self, "safety_net", None)),
            ]:
                if obj is None:
                    out[name] = {"enabled": False}
                    continue
                try:
                    h = await obj.health_check() if hasattr(obj, "health_check") else (
                        obj.status() if hasattr(obj, "status") else {}
                    )
                    m = obj.metrics() if hasattr(obj, "metrics") else {}
                    out[name] = {"enabled": True, "health": h, "metrics": m}
                except Exception as e:
                    out[name] = {"enabled": True, "error": str(e)}
            # ─── Phase 6: Oracle Stack (additive) ─────────────────
            try:
                bus = getattr(self, "oracle_signal_bus", None)
                detectors = list(getattr(self, "_oracle_detectors", []) or [])
                oracle_block: Dict[str, Any] = {
                    "enabled": bus is not None,
                    "detectors_active": len(detectors),
                    "channels_registered": (
                        len(bus.registered_channels()) if bus is not None and hasattr(bus, "registered_channels") else 0
                    ),
                    "modules": {},
                }
                for name, det in detectors:
                    try:
                        h = await det.health_check() if hasattr(det, "health_check") else {}
                        m = det.metrics() if hasattr(det, "metrics") else {}
                        oracle_block["modules"][name] = {
                            "enabled": True,
                            "channel": getattr(det, "ORACLE_CHANNEL_NAME", None),
                            "health": h,
                            "metrics": m,
                        }
                    except Exception as e:
                        oracle_block["modules"][name] = {"enabled": True, "error": str(e)}
                out["oracle"] = oracle_block
            except Exception as e:
                out["oracle"] = {"enabled": False, "error": str(e)}
            return web.json_response(out)

        # ─── Phase 6: Oracle Stack endpoints ───
        async def get_oracle_summary(request):
            """Oracle dedektör + signal bus özeti."""
            bus = getattr(self, "oracle_signal_bus", None)
            detectors = list(getattr(self, "_oracle_detectors", []) or [])
            payload: Dict[str, Any] = {
                "enabled": bus is not None,
                "detectors": [],
                "channels": [],
            }
            if bus is not None and hasattr(bus, "registered_channels"):
                try:
                    payload["channels"] = list(bus.registered_channels())
                except Exception as e:
                    payload["channels_error"] = str(e)
            for name, det in detectors:
                try:
                    h = await det.health_check() if hasattr(det, "health_check") else {}
                    m = det.metrics() if hasattr(det, "metrics") else {}
                    payload["detectors"].append({
                        "name": name,
                        "channel": getattr(det, "ORACLE_CHANNEL_NAME", None),
                        "health": h,
                        "metrics": m,
                    })
                except Exception as e:
                    payload["detectors"].append({"name": name, "error": str(e)})
            # §10 + §11 blocks
            fg = getattr(self, "factor_graph", None)
            if fg is not None:
                try:
                    payload["factor_graph"] = {
                        "enabled": True,
                        "health": await fg.health_check(),
                        "metrics": fg.metrics(),
                    }
                except Exception as e:
                    payload["factor_graph"] = {"enabled": True, "error": str(e)}
            else:
                payload["factor_graph"] = {"enabled": False}
            brain = getattr(self, "oracle_brain", None)
            if brain is not None:
                try:
                    payload["brain"] = {
                        "enabled": True,
                        "shadow": brain.shadow,
                        "health": await brain.health_check(),
                        "metrics": brain.metrics(),
                    }
                except Exception as e:
                    payload["brain"] = {"enabled": True, "error": str(e)}
            else:
                payload["brain"] = {"enabled": False}
            return web.json_response(payload)

        async def get_oracle_channels_symbol(request):
            """Bir sembol için tüm oracle kanal değerleri."""
            symbol = (request.match_info.get("symbol") or "").upper()
            if not symbol:
                return web.json_response({"error": "symbol required"}, status=400)
            bus = getattr(self, "oracle_signal_bus", None)
            detectors = list(getattr(self, "_oracle_detectors", []) or [])
            channels: Dict[str, Any] = {}
            if bus is not None and hasattr(bus, "all_snapshots"):
                try:
                    snap = bus.all_snapshots()
                    if isinstance(snap, dict):
                        sym_block = snap.get(symbol) or {}
                        if isinstance(sym_block, dict):
                            channels.update(sym_block)
                except Exception as e:
                    channels["__bus_error__"] = str(e)
            # Fallback: query detectors directly for their channel value
            for name, det in detectors:
                ch = getattr(det, "ORACLE_CHANNEL_NAME", None)
                if ch and ch not in channels:
                    try:
                        v = det.oracle_channel_value(symbol) if hasattr(det, "oracle_channel_value") else None
                        if v is not None:
                            channels[ch] = {"value": v, "source": name}
                    except Exception:
                        pass
            return web.json_response({"symbol": symbol, "channels": channels})

        async def get_oracle_detector_snapshot(request):
            """Bir dedektörün tüm sembol snapshot'ları."""
            dname = (request.match_info.get("name") or "").lower()
            if not dname:
                return web.json_response({"error": "name required"}, status=400)
            for name, det in list(getattr(self, "_oracle_detectors", []) or []):
                if name == dname:
                    try:
                        snaps = det.all_snapshots() if hasattr(det, "all_snapshots") else {}
                        return web.json_response({"name": name, "snapshots": snaps})
                    except Exception as e:
                        return web.json_response({"name": name, "error": str(e)}, status=500)
            return web.json_response({"error": f"detector '{dname}' not found"}, status=404)

        async def get_oracle_factor_graph(request):
            """§10 FactorGraph: sembol IFI + per-channel marginals."""
            symbol = (request.match_info.get("symbol") or "").upper()
            fg = getattr(self, "factor_graph", None)
            if fg is None:
                return web.json_response({"enabled": False}, status=200)
            if not symbol:
                return web.json_response({"enabled": True, "all": fg.all_snapshots()})
            snap = fg.snapshot(symbol)
            if snap is None:
                return web.json_response({"enabled": True, "symbol": symbol, "snapshot": None})
            return web.json_response({"enabled": True, "symbol": symbol, "snapshot": snap})

        async def get_oracle_brain_directives(request):
            """§11 Brain: sembol bazlı son direktifler."""
            brain = getattr(self, "oracle_brain", None)
            if brain is None:
                return web.json_response({"enabled": False, "directives": {}}, status=200)
            try:
                return web.json_response({
                    "enabled": True,
                    "shadow": brain.shadow,
                    "directives": brain.all_last_directives(),
                })
            except Exception as e:
                return web.json_response({"enabled": True, "error": str(e)}, status=500)

        async def get_oracle_brain_traces(request):
            """§11 Brain: son reasoning trace'leri."""
            brain = getattr(self, "oracle_brain", None)
            if brain is None:
                return web.json_response({"enabled": False, "traces": []}, status=200)
            try:
                limit = int(request.query.get("limit", "50"))
            except (TypeError, ValueError):
                limit = 50
            try:
                return web.json_response({
                    "enabled": True,
                    "shadow": brain.shadow,
                    "traces": brain.recent_traces(limit=limit),
                })
            except Exception as e:
                return web.json_response({"enabled": True, "error": str(e)}, status=500)

        async def get_oracle_brain_health(request):
            """§11 Brain: health + metrics."""
            brain = getattr(self, "oracle_brain", None)
            if brain is None:
                return web.json_response({"enabled": False}, status=200)
            try:
                h = await brain.health_check()
                m = brain.metrics()
                rag = getattr(self, "oracle_rag", None)
                return web.json_response({
                    "enabled": True, "health": h, "metrics": m,
                    "rag": rag.stats() if rag is not None else None,
                })
            except Exception as e:
                return web.json_response({"enabled": True, "error": str(e)}, status=500)

        async def get_runtime_status(request):
            """§12 Runtime Supervisor status."""
            sup = getattr(self, "runtime_supervisor", None)
            if sup is None:
                return web.json_response({"enabled": False}, status=200)
            try:
                return web.json_response({
                    "enabled": True,
                    "status": sup.status(),
                    "metrics": sup.metrics(),
                })
            except Exception as e:
                return web.json_response({"enabled": True, "error": str(e)}, status=500)

        app.router.add_get("/api/confluence/{symbol}", get_confluence_symbol)
        app.router.add_get("/api/intel/summary", get_intel_summary)
        app.router.add_get("/api/oracle/summary", get_oracle_summary)
        app.router.add_get("/api/oracle/channels/{symbol}", get_oracle_channels_symbol)
        app.router.add_get("/api/oracle/detector/{name}", get_oracle_detector_snapshot)
        app.router.add_get("/api/oracle/factor-graph", get_oracle_factor_graph)
        app.router.add_get("/api/oracle/factor-graph/{symbol}", get_oracle_factor_graph)
        app.router.add_get("/api/oracle/brain/directives", get_oracle_brain_directives)
        app.router.add_get("/api/oracle/brain/traces", get_oracle_brain_traces)
        app.router.add_get("/api/oracle/brain/health", get_oracle_brain_health)
        app.router.add_get("/api/runtime/status", get_runtime_status)

        # ─── Aşama 1 — Gatekeeper + AutoRollback + Warmup endpoints ───
        async def get_gatekeeper_stats(request):
            try:
                from directive_gatekeeper import get_directive_gatekeeper
                gk = get_directive_gatekeeper()
                data = gk.stats()
                data["recent_rejections"] = gk.load_recent_rejections(limit=10)
                return web.json_response({"enabled": True, **data})
            except Exception as e:
                return web.json_response({"enabled": False, "error": str(e)}, status=200)

        async def get_autorollback_status(request):
            try:
                from auto_rollback_monitor import get_auto_rollback_monitor
                mon = get_auto_rollback_monitor()
                return web.json_response(mon.status())
            except Exception as e:
                return web.json_response({"enabled": False, "error": str(e)}, status=200)

        async def post_autorollback_force(request):
            try:
                from auto_rollback_monitor import get_auto_rollback_monitor
                mon = get_auto_rollback_monitor()
                try:
                    body = await request.json()
                except Exception:
                    body = {}
                reason = str(body.get("reason") or "operator force")[:240]
                state = mon.force_rollback(reason=reason)
                return web.json_response({
                    "ok": True, "state": {
                        "rolled_back": state.rolled_back, "trigger": state.trigger,
                        "reason": state.reason, "ts": state.ts,
                    },
                })
            except Exception as e:
                return web.json_response({"ok": False, "error": str(e)}, status=500)

        async def get_warmup_report(request):
            """Return the most recent warmup report summary (md tail + checkpoint)."""
            try:
                from config import Config
                from pathlib import Path as _P
                import json as _json, os as _os
                checkpoint_path = _P(Config.WARMUP_CHECKPOINT_PATH)
                report_dir = _P(Config.WARMUP_REPORT_DIR)
                payload: Dict[str, Any] = {"enabled": True}
                if checkpoint_path.exists():
                    try:
                        payload["checkpoint"] = _json.loads(checkpoint_path.read_text(encoding="utf-8") or "{}")
                    except Exception:
                        payload["checkpoint"] = None
                latest = None
                if report_dir.exists():
                    reports = sorted(report_dir.glob("warmup_report_*.md"))
                    if reports:
                        latest = reports[-1]
                        payload["latest_report_path"] = str(latest)
                        try:
                            payload["latest_report_excerpt"] = latest.read_text(encoding="utf-8")[:6000]
                        except Exception:
                            payload["latest_report_excerpt"] = None
                trust_path = _P(Config.WARMUP_TRUST_SCORES_PATH)
                payload["trust_file_exists"] = trust_path.exists()
                baseline_path = _P(Config.SAFETY_NET_BASELINE_PATH)
                payload["baseline_file_exists"] = baseline_path.exists()
                return web.json_response(payload)
            except Exception as e:
                return web.json_response({"enabled": False, "error": str(e)}, status=200)

        app.router.add_get("/api/oracle/gatekeeper/stats", get_gatekeeper_stats)
        app.router.add_get("/api/oracle/autorollback/status", get_autorollback_status)
        app.router.add_post("/api/oracle/autorollback/force", post_autorollback_force)
        app.router.add_get("/api/oracle/warmup/report", get_warmup_report)

        # ─── Aşama 2 — Directive Impact endpoints ───
        async def get_impact_recent(request):
            try:
                from directive_impact_tracker import get_directive_impact_tracker
                tracker = get_directive_impact_tracker()
                limit = int(request.rel_url.query.get("limit", "50"))
                return web.json_response({"items": tracker.recent(n=limit)})
            except Exception as e:
                return web.json_response({"error": str(e)}, status=200)

        async def get_impact_by_type(request):
            try:
                from directive_impact_tracker import get_directive_impact_tracker
                tracker = get_directive_impact_tracker()
                return web.json_response({"by_type": tracker.aggregate_by_type()})
            except Exception as e:
                return web.json_response({"error": str(e)}, status=200)

        async def get_impact_synthetic_vs_live(request):
            try:
                from directive_impact_tracker import get_directive_impact_tracker
                tracker = get_directive_impact_tracker()
                by_type = tracker.aggregate_by_type()
                rows = [
                    {
                        "directive_type": t,
                        "live_mean": v["live_mean"],
                        "live_count": v["live_count"],
                        "synthetic_mean": v["synthetic_mean"],
                        "synthetic_count": v["synthetic_count"],
                        "delta": v["live_mean"] - v["synthetic_mean"],
                    }
                    for t, v in by_type.items()
                ]
                return web.json_response({
                    "rows": rows,
                    "summary": {
                        "rolling_24h_live": tracker.rolling_mean_impact(24, synthetic=False),
                        "rolling_24h_synthetic": tracker.rolling_mean_impact(24, synthetic=True),
                        "synthetic_baseline": tracker.synthetic_baseline(),
                    },
                })
            except Exception as e:
                return web.json_response({"error": str(e)}, status=200)

        app.router.add_get("/api/oracle/impact/recent", get_impact_recent)
        app.router.add_get("/api/oracle/impact/by-type", get_impact_by_type)
        app.router.add_get("/api/oracle/impact/synthetic-vs-live", get_impact_synthetic_vs_live)

        # ─── Aşama 3 — Free Roam endpoints ───
        async def get_asama3_status(request):
            try:
                from weekly_ack_watchdog import get_weekly_ack_watchdog
                from emergency_lockdown import get_emergency_lockdown
                from config import Config
                wd = get_weekly_ack_watchdog().status()
                lock = get_emergency_lockdown().status()
                # Latest self-audit
                from pathlib import Path
                import json as _json
                sa_path = Path(getattr(Config, "QWEN_SELF_AUDIT_LATEST_PATH", "python_agents/.self_audit_latest.json"))
                self_audit: dict = {}
                if sa_path.exists():
                    try:
                        obj = _json.loads(sa_path.read_text(encoding="utf-8"))
                        self_audit = {
                            "month_label": obj.get("month_label"),
                            "disagreement_rate": obj.get("disagreement_rate"),
                            "sample_size": obj.get("sample_size"),
                            "alert_emitted": obj.get("alert_emitted"),
                            "threshold": obj.get("threshold"),
                        }
                    except Exception as _e:
                        self_audit = {"error": str(_e)}
                return web.json_response({
                    "phase": "asama_3" if not wd.get("degraded") else "asama_2_degraded",
                    "weekly_ack": wd,
                    "emergency_lockdown": lock,
                    "self_audit": self_audit,
                    "config": {
                        "max_directives_per_hour": getattr(Config, "ORACLE_BRAIN_MAX_DIRECTIVES_PER_HOUR", None),
                        "allowlist": list(getattr(Config, "ORACLE_BRAIN_DIRECTIVE_ALLOWLIST", []) or []),
                        "blocklist_hard": list(getattr(Config, "ORACLE_BRAIN_DIRECTIVE_BLOCKLIST_HARD", []) or []),
                    },
                })
            except Exception as e:
                return web.json_response({"error": str(e), "phase": "unknown"}, status=200)

        async def post_emergency_lockdown(request):
            try:
                from emergency_lockdown import get_emergency_lockdown
                from config import Config
                token_hdr = request.headers.get("X-Emergency-Token", "")
                expected = getattr(Config, "EMERGENCY_TOKEN", "") or ""
                if not expected:
                    return web.json_response({"ok": False, "error": "EMERGENCY_TOKEN not configured"}, status=503)
                if token_hdr != expected:
                    return web.json_response({"ok": False, "error": "invalid token"}, status=401)
                body = await request.json() if request.can_read_body else {}
                reason = str(body.get("reason") or "manual halt")[:512]
                lock = get_emergency_lockdown()
                out = lock.engage(reason=reason, source="api", extra={"ip": request.remote})
                return web.json_response({"ok": True, "state": out})
            except Exception as e:
                return web.json_response({"ok": False, "error": str(e)}, status=500)

        app.router.add_get("/api/oracle/asama3/status", get_asama3_status)
        app.router.add_post("/api/oracle/emergency-lockdown", post_emergency_lockdown)

        # ─── Intel Upgrade (Phase 2) endpoints ───
        async def get_cross_asset_graph(request):
            """Tüm cross-asset lead/lag grafiği (JSON)."""
            eng = getattr(self, "cross_asset_engine", None)
            if eng is None:
                return web.json_response({"error": "cross_asset disabled"}, status=503)
            try:
                return web.json_response(eng.graph_snapshot())
            except Exception as e:
                logger.error(f"cross_asset graph error: {e}")
                return web.json_response({"error": str(e)}, status=500)

        async def get_cross_asset_neighbors(request):
            """Bir sembolün leader/follower komşuları."""
            symbol = (request.match_info.get("symbol") or "").upper()
            eng = getattr(self, "cross_asset_engine", None)
            if eng is None:
                return web.json_response({"error": "cross_asset disabled"}, status=503)
            if not symbol:
                return web.json_response({"error": "symbol required"}, status=400)
            try:
                leaders = [
                    {"symbol": s, "lag_sec": l, "rho": round(r, 4)}
                    for s, l, r in eng.leaders_of(symbol)
                ]
                followers = [
                    {"symbol": s, "lag_sec": l, "rho": round(r, 4)}
                    for s, l, r in eng.followers_of(symbol)
                ]
                return web.json_response({
                    "symbol": symbol,
                    "leaders": leaders,
                    "followers": followers,
                    "active_spillover": eng.spillover_signal(symbol),
                })
            except Exception as e:
                logger.error(f"cross_asset neighbors error: {e}")
                return web.json_response({"error": str(e)}, status=500)

        app.router.add_get("/api/cross-asset/graph", get_cross_asset_graph)
        app.router.add_get("/api/cross-asset/{symbol}", get_cross_asset_neighbors)

        # ─── Intel Upgrade (Phase 3) endpoints ───
        async def get_fast_brain_prediction(request):
            """Bir sembol için canlı FastBrain tahmini."""
            symbol = (request.match_info.get("symbol") or "").upper()
            eng = getattr(self, "fast_brain_engine", None)
            if eng is None:
                return web.json_response({"error": "fast_brain disabled"}, status=503)
            if not symbol:
                return web.json_response({"error": "symbol required"}, status=400)
            if not eng.enabled:
                snap = eng.snapshot(symbol)
                return web.json_response({
                    "symbol": symbol,
                    "enabled": False,
                    "last": snap,
                    "reason": "model dosyası yok (dormant)",
                })
            try:
                pred = eng.predict(symbol)
                if pred is None:
                    return web.json_response({
                        "symbol": symbol,
                        "enabled": True,
                        "prediction": None,
                        "reason": "yeterli feature yok",
                    })
                return web.json_response({
                    "symbol": symbol,
                    "enabled": True,
                    "prediction": pred.to_dict(),
                })
            except Exception as e:
                logger.error(f"fast_brain endpoint error: {e}")
                return web.json_response({"error": str(e)}, status=500)

        async def get_decision_router_status(request):
            """DecisionRouter health + son kararlar."""
            router = getattr(self, "decision_router", None)
            if router is None:
                return web.json_response({"enabled": False})
            try:
                h = await router.health_check()
                m = router.metrics()
                last = {sym: d.to_dict() for sym, d in router._last_by_symbol.items()}
                return web.json_response({
                    "enabled": True,
                    "health": h,
                    "metrics": m,
                    "last_decisions": last,
                })
            except Exception as e:
                return web.json_response({"error": str(e)}, status=500)

        app.router.add_get("/api/fast-brain/{symbol}", get_fast_brain_prediction)
        app.router.add_get("/api/decision-router/status", get_decision_router_status)

        # ─── Intel Upgrade (Phase 4) endpoints ───
        async def get_online_learning_stats(request):
            ev = getattr(self, "online_learning", None)
            if ev is None:
                return web.json_response({"enabled": False})
            try:
                symbol = request.query.get("symbol")
                return web.json_response({
                    "enabled": True,
                    "health": await ev.health_check(),
                    "rolling": ev.rolling_metrics(symbol.upper() if symbol else None),
                })
            except Exception as e:
                return web.json_response({"error": str(e)}, status=500)

        app.router.add_get("/api/online-learning/stats", get_online_learning_stats)

        # ─── Intel Upgrade (Phase 5 Finalization) — Safety Net ───
        async def get_safety_net_status(request):
            sn = getattr(self, "safety_net", None)
            if sn is None:
                return web.json_response({"enabled": False, "reason": "SAFETY_NET_ENABLED=0"})
            try:
                return web.json_response(sn.status())
            except Exception as e:
                return web.json_response({"error": str(e)}, status=500)

        async def get_counterfactual_metrics(request):
            db = getattr(self, "db", None)
            if db is None or not hasattr(db, "counterfactual_metrics"):
                return web.json_response({"enabled": False})
            try:
                hours = int(request.query.get("window_hours", "24"))
            except Exception:
                hours = 24
            try:
                return web.json_response(await db.counterfactual_metrics(window_hours=hours))
            except Exception as e:
                return web.json_response({"error": str(e)}, status=500)

        app.router.add_get("/api/intel/safety_net", get_safety_net_status)
        app.router.add_get("/api/intel/counterfactuals", get_counterfactual_metrics)

        # ─── Target Cards endpoint ───
        async def get_target_cards(request):
            """SimulationEngine'den aktif hedef kartlarını döndür."""
            try:
                from simulation_engine import get_simulation_engine
                engine = get_simulation_engine()
                cards = engine.get_live_cards()
                archive = engine.get_archive(limit=20)
                stats = engine.get_stats()
                return web.json_response({
                    "live_cards": cards,
                    "recent_archive": archive,
                    "stats": stats,
                })
            except Exception as e:
                logger.error(f"Target cards endpoint error: {e}")
                return web.json_response({"live_cards": [], "recent_archive": [], "stats": {}, "error": str(e)})

        app.router.add_get("/api/target-cards", get_target_cards)

        # ─── Dashboard Summary endpoint ───
        async def get_dashboard_summary(request):
            """Dashboard için özet istatistikleri döndür."""
            try:
                summary = await self.db.get_dashboard_summary()
                
                # Closed simulations'dan win/loss ayır
                closed_count = summary.get("strategy_closed_trades", 0)
                wins = summary.get("strategy_wins", 0)
                losses = closed_count - wins
                
                return web.json_response({
                    "total_trades": summary.get("total_trades", 0),
                    "active_signals": summary.get("active_signals", 0),
                    "open_simulations": summary.get("open_simulations", 0),
                    "total_pnl": summary.get("total_pnl", 0),
                    "win_rate": summary.get("win_rate", 0),
                    "closed_simulations": closed_count,
                    "winning_simulations": wins,
                    "losing_simulations": losses,
                    "market_ticks_total": summary.get("market_ticks_total", 0),
                    "recent_movements_24h": summary.get("recent_movements_24h", 0),
                    "risk_rejected_24h": summary.get("risk_rejected_24h", 0),
                })
            except Exception as e:
                logger.error(f"Dashboard summary endpoint error: {e}")
                return web.json_response({
                    "total_trades": 0, "active_signals": 0, "open_simulations": 0,
                    "total_pnl": 0, "win_rate": 0, "closed_simulations": 0,
                    "winning_simulations": 0, "losing_simulations": 0,
                    "error": str(e)
                })

        app.router.add_get("/api/dashboard/summary", get_dashboard_summary)

        # ─── Simulations endpoint ───
        async def get_simulations(request):
            """Açık ve kapalı simülasyonları döndür."""
            try:
                status_filter = request.query.get("status")
                limit = int(request.query.get("limit", "50"))
                
                async with self.db.pool.acquire() as conn:
                    if status_filter:
                        rows = await conn.fetch("""
                            SELECT id, symbol, entry_price, side, status, pnl, pnl_pct,
                                   entry_time, exit_time, exit_price, created_at
                            FROM simulations
                            WHERE status = $1
                            ORDER BY created_at DESC
                            LIMIT $2
                        """, status_filter, limit)
                    else:
                        rows = await conn.fetch("""
                            SELECT id, symbol, entry_price, side, status, pnl, pnl_pct,
                                   entry_time, exit_time, exit_price, created_at
                            FROM simulations
                            ORDER BY created_at DESC
                            LIMIT $1
                        """, limit)
                    
                    simulations = []
                    for row in rows:
                        simulations.append({
                            "id": row["id"],
                            "symbol": row["symbol"],
                            "entry_price": float(row["entry_price"]) if row["entry_price"] else 0,
                            "side": row["side"],
                            "status": row["status"],
                            "pnl": float(row["pnl"]) if row["pnl"] else None,
                            "pnl_pct": float(row["pnl_pct"]) if row["pnl_pct"] else None,
                            "entry_time": row["entry_time"].isoformat() if row["entry_time"] else None,
                            "exit_time": row["exit_time"].isoformat() if row["exit_time"] else None,
                            "exit_price": float(row["exit_price"]) if row["exit_price"] else None,
                        })
                    
                    return web.json_response(simulations)
            except Exception as e:
                logger.error(f"Simulations endpoint error: {e}")
                return web.json_response([], status=500)

        app.router.add_get("/api/simulations", get_simulations)

        # ─── Signals endpoint ───
        async def get_signals(request):
            """Aktif ve son sinyalleri döndür."""
            try:
                status_filter = request.query.get("status", "pending")
                limit = int(request.query.get("limit", "50"))
                
                async with self.db.pool.acquire() as conn:
                    if status_filter == "all":
                        rows = await conn.fetch("""
                            SELECT id, symbol, signal_type, direction, confidence, price,
                                   entry_price, target_price, status, timestamp, source,
                                   expires_at, exchange, market_type, metadata
                            FROM signals
                            ORDER BY timestamp DESC
                            LIMIT $1
                        """, limit)
                    else:
                        rows = await conn.fetch("""
                            SELECT id, symbol, signal_type, direction, confidence, price,
                                   entry_price, target_price, status, timestamp, source,
                                   expires_at, exchange, market_type, metadata
                            FROM signals
                            WHERE status = $1
                            ORDER BY timestamp DESC
                            LIMIT $2
                        """, status_filter, limit)
                    
                    signals = []
                    for row in rows:
                        entry = float(row["entry_price"]) if row["entry_price"] else float(row["price"]) if row["price"] else 0
                        target = float(row["target_price"]) if row["target_price"] else 0
                        target_pct = ((target - entry) / entry) if entry > 0 and target > 0 else 0
                        
                        signals.append({
                            "id": row["id"],
                            "symbol": row["symbol"],
                            "signal_type": row["signal_type"],
                            "direction": row["direction"],
                            "confidence": float(row["confidence"]) if row["confidence"] else 0,
                            "price": float(row["price"]) if row["price"] else 0,
                            "entry_price": entry,
                            "target_price": target,
                            "target_pct": target_pct,
                            "status": row["status"],
                            "timestamp": row["timestamp"].isoformat() if row["timestamp"] else None,
                            "signal_time": row["timestamp"].isoformat() if row["timestamp"] else None,
                            "source": row["source"],
                            "expires_at": row["expires_at"].isoformat() if row["expires_at"] else None,
                            "exchange": row["exchange"],
                            "market_type": row["market_type"],
                        })
                    
                    return web.json_response(signals)
            except Exception as e:
                logger.error(f"Signals endpoint error: {e}")
                return web.json_response([], status=500)

        async def dismiss_signal(request):
            """Sinyali dismiss et."""
            try:
                signal_id = int(request.match_info["signal_id"])
                async with self.db.pool.acquire() as conn:
                    await conn.execute("""
                        UPDATE signals SET status = 'dismissed' WHERE id = $1
                    """, signal_id)
                return web.json_response({"success": True, "id": signal_id})
            except Exception as e:
                logger.error(f"Dismiss signal error: {e}")
                return web.json_response({"error": str(e)}, status=500)

        async def clear_signals(request):
            """Birden fazla sinyali temizle."""
            try:
                data = await request.json()
                ids = data.get("ids", [])
                if not ids:
                    return web.json_response({"error": "No IDs provided"}, status=400)
                async with self.db.pool.acquire() as conn:
                    await conn.execute("""
                        UPDATE signals SET status = 'dismissed' WHERE id = ANY($1::int[])
                    """, ids)
                return web.json_response({"success": True, "cleared": len(ids)})
            except Exception as e:
                logger.error(f"Clear signals error: {e}")
                return web.json_response({"error": str(e)}, status=500)

        app.router.add_get("/api/signals", get_signals)
        app.router.add_post("/api/signals/{signal_id}/dismiss", dismiss_signal)
        app.router.add_post("/api/signals/clear", clear_signals)

        # ─── Storage Manager API endpoints ───
        async def get_storage_status(request):
            """StorageManager durumunu döndür."""
            try:
                if self.storage_manager:
                    return web.json_response(self.storage_manager.get_status())
                return web.json_response({"error": "StorageManager not initialized"}, status=503)
            except Exception as e:
                logger.error(f"Storage status endpoint error: {e}")
                return web.json_response({"error": str(e)}, status=500)

        async def force_storage_prune(request):
            """Manuel pruning tetikle."""
            try:
                if self.storage_manager:
                    summary = await self.storage_manager.force_prune()
                    return web.json_response(summary.to_dict())
                return web.json_response({"error": "StorageManager not initialized"}, status=503)
            except Exception as e:
                logger.error(f"Force prune endpoint error: {e}")
                return web.json_response({"error": str(e)}, status=500)

        async def get_history_summaries(request):
            """Özetlenmiş geçmiş verileri döndür."""
            try:
                if self.storage_manager:
                    table_name = request.query.get("table")
                    symbol = request.query.get("symbol")
                    limit = int(request.query.get("limit", "100"))
                    summaries = await self.storage_manager.get_history_summaries(
                        table_name=table_name,
                        symbol=symbol,
                        limit=limit,
                    )
                    return web.json_response({"summaries": summaries, "count": len(summaries)})
                return web.json_response({"error": "StorageManager not initialized"}, status=503)
            except Exception as e:
                logger.error(f"History summaries endpoint error: {e}")
                return web.json_response({"error": str(e)}, status=500)

        async def scan_storage(request):
            """Disk kullanımını tara."""
            try:
                if self.storage_manager:
                    metrics = await self.storage_manager.scan_storage()
                    return web.json_response(metrics.to_dict())
                return web.json_response({"error": "StorageManager not initialized"}, status=503)
            except Exception as e:
                logger.error(f"Storage scan endpoint error: {e}")
                return web.json_response({"error": str(e)}, status=500)

        app.router.add_get("/api/storage/status", get_storage_status)
        app.router.add_post("/api/storage/prune", force_storage_prune)
        app.router.add_get("/api/storage/summaries", get_history_summaries)
        app.router.add_get("/api/storage/scan", scan_storage)

        # ─── Mission Control endpoints (additive, read-only) ───
        async def _refresh_mission_control_externals():
            """Best-effort push of DB / safety-net / lockdown facts into the
            aggregator's external signal cache. Never raises."""
            try:
                from mission_control_aggregator import (
                    set_vital_sign,
                    set_supervisor_heartbeat,
                )
            except Exception:
                return
            try:
                if getattr(self, "db", None) is not None:
                    try:
                        summary = await self.db.get_dashboard_summary()
                        if isinstance(summary, dict):
                            active = summary.get("active_signals")
                            if active is None:
                                active = summary.get("signals_active")
                            if active is not None:
                                set_vital_sign("active_signals", int(active))
                            pnl24 = summary.get("ghost_pnl_24h_pct") or summary.get("pnl_24h_pct")
                            if isinstance(pnl24, (int, float)):
                                set_vital_sign("ghost_pnl_24h_pct", float(pnl24))
                            ws_up = summary.get("ws_uptime_pct_24h") or summary.get("ws_uptime_24h")
                            if isinstance(ws_up, (int, float)):
                                set_vital_sign("ws_uptime_pct_24h", float(ws_up))
                    except Exception as e:
                        logger.debug("mission_control: db summary skip: %s", e)
            except Exception:
                pass
            try:
                sn = getattr(self, "safety_net", None)
                if sn is not None:
                    state = "ok"
                    if hasattr(sn, "status"):
                        try:
                            st = sn.status() if callable(sn.status) else sn.status
                            if isinstance(st, dict):
                                if st.get("tripped"):
                                    state = "tripped"
                                elif st.get("warning") or st.get("degraded"):
                                    state = "warning"
                        except Exception:
                            pass
                    set_vital_sign("safety_net_state", state)
            except Exception:
                pass
            try:
                sup = getattr(self, "runtime_supervisor", None)
                if sup is not None and hasattr(sup, "status"):
                    try:
                        st = sup.status()
                        ts = float(st.get("last_cycle_ts") or 0.0)
                        if ts > 0:
                            set_supervisor_heartbeat("runtime_supervisor", ts)
                    except Exception:
                        pass
            except Exception:
                pass

        async def get_mission_control_snapshot(request):
            try:
                from mission_control_aggregator import snapshot
                await _refresh_mission_control_externals()
                snap = snapshot(force=False)
                return web.json_response(snap)
            except Exception as e:
                logger.error("mission-control snapshot error: %s", e)
                return web.json_response({"error": str(e)}, status=500)

        async def stream_mission_control(request):
            """SSE stream: emits one snapshot per second until client disconnects."""
            try:
                from mission_control_aggregator import snapshot
            except Exception as e:
                return web.json_response({"error": str(e)}, status=500)
            response = web.StreamResponse(
                status=200,
                reason="OK",
                headers={
                    "Content-Type": "text/event-stream",
                    "Cache-Control": "no-cache, no-store, must-revalidate",
                    "Connection": "keep-alive",
                    "X-Accel-Buffering": "no",
                },
            )
            await response.prepare(request)
            import json as _json
            try:
                while True:
                    try:
                        await _refresh_mission_control_externals()
                        snap = snapshot(force=True)
                        payload = _json.dumps(snap, default=str, separators=(",", ":"))
                        await response.write(f"data: {payload}\n\n".encode("utf-8"))
                    except Exception as e:
                        logger.debug("mission-control SSE frame err: %s", e)
                        await response.write(
                            f"event: error\ndata: {{\"error\": \"{e}\"}}\n\n".encode("utf-8")
                        )
                    await asyncio.sleep(1.0)
            except (asyncio.CancelledError, ConnectionResetError):
                pass
            except Exception as e:
                logger.debug("mission-control SSE loop err: %s", e)
            return response

        # Qwen autopsy cache: module_id → {diagnosis, generated_at, in_flight}
        # LLM is fired-and-forgotten so the HTTP response is never blocked by
        # a 20-30s cold-start (which upstream proxies can convert to 500/504).
        _mc_autopsy_cache: Dict[str, Dict[str, Any]] = {}
        _MC_AUTOPSY_TTL = 300.0  # 5 minutes

        async def _generate_qwen_diagnosis(
            module_id: str, bundle: Dict[str, Any], log_tail: list
        ):
            """Background task: compute Qwen diagnosis and store in cache."""
            try:
                from module_registry import MODULE_REGISTRY
                bridge = self.llm_bridge if hasattr(self, "llm_bridge") else None
                if bridge is None:
                    return
                try:
                    available = bool(await bridge.is_available())
                except Exception:
                    available = False
                if not available:
                    return
                spec = MODULE_REGISTRY[module_id]
                metric_windows = bundle.get("timeline_5min", {})
                deps_summary = "\n".join(
                    f"- {d['id']} ({d['impact_direction']}, {d['status']})"
                    for d in bundle.get("dependencies_status", [])
                ) or "-"
                log_block = "\n".join(log_tail[-20:]) or "-"
                prompt = (
                    f"Hücre: {spec.display_name} ({module_id})\n"
                    f"Görevi: {spec.description}\n"
                    f"Mevcut sağlık skoru: {bundle.get('current_health', 0)}/100\n"
                    f"Durum: {bundle.get('status')}\n\n"
                    f"Son 5 dakika metrikleri (kısa):\n{metric_windows}\n\n"
                    f"Son 20 log satırı:\n{log_block}\n\n"
                    f"Bağlı hücreler:\n{deps_summary}\n\n"
                    "Lütfen Türkçe yanıt ver:\n"
                    "1. Özet (1-2 cümle)\n"
                    "2. Muhtemel kök neden\n"
                    "3. Bağlı hücreler etkisi\n"
                    "4. Önerilen 2-3 aksiyon\n"
                    "5. Aciliyet: düşük | orta | yüksek | kritik"
                )
                system_prompt = (
                    "Sen QuenBot sistem tanı uzmanısın. "
                    "Yalnızca verilen verilere dayanarak kısa, operatöre dönük Türkçe tanı yaz."
                )
                try:
                    res = await asyncio.wait_for(
                        bridge.call_llm(
                            task="mission_control_autopsy",
                            system=system_prompt,
                            prompt=prompt,
                            json_mode=False,
                            temperature=0.2,
                        ),
                        timeout=90.0,
                    )
                except asyncio.TimeoutError:
                    res = None
                    logger.debug("mission-control qwen diagnosis timeout (bg)")
                if isinstance(res, dict) and res.get("success") and res.get("text"):
                    _mc_autopsy_cache[module_id] = {
                        "diagnosis": {
                            "summary_tr": str(res["text"]).strip(),
                            "suggested_actions_tr": [],
                            "confidence": 0.7,
                            "generated_at": time.time(),
                        },
                        "generated_at": time.time(),
                        "in_flight": False,
                    }
                else:
                    # Keep an in_flight=False marker so we do not retry for 60s.
                    _mc_autopsy_cache[module_id] = {
                        "diagnosis": None,
                        "generated_at": time.time(),
                        "in_flight": False,
                    }
            except Exception as e:
                logger.debug("mission-control qwen diagnosis bg err: %s", e)
                _mc_autopsy_cache[module_id] = {
                    "diagnosis": None,
                    "generated_at": time.time(),
                    "in_flight": False,
                }

        async def get_mission_control_autopsy(request):
            module_id = request.match_info.get("module_id", "")
            try:
                from mission_control_aggregator import autopsy_bundle
                from module_registry import MODULE_REGISTRY
            except Exception as e:
                return web.json_response({"error": str(e)}, status=500)
            if module_id not in MODULE_REGISTRY:
                return web.json_response(
                    {"error": f"unknown module: {module_id}"}, status=404
                )

            # Pull recent log lines for this module from the event_bus history
            # (best-effort; no filesystem reads to avoid IO on dashboard polls).
            log_tail: list = []
            try:
                bus = self.event_bus
                hist = list(getattr(bus, "_history", []) or [])[-200:]
                for e in reversed(hist):
                    if str(e.get("source") or "") == module_id:
                        log_tail.append(
                            f"{e.get('timestamp'):.2f} {e.get('type')} {e.get('data_preview')}"
                        )
                        if len(log_tail) >= 50:
                            break
                log_tail.reverse()
            except Exception:
                log_tail = []

            bundle = autopsy_bundle(module_id, log_tail=log_tail)

            # Non-blocking Qwen diagnosis: serve from cache if fresh, otherwise
            # kick off a background task and tell the client to poll again.
            now = time.time()
            entry = _mc_autopsy_cache.get(module_id)
            if entry and (now - entry.get("generated_at", 0)) < _MC_AUTOPSY_TTL:
                bundle["qwen_diagnosis"] = entry.get("diagnosis")
                bundle["qwen_pending"] = bool(entry.get("in_flight"))
            else:
                bundle["qwen_diagnosis"] = None
                bundle["qwen_pending"] = True
                # Only start one background task per module at a time
                if not entry or not entry.get("in_flight"):
                    _mc_autopsy_cache[module_id] = {
                        "diagnosis": entry.get("diagnosis") if entry else None,
                        "generated_at": now,
                        "in_flight": True,
                    }
                    try:
                        asyncio.create_task(
                            _generate_qwen_diagnosis(module_id, bundle, log_tail)
                        )
                    except Exception as e:
                        logger.debug("mc autopsy bg dispatch err: %s", e)
                        _mc_autopsy_cache[module_id]["in_flight"] = False
                # Prune cache keep last 64 entries
                if len(_mc_autopsy_cache) > 64:
                    for k in list(_mc_autopsy_cache.keys())[:-64]:
                        _mc_autopsy_cache.pop(k, None)

            return web.json_response(bundle)

        async def post_mission_control_restart(request):
            module_id = request.match_info.get("module_id", "")
            try:
                from module_registry import MODULE_REGISTRY
            except Exception as e:
                return web.json_response({"error": str(e)}, status=500)
            if module_id not in MODULE_REGISTRY:
                return web.json_response(
                    {"error": f"unknown module: {module_id}"}, status=404
                )
            # Optional admin gate.
            required = os.environ.get("QUENBOT_ADMIN_TOKEN", "")
            if required:
                supplied = request.headers.get("X-Admin-Token") or request.query.get("token") or ""
                if supplied != required:
                    return web.json_response({"error": "unauthorized"}, status=401)
            sup = getattr(self, "runtime_supervisor", None)
            if sup is None or not hasattr(sup, "_restart_callback") or sup._restart_callback is None:
                return web.json_response(
                    {"ok": False, "error": "restart not available"}, status=503
                )
            try:
                await sup._restart_callback(module_id)
                return web.json_response({
                    "ok": True,
                    "module_id": module_id,
                    "restarted_at": time.time(),
                })
            except Exception as e:
                logger.error("mission-control restart fail %s: %s", module_id, e)
                return web.json_response({"ok": False, "error": str(e)}, status=500)

        app.router.add_get("/api/mission-control/snapshot", get_mission_control_snapshot)
        app.router.add_get("/api/mission-control/stream", stream_mission_control)
        app.router.add_get("/api/mission-control/autopsy/{module_id}", get_mission_control_autopsy)
        app.router.add_post("/api/mission-control/restart/{module_id}", post_mission_control_restart)

        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "0.0.0.0", 3002)
        await site.start()
        logger.info("📡 Directive API server running on port 3002")

        # Keep alive
        while self.running:
            await asyncio.sleep(5)

        await runner.cleanup()

async def main(dry_run: bool = False, exit_after_seconds: Optional[int] = None):
    orchestrator = AgentOrchestrator()
    # §12 Graceful shutdown: SIGINT/SIGTERM → orchestrator.running = False
    try:
        import signal as _signal
        loop = asyncio.get_running_loop()
        def _request_shutdown(sig_name: str):
            try:
                logger.warning("⚠️  %s received — graceful shutdown baslatiliyor...", sig_name)
                orchestrator.running = False
            except Exception:
                pass
        for _sig, _name in ((getattr(_signal, "SIGINT", None), "SIGINT"),
                            (getattr(_signal, "SIGTERM", None), "SIGTERM")):
            if _sig is not None:
                try:
                    loop.add_signal_handler(_sig, _request_shutdown, _name)
                except (NotImplementedError, RuntimeError):
                    pass
    except Exception as e:
        logger.debug("signal handler setup skip: %s", e)
    if dry_run:
        logger.warning("🧪 DRY-RUN mode: initialize only; no start / no network loops")
        try:
            await orchestrator.initialize()
            logger.info("✅ dry-run initialize complete")
        finally:
            try:
                await orchestrator.stop()
            except Exception:
                pass
        return
    try:
        if exit_after_seconds and exit_after_seconds > 0:
            async def _killer():
                await asyncio.sleep(int(exit_after_seconds))
                logger.warning("⏱️  exit-after-seconds=%ss reached; shutting down", exit_after_seconds)
                orchestrator.running = False
            asyncio.create_task(_killer())
        await orchestrator.initialize()
        await orchestrator.start()
    except Exception as e:
        logger.error(f"Orchestrator failed: {e}")
        raise
    finally:
        await orchestrator.stop()

if __name__ == "__main__":
    import argparse as _argparse
    _parser = _argparse.ArgumentParser(description="Quenbot agent orchestrator")
    _parser.add_argument("--dry-run", action="store_true",
                         help="Initialize subsystems and exit without starting live loops")
    _parser.add_argument("--exit-after-seconds", type=int, default=0,
                         help="Graceful shutdown after N seconds (0 = disabled)")
    _args, _ = _parser.parse_known_args()
    asyncio.run(main(
        dry_run=bool(_args.dry_run),
        exit_after_seconds=int(_args.exit_after_seconds) if _args.exit_after_seconds else None,
    ))
