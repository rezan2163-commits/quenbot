"""
Aşama 3 — Qwen Self-Audit (monthly meta-cognition)
==================================================

Procedure (runs 1st of each month 03:00 UTC, also CLI-invokable):

1. Sample N random directives from the last D days.
2. For each, prepare the (context, outcome) pair.
3. Ask Qwen via :class:`LLMBridge`: "Bildiklerinle, bu direktifi tekrar verir
   miydin? Evet/Hayır/Kısmi. Neden?"
4. Aggregate disagreement rate (Hayır/Kısmi → disagreement).
5. Write ``python_agents/reports/self_audit_<YYYY-MM>.md`` and a JSON
   sidecar at :attr:`Config.QWEN_SELF_AUDIT_LATEST_PATH`.
6. If disagreement > threshold → emit ``QWEN_MODEL_EVOLUTION_ALERT``.

The script must produce *valid markdown even with sparse history* (zero
samples → an honest empty report, no exceptions).
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import logging
import os
import random
import sys
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Iterable

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

logger = logging.getLogger("qwen_self_audit")


@dataclass
class AuditVerdict:
    directive_id: str
    verdict: str  # "yes" | "no" | "partial" | "unknown"
    reason: str = ""
    raw: str = ""

    def disagreement(self) -> int:
        return 1 if self.verdict in ("no", "partial") else 0


@dataclass
class SelfAuditReport:
    month_label: str  # YYYY-MM
    sample_size: int
    disagreement_rate: float
    verdicts: List[Dict[str, Any]] = field(default_factory=list)
    started_at: str = ""
    finished_at: str = ""
    threshold: float = 0.40
    alert_emitted: bool = False
    output_path: str = ""
    json_path: str = ""

    def as_dict(self) -> Dict[str, Any]:
        return asdict(self)


# ─── data sources ──────────────────────────────────────────────────
class DirectiveHistorySource:
    """Adapter — samples directives from the brain's in-memory log or a
    custom iterable. Robust to empty history."""

    def __init__(self, *, brain: Any = None, iterable: Optional[Iterable[Any]] = None) -> None:
        self.brain = brain
        self.iterable = list(iterable) if iterable is not None else None

    def sample(self, *, n: int, lookback_days: int, rng: Optional[random.Random] = None) -> List[Dict[str, Any]]:
        rng = rng or random.Random(42)
        rows: List[Dict[str, Any]] = []
        if self.iterable is not None:
            rows = [self._normalize(r) for r in self.iterable]
        elif self.brain is not None:
            log = getattr(self.brain, "_directive_log", None)
            if log is not None:
                cutoff = time.time() - lookback_days * 86400
                rows = [
                    self._normalize(r)
                    for r in list(log)
                    if float(getattr(r, "ts", 0.0) or 0.0) >= cutoff
                ]
        if not rows:
            return []
        if len(rows) <= n:
            return rows
        return rng.sample(rows, n)

    def _normalize(self, r: Any) -> Dict[str, Any]:
        if isinstance(r, dict):
            return r
        # Try dataclass-like .to_dict, else attr probe.
        if hasattr(r, "to_dict"):
            try:
                return dict(r.to_dict())
            except Exception:
                pass
        return {
            "directive_id": getattr(r, "directive_id", None) or getattr(r, "id", None),
            "symbol": getattr(r, "symbol", None),
            "action": getattr(r, "action", None),
            "severity": getattr(r, "severity", None),
            "rationale": getattr(r, "rationale", None),
            "ts": getattr(r, "ts", None),
        }


# ─── auditor ────────────────────────────────────────────────────────
class QwenSelfAuditor:
    def __init__(
        self,
        *,
        history: Optional[DirectiveHistorySource] = None,
        llm_bridge: Any = None,
        event_bus: Any = None,
        report_dir: Optional[Path] = None,
        latest_path: Optional[Path] = None,
        sample_size: Optional[int] = None,
        lookback_days: Optional[int] = None,
        threshold: Optional[float] = None,
        rng_seed: int = 42,
    ) -> None:
        from config import Config
        self.history = history or DirectiveHistorySource()
        self.llm_bridge = llm_bridge
        self.event_bus = event_bus
        self.report_dir = Path(report_dir or getattr(Config, "QWEN_SELF_AUDIT_REPORT_DIR", "python_agents/reports"))
        self.latest_path = Path(latest_path or getattr(Config, "QWEN_SELF_AUDIT_LATEST_PATH", "python_agents/.self_audit_latest.json"))
        self.sample_size = int(sample_size if sample_size is not None else getattr(Config, "QWEN_SELF_AUDIT_SAMPLE_SIZE", 100))
        self.lookback_days = int(lookback_days if lookback_days is not None else getattr(Config, "QWEN_SELF_AUDIT_LOOKBACK_DAYS", 30))
        self.threshold = float(threshold if threshold is not None else getattr(Config, "QWEN_SELF_AUDIT_DISAGREEMENT_ALERT", 0.40))
        self._rng = random.Random(rng_seed)

    async def run(self, *, month_label: Optional[str] = None) -> SelfAuditReport:
        started = datetime.now(timezone.utc)
        wk = month_label or started.strftime("%Y-%m")
        sample = self.history.sample(n=self.sample_size, lookback_days=self.lookback_days, rng=self._rng)
        verdicts: List[AuditVerdict] = []
        for row in sample:
            v = await self._audit_one(row)
            verdicts.append(v)
        rate = sum(v.disagreement() for v in verdicts) / max(len(verdicts), 1)
        finished = datetime.now(timezone.utc)
        rep = SelfAuditReport(
            month_label=wk,
            sample_size=len(verdicts),
            disagreement_rate=rate,
            verdicts=[asdict(v) for v in verdicts],
            started_at=started.isoformat(),
            finished_at=finished.isoformat(),
            threshold=self.threshold,
        )
        # Write outputs.
        self.report_dir.mkdir(parents=True, exist_ok=True)
        md_path = self.report_dir / f"self_audit_{wk}.md"
        md_path.write_text(self._render_markdown(rep), encoding="utf-8")
        rep.output_path = str(md_path)
        try:
            self.latest_path.parent.mkdir(parents=True, exist_ok=True)
            self.latest_path.write_text(json.dumps(rep.as_dict(), indent=2, ensure_ascii=False), encoding="utf-8")
            rep.json_path = str(self.latest_path)
        except Exception as exc:
            logger.warning("self-audit json sidecar write failed: %s", exc)
        # Alert
        if rep.sample_size > 0 and rep.disagreement_rate > self.threshold:
            rep.alert_emitted = True
            self._publish("QWEN_MODEL_EVOLUTION_ALERT", {
                "month": wk,
                "disagreement_rate": rep.disagreement_rate,
                "threshold": self.threshold,
                "sample_size": rep.sample_size,
            })
        self._publish("SELF_AUDIT_COMPLETED", {
            "month": wk,
            "disagreement_rate": rep.disagreement_rate,
            "sample_size": rep.sample_size,
            "alert_emitted": rep.alert_emitted,
        })
        return rep

    async def _audit_one(self, row: Dict[str, Any]) -> AuditVerdict:
        did = str(row.get("directive_id") or hashlib.md5(json.dumps(row, sort_keys=True, default=str).encode()).hexdigest()[:12])
        if self.llm_bridge is None:
            # Offline fallback — neutral verdict so reports still render.
            return AuditVerdict(directive_id=did, verdict="unknown", reason="llm_unavailable")
        prompt = (
            "Sen Qwen, kendi geçmiş kararını gözden geçiriyorsun.\n"
            "Aşağıdaki direktifi VE bilinen sonucunu inceleyerek tek satır cevap ver.\n"
            f"Direktif: {json.dumps(row, ensure_ascii=False)[:1500]}\n"
            "Cevap formatı: 'EVET|HAYIR|KISMİ — kısa neden'."
        )
        raw = ""
        try:
            raw = await self.llm_bridge.chat_respond(user_message=prompt, system_context={"mode": "self_audit"})
        except Exception as exc:
            logger.debug("self_audit llm err: %s", exc)
            return AuditVerdict(directive_id=did, verdict="unknown", reason=f"llm_error:{exc}", raw="")
        return self._parse_verdict(did, raw or "")

    @staticmethod
    def _parse_verdict(did: str, raw: str) -> AuditVerdict:
        text = (raw or "").strip()
        upper = text.upper()
        # Order matters — KISMİ before EVET (substring guard).
        if not upper:
            verdict = "unknown"
        elif upper.startswith("HAYIR") or upper.startswith("HAYİR"):
            verdict = "no"
        elif upper.startswith("KISMİ") or upper.startswith("KISMI"):
            verdict = "partial"
        elif upper.startswith("EVET"):
            verdict = "yes"
        else:
            verdict = "unknown"
        # Extract reason (after first separator).
        reason = ""
        for sep in ("—", "-", ":"):
            if sep in text:
                reason = text.split(sep, 1)[1].strip()
                break
        return AuditVerdict(directive_id=did, verdict=verdict, reason=reason[:240], raw=text[:512])

    def _render_markdown(self, rep: SelfAuditReport) -> str:
        from collections import Counter
        c = Counter([v["verdict"] for v in rep.verdicts])
        body = (
            f"# QuenBot Aylık Öz-Denetim — {rep.month_label}\n\n"
            f"_Pencere: {rep.started_at} → {rep.finished_at}_\n\n"
            f"## Özet\n"
            f"- Örneklem: **{rep.sample_size}** direktif (lookback={self.lookback_days} gün)\n"
            f"- Disagreement rate: **{rep.disagreement_rate*100:.1f}%** (eşik {rep.threshold*100:.0f}%)\n"
            f"- Verdict dağılımı: yes={c.get('yes', 0)}, no={c.get('no', 0)}, partial={c.get('partial', 0)}, unknown={c.get('unknown', 0)}\n"
            f"- Alert emit edildi: **{'EVET' if rep.alert_emitted else 'hayır'}**\n\n"
            f"## Aksiyon\n"
        )
        if rep.alert_emitted:
            body += (
                "- 🟠 Disagreement eşiği aşıldı. Prompt template + RAG koleksiyonunu gözden geçir.\n"
                "- Fast Brain retraining gerekebilir.\n"
            )
        else:
            body += "- Sistem stabil. Eşik altı disagreement.\n"
        if rep.sample_size == 0:
            body += "\n> ⚠️ Sample boş — son ay yetersiz direktif verisi. Audit boş raporlanır.\n"
        # Add a small sample of raw verdicts.
        if rep.verdicts:
            body += "\n## Örnek verdictler (ilk 10)\n"
            for v in rep.verdicts[:10]:
                body += f"- `{v['directive_id']}` → **{v['verdict']}** — {v.get('reason', '')[:120]}\n"
        return body

    def _publish(self, event_name: str, payload: Dict[str, Any]) -> None:
        bus = self.event_bus
        if bus is None:
            return
        try:
            from event_bus import EventType, Event
            ev = getattr(EventType, event_name, None)
            if ev is None:
                return
            try:
                asyncio.get_event_loop().create_task(bus.publish(Event(type=ev, source="qwen_self_audit", data=payload)))
            except RuntimeError:
                pub = getattr(bus, "publish_sync", None)
                if pub is not None:
                    pub(Event(type=ev, source="qwen_self_audit", data=payload))
        except Exception as exc:
            logger.debug("self_audit publish skip: %s", exc)


# ─── CLI ──────────────────────────────────────────────────────────
def _parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Run Qwen self-audit (monthly).")
    p.add_argument("--month", type=str, default=None, help="YYYY-MM label override")
    p.add_argument("--sample", type=int, default=None)
    p.add_argument("--lookback-days", type=int, default=None)
    p.add_argument("--verbose", action="store_true")
    return p.parse_args(argv)


async def _amain(argv: Optional[List[str]] = None) -> int:
    args = _parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )
    auditor = QwenSelfAuditor(
        sample_size=args.sample,
        lookback_days=args.lookback_days,
    )
    rep = await auditor.run(month_label=args.month)
    print(json.dumps({
        "month": rep.month_label,
        "sample_size": rep.sample_size,
        "disagreement_rate": rep.disagreement_rate,
        "alert_emitted": rep.alert_emitted,
        "output_path": rep.output_path,
        "json_path": rep.json_path,
    }, indent=2))
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    return asyncio.run(_amain(argv))


if __name__ == "__main__":
    sys.exit(main())
