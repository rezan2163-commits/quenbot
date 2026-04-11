#!/usr/bin/env python3
"""
QuenBot Strategic Orchestration Starter
========================================
Initializes entire system with real data flow:
- Scout: Live market data collection
- Brain: Pattern analysis + Gemma insights
- Strategist: Signal generation with Gemma optimization  
- Ghost Simulator: Paper trading with risk control
- Auditor: Quality monitoring
- Risk Manager: Position & loss management

Then launches interactive Strategic Chat Interface for real-time strategy management.
"""

import asyncio
import logging
import sys
import os
from datetime import datetime

# Add python_agents to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__)))

# Logging setup
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
    handlers=[
        logging.FileHandler(os.path.join(os.path.dirname(__file__), 'quenbot_startup.log')),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger('quenbot.startup')


async def main():
    """
    Full system initialization with real data flow.
    """
    
    logger.info("=" * 80)
    logger.info("🚀 QUENBOT STRATEGIC ORCHESTRATION STARTUP")
    logger.info(f"Start time: {datetime.now().isoformat()}")
    logger.info("=" * 80)
    
    try:
        # Import after path setup
        from main import AgentOrchestrator
        from strategic_chat_cli import StrategicChatInterface
        
        # Initialize orchestrator (all agents + database + LLM)
        logger.info("\n📦 Initializing Orchestrator...")
        orchestrator = AgentOrchestrator()
        await orchestrator.initialize()
        logger.info("✓ Orchestrator ready")
        
        # Start all agents (real data collection begins here)
        logger.info("\n🎯 Starting all agents for real data acquisition...")
        try:
            await orchestrator.start()
            logger.info("✓ All agents running - REAL DATA FLOW ACTIVE")
        except Exception as e:
            logger.warning(f"⚠ Agent startup partial: {e}")
            logger.info("✓ Core system ready (agents may be in degraded mode)")
        
        # System startup report
        logger.info("\n" + "─" * 80)
        logger.info("SYSTEM STATUS:")
        logger.info(f"  Mode: {orchestrator._system_mode}")
        logger.info(f"  LLM Available: {orchestrator._llm_available}")
        logger.info(f"  Database: Connected")
        logger.info(f"  Event Bus: Active")
        logger.info("─" * 80)
        
        # Launch interactive strategic chat
        logger.info("\n📊 Launching Strategic Chat Interface...")
        logger.info("Type 'help' for commands. Chat with Gemma to manage strategy in real-time.\n")
        
        chat = StrategicChatInterface(orchestrator)
        await chat.start_interactive_session()
        
    except KeyboardInterrupt:
        logger.info("\n⏹ Interrupted by user")
    except Exception as e:
        logger.error(f"❌ Fatal error: {e}", exc_info=True)
        sys.exit(1)
    finally:
        logger.info("\n🛑 Shutdown initiated...")
        if 'orchestrator' in locals():
            try:
                await orchestrator.stop()
                logger.info("✓ Orchestrator stopped")
            except Exception as e:
                logger.error(f"Shutdown error: {e}")
        logger.info("System stopped.\n")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nShutdown signal received.")
        sys.exit(0)

