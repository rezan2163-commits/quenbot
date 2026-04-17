#!/usr/bin/env python3
"""
Backfill Feature Store — Phase 1 Intel Upgrade
==============================================
`trades` tablosundaki geçmiş verilerden türetilebilen özellikleri hesaplayıp
Parquet-backed feature_store'a yazar.

Kapsam (sadece trade verisi mevcut):
  • multi_horizon signatures (5m/30m/2h/6h) → coherence, per-horizon conf/acc
  • systematic trade detection özeti (dominant_bot_type, systematic_ratio)

Kapsam DIŞI (order book geçmişi olmadan hesaplanamaz):
  • OFI, Hurst, VPIN, Kyle-λ, iceberg fingerprint
  Bunlar canlı veri akışıyla birlikte doldurulur.

Kullanım:
  python scripts/backfill_feature_store.py --days 30 [--symbols BTCUSDT,ETHUSDT]
  python scripts/backfill_feature_store.py --days 7 --resume

Resume:
  .backfill_checkpoint.json dosyasına per-symbol son işlenmiş timestamp yazılır.
  --resume ile kaldığı yerden devam eder.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Optional

# python_agents'i path'e ekle (scripts/ alt klasöründen çağırıldığında)
_HERE = Path(__file__).resolve().parent
_AGENTS_DIR = _HERE.parent
if str(_AGENTS_DIR) not in sys.path:
    sys.path.insert(0, str(_AGENTS_DIR))

from config import Config  # noqa: E402
from database import Database  # noqa: E402
from feature_store import get_feature_store  # noqa: E402
from systematic_trade_detector import get_systematic_detector  # noqa: E402
from multi_horizon_signatures import MultiHorizonSignatureEngine  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
)
logger = logging.getLogger("backfill")

CHECKPOINT_PATH = _AGENTS_DIR / ".backfill_checkpoint.json"


def _load_checkpoint() -> Dict[str, str]:
    try:
        if CHECKPOINT_PATH.exists():
            return json.loads(CHECKPOINT_PATH.read_text(encoding="utf-8"))
    except Exception as e:
        logger.warning("checkpoint okunamadı: %s", e)
    return {}


def _save_checkpoint(cp: Dict[str, str]) -> None:
    try:
        CHECKPOINT_PATH.write_text(json.dumps(cp, indent=2), encoding="utf-8")
    except Exception as e:
        logger.warning("checkpoint yazılamadı: %s", e)


async def _process_symbol(
    db: Database,
    symbol: str,
    start: datetime,
    end: datetime,
    fs,
    mh_engine: MultiHorizonSignatureEngine,
    sys_detector,
    chunk_minutes: int = 60,
) -> Dict[str, int]:
    """Bir sembol için [start, end) aralığını chunk'lar halinde işle."""
    stats = {"chunks": 0, "trades": 0, "features_written": 0, "errors": 0}
    cur = start
    while cur < end:
        chunk_end = min(cur + timedelta(minutes=chunk_minutes), end)
        try:
            async with db.pool.acquire() as conn:
                rows = await conn.fetch(
                    """
                    SELECT price, quantity, timestamp, side
                    FROM trades
                    WHERE symbol = $1 AND timestamp >= $2 AND timestamp < $3
                    ORDER BY timestamp ASC
                    """,
                    symbol, cur, chunk_end,
                )
            if not rows:
                cur = chunk_end
                continue

            # ingest_trade API: microstructure + systematic_detector akışını besler
            for r in rows:
                try:
                    price = float(r["price"])
                    qty = float(r["quantity"])
                    side = str(r["side"] or "").lower()
                    ts = r["timestamp"]
                    if ts.tzinfo is None:
                        ts = ts.replace(tzinfo=timezone.utc)
                    trade_obj = {
                        "symbol": symbol,
                        "price": price,
                        "quantity": qty,
                        "side": side,
                        "timestamp": ts,
                        "trade_id": None,
                    }
                    # Multi-horizon engine: her 4 detector'a aynı trade'i pompala
                    try:
                        for det in mh_engine._detectors.values():
                            det.ingest_trade(trade_obj)
                    except Exception:
                        pass
                    # Standalone systematic detector (mh'dakilerden bağımsız)
                    try:
                        sys_detector.ingest_trade(trade_obj)
                    except Exception:
                        pass
                except Exception:
                    stats["errors"] += 1
            stats["trades"] += len(rows)

            # Chunk sonunda snapshot hesapla → feature_store'a yaz
            try:
                await mh_engine._analyze_and_publish(symbol, time.time())
            except Exception as e:
                logger.debug("mh analyze fail %s: %s", symbol, e)
            mh_snap = mh_engine.snapshot(symbol)

            features: Dict[str, float] = {}
            if isinstance(mh_snap, dict):
                coh = mh_snap.get("coherence")
                if coh is not None:
                    try:
                        features["mh.coherence"] = float(coh)
                    except (TypeError, ValueError):
                        pass
                per_h = mh_snap.get("per_horizon") or {}
                if isinstance(per_h, dict):
                    for h_key, h_val in per_h.items():
                        if not isinstance(h_val, dict):
                            continue
                        dc = h_val.get("direction_confidence")
                        acc = h_val.get("accumulation_score")
                        if dc is not None:
                            try:
                                features[f"mh.conf_{h_key}"] = float(dc)
                            except (TypeError, ValueError):
                                pass
                        if acc is not None:
                            try:
                                features[f"mh.acc_{h_key}"] = float(acc)
                            except (TypeError, ValueError):
                                pass

            try:
                report = sys_detector.get_last_report(symbol)
                if report is not None:
                    features["sys.systematic_trade_ratio"] = float(
                        getattr(report, "systematic_trade_ratio", 0.0) or 0.0
                    )
                    features["sys.direction_confidence"] = float(
                        getattr(report, "direction_confidence", 0.0) or 0.0
                    )
                    features["sys.accumulation_score"] = float(
                        getattr(report, "accumulation_score", 0.0) or 0.0
                    )
                    features["sys.smart_money_flow"] = float(
                        getattr(report, "smart_money_flow", 0.0) or 0.0
                    )
            except Exception:
                pass

            if features:
                try:
                    await fs.write(symbol=symbol, ts=chunk_end, features=features)
                    stats["features_written"] += 1
                except Exception as e:
                    logger.debug("feature_store write fail %s: %s", symbol, e)
                    stats["errors"] += 1

            stats["chunks"] += 1
        except Exception as e:
            logger.error("chunk %s %s hata: %s", symbol, cur, e)
            stats["errors"] += 1
        cur = chunk_end

    return stats


async def main():
    parser = argparse.ArgumentParser(description="Feature Store backfill (Phase 1 Intel).")
    parser.add_argument("--days", type=int, default=30, help="Geriye dönük gün sayısı")
    parser.add_argument("--symbols", type=str, default="", help="Virgülle ayrılmış sembol listesi; boş=WATCHLIST")
    parser.add_argument("--resume", action="store_true", help="Checkpoint'ten devam et")
    parser.add_argument("--chunk-minutes", type=int, default=60, help="Chunk boyutu (dk)")
    args = parser.parse_args()

    if not getattr(Config, "FEATURE_STORE_ENABLED", False):
        logger.warning("FEATURE_STORE_ENABLED=False — çıkılıyor.")
        return

    symbols: List[str]
    if args.symbols.strip():
        symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    else:
        symbols = list(getattr(Config, "WATCHLIST", []) or [])
    if not symbols:
        logger.error("Sembol listesi boş — --symbols veya Config.WATCHLIST doldurun.")
        return

    end = datetime.now(tz=timezone.utc)
    default_start = end - timedelta(days=args.days)

    checkpoint = _load_checkpoint() if args.resume else {}

    # DB
    db = Database()
    await db.initialize()

    # Feature Store
    fs = get_feature_store(
        base_path=getattr(Config, "FEATURE_STORE_PATH", "python_agents/.feature_store"),
        flush_rows=getattr(Config, "FEATURE_STORE_FLUSH_ROWS", 2000),
        flush_seconds=getattr(Config, "FEATURE_STORE_FLUSH_SECONDS", 5.0),
        queue_max=getattr(Config, "FEATURE_STORE_QUEUE_MAX", 20000),
    )
    await fs.start()

    # Engines (standalone — event_bus'suz simülasyon modu)
    sys_detector = get_systematic_detector()
    mh_engine = MultiHorizonSignatureEngine(event_bus=None, feature_store=None)

    total = {"chunks": 0, "trades": 0, "features_written": 0, "errors": 0}
    t0 = time.time()

    for sym in symbols:
        try:
            if args.resume and sym in checkpoint:
                start = datetime.fromisoformat(checkpoint[sym])
                if start.tzinfo is None:
                    start = start.replace(tzinfo=timezone.utc)
                if start >= end:
                    logger.info("[%s] checkpoint zaten güncel, atlanıyor.", sym)
                    continue
            else:
                start = default_start
            logger.info("[%s] backfill %s → %s", sym, start.isoformat(), end.isoformat())
            stats = await _process_symbol(
                db, sym, start, end, fs, mh_engine, sys_detector,
                chunk_minutes=args.chunk_minutes,
            )
            logger.info(
                "[%s] ✓ chunks=%d trades=%d features=%d errors=%d",
                sym, stats["chunks"], stats["trades"],
                stats["features_written"], stats["errors"],
            )
            for k, v in stats.items():
                total[k] += v
            checkpoint[sym] = end.isoformat()
            _save_checkpoint(checkpoint)
        except Exception as e:
            logger.error("[%s] fatal: %s", sym, e)

    await fs.flush()
    await fs.stop()
    await db.close()

    dur = time.time() - t0
    logger.info(
        "BACKFILL TAMAMLANDI | semboller=%d süre=%.1fs chunks=%d trades=%d features=%d errors=%d",
        len(symbols), dur, total["chunks"], total["trades"],
        total["features_written"], total["errors"],
    )


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("iptal edildi.")
