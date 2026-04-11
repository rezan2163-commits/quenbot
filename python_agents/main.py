#!/usr/bin/env python3
import asyncio
import logging
import os
import json
from datetime import datetime
from dotenv import load_dotenv

from config import Config
from database import Database
from brain import BrainModule
from chat_engine import ChatEngine
from scout_agent import ScoutAgent
from strategist_agent import StrategistAgent
from ghost_simulator_agent import GhostSimulatorAgent
from auditor_agent import AuditorAgent
from state_tracker import StateTracker
from risk_manager import RiskManager
from rca_engine import RCAEngine
from llm_client import get_llm_client
from llm_bridge import get_llm_bridge
from directive_store import get_directive_store
from task_queue import get_task_queue

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
    def __init__(self):
        self.db = Database()
        self.brain = None
        self.chat_engine = None
        self.scout = None
        self.strategist = None
        self.ghost_simulator = None
        self.auditor = None
        self.state_tracker = None
        self.risk_manager = None
        self.rca_engine = None
        self.llm_client = None
        self.llm_bridge = None
        self.task_queue = None
        self.directive_store = None
        self.running = False
        self._agent_restart_counts: dict = {}
        self._max_restarts = 50  # max restart per agent before giving up

    async def initialize(self):
        """Initialize all components"""
        logger.info("=" * 80)
        logger.info("🤖 QUENBOT - AI-Powered Multi-Agent Market Intelligence System")
        logger.info("=" * 80)
        
        # Connect to database
        await self.db.connect()
        logger.info("✓ Database initialized")

        # Merkezi AI beyin modülü
        self.brain = BrainModule(self.db)
        await self.brain.initialize()
        logger.info(f"🧠 Brain initialized ({self.brain.get_brain_status()['total_patterns']} patterns)")

        # StateTracker - persistent state
        self.state_tracker = StateTracker(self.db)
        await self.state_tracker.load_state()
        logger.info(f"📊 StateTracker initialized (mode={self.state_tracker.get_mode()}, "
                     f"trades={self.state_tracker.state['total_trades']})")

        # RiskManager - signal gate
        self.risk_manager = RiskManager(self.state_tracker)
        logger.info(f"🛡 RiskManager initialized (max_daily={self.risk_manager.MAX_DAILY_TRADES})")

        # RCA Engine - failure analysis
        self.rca_engine = RCAEngine(self.db)
        logger.info("🔍 RCA Engine initialized")

        # LLM Central Intelligence - local Ollama backbone
        try:
            self.llm_client = get_llm_client()
            self.task_queue = get_task_queue()
            await self.task_queue.start()
            self.directive_store = get_directive_store()
            self.llm_bridge = get_llm_bridge()

            llm_healthy = await self.llm_client.health_check()
            if llm_healthy:
                logger.info(f"🧠 LLM connected (model: {self.llm_client.model})")
            else:
                # Ollama reachable but no model — try to pull one
                logger.info("🧠 LLM backend reachable, checking for models...")
                model_ok = await self.llm_client.ensure_model()
                if model_ok:
                    logger.info(f"🧠 LLM model ready: {self.llm_client.model}")
                else:
                    logger.warning("⚠ No LLM model available — agents will use rule-based logic")
        except Exception as e:
            logger.warning(f"⚠ LLM initialization failed (degraded mode): {e}")
            self.llm_bridge = None

        # Initialize agents with brain + state + risk connections
        self.scout = ScoutAgent(self.db, brain=self.brain)
        self.strategist = StrategistAgent(self.db, brain=self.brain,
                                           state_tracker=self.state_tracker,
                                           risk_manager=self.risk_manager)
        self.ghost_simulator = GhostSimulatorAgent(self.db, brain=self.brain,
                                                     state_tracker=self.state_tracker,
                                                     risk_manager=self.risk_manager)
        self.auditor = AuditorAgent(self.db, brain=self.brain, rca_engine=self.rca_engine)

        await self.scout.initialize()
        await self.strategist.initialize()
        await self.ghost_simulator.initialize()
        await self.auditor.initialize()

        # Chat engine - doğal dil AI yanıt motoru
        self.chat_engine = ChatEngine(self.db, self.brain)
        self.chat_engine.register_agent('Scout', self.scout)
        self.chat_engine.register_agent('Strategist', self.strategist)
        self.chat_engine.register_agent('Ghost', self.ghost_simulator)
        self.chat_engine.register_agent('Auditor', self.auditor)
        self.chat_engine.state_tracker = self.state_tracker
        self.chat_engine.risk_manager = self.risk_manager
        self.chat_engine.rca_engine = self.rca_engine

        logger.info("✓ All agents initialized with Brain + StateTracker + RiskManager")
        logger.info(f"✓ Monitoring {len(Config.WATCHLIST)} symbols: {Config.WATCHLIST}")
        logger.info("=" * 80)

    async def start(self):
        """Start all agents with crash resilience — one agent failing does NOT kill the system"""
        self.running = True
        logger.info("🚀 Starting agent system with crash resilience...")

        tasks = [
            self._resilient_task("Scout", self.scout.start),
            self._resilient_task("Strategist", self.strategist.start),
            self._resilient_task("GhostSimulator", self.ghost_simulator.start),
            self._resilient_task("Auditor", self.auditor.start),
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
            if self.task_queue:
                await self.task_queue.stop()
            if self.llm_client:
                await self.llm_client.close()
            await self.db.disconnect()
            logger.info("✓ All agents stopped")
        except Exception as e:
            logger.error(f"Error during shutdown: {e}")

    async def _chat_processor(self):
        """Chat mesajlarını hızlı kontrol et ve anında cevapla"""
        last_processed_id = 0
        while self.running:
            try:
                await asyncio.sleep(1)  # 1 saniyede bir kontrol (hızlı yanıt)
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
        """Monitor health of all agents and send heartbeats"""
        while self.running:
            try:
                await asyncio.sleep(30)
                
                scout_health = await self.scout.health_check()
                strategist_health = await self.strategist.health_check()
                ghost_health = await self.ghost_simulator.health_check()
                auditor_health = await self.auditor.health_check()
                brain_status = self.brain.get_brain_status()

                # Heartbeat'leri DB'ye yaz
                await self.db.update_heartbeat('scout', 
                    'running' if scout_health.get('healthy') else 'error', scout_health)
                await self.db.update_heartbeat('strategist',
                    'running' if strategist_health.get('healthy') else 'error', strategist_health)
                await self.db.update_heartbeat('ghost_simulator',
                    'running' if ghost_health.get('healthy') else 'error', ghost_health)
                await self.db.update_heartbeat('auditor',
                    'running' if auditor_health.get('healthy') else 'error', auditor_health)
                await self.db.update_heartbeat('brain', 'running', brain_status)
                await self.db.update_heartbeat('chat_engine', 'running', {
                    'registered_agents': list(self.chat_engine.agents.keys())
                })

                # LLM health
                if self.llm_bridge:
                    llm_available = await self.llm_client.health_check()
                    llm_stats = self.llm_bridge.get_stats()
                    await self.db.update_heartbeat('llm_brain', 
                        'running' if llm_available else 'degraded', llm_stats)

                # Brain pattern'larını yenile (yeni pattern'lar memory'ye yüklensin)
                await self.brain.refresh_patterns()

                # StateTracker: mode güncelle, state kaydet, snapshot al
                if self.state_tracker:
                    self.state_tracker.update_mode()
                    await self.state_tracker.save_state()
                    await self.state_tracker.snapshot_history()

                # Her 2 dakikada bir loglama
                if int(asyncio.get_event_loop().time()) % 120 < 35:
                    logger.info(f"📊 HEALTH CHECK")
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
                    if self.state_tracker:
                        st = self.state_tracker.state
                        logger.info(f"  📊 State: mode={self.state_tracker.get_mode()} | "
                                     f"trades={st['total_trades']} | "
                                     f"PnL={st['cumulative_pnl']:.2f}% | "
                                     f"DD={st['current_drawdown']:.2f}%")

            except Exception as e:
                logger.error(f"Health monitoring error: {e}")

    async def _directive_api_server(self):
        """Lightweight HTTP server for directive management (Master Control).
        Listens on port 3002 for directive CRUD operations."""
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
                healthy = await self.llm_client.health_check()
                models = await self.llm_client.list_models()
                client_stats = self.llm_client.get_stats()
                bridge_stats = self.llm_bridge.get_stats() if self.llm_bridge else {}
                return web.json_response({
                    "healthy": healthy,
                    "active_model": self.llm_client.model,
                    "available_models": models,
                    "call_count": client_stats.get("total_calls", 0),
                    "llm_stats": client_stats,
                    **bridge_stats,
                })
            except Exception as e:
                return web.json_response({"healthy": False, "error": str(e)})

        async def get_queue_status(request):
            if self.task_queue:
                return web.json_response(self.task_queue.get_stats())
            return web.json_response({"error": "Queue not initialized"})

        app = web.Application()
        app.router.add_get("/api/directives", get_directives)
        app.router.add_post("/api/directives", set_directive)
        app.router.add_delete("/api/directives", clear_directives)
        app.router.add_get("/api/llm/status", get_llm_status)
        app.router.add_get("/api/llm/queue", get_queue_status)

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
