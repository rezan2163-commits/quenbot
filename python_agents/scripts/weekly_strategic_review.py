"""
Aşama 3 — Weekly Strategic Review
=================================

Generates a 2-page Turkish executive summary every Sunday 18:00 Europe/Istanbul.

Inputs (best-effort, all optional — script must produce valid markdown even
with no DB / no impact tracker / no rca / no safety_net history):

* last 7 days of ``oracle_directives`` rows
* :class:`DirectiveImpactTracker` aggregates
* Counterfactual labels (latest week)
* Channel trust score trajectories (warmup file)
* Safety net trip events
* RCA reports (if a folder exists)

Output:
    ``python_agents/reports/weekly_strategic_<YYYY-WW>.md``

CLI::

    python python_agents/scripts/weekly_strategic_review.py [--dry-run] [--week 2026-16]
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

logger = logging.getLogger("weekly_strategic_review")


# ─── data shaping ──────────────────────────────────────────────────
@dataclass
class WeeklyData:
    week_label: str
    started_at: str
    finished_at: str
    directives_total: int = 0
    impact_mean: Optional[float] = None
    qwen_authority_pct: Optional[float] = None
    top_trusted_channels: List[Dict[str, Any]] = field(default_factory=list)
    least_trusted_channels: List[Dict[str, Any]] = field(default_factory=list)
    strategy_weight_changes: List[Dict[str, Any]] = field(default_factory=list)
    winning_strategy_families: List[str] = field(default_factory=list)
    losing_strategy_families: List[str] = field(default_factory=list)
    auto_rollback_events: List[Dict[str, Any]] = field(default_factory=list)
    safety_net_trips: List[Dict[str, Any]] = field(default_factory=list)
    drift_alerts: List[Dict[str, Any]] = field(default_factory=list)
    trust_score_jumps: List[Dict[str, Any]] = field(default_factory=list)
    rag_size: Optional[int] = None
    rag_size_prev: Optional[int] = None
    warmup_active_usage_pct: Optional[float] = None
    operator_actions: List[str] = field(default_factory=list)


def _iso_week_label(dt: Optional[datetime] = None) -> str:
    dt = dt or datetime.now(timezone.utc)
    iso = dt.isocalendar()
    return f"{iso[0]}-{iso[1]:02d}"


def _ack_filename(week_label: str, ack_dir: Path) -> Path:
    return ack_dir / f".weekly_ack_{week_label}.json"


def _safe_float(x: Any) -> Optional[float]:
    try:
        f = float(x)
        if f != f:  # NaN
            return None
        return f
    except Exception:
        return None


# ─── data sources (best-effort) ────────────────────────────────────
class WeeklyDataCollector:
    def __init__(
        self,
        *,
        impact_tracker: Any = None,
        brain: Any = None,
        safety_net: Any = None,
        warmup_dir: Optional[Path] = None,
        rca_dir: Optional[Path] = None,
        warmup_trust_path: Optional[Path] = None,
    ) -> None:
        self.impact_tracker = impact_tracker
        self.brain = brain
        self.safety_net = safety_net
        self.warmup_dir = Path(warmup_dir) if warmup_dir else None
        self.rca_dir = Path(rca_dir) if rca_dir else None
        self.warmup_trust_path = Path(warmup_trust_path) if warmup_trust_path else None

    def collect(self, week_label: str, started: datetime, finished: datetime) -> WeeklyData:
        data = WeeklyData(
            week_label=week_label,
            started_at=started.isoformat(),
            finished_at=finished.isoformat(),
        )
        # Impact tracker stats
        try:
            if self.impact_tracker is not None:
                if hasattr(self.impact_tracker, "rolling_mean_impact"):
                    data.impact_mean = _safe_float(
                        self.impact_tracker.rolling_mean_impact(168, synthetic=False)  # 7d
                    )
                if hasattr(self.impact_tracker, "aggregate_by_type"):
                    by_type = self.impact_tracker.aggregate_by_type() or {}
                    rows = []
                    for t, v in by_type.items():
                        live_count = int(v.get("live_count", 0) or 0)
                        live_mean = _safe_float(v.get("live_mean"))
                        if live_count > 0 and live_mean is not None:
                            rows.append({"directive_type": t, "live_count": live_count, "live_mean": live_mean})
                    rows_sorted = sorted(rows, key=lambda r: r["live_mean"], reverse=True)
                    data.top_trusted_channels = rows_sorted[:3]
                    data.least_trusted_channels = list(reversed(rows_sorted[-3:]))[:3]
        except Exception as exc:
            logger.debug("collect impact: %s", exc)
        # Brain authority
        try:
            if self.brain is not None and hasattr(self.brain, "authority_override_pct_1h"):
                data.qwen_authority_pct = _safe_float(self.brain.authority_override_pct_1h())
            if self.brain is not None:
                # directive count rough estimate: walk _directive_log; otherwise leave 0.
                log = getattr(self.brain, "_directive_log", None)
                if log is not None:
                    data.directives_total = int(len(log))
        except Exception as exc:
            logger.debug("collect brain: %s", exc)
        # Safety net
        try:
            if self.safety_net is not None and hasattr(self.safety_net, "snapshot"):
                snap = self.safety_net.snapshot()
                if snap and snap.get("tripped"):
                    data.safety_net_trips.append({"reason": snap.get("reason"), "at": snap.get("tripped_at")})
        except Exception as exc:
            logger.debug("collect safety_net: %s", exc)
        # Warmup file (trust scores)
        try:
            if self.warmup_trust_path and self.warmup_trust_path.exists():
                obj = json.loads(self.warmup_trust_path.read_text(encoding="utf-8"))
                if isinstance(obj, dict):
                    items = [(k, _safe_float(v)) for k, v in obj.items()]
                    items = [(k, v) for k, v in items if v is not None]
                    items.sort(key=lambda x: x[1], reverse=True)
                    if not data.top_trusted_channels:
                        data.top_trusted_channels = [{"channel": k, "trust": v} for k, v in items[:3]]
                    if not data.least_trusted_channels:
                        data.least_trusted_channels = [{"channel": k, "trust": v} for k, v in items[-3:]]
        except Exception as exc:
            logger.debug("collect trust: %s", exc)
        # RCA folder file count (proxy for activity)
        try:
            if self.rca_dir and self.rca_dir.exists():
                files = sorted(self.rca_dir.glob("*.json"))
                data.drift_alerts = [{"file": f.name} for f in files[-5:]]
        except Exception:
            pass
        return data


# ─── markdown renderer ─────────────────────────────────────────────
def render_markdown(data: WeeklyData) -> str:
    def _fmt_pct(x: Optional[float]) -> str:
        return f"%{x*100:.1f}" if x is not None else "—"

    def _fmt_num(x: Optional[float], digits: int = 3) -> str:
        return f"{x:.{digits}f}" if x is not None else "—"

    def _fmt_list(rows: List[Dict[str, Any]]) -> str:
        if not rows:
            return "  - (veri yok)"
        out = []
        for r in rows:
            label = r.get("directive_type") or r.get("channel") or r.get("file") or "?"
            score = r.get("live_mean") if "live_mean" in r else r.get("trust")
            count = r.get("live_count")
            extra = []
            if score is not None:
                extra.append(f"score={_fmt_num(_safe_float(score))}")
            if count is not None:
                extra.append(f"n={count}")
            tail = " (" + ", ".join(extra) + ")" if extra else ""
            out.append(f"  - {label}{tail}")
        return "\n".join(out)

    md = (
        f"# QuenBot Haftalık Stratejik İnceleme — Hafta {data.week_label}\n\n"
        f"_Pencere: {data.started_at} → {data.finished_at}_\n\n"
        f"## 🎯 Bu Haftanın Performansı\n"
        f"- Üretilen direktif: **{data.directives_total}**\n"
        f"- Ortalama impact_score: **{_fmt_num(data.impact_mean)}**\n"
        f"- Qwen Authority Usage: **{_fmt_pct(data.qwen_authority_pct)}**\n"
        f"- En güvenilen 3 kanal:\n{_fmt_list(data.top_trusted_channels)}\n"
        f"- En az güvenilen 3 kanal:\n{_fmt_list(data.least_trusted_channels)}\n\n"
        f"## 📈 Strateji Evrimi\n"
        f"- Strateji ağırlık değişiklikleri: **{len(data.strategy_weight_changes)}** kayıt\n"
        f"- Kazanan strateji aileleri: {', '.join(data.winning_strategy_families) or '—'}\n"
        f"- Kaybeden strateji aileleri: {', '.join(data.losing_strategy_families) or '—'}\n\n"
        f"## ⚠️ Anormallikler ve İkazlar\n"
        f"- Auto-rollback olayı: **{len(data.auto_rollback_events)}**\n"
        f"- Safety Net trip: **{len(data.safety_net_trips)}**\n"
        f"- Drift uyarıları: **{len(data.drift_alerts)}**\n"
        f"- Trust score sıçramaları: **{len(data.trust_score_jumps)}**\n\n"
        f"## 🧠 Qwen'in Öğrenme Trajektorisi\n"
        f"- RAG koleksiyonu boyutu: **{data.rag_size if data.rag_size is not None else '—'}** "
        f"(geçen hafta: {data.rag_size_prev if data.rag_size_prev is not None else '—'})\n"
        f"- Tarihsel warmup entries aktif kullanım oranı: **{_fmt_pct(data.warmup_active_usage_pct)}**\n\n"
        f"## 📋 Operator Aksiyon Gerektiren\n"
        + (
            "\n".join(f"- [ ] {a}" for a in data.operator_actions)
            if data.operator_actions
            else "- [ ] Prompt template review gerekli mi?\n- [ ] Fast Brain retraining önerilir mi?\n- [ ] Yeni oracle kanalı değerlendirmesi?"
        )
        + "\n\n"
        f"## ✍️ Operator Onayı\n"
        f"Acknowledgment dosyası: `.weekly_ack_{data.week_label}.json`\n\n"
        f"Onay süresi: **48 saat hedef / 7 gün hard limit** — "
        f"aksi halde sistem otomatik olarak Aşama 2 throttles'a geri döner.\n"
    )
    return md


# ─── orchestrator ──────────────────────────────────────────────────
@dataclass
class WeeklyReviewReport:
    week_label: str
    output_path: str
    bytes_written: int
    dry_run: bool


class WeeklyStrategicReview:
    def __init__(
        self,
        *,
        collector: Optional[WeeklyDataCollector] = None,
        report_dir: Optional[Path] = None,
        event_bus: Any = None,
        clock: Any = None,
    ) -> None:
        from config import Config
        self.collector = collector or WeeklyDataCollector()
        self.report_dir = Path(report_dir or getattr(Config, "WEEKLY_REVIEW_REPORT_DIR", "python_agents/reports"))
        self.event_bus = event_bus
        self.clock = clock or (lambda: datetime.now(timezone.utc))

    def generate(self, *, week_label: Optional[str] = None, dry_run: bool = False) -> WeeklyReviewReport:
        finished = self.clock() if callable(self.clock) else self.clock
        if not isinstance(finished, datetime):
            finished = datetime.now(timezone.utc)
        wk = week_label or _iso_week_label(finished)
        started = finished - timedelta(days=7)
        data = self.collector.collect(week_label=wk, started=started, finished=finished)
        md = render_markdown(data)
        out_path = self.report_dir / f"weekly_strategic_{wk}.md"
        bytes_written = 0
        if not dry_run:
            self.report_dir.mkdir(parents=True, exist_ok=True)
            out_path.write_text(md, encoding="utf-8")
            bytes_written = len(md.encode("utf-8"))
            self._publish("WEEKLY_REVIEW_GENERATED", {"week": wk, "path": str(out_path), "bytes": bytes_written})
        return WeeklyReviewReport(week_label=wk, output_path=str(out_path), bytes_written=bytes_written, dry_run=dry_run)

    def _publish(self, event_name: str, payload: Dict[str, Any]) -> None:
        bus = self.event_bus
        if bus is None:
            return
        try:
            from event_bus import EventType, Event
            ev = getattr(EventType, event_name, None)
            if ev is None:
                return
            asyncio.get_event_loop().create_task(bus.publish(Event(type=ev, source="weekly_review", data=payload)))
        except Exception:
            pass


# ─── CLI ──────────────────────────────────────────────────────────
def _parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Generate weekly strategic review.")
    p.add_argument("--week", type=str, default=None, help="ISO week label, e.g. 2026-16")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--verbose", action="store_true")
    return p.parse_args(argv)


def main(argv: Optional[List[str]] = None) -> int:
    args = _parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )
    runner = WeeklyStrategicReview()
    rep = runner.generate(week_label=args.week, dry_run=args.dry_run)
    print(json.dumps({
        "week": rep.week_label,
        "output_path": rep.output_path,
        "bytes_written": rep.bytes_written,
        "dry_run": rep.dry_run,
    }, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
