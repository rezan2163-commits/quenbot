#!/usr/bin/env python3
import asyncio
import logging
import os
import json
from datetime import datetime
from dotenv import load_dotenv

from config import Config
from database import Database
from brain import BrainModule, ChatHandler
from scout_agent import ScoutAgent
from strategist_agent import StrategistAgent
from ghost_simulator_agent import GhostSimulatorAgent
from auditor_agent import AuditorAgent

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
    handlers=[
        logging.FileHandler('/workspaces/quenbot/python_agents/agents.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

load_dotenv()

class AgentOrchestrator:
    def __init__(self):
        self.db = Database()
        self.brain = None
        self.chat_handler = None
        self.scout = None
        self.strategist = None
        self.ghost_simulator = None
        self.auditor = None
        self.running = False

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

        # Chat handler
        self.chat_handler = ChatHandler(self.db, self.brain)

        # Initialize agents with brain connection
        self.scout = ScoutAgent(self.db, brain=self.brain)
        self.strategist = StrategistAgent(self.db, brain=self.brain)
        self.ghost_simulator = GhostSimulatorAgent(self.db, brain=self.brain)
        self.auditor = AuditorAgent(self.db, brain=self.brain)

        await self.scout.initialize()
        await self.strategist.initialize()
        await self.ghost_simulator.initialize()
        await self.auditor.initialize()

        # Chat handler'a agent'ları kaydet
        self.chat_handler.register_agent('Scout', self.scout)
        self.chat_handler.register_agent('Strategist', self.strategist)
        self.chat_handler.register_agent('Ghost', self.ghost_simulator)
        self.chat_handler.register_agent('Auditor', self.auditor)

        logger.info("✓ All agents initialized with Brain connection")
        logger.info(f"✓ Monitoring {len(Config.WATCHLIST)} symbols: {Config.WATCHLIST}")
        logger.info("=" * 80)

    async def start(self):
        """Start all agents"""
        self.running = True
        logger.info("🚀 Starting agent system...")

        try:
            tasks = [
                self.scout.start(),
                self.strategist.start(),
                self.ghost_simulator.start(),
                self.auditor.start(),
                self._health_monitor(),
                self._chat_processor(),
            ]
            await asyncio.gather(*tasks)
        except KeyboardInterrupt:
            logger.info("Received shutdown signal")
        except Exception as e:
            logger.error(f"Fatal error: {e}")
            raise
        finally:
            await self.stop()

    async def stop(self):
        """Stop all agents gracefully"""
        self.running = False
        logger.info("🛑 Shutting down agent system...")

        try:
            await self.scout.stop()
            await self.strategist.stop()
            await self.ghost_simulator.stop()
            await self.auditor.stop()
            await self.db.disconnect()
            logger.info("✓ All agents stopped")
        except Exception as e:
            logger.error(f"Error during shutdown: {e}")

    async def _chat_processor(self):
        """Chat mesajlarını periyodik kontrol et ve cevapla"""
        while self.running:
            try:
                await asyncio.sleep(3)
                # Son işlenmemiş kullanıcı mesajlarını kontrol et
                messages = await self.db.get_chat_messages(limit=5)
                for msg in messages:
                    if msg['role'] == 'user' and not any(
                        m['role'] == 'assistant' and m['created_at'] > msg['created_at']
                        for m in messages
                    ):
                        response = await self.chat_handler.process_message(msg['message'])
                        await self.db.insert_chat_message('assistant', response, 'QuenBot AI')
            except Exception as e:
                logger.debug(f"Chat processor: {e}")

    async def _health_monitor(self):
        """Monitor health of all agents"""
        while self.running:
            try:
                await asyncio.sleep(120)
                
                scout_health = await self.scout.health_check()
                strategist_health = await self.strategist.health_check()
                ghost_health = await self.ghost_simulator.health_check()
                auditor_health = await self.auditor.health_check()
                brain_status = self.brain.get_brain_status()

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

            except Exception as e:
                logger.error(f"Health monitoring error: {e}")

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
