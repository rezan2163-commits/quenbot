"""
backfill_directive_impact.py — Aşama 2 Historical Impact Backfill
===================================================================
Produces 3k–8k synthetic directive-impact pairs from the last N days of
``counterfactual_observations``. The rows populate ``oracle_directives``
with ``synthetic=TRUE``, ``source_tag='aşama2_backfill'`` and a computed
``impact_score ∈ [-1, +1]`` so the Qwen Oracle Brain has real historical
feedback to learn from — bypassing the 4-day wait of Aşama 2.

Guardrails
----------
  - Only rows produced by this script ever carry ``synthetic=TRUE``.
  - Every row carries ``source_tag='aşama2_backfill'`` so live learning
    pipelines can filter them out.
  - The script never touches existing live directive rows.
  - Idempotent: re-running skips rows that already have a matching
    synthetic directive (directive_id prefix ``syn_``).

Usage
-----
    python python_agents/scripts/backfill_directive_impact.py \\
        --days 90 \\
        --symbols BTCUSDT,ETHUSDT,SOLUSDT \\
        --dry-run
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import math
import os
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

logger = logging.getLogger("backfill_directive_impact")


DEFAULT_SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT"]
SOURCE_TAG = "aşama2_backfill"
SYNTH_PREFIX = "syn_"

# Directive types exercised by the backfill. We cover the Aşama 2
# allowlist so the Qwen prompt sees feedback on every type.
BACKFILL_TYPES = [
    "ADJUST_CONFIDENCE_THRESHOLD",
    "ADJUST_POSITION_SIZE_MULT",
    "PAUSE_SYMBOL",
    "RESUME_SYMBOL",
    "CHANGE_STRATEGY_WEIGHT",
    "ADJUST_TP_SL_RATIO",
]


# ─── counterfactual row → synthetic directive type ─────────────────
def pick_directive_type(row: Dict[str, Any]) -> str:
    """Deterministic mapping so tests are reproducible. Different
    (label, magnitude) buckets get different directive types so the
    Qwen prompt sees a balanced distribution."""
    label = str(row.get("label") or "").upper()
    mag = abs(float(row.get("move_magnitude_pct") or 0.0))
    if label == "TP" and mag >= 0.03:
        return "ADJUST_POSITION_SIZE_MULT"
    if label == "TP":
        return "ADJUST_CONFIDENCE_THRESHOLD"
    if label == "FP" and mag >= 0.03:
        return "PAUSE_SYMBOL"
    if label == "FP":
        return "CHANGE_STRATEGY_WEIGHT"
    if label == "FN":
        return "RESUME_SYMBOL"
    return "ADJUST_TP_SL_RATIO"


def compute_impact_score(row: Dict[str, Any]) -> float:
    """Map a counterfactual row to an ``impact_score ∈ [-1, +1]``.

    Signed effectiveness: a directive that would have fired on an
    eventually-profitable move has positive impact; one that would have
    triggered on a losing move has negative impact. ``realized_pnl_pct``
    carries the sign and magnitude; we scale by 5% and clip.
    """
    pnl = float(row.get("realized_pnl_pct") or 0.0)
    label = str(row.get("label") or "").upper()
    sim = row.get("historical_impact_simulation") or {}
    if isinstance(sim, dict) and "impact" in sim:
        try:
            v = float(sim["impact"])
            if -1.0 <= v <= 1.0:
                return v
        except Exception:
            pass
    # Derive from pnl sign + label polarity.
    polarity = 1.0 if label in ("TP", "TN") else -1.0
    scaled = max(-1.0, min(1.0, pnl / 0.05))
    impact = polarity * abs(scaled)
    # Tie-break to avoid perfectly zero impact.
    if impact == 0.0:
        impact = 0.01 * polarity
    return float(round(impact, 4))


def build_synthetic_row(row: Dict[str, Any], *, now_ts: float) -> Dict[str, Any]:
    dtype = pick_directive_type(row)
    impact = compute_impact_score(row)
    event_ts = row.get("event_ts")
    try:
        ts = event_ts.timestamp() if hasattr(event_ts, "timestamp") else float(event_ts or now_ts)
    except Exception:
        ts = now_ts
    sid = f"{SYNTH_PREFIX}{int(ts)}_{row.get('id') or int(ts*1000)}_{dtype[:6]}"
    severity = "high" if abs(impact) >= 0.6 else ("med" if abs(impact) >= 0.25 else "low")
    return {
        "directive_id": sid,
        "ts": ts,
        "symbol": str(row.get("symbol") or "UNKNOWN"),
        "action": dtype,
        "severity": severity,
        "confidence": max(0.0, min(1.0, 0.5 + abs(impact) * 0.4)),
        "rationale": f"synthetic backfill from counterfactual {row.get('id')} ({row.get('label')})",
        "params_json": json.dumps({
            "source_row_id": row.get("id"),
            "counterfactual_label": row.get("label"),
        }),
        "ttl_sec": 0,
        "source": "historical_backfill",
        "shadow": True,
        "impact_score": impact,
        "impact_measured_at": datetime.fromtimestamp(ts, tz=timezone.utc),
        "synthetic": True,
        "source_tag": SOURCE_TAG,
    }


# ─── DB adapter ───────────────────────────────────────────────────
class BackfillDB:
    def __init__(self, *, pool: Any = None) -> None:
        self.pool = pool

    async def fetch_counterfactuals(
        self, *, since_ts: datetime, symbols: List[str], limit: int,
    ) -> List[Dict[str, Any]]:
        if self.pool is None:
            return []
        query = """
            SELECT id, symbol, event_ts, label, move_magnitude_pct,
                   realized_pnl_pct, historical_impact_simulation
            FROM counterfactual_observations
            WHERE event_ts >= $1
              AND symbol = ANY($2)
              AND label IS NOT NULL
            ORDER BY event_ts ASC
            LIMIT $3
        """
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(query, since_ts, symbols, limit)
        return [dict(r) for r in rows]

    async def directive_exists(self, directive_id: str) -> bool:
        if self.pool is None:
            return False
        async with self.pool.acquire() as conn:
            r = await conn.fetchval(
                "SELECT 1 FROM oracle_directives WHERE directive_id = $1", directive_id,
            )
            return bool(r)

    async def insert_synthetic(self, rows: List[Dict[str, Any]]) -> int:
        if self.pool is None or not rows:
            return 0
        insert_sql = """
            INSERT INTO oracle_directives (
                directive_id, ts, symbol, action, severity, confidence,
                rationale, params_json, ttl_sec, source, shadow,
                impact_score, impact_measured_at, synthetic, source_tag
            ) VALUES (
                $1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15
            )
            ON CONFLICT (directive_id) DO NOTHING
        """
        inserted = 0
        async with self.pool.acquire() as conn:
            async with conn.transaction():
                for r in rows:
                    res = await conn.execute(
                        insert_sql,
                        r["directive_id"], r["ts"], r["symbol"], r["action"],
                        r["severity"], r["confidence"], r["rationale"],
                        r["params_json"], r["ttl_sec"], r["source"], r["shadow"],
                        r["impact_score"], r["impact_measured_at"],
                        r["synthetic"], r["source_tag"],
                    )
                    if isinstance(res, str) and res.startswith("INSERT") and res.endswith(" 1"):
                        inserted += 1
        return inserted


# ─── orchestrator ────────────────────────────────────────────────
@dataclass
class BackfillReport:
    days: int
    symbols: List[str]
    rows_fetched: int
    rows_inserted: int
    skipped_existing: int
    impact_mean: float
    impact_std: float
    by_type: Dict[str, int]
    started_at: str
    finished_at: Optional[str] = None
    dry_run: bool = False


class DirectiveImpactBackfill:
    def __init__(
        self,
        *,
        db: BackfillDB,
        days: int = 90,
        symbols: Optional[List[str]] = None,
        limit: int = 20000,
        dry_run: bool = False,
        clock: Any = None,
        tracker: Any = None,
    ) -> None:
        self.db = db
        self.days = int(days)
        self.symbols = list(symbols or DEFAULT_SYMBOLS)
        self.limit = int(limit)
        self.dry_run = bool(dry_run)
        self._now = clock or time.time
        self._tracker = tracker

    async def run(self) -> BackfillReport:
        now_ts = self._now()
        since = datetime.fromtimestamp(now_ts - self.days * 86400, tz=timezone.utc)
        started = datetime.fromtimestamp(now_ts, tz=timezone.utc).isoformat()
        rows = await self.db.fetch_counterfactuals(
            since_ts=since, symbols=self.symbols, limit=self.limit,
        )
        synth_rows: List[Dict[str, Any]] = []
        by_type: Dict[str, int] = {}
        skipped = 0
        for row in rows:
            s = build_synthetic_row(row, now_ts=now_ts)
            if not self.dry_run and await self.db.directive_exists(s["directive_id"]):
                skipped += 1
                continue
            synth_rows.append(s)
            by_type[s["action"]] = by_type.get(s["action"], 0) + 1
        impacts = [r["impact_score"] for r in synth_rows]
        mean = sum(impacts) / len(impacts) if impacts else 0.0
        var = sum((v - mean) ** 2 for v in impacts) / len(impacts) if impacts else 0.0
        std = math.sqrt(var) if var > 0 else 0.0

        inserted = 0
        if not self.dry_run and synth_rows:
            inserted = await self.db.insert_synthetic(synth_rows)

        # Also feed tracker so the Qwen prompt immediately has a buffer.
        if self._tracker is not None:
            for s in synth_rows[-500:]:
                try:
                    await self._tracker.measure_synthetic(
                        type("D", (), {
                            "directive_id": s["directive_id"],
                            "action": s["action"],
                            "symbol": s["symbol"],
                            "ts": s["ts"],
                        })(),
                        baseline={"signal_quality": 0.0},
                        after={"signal_quality": s["impact_score"]},
                        source_tag=SOURCE_TAG,
                    )
                except Exception:
                    pass

        report = BackfillReport(
            days=self.days, symbols=self.symbols,
            rows_fetched=len(rows), rows_inserted=inserted if not self.dry_run else len(synth_rows),
            skipped_existing=skipped, impact_mean=mean, impact_std=std,
            by_type=by_type, started_at=started,
            finished_at=datetime.now(tz=timezone.utc).isoformat(),
            dry_run=self.dry_run,
        )
        return report


# ─── CLI ──────────────────────────────────────────────────────────
def _parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Backfill synthetic directive-impact pairs.")
    p.add_argument("--days", type=int, default=90)
    p.add_argument("--symbols", type=str, default=",".join(DEFAULT_SYMBOLS))
    p.add_argument("--limit", type=int, default=20000)
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--verbose", action="store_true")
    return p.parse_args(argv)


async def _amain(argv: Optional[List[str]] = None) -> int:
    args = _parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )
    try:
        import asyncpg  # type: ignore
    except Exception:
        asyncpg = None  # type: ignore
    pool = None
    if asyncpg is not None and os.getenv("DATABASE_URL"):
        try:
            pool = await asyncpg.create_pool(os.environ["DATABASE_URL"])
        except Exception as exc:
            logger.warning("backfill: db unavailable (%s) — dry-run only", exc)
            args.dry_run = True

    db = BackfillDB(pool=pool)
    runner = DirectiveImpactBackfill(
        db=db,
        days=args.days,
        symbols=[s.strip() for s in args.symbols.split(",") if s.strip()],
        limit=args.limit,
        dry_run=args.dry_run,
    )
    report = await runner.run()
    logger.info(
        "✅ backfill complete: fetched=%d inserted=%d skipped=%d mean=%.3f std=%.3f by_type=%s",
        report.rows_fetched, report.rows_inserted, report.skipped_existing,
        report.impact_mean, report.impact_std, report.by_type,
    )
    # JSON summary for machine consumers.
    print(json.dumps({
        "rows_fetched": report.rows_fetched,
        "rows_inserted": report.rows_inserted,
        "skipped_existing": report.skipped_existing,
        "impact_mean": report.impact_mean,
        "impact_std": report.impact_std,
        "by_type": report.by_type,
        "dry_run": report.dry_run,
        "days": report.days,
        "symbols": report.symbols,
    }, indent=2))
    if pool is not None:
        await pool.close()
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    return asyncio.run(_amain(argv))


if __name__ == "__main__":
    sys.exit(main())
