#!/usr/bin/env python3
import asyncio
import logging
import os
import json
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
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

from state_tracker import StateTracker
from risk_manager import RiskManager
from rca_engine import RCAEngine
from llm_client import get_llm_client
from llm_bridge import get_llm_bridge
from directive_store import get_directive_store
from task_queue import get_task_queue
from event_bus import get_event_bus, EventBus, Event, EventType
from resource_monitor import ResourceMonitor

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
        self.state_tracker = None
        self.risk_manager = None
        self.rca_engine = None
        self.llm_client = None
        self.llm_bridge = None
        self.task_queue = None
        self.directive_store = None
        self.event_bus: EventBus = get_event_bus()
        self.resource_monitor = ResourceMonitor()
        self.running = False
        self._agent_restart_counts: dict = {}
        self._max_restarts = 50
        self._system_mode = "initializing"  # initializing | healthy | degraded
        self._llm_available = False
        self._start_time = time.time()
        self._last_resource_snapshot = None
        self._resource_warnings: list[dict] = []
        # Thread pool for CPU-bound work (pattern matching, similarity calc)
        self._thread_pool = ThreadPoolExecutor(
            max_workers=6, thread_name_prefix="quenbot-cpu"
        )

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
        )
        logger.info("⚡ GemmaDecisionCore initialized (Katman 3 — Gemma nihai karar)")
        startup_report["components"]["decision_core"] = {"status": "ok"}
        logger.info("🔍 RCA Engine initialized")
        startup_report["components"]["rca_engine"] = {"status": "ok"}

        # 6. LLM — degraded mode if unavailable
        try:
            self.llm_client = get_llm_client()
            self.task_queue = get_task_queue()
            await self.task_queue.start()
            self.directive_store = get_directive_store()
            self.llm_bridge = get_llm_bridge()

            llm_healthy = await self.llm_client.health_check()
            if llm_healthy:
                self._llm_available = True
                logger.info(f"🧠 LLM connected (model: {self.llm_client.model})")
                startup_report["components"]["llm"] = {"status": "ok", "model": self.llm_client.model}
            else:
                logger.info("🧠 LLM backend reachable, checking for models...")
                model_ok = await self.llm_client.ensure_model()
                if model_ok:
                    self._llm_available = True
                    logger.info(f"🧠 LLM model ready: {self.llm_client.model}")
                    startup_report["components"]["llm"] = {"status": "ok", "model": self.llm_client.model}
                else:
                    self._llm_available = False
                    logger.warning("⚠ No LLM model available — DEGRADED MODE (rule-based logic)")
                    startup_report["components"]["llm"] = {"status": "degraded", "reason": "no model"}
        except Exception as e:
            self._llm_available = False
            logger.warning(f"⚠ LLM initialization failed — DEGRADED MODE: {e}")
            self.llm_bridge = None
            startup_report["components"]["llm"] = {"status": "degraded", "reason": str(e)}

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
                                                     risk_manager=self.risk_manager)
        self.auditor = AuditorAgent(self.db, brain=self.brain, rca_engine=self.rca_engine)
        self.pattern_matcher = PatternMatcherAgent(self.db, brain=self.brain)

        # Parallel agent initialization — utilize multiple cores
        await asyncio.gather(
            self.scout.initialize(),
            self.strategist.initialize(),
            self.ghost_simulator.initialize(),
            self.auditor.initialize(),
            self.pattern_matcher.initialize(),
        )

        # Chat engine
        self.chat_engine = ChatEngine(self.db, self.brain)
        self.chat_engine.register_agent('Scout', self.scout)
        self.chat_engine.register_agent('Strategist', self.strategist)
        self.chat_engine.register_agent('Ghost', self.ghost_simulator)
        self.chat_engine.register_agent('Auditor', self.auditor)
        self.chat_engine.register_agent('PatternMatcher', self.pattern_matcher)
        self.chat_engine.state_tracker = self.state_tracker
        self.chat_engine.risk_manager = self.risk_manager
        self.chat_engine.rca_engine = self.rca_engine

        # 9. Wire event bus subscriptions
        self._setup_event_subscriptions()

        logger.info("✓ All agents initialized with Brain + StateTracker + RiskManager")
        logger.info(f"✓ Monitoring {len(Config.WATCHLIST)} symbols: {Config.WATCHLIST}")
        logger.info(f"✓ System mode: {self._system_mode.upper()}")

        # 10. Startup report to DB
        startup_report["system_mode"] = self._system_mode
        startup_report["symbols"] = Config.WATCHLIST
        try:
            await self.db.update_heartbeat('system', self._system_mode, startup_report)
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

        # Resource warnings → log + DB
        bus.subscribe(EventType.RESOURCE_WARNING, self._on_resource_warning)

        # LLM status changes
        bus.subscribe(EventType.LLM_STATUS_CHANGE, self._on_llm_status_change)

        # Pattern match → Brain evaluation → Signal generation
        bus.subscribe(EventType.PATTERN_MATCH, self._on_pattern_match)

        logger.info("✓ Event bus subscriptions wired")

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
        """Simulation closed → update StateTracker."""
        sim_result = event.data
        if self.state_tracker and sim_result.get("pnl_pct") is not None:
            await self.state_tracker.record_trade(sim_result)
            logger.debug(f"📊 StateTracker updated: {sim_result.get('symbol')} PnL={sim_result.get('pnl_pct', 0):.2f}%")

    async def _on_resource_warning(self, event: Event):
        """Resource threshold exceeded → log and save."""
        for w in event.data.get("warnings", []):
            if w["level"] == "critical":
                logger.critical(f"🚨 {w['message']}")
            else:
                logger.warning(f"⚠ {w['message']}")

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
            logger.info(f"✓ LLM back online — switching to HEALTHY mode (model: {event.data.get('model', '?')})")

    async def _on_pattern_match(self, event: Event):
        """PatternMatcher found high-similarity match → Brain evaluates → Signal pipeline."""
        match_data = event.data
        symbol = match_data.get('symbol', '')
        similarity = match_data.get('similarity', 0)
        match_id = match_data.get('match_id')

        logger.info(f"🎯 Pattern Match event: {symbol} similarity={similarity:.4f}")

        # Brain (Gemma) merkezi karar otoritesi olarak değerlendirir
        if self.brain:
            try:
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

                    # Brain onayladı → Sinyal üret ve pipeline'a gönder
                    signal_data = {
                        'symbol': symbol,
                        'direction': trade_direction,
                        'signal_type': f"pattern_match_{trade_direction}",
                        'confidence': decision['confidence'],
                        'price': float(match_data.get('current_price') or 0),
                        'source': 'pattern_matcher',
                        'metadata': {
                            'raw_direction': raw_direction,
                            'similarity': similarity,
                            'euclidean_distance': match_data.get('euclidean_distance'),
                            'predicted_magnitude': decision['magnitude'],
                            'match_count': match_data.get('match_count', 0),
                            'brain_reasoning': decision.get('reasoning', ''),
                            'llm_analysis': decision.get('llm_analysis'),
                        },
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
                            'timestamp': datetime.utcnow(),
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

                    logger.info(f"✅ Brain approved pattern match signal: "
                                f"{symbol} {decision['direction']} "
                                f"(confidence={decision['confidence']:.2%})")
                else:
                    logger.info(f"🚫 Brain rejected pattern match: {symbol} — "
                                f"{decision.get('reasoning', '')[:100]}")

            except Exception as e:
                logger.error(f"Pattern match brain evaluation error: {e}")

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
            self._resilient_task("HealthMonitor", self._health_monitor),
            self._resilient_task("ChatProcessor", self._chat_processor),
            self._resilient_task("DirectiveAPI", self._directive_api_server),
        ]

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
            await self.scout.stop()
            await self.strategist.stop()
            await self.ghost_simulator.stop()
            await self.auditor.stop()
            await self.pattern_matcher.stop()
            if self.task_queue:
                await self.task_queue.stop()
            if self.llm_client:
                await self.llm_client.close()
            self._thread_pool.shutdown(wait=False)
            await self.db.disconnect()
            logger.info("✓ All agents stopped")
        except Exception as e:
            logger.error(f"Error during shutdown: {e}")

    async def _chat_processor(self):
        """Chat mesajlarını hızlı kontrol et ve anında cevapla"""
        last_processed_id = 0
        while self.running:
            try:
                await asyncio.sleep(0.5)  # 500ms polling — hızlı yanıt
                messages = await self.db.get_chat_messages(limit=10)
                for msg in messages:
                    if msg['role'] == 'user' and msg['id'] > last_processed_id:
                        # Hemen yanıtla - async ve hızlı
                        response = await self.chat_engine.respond(msg['message'])
                        await self.db.insert_chat_message('assistant', response, 'QuenBot AI')
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
                scout_health, strategist_health, ghost_health, auditor_health, pm_health = await asyncio.gather(
                    self.scout.health_check(),
                    self.strategist.health_check(),
                    self.ghost_simulator.health_check(),
                    self.auditor.health_check(),
                    self.pattern_matcher.health_check(),
                )
                brain_status = self.brain.get_brain_status()

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
                    self.db.update_heartbeat('brain', 'running', brain_status),
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
            st = self.state_tracker.state if self.state_tracker else {}
            brain = self.brain.get_brain_status() if self.brain else {}
            pm = {}
            try:
                pm = await self.pattern_matcher.health_check()
            except Exception:
                pm = {}

            return web.json_response({
                "mode": self._system_mode,
                "llm": {
                    "ok": llm_healthy,
                    "model": self.llm_client.model if self.llm_client and llm_healthy else None,
                },
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
                },
                "pattern_matcher": {
                    "ok": pm.get("healthy", False),
                    "scans": pm.get("scan_count", 0),
                    "matches": pm.get("match_count", 0),
                    "best_similarity": pm.get("best_similarity", 0),
                },
                "warnings": [{"level": w["level"], "comp": w["component"],
                              "msg": w["message"][:120]} for w in warnings],
                "uptime": int(time.time() - self._start_time),
            })

        async def get_event_log(request):
            """Recent event bus activity."""
            stats = self.event_bus.get_stats()
            return web.json_response(stats)

        async def post_chat(request):
            """Gemma AI Chat - Natural language commands with Gemma response"""
            try:
                data = await request.json()
                message = data.get("message", "").strip()
                if not message:
                    return web.json_response({"error": "Message required"}, status=400)
                
                if not self.chat_engine:
                    return web.json_response({"error": "Chat engine not initialized"}, status=500)
                
                # Get Gemma response
                response = await self.chat_engine.respond(message)
                
                # Store in database
                await self.db.insert_chat_message('user', message, 'user')
                await self.db.insert_chat_message('assistant', response, 'Gemma')
                
                return web.json_response({
                    "success": True,
                    "message": response,
                    "timestamp": datetime.now(timezone.utc).isoformat()
                })
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
        app.router.add_get("/api/pattern/matches", get_pattern_matches)

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
