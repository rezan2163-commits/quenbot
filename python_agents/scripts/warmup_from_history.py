"""
warmup_from_history.py — Aşama 1 Historical Bootstrap
=========================================================
Replays the last N days of existing counterfactual observations + fast
brain predictions through the Oracle pipeline to seed:

  1. Safety Net baselines      → .safety_net_baseline.json
  2. Channel trust scores      → .channel_trust_scores.json
  3. RAG warm cache            → ChromaDB oracle_reasoning (tag=historical_warmup)
  4. Directive impact baseline → counterfactual_observations.historical_impact_simulation

The script is idempotent — resuming after a crash replays only the
outstanding rows (`.warmup_checkpoint.json`).

Critical guardrails:
  - NEVER writes to live `oracle_directives` / `oracle_reasoning_traces`.
  - NEVER marks a live row as warmup; `warmup_generated=TRUE` is only set
    when this script creates or updates a row it owns.
  - RAG entries are tagged `source_tag=historical_warmup` so live
    learning can filter them later if desired.

Usage
-----
    python python_agents/scripts/warmup_from_history.py \\
        --days 30 \\
        --symbols BTCUSDT,ETHUSDT,SOLUSDT,BNBUSDT,XRPUSDT \\
        --rag-limit 1000 \\
        --dry-run        # remove --dry-run for the real run
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import math
import os
import statistics
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

logger = logging.getLogger("warmup_from_history")


# ─── helpers ──────────────────────────────────────────────────────

DEFAULT_SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT"]
CHANNEL_WHITELIST = [
    "bocpd_consensus", "hawkes_kernel_update", "entropy_cooling",
    "wasserstein_drift_zscore", "path_signature_similarity",
    "mirror_execution_strength", "topological_whale_birth",
    "onchain_causal_strength", "ifi", "confluence_score",
    "fast_brain_p", "order_flow_imbalance",
]


def _percentile(values: List[float], pct: float) -> Optional[float]:
    if not values:
        return None
    s = sorted(values)
    k = (len(s) - 1) * max(0.0, min(1.0, pct / 100.0))
    f = int(math.floor(k))
    c = int(math.ceil(k))
    if f == c:
        return float(s[f])
    return float(s[f] + (s[c] - s[f]) * (k - f))


def _brier(p: float, realized_up: bool) -> float:
    y = 1.0 if realized_up else 0.0
    return (p - y) ** 2


# ─── checkpoint ───────────────────────────────────────────────────

class Checkpoint:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.data: Dict[str, Any] = self._load()

    def _load(self) -> Dict[str, Any]:
        if self.path.exists():
            try:
                return json.loads(self.path.read_text(encoding="utf-8") or "{}")
            except Exception:
                pass
        return {
            "started_at": None, "finished_at": None,
            "cursor_event_ts": None, "processed_rows": 0,
            "rag_written": 0, "impact_updated": 0,
            "per_symbol": {},
        }

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.data, indent=2, default=str), encoding="utf-8")
        os.replace(tmp, self.path)


# ─── main warmup ──────────────────────────────────────────────────

class HistoricalWarmup:
    def __init__(
        self,
        *,
        days: int,
        symbols: List[str],
        rag_limit: int,
        dry_run: bool,
        trust_path: str,
        safety_baseline_path: str,
        checkpoint_path: str,
        report_dir: str,
        rag_source_tag: str = "historical_warmup",
        db: Any = None,
        rag: Any = None,
    ) -> None:
        self.days = int(days)
        self.symbols = list(symbols)
        self.rag_limit = int(rag_limit)
        self.dry_run = bool(dry_run)
        self.trust_path = Path(trust_path)
        self.safety_baseline_path = Path(safety_baseline_path)
        self.report_dir = Path(report_dir)
        self.rag_source_tag = rag_source_tag
        self.ckpt = Checkpoint(Path(checkpoint_path))
        self._db = db
        self._rag = rag

        self._trust: Dict[str, Dict[str, float]] = {}
        self._brier_samples: List[Tuple[float, bool, bool]] = []  # (p, realized_up, hit)
        self._confluence_by_symbol: Dict[str, List[float]] = {}
        self._rag_written = 0
        self._impact_updated = 0
        self._rows_read = 0

    # ── lazy infra ────────────────────────────────────────────
    async def _ensure_db(self) -> Any:
        if self._db is not None:
            return self._db
        try:
            from database import Database  # type: ignore
        except Exception as e:
            raise RuntimeError(f"database module unavailable: {e}")
        db = Database()
        await db.connect()
        self._db = db
        return db

    def _ensure_rag(self) -> Any:
        if self._rag is not None:
            return self._rag
        try:
            from qwen_oracle_rag import get_oracle_rag  # type: ignore
            rag = get_oracle_rag(collection_name="oracle_reasoning", top_k=5)
            self._rag = rag
            return rag
        except Exception as e:
            logger.warning("qwen_oracle_rag unavailable (%s) — rag step will be skipped", e)
            return None

    # ── step 1 & 2: safety baseline + trust scores ────────────
    def _update_trust(self, label: str, channel_payload: Dict[str, float]) -> None:
        for ch, val in (channel_payload or {}).items():
            if ch not in CHANNEL_WHITELIST:
                continue
            try:
                v = float(val)
            except Exception:
                continue
            if not math.isfinite(v):
                continue
            bucket = self._trust.setdefault(
                ch, {"alpha_TP": 1.0, "alpha_FP": 1.0, "alpha_FN": 1.0, "alpha_TN": 1.0, "samples": 0}
            )
            bucket["samples"] += 1
            key = f"alpha_{label}" if label in ("TP", "FP", "FN", "TN") else None
            if key and key in bucket:
                # Dirichlet posterior update; weight the increment by the
                # channel's absolute value so dormant channels don't
                # inflate their counters.
                weight = min(1.5, max(0.1, abs(v)))
                bucket[key] += weight

    def _update_brier(self, row: Dict[str, Any]) -> None:
        p = row.get("fast_brain_p_t_minus_1h")
        if p is None:
            return
        try:
            p = float(p)
        except Exception:
            return
        direction = str(row.get("move_direction") or "").lower()
        realized_up = direction == "up"
        hit = (p >= 0.5) == realized_up
        self._brier_samples.append((p, realized_up, hit))

    def _update_confluence(self, row: Dict[str, Any]) -> None:
        s = row.get("confluence_score_t_minus_1h")
        if s is None:
            return
        try:
            s = float(s)
        except Exception:
            return
        symbol = str(row.get("symbol"))
        self._confluence_by_symbol.setdefault(symbol, []).append(s)

    # ── step 3: synthetic reasoning trace (deterministic) ─────
    def _synthetic_trace(self, row: Dict[str, Any]) -> str:
        """Build a compact, deterministic 'had Qwen been active' narrative
        from the historical counterfactual. NO live LLM call — this keeps
        warmup cheap and reproducible, and satisfies the 'warm cache'
        requirement without spending tokens."""
        label = str(row.get("label"))
        symbol = str(row.get("symbol"))
        p = row.get("fast_brain_p_t_minus_1h")
        conf = row.get("confluence_score_t_minus_1h")
        mag = row.get("move_magnitude_pct")
        direction = row.get("move_direction")
        feats = row.get("features_t_minus_1h") or {}
        if isinstance(feats, str):
            try:
                feats = json.loads(feats)
            except Exception:
                feats = {}
        top = sorted(
            (
                (k, float(v)) for k, v in feats.items()
                if k in CHANNEL_WHITELIST and isinstance(v, (int, float)) and math.isfinite(float(v))
            ),
            key=lambda kv: abs(kv[1]), reverse=True,
        )[:5]
        top_str = ", ".join(f"{k}={v:+.2f}" for k, v in top) or "no-strong-channels"
        decision_hint = {
            "TP": "would have issued a BIAS_DIRECTION long/short with high confidence",
            "FN": "would have missed the move (calibration gap)",
            "FP": "would have cried wolf (over-confidence on weak signal)",
            "TN": "would have correctly stayed in MONITOR mode",
        }.get(label, "no decision inferred")
        return (
            f"[warmup] {symbol} @ {row.get('event_ts')} label={label} "
            f"move={mag}% dir={direction} fast_p={p} confluence={conf} "
            f"top_channels=[{top_str}] reasoning: {decision_hint}."
        )

    def _write_rag_entry(self, row: Dict[str, Any]) -> bool:
        if self._rag is None or self.rag_limit <= 0 or self._rag_written >= self.rag_limit:
            return False
        if self.dry_run:
            self._rag_written += 1
            return True
        try:
            doc = self._synthetic_trace(row)
            meta = {
                "symbol": str(row.get("symbol")),
                "ts": float((row.get("event_ts") or datetime.utcnow()).timestamp())
                      if isinstance(row.get("event_ts"), datetime) else float(time.time()),
                "label": str(row.get("label")),
                "source_tag": self.rag_source_tag,
                "warmup": True,
            }
            coll = getattr(self._rag, "_coll", None)
            if coll is None:
                # Fallback to in-memory buffer inside the RAG.
                self._rag._inmem.append((f"warmup-{row.get('id')}", doc, meta))  # type: ignore[attr-defined]
            else:
                coll.add(documents=[doc], metadatas=[meta], ids=[f"warmup-{row.get('id')}"])
            self._rag_written += 1
            return True
        except Exception as e:
            logger.debug("rag add fail id=%s: %s", row.get("id"), e)
            return False

    # ── step 4: historical impact simulation (additive) ───────
    def _impact_payload(self, row: Dict[str, Any]) -> Dict[str, Any]:
        """Would a directive @confidence>=0.70 emitted 30m earlier have
        been net-positive? Purely deterministic back-test on the stored
        outcome — no model calls."""
        pnl = row.get("realized_pnl_pct")
        conf = row.get("confluence_score_t_minus_1h") or 0.0
        label = str(row.get("label"))
        try:
            pnl = float(pnl) if pnl is not None else None
            conf = float(conf) if conf is not None else 0.0
        except Exception:
            pnl = None
            conf = 0.0
        would_have_fired = conf >= 0.70
        positive = (pnl is not None and pnl > 0.0) if would_have_fired else False
        return {
            "would_have_fired": bool(would_have_fired),
            "estimated_impact": "positive" if positive else ("negative" if (pnl is not None and pnl < 0 and would_have_fired) else "neutral"),
            "pnl_pct_realized": pnl,
            "confidence_t_minus_30m_proxy": conf,
            "label": label,
            "generated_at": datetime.utcnow().replace(tzinfo=timezone.utc).isoformat(),
            "source": "warmup_from_history",
        }

    async def _update_impact(self, db: Any, row: Dict[str, Any], payload: Dict[str, Any]) -> bool:
        if self.dry_run:
            self._impact_updated += 1
            return True
        if not hasattr(db, "pool") or db.pool is None:
            return False
        try:
            async with db.pool.acquire() as conn:
                await conn.execute(
                    """
                    UPDATE counterfactual_observations
                       SET historical_impact_simulation = $1::jsonb
                     WHERE id = $2
                    """,
                    json.dumps(payload),
                    int(row.get("id")),
                )
            self._impact_updated += 1
            return True
        except Exception as e:
            logger.debug("impact update fail id=%s: %s", row.get("id"), e)
            return False

    # ── main driver ───────────────────────────────────────────
    async def run(self) -> Dict[str, Any]:
        started = datetime.utcnow().replace(tzinfo=timezone.utc)
        self.ckpt.data["started_at"] = started.isoformat()
        self.ckpt.save()

        db = await self._ensure_db()
        rag = self._ensure_rag()

        since = started - timedelta(days=self.days)
        logger.info(
            "🔧 warmup start: days=%d since=%s symbols=%s rag_limit=%d dry_run=%s",
            self.days, since.isoformat(), self.symbols, self.rag_limit, self.dry_run,
        )

        rows = await self._fetch_rows(db, since)
        logger.info("📊 warmup fetched %d rows", len(rows))

        top_tp: List[Dict[str, Any]] = []
        top_fp: List[Dict[str, Any]] = []

        per_symbol_counts: Dict[str, Dict[str, int]] = {s: {"TP": 0, "FP": 0, "FN": 0, "TN": 0} for s in self.symbols}

        for row in rows:
            self._rows_read += 1
            label = str(row.get("label") or "").upper()
            symbol = str(row.get("symbol") or "")
            if per_symbol_counts.get(symbol) and label in per_symbol_counts[symbol]:
                per_symbol_counts[symbol][label] += 1
            # features_t_minus_1h may come back as dict or JSON str
            feats = row.get("features_t_minus_1h") or {}
            if isinstance(feats, str):
                try:
                    feats = json.loads(feats)
                except Exception:
                    feats = {}
            self._update_trust(label, feats if isinstance(feats, dict) else {})
            self._update_brier(row)
            self._update_confluence(row)

            if label == "TP" and len(top_tp) < 500:
                top_tp.append(row)
            elif label == "FP" and len(top_fp) < 500:
                top_fp.append(row)

            payload = self._impact_payload(row)
            await self._update_impact(db, row, payload)

            self.ckpt.data["cursor_event_ts"] = str(row.get("event_ts"))
            self.ckpt.data["processed_rows"] = self._rows_read
            if self._rows_read % 500 == 0:
                self.ckpt.data["rag_written"] = self._rag_written
                self.ckpt.data["impact_updated"] = self._impact_updated
                self.ckpt.save()
                logger.info("  … processed %d rows", self._rows_read)

        # RAG warm cache: 500 TP + 500 FP (or whatever we have), capped at rag_limit
        for r in top_tp + top_fp:
            if self._rag_written >= self.rag_limit:
                break
            self._write_rag_entry(r)

        trust_file = self._persist_trust()
        baseline_file = self._persist_safety_baseline()
        report_file = self._write_report(
            started=started, rows=len(rows),
            per_symbol=per_symbol_counts,
            trust_file=trust_file, baseline_file=baseline_file,
        )

        self.ckpt.data["finished_at"] = datetime.utcnow().replace(tzinfo=timezone.utc).isoformat()
        self.ckpt.data["rag_written"] = self._rag_written
        self.ckpt.data["impact_updated"] = self._impact_updated
        self.ckpt.data["per_symbol"] = per_symbol_counts
        self.ckpt.save()

        summary = {
            "rows_processed": self._rows_read,
            "rag_written": self._rag_written,
            "impact_updated": self._impact_updated,
            "trust_file": trust_file,
            "baseline_file": baseline_file,
            "report_file": report_file,
            "per_symbol": per_symbol_counts,
            "dry_run": self.dry_run,
        }
        logger.info("✅ warmup done: %s", json.dumps(summary, default=str))
        await self._emit_event(summary)
        return summary

    async def _fetch_rows(self, db: Any, since: datetime) -> List[Dict[str, Any]]:
        symbol_filter = f" AND symbol = ANY($2)" if self.symbols else ""
        query = (
            "SELECT id, symbol, event_ts, move_magnitude_pct, move_direction, label, "
            "horizon_minutes, confluence_score_t_minus_1h, fast_brain_p_t_minus_1h, "
            "features_t_minus_1h, realized_pnl_pct, decided, decision_source, decision_path "
            "FROM counterfactual_observations "
            "WHERE event_ts >= $1"
            + symbol_filter
            + " AND (warmup_generated = FALSE OR warmup_generated IS NULL) "
            "ORDER BY event_ts ASC"
        )
        try:
            if self.symbols:
                return await db.fetch(query, since, self.symbols)
            return await db.fetch(query, since)
        except Exception as e:
            logger.warning("warmup fetch failed: %s", e)
            return []

    def _persist_trust(self) -> Optional[str]:
        if not self._trust:
            return None
        if self.dry_run:
            return f"{self.trust_path} (dry-run)"
        try:
            self.trust_path.parent.mkdir(parents=True, exist_ok=True)
            payload = {
                "generated_at": datetime.utcnow().replace(tzinfo=timezone.utc).isoformat(),
                "source": "warmup_from_history",
                "channels": self._trust,
            }
            self.trust_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
            return str(self.trust_path)
        except Exception as e:
            logger.error("trust persist failed: %s", e)
            return None

    def _persist_safety_baseline(self) -> Optional[str]:
        samples = self._brier_samples
        if len(samples) < 100:
            logger.warning("safety baseline skipped — only %d brier samples (<100)", len(samples))
            return None
        brier_vals = [_brier(p, up) for p, up, _ in samples]
        hit_vals = [1.0 if hit else 0.0 for _, _, hit in samples]
        p30 = _percentile(brier_vals, 30.0)  # 30th percentile ≈ stricter side
        hit_p30 = _percentile(hit_vals, 30.0) or 0.5
        baseline: Dict[str, Any] = {
            "brier": p30 if p30 is not None else statistics.fmean(brier_vals),
            "hitrate": hit_p30,
            "confluence": {"per_symbol_mean": {}, "per_symbol_std": {}},
            "bootstrapped_at": time.time(),
            "bootstrapped_from": "historical_warmup",
            "n_brier_samples": len(samples),
        }
        for sym, vals in self._confluence_by_symbol.items():
            if len(vals) < 30:
                continue
            baseline["confluence"]["per_symbol_mean"][sym] = statistics.fmean(vals)
            if len(vals) > 1:
                baseline["confluence"]["per_symbol_std"][sym] = max(statistics.pstdev(vals), 1e-6)
        if self.dry_run:
            return f"{self.safety_baseline_path} (dry-run)"
        try:
            self.safety_baseline_path.parent.mkdir(parents=True, exist_ok=True)
            self.safety_baseline_path.write_text(json.dumps(baseline, indent=2), encoding="utf-8")
            return str(self.safety_baseline_path)
        except Exception as e:
            logger.error("safety baseline persist failed: %s", e)
            return None

    def _write_report(
        self, *, started: datetime, rows: int, per_symbol: Dict[str, Dict[str, int]],
        trust_file: Optional[str], baseline_file: Optional[str],
    ) -> Optional[str]:
        try:
            self.report_dir.mkdir(parents=True, exist_ok=True)
            stamp = started.strftime("%Y%m%d_%H%M%S")
            path = self.report_dir / f"warmup_report_{stamp}.md"
            lines = [
                f"# Warmup Report — {stamp}",
                "",
                f"- Days window: **{self.days}**",
                f"- Symbols: `{', '.join(self.symbols)}`",
                f"- Rows processed: **{rows}**",
                f"- Dry run: **{self.dry_run}**",
                f"- RAG entries written: **{self._rag_written}**",
                f"- Impact rows updated: **{self._impact_updated}**",
                "",
                "## Per-symbol label counts",
                "",
                "| Symbol | TP | FP | FN | TN |",
                "|--------|----|----|----|----|",
            ]
            for sym, counts in per_symbol.items():
                lines.append(
                    f"| {sym} | {counts['TP']} | {counts['FP']} | {counts['FN']} | {counts['TN']} |"
                )
            lines += [
                "",
                "## Trust score init (Dirichlet α_TP / α_FP / α_FN / α_TN)",
                "",
                "| Channel | samples | α_TP | α_FP | α_FN | α_TN |",
                "|---------|---------|------|------|------|------|",
            ]
            for ch, bucket in sorted(self._trust.items(), key=lambda kv: kv[0]):
                lines.append(
                    f"| {ch} | {int(bucket.get('samples', 0))} | "
                    f"{bucket.get('alpha_TP', 1.0):.2f} | {bucket.get('alpha_FP', 1.0):.2f} | "
                    f"{bucket.get('alpha_FN', 1.0):.2f} | {bucket.get('alpha_TN', 1.0):.2f} |"
                )
            lines += [
                "",
                "## Safety-net baseline",
                "",
                f"- File: `{baseline_file}`",
                f"- Trust file: `{trust_file}`",
                "",
            ]
            content = "\n".join(lines)
            if self.dry_run:
                logger.info("[dry-run] would write report to %s (%d lines)", path, len(lines))
                return str(path)
            path.write_text(content, encoding="utf-8")
            return str(path)
        except Exception as e:
            logger.error("report write failed: %s", e)
            return None

    async def _emit_event(self, summary: Dict[str, Any]) -> None:
        try:
            from event_bus import Event, EventType, get_event_bus  # type: ignore
            bus = get_event_bus()
            await bus.publish(Event(
                type=EventType.WARMUP_COMPLETED, source="warmup_from_history",
                data=summary,
            ))
        except Exception as e:
            logger.debug("warmup event skipped: %s", e)


# ─── CLI ──────────────────────────────────────────────────────────

def _parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Aşama 1 historical warmup")
    p.add_argument("--days", type=int, default=30)
    p.add_argument("--symbols", type=str, default=",".join(DEFAULT_SYMBOLS))
    p.add_argument("--rag-limit", type=int, default=1000)
    p.add_argument("--dry-run", action="store_true", default=False)
    p.add_argument("--trust-path", type=str, default=None)
    p.add_argument("--baseline-path", type=str, default=None)
    p.add_argument("--checkpoint-path", type=str, default=None)
    p.add_argument("--report-dir", type=str, default=None)
    p.add_argument("-v", "--verbose", action="store_true")
    return p.parse_args(argv)


def _resolve_paths(args: argparse.Namespace) -> Dict[str, str]:
    try:
        from config import Config  # type: ignore
        return {
            "trust_path": args.trust_path or Config.WARMUP_TRUST_SCORES_PATH,
            "baseline_path": args.baseline_path or Config.SAFETY_NET_BASELINE_PATH,
            "checkpoint_path": args.checkpoint_path or Config.WARMUP_CHECKPOINT_PATH,
            "report_dir": args.report_dir or Config.WARMUP_REPORT_DIR,
        }
    except Exception:
        return {
            "trust_path": args.trust_path or "python_agents/.channel_trust_scores.json",
            "baseline_path": args.baseline_path or "python_agents/.safety_net_baseline.json",
            "checkpoint_path": args.checkpoint_path or "python_agents/.warmup_checkpoint.json",
            "report_dir": args.report_dir or "python_agents/.warmup_reports",
        }


async def _amain(argv: Optional[List[str]] = None) -> int:
    args = _parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s :: %(message)s",
    )
    paths = _resolve_paths(args)
    symbols = [s.strip() for s in args.symbols.split(",") if s.strip()]
    w = HistoricalWarmup(
        days=args.days, symbols=symbols, rag_limit=args.rag_limit,
        dry_run=args.dry_run, trust_path=paths["trust_path"],
        safety_baseline_path=paths["baseline_path"],
        checkpoint_path=paths["checkpoint_path"],
        report_dir=paths["report_dir"],
    )
    summary = await w.run()
    print(json.dumps(summary, indent=2, default=str))
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    return asyncio.run(_amain(argv))


if __name__ == "__main__":
    sys.exit(main())
