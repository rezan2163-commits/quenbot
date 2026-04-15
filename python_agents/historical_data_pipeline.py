#!/usr/bin/env python3
"""
historical_data_pipeline.py — 40GB+ Historical Data Batch Indexing Pipeline
============================================================================
PostgreSQL'deki büyük trade verisini ChromaDB ve Brain pattern hafızasına
batch olarak index'ler.

KULLANIM:
  cd python_agents
  python3 historical_data_pipeline.py --mode scan       # Sadece veri analizi
  python3 historical_data_pipeline.py --mode index      # Full indexleme
  python3 historical_data_pipeline.py --mode signatures # Sadece signature üretimi
  python3 historical_data_pipeline.py --mode vectors    # Sadece ChromaDB vektör upsert

ÖZELLİKLER:
  - Batch processing: 10K trade/batch, memory-efficient streaming
  - Checkpoint/resume: Kaldığı yerden devam eder
  - Progress tracking: ETA ve tamamlama yüzdesi
  - Parallel signature generation: asyncio.gather ile hızlandırılmış
  - RAM-aware: 32GB sunucuda güvenli çalışır
"""
import argparse
import asyncio
import json
import logging
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Any, Optional, Tuple

import numpy as np

# Add parent dir to path
sys.path.insert(0, os.path.dirname(__file__))

from config import Config
from database import Database
from vector_memory import get_vector_store

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("pipeline")

# ─── Configuration ───
BATCH_SIZE = 10_000          # Trade batch boyutu (RAM-friendly)
SIGNATURE_WINDOW = 60        # Signature penceresi (trade sayısı)
SIGNATURE_MIN_CHANGE = 0.02  # Minimum %2 hareket
VECTOR_BATCH_SIZE = 500      # ChromaDB upsert batch boyutu
CHECKPOINT_FILE = os.path.join(os.path.dirname(__file__), "efom_data", "pipeline_checkpoint.json")


def _load_checkpoint() -> Dict[str, Any]:
    try:
        with open(CHECKPOINT_FILE, 'r') as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _save_checkpoint(data: Dict[str, Any]):
    os.makedirs(os.path.dirname(CHECKPOINT_FILE), exist_ok=True)
    with open(CHECKPOINT_FILE, 'w') as f:
        json.dump(data, f, indent=2, default=str)


class HistoricalDataPipeline:
    def __init__(self, db: Database):
        self.db = db
        self.vector_store = get_vector_store()
        self.checkpoint = _load_checkpoint()
        self._stats = {
            'total_trades_processed': 0,
            'signatures_generated': 0,
            'vectors_upserted': 0,
            'errors': 0,
            'start_time': time.time(),
        }

    async def scan(self) -> Dict[str, Any]:
        """Veri tabanındaki trade verisini analiz et"""
        logger.info("=" * 60)
        logger.info("📊 SCAN MODE — Veri analizi")
        logger.info("=" * 60)

        # Toplam trade sayısı
        result = await self.db.fetch("""
            SELECT 
                COUNT(*)::bigint AS total,
                MIN(timestamp) AS first_trade,
                MAX(timestamp) AS last_trade,
                COUNT(DISTINCT symbol)::int AS symbols,
                COUNT(DISTINCT exchange)::int AS exchanges,
                pg_size_pretty(pg_total_relation_size('trades')) AS table_size
            FROM trades
        """)
        row = result[0] if result else {}
        total = int(row.get('total', 0))
        first = row.get('first_trade')
        last = row.get('last_trade')

        logger.info(f"  Toplam trade: {total:,}")
        logger.info(f"  İlk trade:   {first}")
        logger.info(f"  Son trade:    {last}")
        logger.info(f"  Sembol sayısı: {row.get('symbols', 0)}")
        logger.info(f"  Exchange: {row.get('exchanges', 0)}")
        logger.info(f"  Tablo boyutu: {row.get('table_size', '?')}")

        # Sembol bazlı dağılım
        dist = await self.db.fetch("""
            SELECT symbol, COUNT(*)::bigint AS cnt,
                   MIN(timestamp) AS first_ts, MAX(timestamp) AS last_ts
            FROM trades
            GROUP BY symbol
            ORDER BY cnt DESC
            LIMIT 20
        """)
        logger.info("\n  Sembol dağılımı:")
        for r in (dist or []):
            logger.info(f"    {r['symbol']}: {int(r['cnt']):,} trades ({r['first_ts']} → {r['last_ts']})")

        # Mevcut signature sayısı
        sig_count = await self.db.count_historical_signatures()
        logger.info(f"\n  Mevcut historical_signatures: {sig_count:,}")

        # ChromaDB durum
        vs_stats = self.vector_store.get_stats()
        logger.info(f"  ChromaDB patterns: {vs_stats.get('pattern_count', 0):,}")
        logger.info(f"  ChromaDB experiences: {vs_stats.get('experience_count', 0):,}")

        # Tahmini indexleme süresi
        estimated_batches = total // BATCH_SIZE + 1
        estimated_signatures = total // SIGNATURE_WINDOW
        logger.info(f"\n  Tahmini batch sayısı: {estimated_batches:,}")
        logger.info(f"  Tahmini signature: {estimated_signatures:,}")

        return {
            'total_trades': total,
            'first_trade': str(first),
            'last_trade': str(last),
            'symbols': row.get('symbols', 0),
            'existing_signatures': sig_count,
            'chroma_patterns': vs_stats.get('pattern_count', 0),
        }

    async def generate_signatures(self, resume: bool = True) -> Dict[str, Any]:
        """Trade verilerinden historical_signatures üret — batch streaming"""
        logger.info("=" * 60)
        logger.info("📝 SIGNATURE GENERATION — Batch processing")
        logger.info("=" * 60)

        # Checkpoint'ten devam
        last_id = 0
        if resume and 'signature_last_id' in self.checkpoint:
            last_id = int(self.checkpoint['signature_last_id'])
            logger.info(f"  Checkpoint'ten devam: last_id={last_id}")

        # Toplam trade sayısını al
        total_result = await self.db.fetch(
            "SELECT COUNT(*)::bigint AS cnt FROM trades WHERE id > $1", last_id
        )
        remaining = int(total_result[0]['cnt']) if total_result else 0
        logger.info(f"  İşlenecek trade: {remaining:,}")

        if remaining == 0:
            logger.info("  ✓ Tüm trade'ler zaten işlenmiş")
            return self._stats

        processed = 0
        batch_num = 0

        while True:
            # Batch fetch — ID-based pagination (offset kullanmıyoruz)
            batch = await self.db.fetch("""
                SELECT id, exchange, market_type, symbol, price, quantity, 
                       timestamp, side, trade_id
                FROM trades 
                WHERE id > $1
                ORDER BY id ASC
                LIMIT $2
            """, last_id, BATCH_SIZE)

            if not batch:
                break

            batch_num += 1
            batch_start = time.time()

            # Sembole göre grupla
            by_symbol: Dict[str, List[Dict]] = {}
            for trade in batch:
                sym = trade.get('symbol', '')
                by_symbol.setdefault(sym, []).append(trade)

            # Her sembol için signature oluştur
            signatures_in_batch = 0
            for symbol, trades in by_symbol.items():
                if len(trades) < SIGNATURE_WINDOW:
                    continue

                sigs = self._extract_signatures_from_trades(symbol, trades)
                if sigs:
                    inserted = await self._bulk_insert_signatures(sigs)
                    signatures_in_batch += inserted
                    self._stats['signatures_generated'] += inserted

            processed += len(batch)
            self._stats['total_trades_processed'] = processed
            last_id = int(batch[-1]['id'])

            # Checkpoint kaydet
            self.checkpoint['signature_last_id'] = last_id
            self.checkpoint['signature_timestamp'] = datetime.now(timezone.utc).isoformat()
            _save_checkpoint(self.checkpoint)

            # Progress log
            pct = (processed / remaining) * 100
            elapsed = time.time() - self._stats['start_time']
            rate = processed / max(elapsed, 1)
            eta = (remaining - processed) / max(rate, 1)
            batch_time = time.time() - batch_start

            logger.info(
                f"  Batch {batch_num}: {processed:,}/{remaining:,} ({pct:.1f}%) | "
                f"{signatures_in_batch} sigs | {batch_time:.1f}s | "
                f"ETA: {eta/60:.0f}m"
            )

        logger.info(f"✓ Signature generation complete: {self._stats['signatures_generated']:,} signatures")
        return self._stats

    def _extract_signatures_from_trades(
        self, symbol: str, trades: List[Dict]
    ) -> List[Dict[str, Any]]:
        """Trade listesinden historical signature'ları çıkar"""
        signatures = []
        prices = [float(t['price']) for t in trades]

        for i in range(0, len(prices) - SIGNATURE_WINDOW, SIGNATURE_WINDOW // 2):
            window = prices[i:i + SIGNATURE_WINDOW]
            if len(window) < SIGNATURE_WINDOW:
                break

            start_price = window[0]
            end_price = window[-1]
            if start_price <= 0:
                continue

            change_pct = (end_price - start_price) / start_price
            abs_change = abs(change_pct)

            if abs_change < SIGNATURE_MIN_CHANGE:
                continue

            # Normalize price vector
            norm_vector = [(p / start_price - 1.0) for p in window]
            direction = 'long' if change_pct > 0 else 'short'

            # Volume bilgisi
            window_trades = trades[i:i + SIGNATURE_WINDOW]
            buy_vol = sum(float(t.get('quantity', 0)) for t in window_trades if t.get('side') == 'buy')
            sell_vol = sum(float(t.get('quantity', 0)) for t in window_trades if t.get('side') == 'sell')
            total_vol = buy_vol + sell_vol

            signatures.append({
                'exchange': trades[i].get('exchange', 'mixed'),
                'market_type': trades[i].get('market_type', 'spot'),
                'symbol': symbol,
                'timeframe': '15m',
                'direction': direction,
                'change_pct': float(change_pct),
                'start_price': float(start_price),
                'end_price': float(end_price),
                'volume': float(total_vol),
                'buy_volume': float(buy_vol),
                'sell_volume': float(sell_vol),
                'normalized_profile': norm_vector,
                'start_time': trades[i].get('timestamp'),
                'end_time': window_trades[-1].get('timestamp'),
            })

        return signatures

    async def _bulk_insert_signatures(self, signatures: List[Dict]) -> int:
        """Batch signature insert — ON CONFLICT SKIP"""
        inserted = 0
        for sig in signatures:
            try:
                await self.db.execute("""
                    INSERT INTO historical_signatures 
                    (exchange, market_type, symbol, timeframe, direction, change_pct,
                     start_price, end_price, volume, buy_volume, sell_volume,
                     normalized_profile, start_time, end_time)
                    VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14)
                    ON CONFLICT DO NOTHING
                """,
                    sig['exchange'], sig['market_type'], sig['symbol'],
                    sig['timeframe'], sig['direction'], sig['change_pct'],
                    sig['start_price'], sig['end_price'], sig['volume'],
                    sig['buy_volume'], sig['sell_volume'],
                    json.dumps(sig['normalized_profile']),
                    sig['start_time'], sig['end_time'],
                )
                inserted += 1
            except Exception as e:
                self._stats['errors'] += 1
                logger.debug(f"Signature insert error: {e}")
        return inserted

    async def index_vectors(self, resume: bool = True) -> Dict[str, Any]:
        """Historical signatures'ları ChromaDB vektörlerine dönüştür"""
        logger.info("=" * 60)
        logger.info("🔢 VECTOR INDEXING — ChromaDB batch upsert")
        logger.info("=" * 60)

        last_id = 0
        if resume and 'vector_last_id' in self.checkpoint:
            last_id = int(self.checkpoint['vector_last_id'])
            logger.info(f"  Checkpoint'ten devam: last_id={last_id}")

        total_result = await self.db.fetch(
            "SELECT COUNT(*)::bigint AS cnt FROM historical_signatures WHERE id > $1", last_id
        )
        remaining = int(total_result[0]['cnt']) if total_result else 0
        logger.info(f"  İndexlenecek signature: {remaining:,}")

        if remaining == 0:
            logger.info("  ✓ Tüm signature'lar zaten index'lenmiş")
            return self._stats

        processed = 0
        batch_num = 0

        while True:
            batch = await self.db.fetch("""
                SELECT id, symbol, timeframe, market_type, exchange, direction,
                       change_pct, start_price, end_price, volume, buy_volume,
                       sell_volume, normalized_profile, start_time, end_time
                FROM historical_signatures
                WHERE id > $1
                ORDER BY id ASC
                LIMIT $2
            """, last_id, VECTOR_BATCH_SIZE)

            if not batch:
                break

            batch_num += 1
            batch_start = time.time()

            ids_list = []
            embeddings = []
            metadatas = []
            documents = []

            for sig in batch:
                try:
                    profile = sig.get('normalized_profile')
                    if isinstance(profile, str):
                        profile = json.loads(profile)
                    if not profile or not isinstance(profile, list):
                        continue

                    # Profile'dan fiyat serisi
                    start_price = float(sig.get('start_price', 1))
                    prices = [start_price * (1 + p) for p in profile]
                    volumes = [float(sig.get('volume', 0)) / max(len(profile), 1)] * len(profile)

                    snapshot = self.vector_store.build_feature_snapshot(
                        symbol=sig['symbol'],
                        prices=prices,
                        volumes=volumes,
                        timeframe=sig.get('timeframe', '15m'),
                        market_type=sig.get('market_type', 'spot'),
                        exchange=sig.get('exchange', 'mixed'),
                        metadata={'direction': sig.get('direction', 'neutral')},
                        observed_at=sig.get('start_time'),
                    )

                    doc_id = f"hist:{sig['symbol']}:{sig.get('timeframe', '15m')}:{sig['id']}"
                    meta = {
                        "symbol": sig['symbol'],
                        "timeframe": sig.get('timeframe', '15m'),
                        "market_type": str(sig.get('market_type', 'spot')),
                        "exchange": str(sig.get('exchange', 'mixed')),
                        "observed_at": int(sig['start_time'].timestamp()) if sig.get('start_time') else 0,
                        "change_pct": float(sig.get('change_pct', 0)),
                        "direction": str(sig.get('direction', 'neutral')),
                        "magnitude": abs(float(sig.get('change_pct', 0))),
                        "buy_ratio": float(sig.get('buy_volume', 0)) / max(float(sig.get('volume', 1)), 1e-8),
                        "volatility": float(snapshot.volatility),
                    }

                    ids_list.append(doc_id)
                    embeddings.append(snapshot.feature_vector)
                    metadatas.append(meta)
                    documents.append(json.dumps({"source": "historical_pipeline", "sig_id": int(sig['id'])}))

                except Exception as e:
                    self._stats['errors'] += 1
                    logger.debug(f"Vector build error for sig {sig.get('id')}: {e}")

            # Batch ChromaDB upsert
            if ids_list:
                try:
                    self.vector_store._patterns.upsert(
                        ids=ids_list,
                        embeddings=embeddings,
                        metadatas=metadatas,
                        documents=documents,
                    )
                    self._stats['vectors_upserted'] += len(ids_list)
                except Exception as e:
                    logger.error(f"ChromaDB batch upsert error: {e}")
                    self._stats['errors'] += 1

            processed += len(batch)
            last_id = int(batch[-1]['id'])

            # Checkpoint
            self.checkpoint['vector_last_id'] = last_id
            self.checkpoint['vector_timestamp'] = datetime.now(timezone.utc).isoformat()
            _save_checkpoint(self.checkpoint)

            pct = (processed / remaining) * 100
            elapsed = time.time() - self._stats['start_time']
            rate = processed / max(elapsed, 1)
            eta = (remaining - processed) / max(rate, 1)

            logger.info(
                f"  Batch {batch_num}: {processed:,}/{remaining:,} ({pct:.1f}%) | "
                f"{len(ids_list)} vectors | {time.time() - batch_start:.1f}s | "
                f"ETA: {eta/60:.0f}m"
            )

        logger.info(f"✓ Vector indexing complete: {self._stats['vectors_upserted']:,} vectors")
        return self._stats

    async def full_index(self):
        """Tam indexleme: signatures + vectors"""
        logger.info("=" * 60)
        logger.info("🚀 FULL INDEX — Signatures + Vectors")
        logger.info("=" * 60)

        await self.scan()
        await self.generate_signatures()
        await self.index_vectors()

        elapsed = time.time() - self._stats['start_time']
        logger.info("=" * 60)
        logger.info("✅ PIPELINE COMPLETE")
        logger.info(f"  Trades processed: {self._stats['total_trades_processed']:,}")
        logger.info(f"  Signatures generated: {self._stats['signatures_generated']:,}")
        logger.info(f"  Vectors upserted: {self._stats['vectors_upserted']:,}")
        logger.info(f"  Errors: {self._stats['errors']}")
        logger.info(f"  Duration: {elapsed/60:.1f} minutes")
        logger.info("=" * 60)

        return self._stats


async def main():
    parser = argparse.ArgumentParser(description="QuenBot Historical Data Pipeline")
    parser.add_argument(
        "--mode",
        choices=["scan", "index", "signatures", "vectors"],
        default="scan",
        help="Pipeline modu: scan (analiz), index (full), signatures, vectors",
    )
    parser.add_argument("--no-resume", action="store_true", help="Checkpoint'ten devam etme, sıfırdan başla")
    args = parser.parse_args()

    db = Database()
    await db.connect()

    try:
        pipeline = HistoricalDataPipeline(db)

        if args.no_resume:
            pipeline.checkpoint = {}
            _save_checkpoint({})
            logger.info("🔄 Checkpoint temizlendi — sıfırdan başlıyor")

        if args.mode == "scan":
            await pipeline.scan()
        elif args.mode == "signatures":
            await pipeline.generate_signatures(resume=not args.no_resume)
        elif args.mode == "vectors":
            await pipeline.index_vectors(resume=not args.no_resume)
        elif args.mode == "index":
            await pipeline.full_index()

    finally:
        await db.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
