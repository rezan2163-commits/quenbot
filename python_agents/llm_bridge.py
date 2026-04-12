"""
QuenBot V2 — LLM-Powered Agent Intelligence Bridge
Connects each agent to the local Ollama LLM for dynamic decision-making.
This module enhances (not replaces) the existing hard-coded logic with
LLM-powered analysis, keeping the proven data pipeline intact.
"""

import asyncio
import json
import logging
from datetime import datetime, timezone
from typing import Any, Optional

from llm_client import get_llm_client, LLMResponse
from agent_instructions import get_agent_prompt_with_data, get_system_prompt
from directive_store import get_directive_store
from task_queue import get_task_queue, TaskPriority

logger = logging.getLogger("quenbot.llm_bridge")


class AgentLLMBridge:
    """
    Bridge between existing agents and the local LLM.
    Provides structured analysis methods for each agent type.
    All calls go through the task queue for CPU scheduling.
    """

    def __init__(self):
        self._client = get_llm_client()
        self._store = get_directive_store()
        self._queue = get_task_queue()
        self._enabled = True
        self._call_count = 0

    async def is_available(self) -> bool:
        """Check if the LLM backend is healthy."""
        if not self._enabled:
            return False
        return await self._client.health_check()

    def disable(self):
        self._enabled = False

    def enable(self):
        self._enabled = True

    async def _call_llm(
        self,
        agent_name: str,
        task: str,
        data_context: dict,
        priority: TaskPriority = TaskPriority.NORMAL,
        json_mode: bool = True,
        temperature: float = 0.3,
    ) -> Optional[dict]:
        """
        Submit an LLM call through the task queue.
        Returns parsed JSON response or None on failure.
        """
        if not self._enabled:
            return None

        directives = await self._store.get_full_directive(agent_name)
        system, prompt = get_agent_prompt_with_data(
            agent_name, data_context, directives=directives, task=task
        )

        async def _do_inference():
            return await self._client.generate(
                prompt=prompt,
                system=system,
                temperature=temperature,
                json_mode=json_mode,
            )

        # Submit to queue and wait
        task_id = await self._queue.submit(
            agent_name=agent_name,
            description=task[:80],
            coroutine_factory=_do_inference,
            priority=priority,
            dedup_key=f"{agent_name}:{task[:40]}",
        )

        if task_id is None:
            response: LLMResponse = await _do_inference()
        else:
            envelope = await self._queue.wait_for_result(task_id, timeout=120)
            if envelope is None:
                logger.warning("LLM queue timeout for %s", agent_name)
                return None
            if envelope.get("status") != "completed":
                logger.warning("LLM queue task failed for %s: %s", agent_name, envelope.get("error"))
                return None
            response: LLMResponse = envelope.get("result")

        if response is None:
            return None

        self._call_count += 1

        if not response.success:
            logger.warning(
                "LLM call failed for %s: %s", agent_name, response.error
            )
            return None

        result = response.as_json()
        if result is None and response.text:
            # Try to extract JSON from response text
            result = self._extract_json(response.text)

        if result is None:
            logger.debug(
                "LLM returned non-JSON for %s: %s",
                agent_name, response.text[:200]
            )
            return {"raw_text": response.text, "_parsed": False}

        result["_parsed"] = True
        result["_latency_ms"] = response.total_duration_ms
        return result

    def _extract_json(self, text: str) -> Optional[dict]:
        """Try to extract JSON from text that may contain non-JSON wrapping."""
        # Try to find JSON object in the text
        start = text.find("{")
        end = text.rfind("}") + 1
        if start >= 0 and end > start:
            try:
                return json.loads(text[start:end])
            except json.JSONDecodeError:
                pass
        return None

    # -----------------------------------------------------------------
    # Scout Agent LLM Methods
    # -----------------------------------------------------------------

    async def scout_analyze_anomaly(
        self,
        symbol: str,
        price_change_pct: float,
        volume_ratio: float,
        buy_sell_ratio: float,
        timeframe: str,
        recent_prices: list[float],
    ) -> Optional[dict]:
        """Ask LLM to classify a detected market anomaly."""
        return await self._call_llm(
            agent_name="scout",
            task="Classify this market anomaly and assess severity",
            data_context={
                "symbol": symbol,
                "price_change_pct": round(price_change_pct, 4),
                "volume_ratio": round(volume_ratio, 2),
                "buy_sell_ratio": round(buy_sell_ratio, 4),
                "timeframe": timeframe,
                "price_samples": recent_prices[-20:],  # Last 20 prices
            },
            priority=TaskPriority.HIGH,
        )

    async def scout_evaluate_data_quality(
        self, symbol: str, trade_count: int, sources: dict
    ) -> Optional[dict]:
        """Ask LLM to evaluate data quality for a symbol."""
        return await self._call_llm(
            agent_name="scout",
            task="Evaluate data quality and identify gaps",
            data_context={
                "symbol": symbol,
                "trade_count_last_hour": trade_count,
                "active_sources": sources,
            },
            priority=TaskPriority.LOW,
        )

    # -----------------------------------------------------------------
    # Strategist Agent LLM Methods
    # -----------------------------------------------------------------

    async def strategist_evaluate_signal(
        self,
        symbol: str,
        signal_type: str,
        direction: str,
        confidence: float,
        indicators: dict,
        regime: str,
        pattern_matches: int,
        recent_performance: dict,
    ) -> Optional[dict]:
        """Ask LLM to evaluate and refine a trading signal."""
        return await self._call_llm(
            agent_name="strategist",
            task="Evaluate this trading signal quality and suggest adjustments",
            data_context={
                "symbol": symbol,
                "signal_type": signal_type,
                "direction": direction,
                "confidence": round(confidence, 4),
                "indicators": indicators,
                "market_regime": regime,
                "pattern_matches": pattern_matches,
                "recent_signal_performance": recent_performance,
            },
            priority=TaskPriority.HIGH,
        )

    async def strategist_multi_timeframe_synthesis(
        self,
        symbol: str,
        timeframe_data: dict,
        current_regime: str,
    ) -> Optional[dict]:
        """Ask LLM to synthesize multi-timeframe analysis."""
        return await self._call_llm(
            agent_name="strategist",
            task="Synthesize multi-timeframe analysis and identify consensus direction",
            data_context={
                "symbol": symbol,
                "timeframes": timeframe_data,
                "regime": current_regime,
            },
            priority=TaskPriority.NORMAL,
        )

    # -----------------------------------------------------------------
    # Ghost Simulator LLM Methods
    # -----------------------------------------------------------------

    async def ghost_evaluate_simulation(
        self,
        symbol: str,
        side: str,
        entry_price: float,
        current_price: float,
        tp: float,
        sl: float,
        holding_time_min: int,
        pnl_pct: float,
        signal_type: str,
    ) -> Optional[dict]:
        """Ask LLM to evaluate an active simulation's status."""
        return await self._call_llm(
            agent_name="ghost_simulator",
            task="Evaluate this active simulation and suggest action",
            data_context={
                "symbol": symbol,
                "side": side,
                "entry_price": entry_price,
                "current_price": current_price,
                "take_profit": tp,
                "stop_loss": sl,
                "holding_time_minutes": holding_time_min,
                "current_pnl_pct": round(pnl_pct, 4),
                "signal_type": signal_type,
            },
            priority=TaskPriority.HIGH,
        )

    async def ghost_post_trade_analysis(
        self,
        closed_sim: dict,
    ) -> Optional[dict]:
        """Ask LLM to analyze a closed simulation for learning."""
        return await self._call_llm(
            agent_name="ghost_simulator",
            task="Analyze this closed trade and extract learning insights",
            data_context={
                "symbol": closed_sim.get("symbol"),
                "side": closed_sim.get("side"),
                "entry_price": closed_sim.get("entry_price"),
                "exit_price": closed_sim.get("exit_price"),
                "pnl_pct": closed_sim.get("pnl_pct"),
                "reason": closed_sim.get("close_reason"),
                "signal_type": closed_sim.get("metadata", {}).get("signal_type"),
                "holding_time": closed_sim.get("holding_time_min"),
            },
            priority=TaskPriority.LOW,
        )

    # -----------------------------------------------------------------
    # Auditor Agent LLM Methods
    # -----------------------------------------------------------------

    async def auditor_analyze_failures(
        self,
        failure_summary: dict,
        win_rate: float,
        avg_win_pct: float,
        avg_loss_pct: float,
        top_failure_types: dict,
    ) -> Optional[dict]:
        """Ask LLM to analyze failure patterns and suggest corrections."""
        return await self._call_llm(
            agent_name="auditor",
            task="Analyze trading failure patterns and generate correction recommendations",
            data_context={
                "overall_win_rate": round(win_rate, 4),
                "avg_win_pct": round(avg_win_pct, 4),
                "avg_loss_pct": round(avg_loss_pct, 4),
                "failure_summary": failure_summary,
                "top_failure_types": top_failure_types,
            },
            priority=TaskPriority.LOW,
        )

    async def auditor_evaluate_rca(
        self,
        simulation: dict,
        rca_result: dict,
    ) -> Optional[dict]:
        """Ask LLM to validate and enhance an RCA result."""
        return await self._call_llm(
            agent_name="auditor",
            task="Validate this root cause analysis and suggest specific parameter adjustments",
            data_context={
                "simulation_symbol": simulation.get("symbol"),
                "simulation_side": simulation.get("side"),
                "simulation_pnl_pct": simulation.get("pnl_pct"),
                "rca_failure_type": rca_result.get("failure_type"),
                "rca_confidence": rca_result.get("confidence"),
                "rca_explanation": rca_result.get("explanation"),
            },
            priority=TaskPriority.LOW,
        )

    # -----------------------------------------------------------------
    # Brain LLM Methods
    # -----------------------------------------------------------------

    async def brain_synthesize_state(
        self,
        pattern_count: int,
        accuracy: float,
        active_signals: int,
        regime: str,
        recent_trades_summary: dict,
        signal_performance: dict,
    ) -> Optional[dict]:
        """Ask LLM to synthesize overall system state and provide recommendations."""
        return await self._call_llm(
            agent_name="brain",
            task="Synthesize current system state and provide strategic recommendations",
            data_context={
                "total_patterns": pattern_count,
                "prediction_accuracy": round(accuracy, 4),
                "active_signals": active_signals,
                "market_regime": regime,
                "recent_trades": recent_trades_summary,
                "signal_type_performance": signal_performance,
            },
            priority=TaskPriority.NORMAL,
        )

    async def brain_predict_with_context(
        self,
        symbol: str,
        snapshot_data: dict,
        matching_patterns: int,
        avg_similarity: float,
        indicators: dict,
    ) -> Optional[dict]:
        """Ask LLM to enhance a pattern-based prediction with contextual analysis."""
        return await self._call_llm(
            agent_name="brain",
            task="Enhance this prediction with contextual market analysis",
            data_context={
                "symbol": symbol,
                "current_snapshot": snapshot_data,
                "matching_patterns": matching_patterns,
                "avg_similarity": round(avg_similarity, 4),
                "technical_indicators": indicators,
            },
            priority=TaskPriority.NORMAL,
        )

    # -----------------------------------------------------------------
    # Chat / General Methods
    # -----------------------------------------------------------------

    async def call_llm(
        self,
        task: str,
        system: str,
        prompt: str,
        json_mode: bool = False,
        temperature: float = 0.3,
    ) -> Optional[dict]:
        """
        Public method for general LLM calls (e.g., from chat interface).
        
        Returns: {"success": bool, "text": str} or {"success": False, "error": str}
        """
        if not self._enabled:
            return {"success": False, "error": "LLM not available"}

        try:
            response = await self._client.generate(
                prompt=prompt,
                system=system,
                temperature=temperature,
                json_mode=json_mode,
            )

            return {
                "success": response.success,
                "text": response.text,
                "error": response.error if not response.success else None,
                "latency_ms": response.total_duration_ms,
            }
        except Exception as e:
            logger.error(f"LLM call failed for {task}: {e}")
            return {"success": False, "error": str(e)}

    async def chat_respond(
        self,
        user_message: str,
        system_context: dict,
    ) -> str:
        """Generate a chat response using the LLM."""
        directives = await self._store.get_full_directive("brain")

        system_prompt = (
            "You are QuenBot AI Assistant, a cryptocurrency trading intelligence system. "
            "Respond in Turkish when the user writes in Turkish. Be concise and data-driven. "
            "You have access to the following system state:\n\n"
            + json.dumps(system_context, default=str, separators=(",", ":"))[:1000]
        )

        if directives:
            system_prompt = f"MASTER DIRECTIVES:\n{directives}\n\n{system_prompt}"

        response = await self._client.generate(
            prompt=user_message,
            system=system_prompt,
            temperature=0.5,
            json_mode=False,
        )

        if response.success and response.text:
            return response.text.strip()
        return "LLM yanıt üretemedi. Mevcut durumu kontrol etmek için 'durum' yazın."

    def get_stats(self) -> dict:
        return {
            "enabled": self._enabled,
            "call_count": self._call_count,
            "llm_stats": self._client.get_stats(),
            "queue_stats": self._queue.get_stats(),
        }


# Singleton
_bridge: Optional[AgentLLMBridge] = None


def get_llm_bridge() -> AgentLLMBridge:
    global _bridge
    if _bridge is None:
        _bridge = AgentLLMBridge()
    return _bridge
