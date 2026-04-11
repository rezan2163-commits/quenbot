#!/usr/bin/env python3
"""
QuenBot Professional Strategic Chat Interface
==============================================
Interactive dialog with Gemma 4 to manage system strategy, monitoring, and agent orchestration.
User ↔ Gemma ↔ Brain ↔ Agents ↔ Market Data

Real data flow:
- Scout: Collects live market anomalies → Database
- Brain: Analyzes patterns, generates insights via Gemma
- Strategist: Creates trade signals from brain analysis
- GhostSimulator: Paper trades with risk controls
- Auditor: Quality control on outcomes
- Risk Manager: Enforces position/daily/drawdown limits
"""

import asyncio
import json
import logging
import sys
from datetime import datetime, timezone
from typing import Dict, List, Any, Optional, Tuple
from dataclasses import dataclass

logger = logging.getLogger("quenbot.strategic_chat")


@dataclass
class ChatMessage:
    role: str  # "user" | "assistant" | "system"
    content: str
    timestamp: datetime


class StrategicChatInterface:
    """Professional interactive chat for strategy management and monitoring"""

    def __init__(self, orchestrator):
        self.orchestrator = orchestrator
        self.chat_history: List[ChatMessage] = []
        self._context_cache = {}
        self._session_id = f"session_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        self._strategy_state = {
            "mode": "PRODUCTION",
            "aggressiveness": "balanced",  # aggressive | balanced | conservative
            "risk_level": "medium",  # low | medium | high
            "active_pairs": [],
            "constraints": {},
        }

    async def start_interactive_session(self):
        """Main interactive chat loop"""
        print("\n" + "=" * 80)
        print("🤖 QUENBOT Strategic Chat Interface")
        print(f"Session: {self._session_id}")
        print("=" * 80)
        print("\nType 'help' for commands, 'status' for system state, 'exit' to quit.\n")

        # Initial system status
        await self._show_system_status()

        while True:
            try:
                user_input = input("\n📊 You: ").strip()
                if not user_input:
                    continue

                if user_input.lower() == "exit":
                    print("👋 Shutting down gracefully...")
                    break

                # Process user command
                response = await self._process_user_message(user_input)
                print(f"\n🧠 Gemma: {response['response']}")

                # Show any actions taken
                if response.get("actions"):
                    print("\n📝 Actions Taken:")
                    for action in response["actions"]:
                        print(f"   → {action['description']}")
                        if action.get("result"):
                            print(f"     Result: {action['result']}")

                # Show updated state if changed
                if response.get("state_changed"):
                    await self._show_strategy_state()

            except KeyboardInterrupt:
                print("\n\n⏹ Chat interrupted. Type 'exit' to quit.")
                continue
            except Exception as e:
                print(f"\n❌ Error: {e}")
                logger.exception("Error in chat loop")

    async def _process_user_message(self, user_input: str) -> Dict[str, Any]:
        """Parse and execute user message through Gemma"""
        self.chat_history.append(
            ChatMessage(role="user", content=user_input, timestamp=datetime.now(timezone.utc))
        )

        # Get system context
        context = await self._build_context()

        # Route special commands
        if user_input.lower() == "help":
            return self._show_help()
        elif user_input.lower() == "status":
            return await self._show_system_status()
        elif user_input.lower().startswith("pair "):
            return await self._handle_pair_command(user_input)
        elif "strategy" in user_input.lower():
            return await self._handle_strategy_command(user_input, context)
        elif "risk" in user_input.lower():
            return await self._handle_risk_command(user_input, context)
        elif "analyze" in user_input.lower():
            return await self._handle_analyze_command(user_input, context)
        elif "signal" in user_input.lower() or "trade" in user_input.lower():
            return await self._handle_signal_command(user_input, context)
        else:
            # General chat through Gemma
            return await self._chat_with_gemma(user_input, context)

    async def _build_context(self) -> Dict[str, Any]:
        """Build complete system context for Gemma decision-making"""
        state = self.orchestrator.state_tracker.state if self.orchestrator.state_tracker else {}
        brain_status = self.orchestrator.brain.get_brain_status() if self.orchestrator.brain else {}
        risk_status = self.orchestrator.risk_manager.get_status() if self.orchestrator.risk_manager else {}

        # Get recent market signals
        recent_signals = await self._get_recent_signals()

        return {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "mode": state.get("mode"),
            "total_trades": state.get("total_trades", 0),
            "total_pnl": state.get("total_pnl", 0),
            "win_rate": state.get("win_rate", 0),
            "patterns_learned": brain_status.get("total_patterns", 0),
            "active_patterns": brain_status.get("active_patterns", []),
            "risk_level": risk_status.get("risk_level", "unknown"),
            "daily_trades_remaining": risk_status.get("daily_trades_remaining", 0),
            "open_positions": len(state.get("open_positions", [])),
            "recent_signals": recent_signals,
            "strategy_state": self._strategy_state,
        }

    async def _get_recent_signals(self) -> List[Dict[str, Any]]:
        """Get recent trading signals from database"""
        try:
            recent = await self.orchestrator.db.fetch(
                """
                SELECT symbol, signal_type, strength, created_at
                FROM signals
                WHERE created_at >= NOW() - INTERVAL '1 hour'
                ORDER BY created_at DESC
                LIMIT 10
            """
            )
            return [dict(row) for row in recent] if recent else []
        except Exception as e:
            logger.warning(f"Failed to fetch recent signals: {e}")
            return []

    async def _handle_strategy_command(self, user_input: str, context: Dict[str, Any]) -> Dict[str, Any]:
        """Handle strategy update commands"""
        # Examples: "make strategy aggressive", "switch to conservative mode", etc.

        prompt = f"""
        The user is adjusting trading strategy.
        
        User command: {user_input}
        Current strategy state: {json.dumps(self._strategy_state, indent=2)}
        System context: {json.dumps(context, indent=2)}
        
        Provide:
        1. Recommended changes with explanation
        2. Impact on risk level
        3. Suggested parameter values (aggressiveness: aggressive|balanced|conservative, risk_level: low|medium|high)
        
        Format as JSON with keys: recommendation, risk_impact, parameters
        """

        response = await self.orchestrator.llm_bridge.call_llm(
            task="strategy_update",
            system="You are QuenBot's strategy advisor. Recommend trading strategy adjustments.",
            prompt=prompt,
            json_mode=True,
        )

        if response and response.get("success"):
            try:
                advice = json.loads(response.get("text", "{}"))
                
                # Update strategy state based on advice
                if "parameters" in advice:
                    params = advice["parameters"]
                    if "aggressiveness" in params:
                        self._strategy_state["aggressiveness"] = params["aggressiveness"]
                    if "risk_level" in params:
                        self._strategy_state["risk_level"] = params["risk_level"]
                    if "constraints" in params:
                        self._strategy_state["constraints"].update(params["constraints"])

                # Log to database
                try:
                    await self.orchestrator.db.execute(
                        """
                        INSERT INTO strategy_updates (timestamp, user_intent, recommendation, parameters, applied)
                        VALUES ($1, $2, $3, $4, $5)
                        """,
                        datetime.now(timezone.utc),
                        user_input,
                        advice.get("recommendation", ""),
                        json.dumps(advice.get("parameters", {})),
                        True
                    )
                except Exception as e:
                    logger.warning(f"Failed to log strategy update: {e}")

                return {
                    "response": f"✓ Strategy updated. {advice.get('recommendation', '')}",
                    "actions": [
                        {
                            "description": f"Updated strategy to {params.get('aggressiveness', 'balanced')} mode",
                            "result": advice.get("parameters", {}),
                        }
                    ],
                    "state_changed": True,
                }
            except json.JSONDecodeError:
                return {"response": response.get("text", "Strategy update processing started"), "actions": []}

        return {"response": "Unable to process strategy update", "actions": []}

    async def _handle_risk_command(self, user_input: str, context: Dict[str, Any]) -> Dict[str, Any]:
        """Handle risk management commands"""
        prompt = f"""
        The user wants to adjust risk parameters.
        
        User command: {user_input}
        Current risk state: {context.get('risk_level', 'unknown')}
        Recent performance: Win rate {context.get('win_rate')}%, Trades {context.get('total_trades')}
        
        Recommend:
        1. New stop loss percentage (2-5%)
        2. New take profit percentage (3-8%)
        3. Max position size
        4. Daily trade limit (5-20)
        
        Format as JSON.
        """

        response = await self.orchestrator.llm_bridge.call_llm(
            task="risk_adjustment",
            system="You are QuenBot's risk manager. Recommend safe but profitable risk settings.",
            prompt=prompt,
            json_mode=True,
        )

        if response and response.get("success"):
            try:
                advice = json.loads(response.get("text", "{}"))
                
                # Update risk strategy state
                self._strategy_state["constraints"] = advice.get("parameters", {})

                return {
                    "response": f"✓ Risk parameters updated. Stop loss: {advice.get('stop_loss')}%, TP: {advice.get('take_profit')}%",
                    "actions": [
                        {
                            "description": "Updated risk limits",
                            "result": advice,
                        }
                    ],
                    "state_changed": True,
                }
            except json.JSONDecodeError:
                return {"response": response.get("text", "Risk adjustment processed"), "actions": []}

        return {"response": "Unable to adjust risk parameters", "actions": []}

    async def _handle_analyze_command(self, user_input: str, context: Dict[str, Any]) -> Dict[str, Any]:
        """Handle market analysis requests"""
        # Extract symbol if mentioned
        words = user_input.split()
        symbol = None
        for word in words:
            if word.isupper() and len(word) in [6, 7]:  # likely a symbol like BTCUSDT
                symbol = word
                break

        if symbol:
            analysis = await self._analyze_symbol(symbol, context)
            return analysis

        # General market analysis
        prompt = f"""
        Provide market analysis based on current system state.
        
        Context: {json.dumps(context, indent=2)}
        
        Analyze:
        1. Overall market condition (bullish/bearish/sideways)
        2. Key opportunities
        3. Risks to watch
        
        Be concise, actionable.
        """

        response = await self.orchestrator.llm_bridge.call_llm(
            task="market_analysis",
            system="You are QuenBot's market analyst.",
            prompt=prompt,
        )

        if response and response.get("success"):
            return {
                "response": response.get("text", "Analysis complete"),
                "actions": [],
            }

        return {"response": "Unable to perform analysis", "actions": []}

    async def _analyze_symbol(self, symbol: str, context: Dict[str, Any]) -> Dict[str, Any]:
        """Detailed analysis of a specific symbol"""
        try:
            # Get symbol data from database
            symbol_data = await self.orchestrator.db.fetch(
                """
                SELECT symbol, COUNT(*) as trades, SUM(quantity) as volume,
                       AVG(price) as avg_price, MAX(price) as high, MIN(price) as low
                FROM trades
                WHERE symbol = $1 AND created_at >= NOW() - INTERVAL '1 day'
                GROUP BY symbol
            """,
                symbol,
            )

            if not symbol_data:
                return {"response": f"No recent data for {symbol}", "actions": []}

            data = dict(symbol_data[0]) if symbol_data else {}

            prompt = f"""
            Analyze this cryptocurrency:
            
            Symbol: {symbol}
            24h Trades: {data.get('trades', 0)}
            24h Volume: {data.get('volume', 0):.2f}
            Avg Price: {data.get('avg_price', 0):.4f}
            Range: {data.get('low', 0):.4f} - {data.get('high', 0):.4f}
            
            Current patterns: {context.get('active_patterns', [])}
            
            Provide trading recommendation: BUY / SELL / HOLD with confidence (0-100%).
            """

            response = await self.orchestrator.llm_bridge.call_llm(
                task=f"analyze_{symbol}",
                system="You are QuenBot's symbol analyst.",
                prompt=prompt,
            )

            if response and response.get("success"):
                return {
                    "response": response.get("text", "Analysis complete"),
                    "actions": [
                        {
                            "description": f"Analyzed {symbol}",
                            "result": data,
                        }
                    ],
                }

            return {"response": f"Unable to analyze {symbol}", "actions": []}

        except Exception as e:
            logger.error(f"Symbol analysis failed: {e}")
            return {"response": f"Error analyzing {symbol}: {e}", "actions": []}

    async def _handle_signal_command(self, user_input: str, context: Dict[str, Any]) -> Dict[str, Any]:
        """Handle trading signal requests"""
        prompt = f"""
        Generate trading signals based on current system state.
        
        User request: {user_input}
        System state: {json.dumps(context, indent=2)}
        
        Identify:
        1. Symbols showing strong signals right now
        2. Risk/reward ratio for each
        3. Position sizing recommendation
        
        Format as JSON with array of signals.
        """

        response = await self.orchestrator.llm_bridge.call_llm(
            task="generate_signals",
            system="You are QuenBot's signal generator.",
            prompt=prompt,
            json_mode=True,
        )

        if response and response.get("success"):
            try:
                signals = json.loads(response.get("text", "[]"))
                
                # Apply signals through ghost simulator
                actions = []
                for signal in signals if isinstance(signals, list) else [signals]:
                    if self.orchestrator.ghost_simulator:
                        result = await self.orchestrator.ghost_simulator.evaluate_signal(
                            signal.get("symbol"),
                            signal.get("direction"),
                            signal.get("strength"),
                        )
                        actions.append({
                            "description": f"Evaluated signal for {signal.get('symbol')}",
                            "result": result,
                        })

                return {
                    "response": f"✓ Generated {len(signals)} signals",
                    "actions": actions,
                }
            except json.JSONDecodeError:
                return {"response": response.get("text", "Signal generation complete"), "actions": []}

        return {"response": "Unable to generate signals", "actions": []}

    async def _handle_pair_command(self, user_input: str) -> Dict[str, Any]:
        """Handle watchlist pair commands: 'pair add BTCUSDT' or 'pair remove ETHUSDT'"""
        parts = user_input.split()
        if len(parts) < 3:
            return {"response": "Usage: pair [add|remove] [SYMBOL]", "actions": []}

        action = parts[1].lower()
        symbol = parts[2].upper()

        try:
            if action == "add":
                if symbol not in self._strategy_state["active_pairs"]:
                    self._strategy_state["active_pairs"].append(symbol)
                    # Log to database
                    try:
                        await self.orchestrator.db.execute(
                            "INSERT INTO watchlist (symbol, market_type, description) VALUES ($1, $2, $3) ON CONFLICT DO NOTHING",
                            symbol, "spot", f"Added via chat"
                        )
                    except Exception as e:
                        logger.debug(f"Watchlist DB update: {e}")
                
                return {
                    "response": f"✓ Added {symbol} to watchlist",
                    "actions": [{"description": f"Watchlist updated: +{symbol}"}],
                    "state_changed": True,
                }

            elif action == "remove":
                if symbol in self._strategy_state["active_pairs"]:
                    self._strategy_state["active_pairs"].remove(symbol)
                return {
                    "response": f"✓ Removed {symbol} from watchlist",
                    "actions": [{"description": f"Watchlist updated: -{symbol}"}],
                    "state_changed": True,
                }
            else:
                return {"response": f"Unknown action: {action}. Use 'add' or 'remove'", "actions": []}

        except Exception as e:
            logger.error(f"Pair command error: {e}")
            return {"response": f"Error: {e}", "actions": []}

    async def _chat_with_gemma(self, user_input: str, context: Dict[str, Any]) -> Dict[str, Any]:
        """General chat with Gemma using system context"""
        prompt = f"""
        You are QuenBot's AI advisor. Help the user understand the system and make trading decisions.
        
        System state:
        - Mode: {context.get('mode')}
        - Total trades: {context.get('total_trades')}
        - Win rate: {context.get('win_rate')}%
        - PnL: ${context.get('total_pnl'):.2f}
        - Patterns learned: {context.get('patterns_learned')}
        - Risk level: {context.get('risk_level')}
        
        User: {user_input}
        
        Respond helpfully in Turkish. Be concise and actionable.
        """

        response = await self.orchestrator.llm_bridge.call_llm(
            task="general_chat",
            system="You are QuenBot's friendly advisor.",
            prompt=prompt,
        )

        if response and response.get("success"):
            return {
                "response": response.get("text", "I understand."),
                "actions": [],
            }

        return {"response": "Unable to process your request.", "actions": []}

    async def _show_system_status(self) -> Dict[str, Any]:
        """Show comprehensive system status"""
        context = await self._build_context()

        print("\n" + "─" * 80)
        print("📊 SYSTEM STATUS")
        print("─" * 80)
        print(f"Mode: {context.get('mode')} | Trades: {context.get('total_trades')} | Win Rate: {context.get('win_rate'):.1f}%")
        print(f"PnL: ${context.get('total_pnl'):.2f} | Patterns: {context.get('patterns_learned')} | Risk: {context.get('risk_level')}")
        print(f"Open Positions: {context.get('open_positions')} | Daily Trades Left: {context.get('daily_trades_remaining')}")
        print("─" * 80)

        return {"response": "Status shown above", "actions": []}

    async def _show_strategy_state(self):
        """Display current strategy configuration"""
        print("\n─ Strategy Configuration:")
        print(f"  Mode: {self._strategy_state['mode']}")
        print(f"  Aggressiveness: {self._strategy_state['aggressiveness']}")
        print(f"  Risk Level: {self._strategy_state['risk_level']}")
        print(f"  Active Pairs: {', '.join(self._strategy_state['active_pairs']) or 'None'}")

    def _show_help(self) -> Dict[str, Any]:
        """Show available commands"""
        help_text = """
COMMANDS:
  status              - Show system status
  strategy [action]   - Adjust trading strategy
    Example: "make strategy aggressive" or "switch to conservative"
  
  risk [action]       - Manage risk parameters
    Example: "increase stop loss to 5%" or "reduce daily trades to 10"
  
  analyze [symbol]    - Analyze a specific cryptocurrency
    Example: "analyze BTCUSDT"
  
  signal/trade        - Generate trading signals
    Example: "what signals do you see right now?"
  
  pair add [SYMBOL]   - Add to watchlist
    Example: "pair add ETHUSDT"
  
  pair remove [SYMBOL] - Remove from watchlist
    Example: "pair remove ALTUSDT"
  
  help                - This message
  exit                - Shut down gracefully
        """
        print(help_text)
        return {"response": "Commands listed above", "actions": []}


async def main():
    """Start interactive strategic chat session"""
    import os
    from dotenv import load_dotenv

    load_dotenv()

    # Import orchestrator
    from main import AgentOrchestrator

    # Initialize and start
    orchestrator = AgentOrchestrator()
    await orchestrator.initialize()

    chat = StrategicChatInterface(orchestrator)
    try:
        await chat.start_interactive_session()
    finally:
        await orchestrator.shutdown()


if __name__ == "__main__":
    asyncio.run(main())
