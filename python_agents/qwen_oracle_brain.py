"""
qwen_oracle_brain.py — Merkezi Oracle orkestrasyon beyni (§11)
===============================================================
12 oracle kanalı + IFI (factor graph) + confluence okur; şu an shadow
modda direktif üretir (kararlara otomatik uygulanmaz, sadece log'lanır).

Döngü:
- observe_interval: her N saniyede gözlem + heuristic karar (LLM'siz)
- learn_interval (default 10dk): son direktiflerin çıktılarıyla ağırlık revizyonu
- teach_interval (default 60dk): opsiyonel LLM çağrısı, insan-okunur özet
- daily_report_hour: günlük rapor (stdout/log)

Hard: shadow=True default; LLM çağrısı try/except ile sarılı; trip edildiyse
her şey no-op.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Deque, Dict, List, Optional, Tuple

from qwen_oracle_schemas import (
    DirectiveAction, DirectiveSeverity, OracleDirective, OracleObservation,
    ReasoningTrace,
)

logger = logging.getLogger(__name__)


# Heuristic thresholds (default; brain revize edebilir)
_IFI_HIGH = 0.75
_IFI_LOW = 0.25
_DIR_STRONG = 0.5
_DIR_BIAS = 0.2


@dataclass
class _BrainStats:
    observations: int = 0
    directives_emitted: int = 0
    learn_cycles: int = 0
    teach_cycles: int = 0
    llm_calls: int = 0
    llm_errors: int = 0
    last_tick_ts: float = 0.0


class QwenOracleBrain:
    def __init__(
        self,
        event_bus: Any = None,
        feature_store: Any = None,
        signal_bus: Any = None,
        factor_graph: Any = None,
        confluence_engine: Any = None,
        llm_bridge: Any = None,
        rag: Any = None,
        symbols: Optional[List[str]] = None,
        shadow: bool = True,
        observe_interval_sec: float = 5.0,
        learn_interval_sec: float = 600.0,
        teach_interval_sec: float = 3600.0,
        daily_report_hour: int = 3,
        max_trace_log: int = 4096,
    ) -> None:
        self.event_bus = event_bus
        self.feature_store = feature_store
        self.signal_bus = signal_bus
        self.factor_graph = factor_graph
        self.confluence_engine = confluence_engine
        self.llm_bridge = llm_bridge
        self.rag = rag
        self.symbols = list(symbols or [])
        self.shadow = bool(shadow)
        self.observe_interval = float(observe_interval_sec)
        self.learn_interval = float(learn_interval_sec)
        self.teach_interval = float(teach_interval_sec)
        self.daily_report_hour = int(daily_report_hour)
        self._trace_log: Deque[ReasoningTrace] = deque(maxlen=int(max_trace_log))
        self._directive_log: Deque[OracleDirective] = deque(maxlen=int(max_trace_log))
        self._last_directive_by_symbol: Dict[str, OracleDirective] = {}
        self._last_learn_ts = 0.0
        self._last_teach_ts = 0.0
        self._last_daily_report_day: int = -1
        self._stats = _BrainStats()
        self._initialized = False
        self._running = False
        self._task: Optional[asyncio.Task] = None

    async def initialize(self) -> None:
        self._initialized = True
        logger.info(
            "QwenOracleBrain ready (shadow=%s, observe=%.1fs, symbols=%d, rag=%s, llm=%s)",
            self.shadow, self.observe_interval, len(self.symbols),
            "yes" if self.rag is not None else "no",
            "yes" if self.llm_bridge is not None else "no",
        )

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._main_loop())

    async def stop(self) -> None:
        self._running = False
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
            self._task = None

    # ── Observation & heuristic decision ────────────────────────────

    def _safety_tripped(self) -> bool:
        sn = getattr(self, "_safety_net", None)
        # Safety_net bağlı değilse tripping kontrol edilemez → false
        try:
            if sn is None:
                return False
            return bool(getattr(sn, "tripped", False))
        except Exception:
            return False

    def _collect_observation(self, symbol: str) -> OracleObservation:
        obs = OracleObservation(symbol=symbol)
        # Channels from signal bus
        channels: Dict[str, float] = {}
        bus = self.signal_bus
        if bus is not None:
            try:
                snaps = bus.all_snapshots()
                if isinstance(snaps, dict):
                    block = snaps.get(symbol) or {}
                    for k, v in block.items():
                        try:
                            val = v.get("value") if isinstance(v, dict) else v
                            if val is None:
                                continue
                            fv = float(val)
                            if fv == fv:
                                channels[k] = fv
                        except (TypeError, ValueError):
                            continue
            except Exception as e:
                logger.debug("Brain channel read fail: %s", e)
        obs.channels = channels
        # IFI
        try:
            if self.factor_graph is not None:
                snap = self.factor_graph.snapshot(symbol)
                if snap:
                    obs.ifi = snap.get("ifi")
                    obs.ifi_direction = snap.get("direction")
        except Exception as e:
            logger.debug("Brain ifi read fail: %s", e)
        # Confluence
        try:
            if self.confluence_engine is not None and hasattr(self.confluence_engine, "last_score"):
                obs.confluence_score = self.confluence_engine.last_score(symbol)
        except Exception:
            pass
        return obs

    def _heuristic_directive(self, obs: OracleObservation) -> OracleDirective:
        action: DirectiveAction = "MONITOR"
        severity: DirectiveSeverity = "info"
        params: Dict[str, Any] = {}
        rationale_bits: List[str] = []
        ifi = obs.ifi if obs.ifi is not None else 0.0
        direction = obs.ifi_direction if obs.ifi_direction is not None else 0.0
        conf = obs.confluence_score if obs.confluence_score is not None else 0.0

        # Critical: topology + mirror_flow high
        topo = obs.channels.get("topological_whale_birth", 0.0)
        mirror = obs.channels.get("mirror_execution_strength", 0.0)
        if topo >= 0.8 and mirror >= 0.8:
            action = "HOLD_OFF"
            severity = "critical"
            params = {"reason": "coordinated_whale_activity"}
            rationale_bits.append(f"topo={topo:.2f}+mirror={mirror:.2f} coordinated regime")

        # High IFI with strong direction
        elif ifi >= _IFI_HIGH and abs(direction) >= _DIR_STRONG:
            action = "BIAS_DIRECTION"
            severity = "high"
            params = {"direction": "long" if direction > 0 else "short",
                      "ifi": ifi, "conviction": abs(direction)}
            rationale_bits.append(f"IFI={ifi:.2f}, dir={direction:+.2f} strong signal")

        # High entropy cooling → tighten stops
        elif obs.channels.get("entropy_cooling", 0.0) >= 0.7:
            action = "TIGHTEN_STOPS"
            severity = "medium"
            params = {"scale": 0.7}
            rationale_bits.append(f"entropy_cooling={obs.channels.get('entropy_cooling'):.2f}")

        # Wasserstein drift high → risk down
        elif abs(obs.channels.get("wasserstein_drift_zscore", 0.0)) >= 0.7:
            action = "ADJUST_RISK"
            severity = "medium"
            params = {"kelly_scale": 0.5,
                      "reason": "distribution_shift"}
            rationale_bits.append(
                f"wasserstein_zscore={obs.channels.get('wasserstein_drift_zscore'):+.2f}"
            )

        # Mild directional bias
        elif ifi >= 0.5 and abs(direction) >= _DIR_BIAS:
            action = "BIAS_DIRECTION"
            severity = "low"
            params = {"direction": "long" if direction > 0 else "short",
                      "ifi": ifi}
            rationale_bits.append(f"mild bias IFI={ifi:.2f} dir={direction:+.2f}")

        # Default: MONITOR
        confidence = min(1.0, max(0.0, ifi * (0.6 + 0.4 * abs(direction))))
        rationale = "; ".join(rationale_bits) or f"IFI={ifi:.2f} dir={direction:+.2f} conf={conf:.2f}"

        return OracleDirective(
            symbol=obs.symbol,
            action=action,
            severity=severity,
            confidence=confidence,
            rationale=rationale,
            params=params,
            shadow=self.shadow,
        )

    async def _tick_symbol(self, symbol: str) -> None:
        if self._safety_tripped():
            return
        # Aşama 3 — emergency lockdown short-circuit (no observation, no directive).
        try:
            from emergency_lockdown import is_engaged as _emergency_engaged
            if _emergency_engaged():
                return
        except Exception:
            pass
        obs = self._collect_observation(symbol)
        self._stats.observations += 1
        directive = self._heuristic_directive(obs)

        # ── Aşama 1: Gatekeeper (flag-gated, additive) ──
        # We always keep the directive in the internal log/trace so the
        # dashboard retains full shadow visibility, but when the
        # gatekeeper rejects we DO NOT publish ORACLE_DIRECTIVE_ISSUED —
        # rejected decisions are surfaced via DIRECTIVE_REJECTED instead.
        gate_accepted = True
        try:
            from directive_gatekeeper import get_directive_gatekeeper
            gk = get_directive_gatekeeper(event_bus=self.event_bus)
            decision = gk.evaluate(directive)
            gate_accepted = bool(decision.accepted)
        except Exception as e:
            logger.debug("gatekeeper skipped: %s", e)

        self._directive_log.append(directive)
        self._last_directive_by_symbol[symbol] = directive
        self._stats.directives_emitted += 1
        # Reasoning trace
        trace = ReasoningTrace(
            symbol=symbol, observation=obs.to_dict(),
            directive=directive.to_dict(),
            prompt="", response="", shadow=self.shadow,
        )
        self._trace_log.append(trace)
        if self.rag is not None:
            try:
                self.rag.add_trace(trace)
            except Exception as e:
                logger.debug("Brain rag add fail: %s", e)
        # Emit event (shadow marker) — skipped when gatekeeper rejects.
        if self.event_bus is not None and gate_accepted:
            try:
                from event_bus import EventType, Event
                await self.event_bus.publish(
                    Event(type=EventType.ORACLE_DIRECTIVE_ISSUED, source="qwen_oracle_brain",
                          data={"directive": directive.to_dict(), "shadow": self.shadow})
                )
            except Exception as e:
                logger.debug("Brain event skip: %s", e)

        # Aşama 2 — register live (accepted) directive for impact tracking.
        if gate_accepted:
            try:
                from directive_impact_tracker import get_directive_impact_tracker
                tracker = get_directive_impact_tracker(event_bus=self.event_bus)
                await tracker.register_directive(directive)
            except Exception as e:
                logger.debug("Brain impact register skip: %s", e)
        if directive.severity in ("high", "critical") and not self.shadow:
            logger.warning("🧭 ORACLE DIRECTIVE [%s] %s %s: %s",
                           directive.severity.upper(), symbol, directive.action, directive.rationale)
        else:
            logger.info("🧭 [shadow=%s] %s %s/%s: %s",
                        self.shadow, symbol, directive.action, directive.severity, directive.rationale)

    async def _maybe_learn(self, now: float) -> None:
        if (now - self._last_learn_ts) < self.learn_interval:
            return
        self._last_learn_ts = now
        self._stats.learn_cycles += 1
        # Şimdilik minimal: log rows + basit istatistik. Gerçek ağırlık
        # revizyonu PR3/sonraki iterasyonda.
        logger.info("🧭 Brain learn cycle #%d — directives=%d, traces=%d",
                    self._stats.learn_cycles, len(self._directive_log), len(self._trace_log))

    async def _maybe_teach(self, now: float) -> None:
        if (now - self._last_teach_ts) < self.teach_interval:
            return
        self._last_teach_ts = now
        self._stats.teach_cycles += 1
        if self.llm_bridge is None:
            return
        # Tek bir özet LLM çağrısı — shadow marker; hata toleranslı.
        try:
            # Son 30 dk'lık direktif dağılımı
            recent = [d for d in list(self._directive_log)[-200:]]
            summary = {
                "window_size": len(recent),
                "by_action": {},
                "by_severity": {},
            }
            for d in recent:
                summary["by_action"][d.action] = summary["by_action"].get(d.action, 0) + 1
                summary["by_severity"][d.severity] = summary["by_severity"].get(d.severity, 0) + 1

            # Aşama 2 — inject impact feedback (last N live + N synthetic).
            impact_live: list = []
            impact_syn: list = []
            try:
                from directive_impact_tracker import get_directive_impact_tracker
                from config import Config
                tracker = get_directive_impact_tracker()
                n_live = int(getattr(Config, "ORACLE_PROMPT_IMPACT_LIVE_N", 20))
                n_syn = int(getattr(Config, "ORACLE_PROMPT_IMPACT_SYNTHETIC_N", 20))
                impact_live = tracker.recent(n=n_live, synthetic=False)
                impact_syn = tracker.recent(n=n_syn, synthetic=True)
            except Exception as _e:
                logger.debug("brain prompt impact inject skip: %s", _e)

            def _fmt_impacts(rows: list) -> str:
                if not rows:
                    return "(henüz veri yok)"
                return "\n".join(
                    f"- {r['directive_type']} {r['symbol']} impact={r['impact_score']:+.3f}"
                    for r in rows
                )

            prompt = (
                "Oracle Brain son 30 dk özet:\n" + json.dumps(summary, ensure_ascii=False) +
                "\n\nÖnceki direktiflerinin GERÇEK etkisi (son 20 canlı):\n" + _fmt_impacts(impact_live) +
                "\n\nGeçmişten TAHMİNİ etki (son 20 tarihsel simülasyon):\n" + _fmt_impacts(impact_syn) +
                "\n\nKural:\n"
                "- Bir direktif tipinin live impact < 0 ise → O tipten KAÇIN.\n"
                "- Synthetic impact güvenilir ama değişkenlik fazla → %60 ağırlık live'a, %40 synthetic'e ver.\n"
                "- Doğrudan ters feedback'i olan direktif tipini tekrar denemeden önce conformal_lower > 0.6 beklemen gerekir.\n"
                + self._self_audit_prompt_block() +
                "\nKısa Türkçe bir değerlendirme yap (3-5 satır)."
            )
            self._stats.llm_calls += 1
            resp = await self.llm_bridge.chat_respond(
                user_message=prompt, system_context={"mode": "teach"}
            )
            logger.info("🧭 Brain teach summary: %s", (resp or "")[:200])
        except Exception as e:
            self._stats.llm_errors += 1
            logger.debug("Brain teach llm fail: %s", e)

    async def _maybe_daily_report(self, now: float) -> None:
        tm = time.localtime(now)
        if tm.tm_hour != self.daily_report_hour:
            return
        if tm.tm_yday == self._last_daily_report_day:
            return
        self._last_daily_report_day = tm.tm_yday
        logger.info("🧭 Brain DAILY REPORT — obs=%d, directives=%d, learn_cycles=%d, teach_cycles=%d",
                    self._stats.observations, self._stats.directives_emitted,
                    self._stats.learn_cycles, self._stats.teach_cycles)

    async def _main_loop(self) -> None:
        while self._running:
            try:
                now = time.time()
                self._stats.last_tick_ts = now
                for sym in list(self.symbols):
                    await self._tick_symbol(sym)
                await self._maybe_learn(now)
                await self._maybe_teach(now)
                await self._maybe_daily_report(now)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.debug("Brain loop err: %s", e)
            await asyncio.sleep(self.observe_interval)

    # ── External API ─────────────────────────────────────────────────

    def last_directive(self, symbol: str) -> Optional[OracleDirective]:
        return self._last_directive_by_symbol.get(symbol)

    def all_last_directives(self) -> Dict[str, Dict[str, Any]]:
        return {k: v.to_dict() for k, v in self._last_directive_by_symbol.items()}

    def recent_traces(self, limit: int = 50) -> List[Dict[str, Any]]:
        return [t.to_dict() for t in list(self._trace_log)[-int(limit):]]

    def _self_audit_prompt_block(self) -> str:
        """Aşama 3 — read latest self-audit JSON sidecar and return a Turkish
        prompt fragment, or an empty string if absent / stale."""
        try:
            from config import Config
            import json as _json
            from pathlib import Path
            p = Path(getattr(Config, "QWEN_SELF_AUDIT_LATEST_PATH", "python_agents/.self_audit_latest.json"))
            if not p.exists():
                return ""
            obj = _json.loads(p.read_text(encoding="utf-8"))
            rate = obj.get("disagreement_rate")
            if rate is None:
                return ""
            return (
                f"\n- Son öz-denetim sonucun: %{float(rate)*100:.1f} direktifin geriye dönük reddedildi.\n"
                f"- Bu yüksekse — mevcut kararlarında temkinli ol, eski hatalarını tekrar etme.\n"
            )
        except Exception:
            return ""

    def authority_override_pct_1h(self) -> float:
        """Aşama 2 cascade guard — fraction of the last hour's directives
        whose severity in {high, critical} AND shadow == False. Returns
        a float ∈ [0, 1]. In shadow mode always returns 0 by design."""
        try:
            cutoff = time.time() - 3600.0
            recent = [d for d in list(self._directive_log) if float(getattr(d, "ts", 0.0)) >= cutoff]
            if not recent:
                return 0.0
            dominant = [
                d for d in recent
                if getattr(d, "severity", "") in ("high", "critical") and not self.shadow
            ]
            return len(dominant) / len(recent)
        except Exception:
            return 0.0

    def set_symbols(self, symbols: List[str]) -> None:
        self.symbols = list(symbols or [])

    async def health_check(self) -> Dict[str, Any]:
        return {
            "healthy": self._initialized,
            "running": self._running,
            "shadow": self.shadow,
            "symbols": len(self.symbols),
            "observations": self._stats.observations,
            "directives_emitted": self._stats.directives_emitted,
            "learn_cycles": self._stats.learn_cycles,
            "teach_cycles": self._stats.teach_cycles,
            "llm_calls": self._stats.llm_calls,
            "llm_errors": self._stats.llm_errors,
            "last_tick_ts": self._stats.last_tick_ts,
        }

    def metrics(self) -> Dict[str, Any]:
        return {
            "brain_observations_total": self._stats.observations,
            "brain_directives_total": self._stats.directives_emitted,
            "brain_learn_cycles_total": self._stats.learn_cycles,
            "brain_teach_cycles_total": self._stats.teach_cycles,
            "brain_llm_calls_total": self._stats.llm_calls,
            "brain_llm_errors_total": self._stats.llm_errors,
        }


_instance: Optional[QwenOracleBrain] = None


def get_oracle_brain(**kwargs: Any) -> QwenOracleBrain:
    global _instance
    if _instance is None:
        _instance = QwenOracleBrain(**kwargs)
    else:
        # DI injection for late-bound deps
        for k in ("event_bus", "feature_store", "signal_bus", "factor_graph",
                  "confluence_engine", "llm_bridge", "rag"):
            v = kwargs.get(k)
            if v is not None and getattr(_instance, k, None) is None:
                setattr(_instance, k, v)
    return _instance


def _reset_for_tests() -> None:
    global _instance
    _instance = None
