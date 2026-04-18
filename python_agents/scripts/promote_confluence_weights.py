"""
Promote Confluence Weights — Phase 4 Finalization
===================================================
Candidate `.confluence_weights_candidate.json` uretilmis demektir
(`online_learning.recompute_from_db` tarafindan). Bu script candidate
ile live weights'i son 30 gun counterfactual uzerinde karsilastirir ve
sadece asagidaki tum kriterleri saglarsa candidate live yapar:

  * Mean log-loss iyilestirmesi >= 5%
  * Sharpe iyilestirmesinin bootstrap %95 CI alt siniri > 0
  * Sembollerin >= %80'inde hit-rate regresyonu YOK

Karar `python_agents/.weight_promotion_log.jsonl` dosyasina islenir.
Promotion olursa `CONFLUENCE_WEIGHTS_ROTATED` eventi yayimlanir.

Kullanim:
  python python_agents/scripts/promote_confluence_weights.py --dry-run
  python python_agents/scripts/promote_confluence_weights.py
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import math
import os
import random
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))

logger = logging.getLogger("promote_confluence_weights")

DEFAULT_LIVE_PATH = Path("python_agents/.confluence_weights.json")
DEFAULT_CANDIDATE_PATH = Path("python_agents/.confluence_weights_candidate.json")
DEFAULT_LOG_PATH = Path("python_agents/.weight_promotion_log.jsonl")

MIN_LOGLOSS_GAIN = 0.05  # 5% relative improvement
MIN_SHARPE_CI_LOWER = 0.0
MIN_SYMBOLS_OK_RATIO = 0.80


# ───────────────── math helpers ─────────────────
def _logistic(x: float) -> float:
    if x > 40:
        return 1.0
    if x < -40:
        return 0.0
    return 1.0 / (1.0 + math.exp(-x))


def _score_row(features: Dict[str, Any], weights: Dict[str, float]) -> float:
    s = 0.0
    for k, v in (features or {}).items():
        try:
            s += float(weights.get(k, 0.0)) * float(v)
        except Exception:
            continue
    return _logistic(s)


def _logloss(probs: List[float], labels: List[int]) -> float:
    eps = 1e-12
    n = len(probs)
    if n == 0:
        return float("inf")
    total = 0.0
    for p, y in zip(probs, labels):
        p = max(eps, min(1.0 - eps, p))
        total += -(y * math.log(p) + (1 - y) * math.log(1 - p))
    return total / n


def _sharpe(returns: List[float]) -> float:
    if len(returns) < 2:
        return 0.0
    mean = sum(returns) / len(returns)
    var = sum((r - mean) ** 2 for r in returns) / (len(returns) - 1)
    std = math.sqrt(max(var, 1e-12))
    return mean / std if std > 0 else 0.0


def _bootstrap_sharpe_ci(returns: List[float], n: int = 400) -> Tuple[float, float]:
    if not returns:
        return (0.0, 0.0)
    random.seed(42)
    samples: List[float] = []
    L = len(returns)
    for _ in range(n):
        idx = [random.randrange(L) for _ in range(L)]
        samples.append(_sharpe([returns[i] for i in idx]))
    samples.sort()
    lo = samples[int(0.025 * len(samples))]
    hi = samples[int(0.975 * len(samples)) - 1 if len(samples) > 1 else 0]
    return (lo, hi)


# ───────────────── data ─────────────────
async def _load_observations(db: Any, days: int = 30, limit: int = 50000) -> List[Dict[str, Any]]:
    if db is None:
        return []
    since = datetime.utcnow() - timedelta(days=int(days))
    try:
        return await db.fetch_counterfactual_labels(since, int(limit))
    except Exception as exc:
        logger.warning("fetch counterfactuals failed: %s", exc)
        return []


def _prepare_samples(rows: List[Dict[str, Any]]) -> List[Tuple[Dict[str, Any], int, float, str]]:
    """(features, label, return_pct, symbol) tuples."""
    out: List[Tuple[Dict[str, Any], int, float, str]] = []
    for r in rows:
        feats = r.get("features_t_minus_1h")
        if isinstance(feats, str):
            try:
                feats = json.loads(feats)
            except Exception:
                feats = {}
        if not isinstance(feats, dict):
            continue
        lbl = str(r.get("label") or "").upper()
        y = 1 if lbl in {"TP", "FN"} else 0
        ret = float(r.get("realized_pnl_pct") or 0.0)
        sym = str(r.get("symbol") or "").upper()
        out.append((feats, y, ret, sym))
    return out


def _evaluate(samples: List[Tuple[Dict[str, Any], int, float, str]],
              weights: Dict[str, float]) -> Dict[str, Any]:
    probs: List[float] = []
    labels: List[int] = []
    returns: List[float] = []
    per_symbol_hits: Dict[str, List[int]] = {}
    for feats, y, ret, sym in samples:
        p = _score_row(feats, weights)
        probs.append(p)
        labels.append(y)
        # simulated long-only return: size by p threshold
        if p >= 0.55:
            returns.append(ret / 100.0)
        elif p <= 0.45:
            returns.append(-ret / 100.0)
        else:
            returns.append(0.0)
        hit = 1 if ((p >= 0.5) == (y == 1)) else 0
        per_symbol_hits.setdefault(sym, []).append(hit)
    ll = _logloss(probs, labels)
    sh = _sharpe(returns)
    sh_lo, sh_hi = _bootstrap_sharpe_ci(returns)
    per_symbol_hitrate = {
        s: (sum(h) / len(h)) if h else 0.0 for s, h in per_symbol_hits.items()
    }
    return {
        "logloss": ll,
        "sharpe": sh,
        "sharpe_ci95": [sh_lo, sh_hi],
        "per_symbol_hitrate": per_symbol_hitrate,
        "n": len(samples),
    }


def _decision(live_eval: Dict[str, Any], cand_eval: Dict[str, Any]) -> Dict[str, Any]:
    if live_eval["n"] == 0 or cand_eval["n"] == 0:
        return {"promote": False, "reason": "insufficient_samples",
                "live": live_eval, "candidate": cand_eval}
    live_ll = live_eval["logloss"]
    cand_ll = cand_eval["logloss"]
    ll_gain = (live_ll - cand_ll) / max(live_ll, 1e-12)
    cand_sh_lo = cand_eval["sharpe_ci95"][0]
    live_sh = live_eval["sharpe"]
    sharpe_gain_ok = (cand_sh_lo - live_sh) > MIN_SHARPE_CI_LOWER
    # symbol-level regression
    live_hr = live_eval["per_symbol_hitrate"]
    cand_hr = cand_eval["per_symbol_hitrate"]
    total_syms = max(1, len(live_hr))
    non_regressed = sum(
        1 for s, v in live_hr.items() if cand_hr.get(s, 0.0) >= v * 0.99
    )
    sym_ok_ratio = non_regressed / total_syms
    promote = (
        ll_gain >= MIN_LOGLOSS_GAIN
        and sharpe_gain_ok
        and sym_ok_ratio >= MIN_SYMBOLS_OK_RATIO
    )
    return {
        "promote": bool(promote),
        "logloss_gain_pct": ll_gain,
        "sharpe_ci_lower_gain": cand_sh_lo - live_sh,
        "symbols_non_regressed_ratio": sym_ok_ratio,
        "live": live_eval,
        "candidate": cand_eval,
    }


def _write_log(log_path: Path, payload: Dict[str, Any]) -> None:
    try:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with log_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps({"ts": datetime.utcnow().isoformat(), **payload}) + "\n")
    except Exception as exc:
        logger.debug("promotion log write failed: %s", exc)


async def _maybe_emit_rotated(payload: Dict[str, Any]) -> None:
    try:
        from event_bus import EventBus, EventType  # type: ignore
        # Best-effort — if bus singleton exists, publish. Otherwise silent.
        bus = EventBus.get_instance() if hasattr(EventBus, "get_instance") else None
        if bus is not None:
            await bus.publish(EventType.CONFLUENCE_WEIGHTS_ROTATED, payload)
    except Exception as exc:
        logger.debug("rotation event emit skipped: %s", exc)


# ───────────────── CLI ─────────────────
async def _amain(args: argparse.Namespace) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    live_path = Path(args.live)
    cand_path = Path(args.candidate)
    if not cand_path.exists():
        logger.error("Candidate weights yok: %s", cand_path)
        return 2
    cand_doc = json.loads(cand_path.read_text(encoding="utf-8"))
    cand_weights = cand_doc.get("weights") if isinstance(cand_doc.get("weights"), dict) else cand_doc
    live_weights: Dict[str, float] = {}
    if live_path.exists():
        try:
            live_doc = json.loads(live_path.read_text(encoding="utf-8"))
            live_weights = live_doc.get("weights") if isinstance(live_doc.get("weights"), dict) else live_doc
        except Exception as exc:
            logger.warning("live weights load failed: %s", exc)

    db: Any = None
    if not args.mock:
        if os.getenv("DATABASE_URL"):
            try:
                from database import Database  # type: ignore
                db = Database()
                await db.connect()
            except Exception as exc:
                logger.error("DB connect failed: %s — pass --mock for offline", exc)
                return 3
        else:
            logger.error("DATABASE_URL not set; pass --mock to bypass")
            return 4

    if args.mock:
        rows = _mock_observations()
    else:
        rows = await _load_observations(db, days=args.days)

    samples = _prepare_samples(rows)
    live_eval = _evaluate(samples, live_weights or {})
    cand_eval = _evaluate(samples, cand_weights or {})
    decision = _decision(live_eval, cand_eval)
    decision["days"] = args.days
    decision["samples"] = len(samples)
    decision["live_path"] = str(live_path)
    decision["candidate_path"] = str(cand_path)

    _write_log(Path(args.log), decision)
    print(json.dumps(decision, indent=2))

    if decision["promote"] and not args.dry_run:
        try:
            live_path.parent.mkdir(parents=True, exist_ok=True)
            live_path.write_text(json.dumps({
                "weights": cand_weights,
                "promoted_at": datetime.utcnow().isoformat(),
                "decision": {k: v for k, v in decision.items() if k in {
                    "logloss_gain_pct", "sharpe_ci_lower_gain",
                    "symbols_non_regressed_ratio", "days", "samples",
                }},
            }, indent=2), encoding="utf-8")
            await _maybe_emit_rotated({
                "path": str(live_path),
                "samples": len(samples),
                "logloss_gain_pct": decision.get("logloss_gain_pct"),
            })
            logger.info("✅ Confluence weights promoted → %s", live_path)
        except Exception as exc:
            logger.error("promote write failed: %s", exc)
            return 5
    else:
        logger.info("No promotion (promote=%s, dry_run=%s)", decision["promote"], args.dry_run)

    if db is not None and hasattr(db, "disconnect"):
        try:
            await db.disconnect()
        except Exception:
            pass
    return 0


def _mock_observations() -> List[Dict[str, Any]]:
    random.seed(11)
    rows: List[Dict[str, Any]] = []
    for i in range(200):
        r = {
            "symbol": "BTCUSDT" if i % 2 else "ETHUSDT",
            "event_ts": datetime.utcnow() - timedelta(hours=i),
            "label": ["TP", "FP", "FN", "TN"][i % 4],
            "features_t_minus_1h": {"f1": random.uniform(-1, 1), "f2": random.uniform(-1, 1)},
            "realized_pnl_pct": random.uniform(-3, 3),
        }
        rows.append(r)
    return rows


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Promote confluence weights after offline eval")
    p.add_argument("--live", type=str, default=str(DEFAULT_LIVE_PATH))
    p.add_argument("--candidate", type=str, default=str(DEFAULT_CANDIDATE_PATH))
    p.add_argument("--log", type=str, default=str(DEFAULT_LOG_PATH))
    p.add_argument("--days", type=int, default=30)
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--mock", action="store_true")
    return p


def main() -> int:
    return asyncio.run(_amain(_build_parser().parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
