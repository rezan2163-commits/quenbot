"""
Decision Router — Phase 3 Intel Upgrade
========================================
FastBrain ve Gemma kararlarını birleştirir.

Çalışma modları:
  - **shadow** (varsayılan): Her iki tarafı da yürüt, anlaşmazlıkları JSONL'e
    logla, ama **asla Gemma'yı override etme**. FastBrain raporlanır ama karar
    değiştirilmez. Risk sıfır.
  - **active**: FastBrain yüksek güvenli (prob ≥ T_HIGH veya ≤ T_LOW) ve
    Gemma ile **yön olarak uyumlu** ise FastBrain'in yönü geçer (LLM'i bypass).
    Aksi halde Gemma'nın kararı geçer. Hiçbir zaman Gemma'nın HOLD/reject'ini
    BUY/SELL'e çeviremez (conservative).

JSONL log'u (`DECISION_ROUTER_LOG_PATH`):
  {ts, symbol, gemma:{action,confidence}, fast:{prob,direction,confidence},
   agreed:bool, chosen:str, shadow:bool}

Rotasyon: `DECISION_ROUTER_MAX_LOG_ROWS` satırı aşınca dosya
`.decision_router_shadow.jsonl.1` olarak devrilir, yeni dosya açılır.
"""
from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

_ACTION_DIRECTION = {
    "BUY": "up", "LONG": "up", "ENTER_LONG": "up",
    "SELL": "down", "SHORT": "down", "ENTER_SHORT": "down",
    "HOLD": "neutral", "NO_ACTION": "neutral", "REJECT": "neutral",
}


def _norm_action(action: Optional[str]) -> str:
    if not action:
        return "HOLD"
    return str(action).upper().strip()


def _action_direction(action: str) -> str:
    return _ACTION_DIRECTION.get(_norm_action(action), "neutral")


@dataclass
class RouterDecision:
    chosen_by: str       # "gemma" | "fast_brain"
    action: str
    confidence: float
    agreed: bool
    shadow: bool
    gemma_action: str
    fast_direction: str
    fast_probability: Optional[float]
    reason: str
    ts: float = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "chosen_by": self.chosen_by,
            "action": self.action,
            "confidence": round(self.confidence, 4),
            "agreed": self.agreed,
            "shadow": self.shadow,
            "gemma_action": self.gemma_action,
            "fast_direction": self.fast_direction,
            "fast_probability": (round(self.fast_probability, 4)
                                 if self.fast_probability is not None else None),
            "reason": self.reason,
            "ts": self.ts,
        }


class DecisionRouter:
    def __init__(
        self,
        shadow: bool = True,
        log_path: str = "python_agents/.decision_router_shadow.jsonl",
        max_log_rows: int = 50000,
        t_high: float = 0.65,
        t_low: float = 0.45,
        event_bus=None,
    ) -> None:
        self.shadow = bool(shadow)
        self.log_path = Path(log_path)
        self.max_log_rows = int(max_log_rows)
        self.t_high = float(t_high)
        self.t_low = float(t_low)
        self.event_bus = event_bus

        self._log_rows = 0
        self._total_routed = 0
        self._total_agree = 0
        self._total_disagree = 0
        self._fast_overrides = 0
        self._last_by_symbol: Dict[str, RouterDecision] = {}

        try:
            self.log_path.parent.mkdir(parents=True, exist_ok=True)
            if self.log_path.exists():
                # naive: count lines once at startup
                with self.log_path.open("r", encoding="utf-8") as f:
                    self._log_rows = sum(1 for _ in f)
        except Exception as e:
            logger.warning("decision_router log init hata: %s", e)

    # ───────── core routing ─────────
    def route(
        self,
        symbol: str,
        gemma_decision: Optional[Dict[str, Any]],
        fast_prediction: Optional[Dict[str, Any]],
    ) -> RouterDecision:
        gemma_action = _norm_action((gemma_decision or {}).get("action") or (gemma_decision or {}).get("decision"))
        gemma_dir = _action_direction(gemma_action)
        gemma_conf = float((gemma_decision or {}).get("confidence", 0.5) or 0.5)

        fast_dir = (fast_prediction or {}).get("direction", "neutral")
        fast_prob = (fast_prediction or {}).get("probability")
        fast_conf = float((fast_prediction or {}).get("confidence", 0.0) or 0.0)

        agreed = bool(fast_dir != "neutral" and fast_dir == gemma_dir)
        chosen_by = "gemma"
        action = gemma_action
        confidence = gemma_conf
        reason = "gemma default"

        if fast_prediction is None:
            reason = "fast_brain unavailable → gemma"
        elif self.shadow:
            reason = "shadow mode (log only)"
        else:
            # active routing: only when fast_brain is strongly confident AND
            # agrees in direction with gemma. We never contradict gemma's
            # HOLD/REJECT; we only raise confidence if both align.
            if agreed and fast_prob is not None:
                if (fast_dir == "up" and fast_prob >= self.t_high) or \
                   (fast_dir == "down" and fast_prob <= self.t_low):
                    chosen_by = "fast_brain"
                    confidence = max(gemma_conf, fast_conf)
                    reason = f"fast_brain high-confidence {fast_dir} ({fast_prob:.3f})"
                    self._fast_overrides += 1

        decision = RouterDecision(
            chosen_by=chosen_by,
            action=action,
            confidence=confidence,
            agreed=agreed,
            shadow=self.shadow,
            gemma_action=gemma_action,
            fast_direction=str(fast_dir),
            fast_probability=(float(fast_prob) if fast_prob is not None else None),
            reason=reason,
        )

        self._total_routed += 1
        if fast_prediction is not None:
            if agreed:
                self._total_agree += 1
            elif fast_dir != "neutral":
                self._total_disagree += 1

        self._last_by_symbol[symbol] = decision
        self._append_log(symbol, decision)
        return decision

    def _append_log(self, symbol: str, decision: RouterDecision) -> None:
        try:
            row = {"symbol": symbol, **decision.to_dict()}
            with self.log_path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(row, ensure_ascii=False) + "\n")
            self._log_rows += 1
            if self._log_rows >= self.max_log_rows:
                self._rotate_log()
        except Exception as e:
            logger.debug("decision_router log yaz hata: %s", e)

    def _rotate_log(self) -> None:
        try:
            backup = self.log_path.with_suffix(self.log_path.suffix + ".1")
            if backup.exists():
                backup.unlink()
            self.log_path.rename(backup)
            self._log_rows = 0
        except Exception as e:
            logger.warning("decision_router rotate hata: %s", e)

    # ───────── health & metrics ─────────
    def snapshot(self, symbol: str) -> Optional[Dict[str, Any]]:
        d = self._last_by_symbol.get(symbol)
        return d.to_dict() if d else None

    async def health_check(self) -> Dict[str, Any]:
        return {
            "healthy": True,
            "shadow": self.shadow,
            "log_path": str(self.log_path),
            "log_rows": self._log_rows,
            "max_log_rows": self.max_log_rows,
            "routed_total": self._total_routed,
            "agree_total": self._total_agree,
            "disagree_total": self._total_disagree,
            "fast_overrides_total": self._fast_overrides,
            "tracked_symbols": len(self._last_by_symbol),
        }

    def metrics(self) -> Dict[str, Any]:
        return {
            "decision_router_routed_total": self._total_routed,
            "decision_router_agree_total": self._total_agree,
            "decision_router_disagree_total": self._total_disagree,
            "decision_router_fast_overrides_total": self._fast_overrides,
            "decision_router_shadow": 1 if self.shadow else 0,
        }


_router: Optional[DecisionRouter] = None


def get_decision_router(*args, **kwargs) -> DecisionRouter:
    global _router
    if _router is None:
        _router = DecisionRouter(*args, **kwargs)
    return _router


def _reset_decision_router_for_tests() -> None:
    global _router
    _router = None
