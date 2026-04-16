"""
Storage Manager — Akıllı Veri Yönetimi ve 70GB Pruning Stratejisi
=================================================================
Disk kullanımını izler, 70GB eşiğine yaklaştığında otomatik veri pruning
işlemi başlatır. Ham veri silinirken matematiksel özetler korunur.

MİMARİ KONUM: Altyapı Katmanı (Background Service)
- INPUT  ← PostgreSQL tablolar, disk kullanım metrikleri
- OUTPUT → Özet tablolar (history_summary), temizlenmiş disk alanı

FELSEFE: "Zekayı Kaybetmeden Veriyi Yönetmek"
- Ham veri silinir, matematiksel karakteristik özeti korunur
- Asenkron çalışır, sistem donmaz
- Near-miss oranı (%0.5) ve zaman skalası bilgisi saklanır
"""
import asyncio
import json
import logging
import os
import shutil
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import asyncpg

from event_bus import Event, EventType, get_event_bus

logger = logging.getLogger("quenbot.storage_manager")


class PruneStatus(str, Enum):
    IDLE = "idle"
    SCANNING = "scanning"
    SUMMARIZING = "summarizing"
    PRUNING = "pruning"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass
class StorageMetrics:
    """Depolama metrikleri."""
    total_bytes: int = 0
    used_bytes: int = 0
    free_bytes: int = 0
    usage_percent: float = 0.0
    db_size_bytes: int = 0
    threshold_bytes: int = 0
    over_threshold: bool = False
    scan_time: Optional[datetime] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "total_gb": round(self.total_bytes / (1024**3), 2),
            "used_gb": round(self.used_bytes / (1024**3), 2),
            "free_gb": round(self.free_bytes / (1024**3), 2),
            "usage_percent": round(self.usage_percent, 1),
            "db_size_gb": round(self.db_size_bytes / (1024**3), 2),
            "threshold_gb": round(self.threshold_bytes / (1024**3), 2),
            "over_threshold": self.over_threshold,
            "scan_time": self.scan_time.isoformat() if self.scan_time else None,
        }


@dataclass
class PruneSummary:
    """Pruning işlemi özeti."""
    status: PruneStatus = PruneStatus.IDLE
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    tables_processed: List[str] = field(default_factory=list)
    rows_summarized: int = 0
    rows_deleted: int = 0
    bytes_freed: int = 0
    summaries_created: int = 0
    errors: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "status": self.status.value,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
            "tables_processed": self.tables_processed,
            "rows_summarized": self.rows_summarized,
            "rows_deleted": self.rows_deleted,
            "mb_freed": round(self.bytes_freed / (1024**2), 2),
            "summaries_created": self.summaries_created,
            "errors": self.errors,
        }


@dataclass
class HistorySummary:
    """Özetlenmiş veri bloğu — ham veri yerine korunan matematiksel öz."""
    id: Optional[int] = None
    table_name: str = ""
    time_bucket: str = ""  # "2026-04-01_to_2026-04-07"
    symbol: str = ""
    market_type: str = "spot"
    
    # Temel istatistikler
    record_count: int = 0
    first_record_at: Optional[datetime] = None
    last_record_at: Optional[datetime] = None
    
    # Fiyat karakteristiği
    avg_price: float = 0.0
    min_price: float = 0.0
    max_price: float = 0.0
    price_volatility: float = 0.0  # Standart sapma
    
    # Hareket karakteristiği
    total_change_pct: float = 0.0
    avg_change_pct: float = 0.0
    up_count: int = 0
    down_count: int = 0
    directional_bias: float = 0.0  # -1 (hep aşağı) to +1 (hep yukarı)
    
    # Hacim karakteristiği
    total_volume: float = 0.0
    avg_volume: float = 0.0
    buy_volume_ratio: float = 0.0  # 0-1
    
    # Performans özeti
    success_rate: float = 0.0
    avg_pnl_pct: float = 0.0
    near_miss_rate: float = 0.0
    
    # Zaman skalası dağılımı (15m, 1h, 4h, 24h hedefleri için)
    horizon_distribution: Dict[str, float] = field(default_factory=dict)
    
    # Meta
    created_at: Optional[datetime] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "table_name": self.table_name,
            "time_bucket": self.time_bucket,
            "symbol": self.symbol,
            "market_type": self.market_type,
            "record_count": self.record_count,
            "first_record_at": self.first_record_at.isoformat() if self.first_record_at else None,
            "last_record_at": self.last_record_at.isoformat() if self.last_record_at else None,
            "avg_price": round(self.avg_price, 8),
            "min_price": round(self.min_price, 8),
            "max_price": round(self.max_price, 8),
            "price_volatility": round(self.price_volatility, 6),
            "total_change_pct": round(self.total_change_pct, 4),
            "avg_change_pct": round(self.avg_change_pct, 4),
            "up_count": self.up_count,
            "down_count": self.down_count,
            "directional_bias": round(self.directional_bias, 4),
            "total_volume": round(self.total_volume, 2),
            "avg_volume": round(self.avg_volume, 4),
            "buy_volume_ratio": round(self.buy_volume_ratio, 4),
            "success_rate": round(self.success_rate, 4),
            "avg_pnl_pct": round(self.avg_pnl_pct, 4),
            "near_miss_rate": round(self.near_miss_rate, 4),
            "horizon_distribution": self.horizon_distribution,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "metadata": self.metadata,
        }


class DataArchiver:
    """
    Veri Arşivleme Motoru — Eski veriyi özetleyip siler.
    
    Her tablo için özel özetleme mantığı içerir:
    - trades: Fiyat ve hacim karakteristiği
    - price_movements: Hareket patternleri
    - signals: Sinyal performansı
    - simulations: PnL ve başarı oranı
    """
    
    # Tablolar ve retention süreleri (gün)
    TABLE_RETENTION = {
        "trades": 7,              # 7 gün sonra özetle
        "price_movements": 90,    # 90 gün sonra özetle
        "signals": 90,            # 90 gün sonra özetle
        "simulations": 90,        # 90 gün sonra özetle
        "pattern_records": 90,    # 90 gün sonra özetle
        "historical_signatures": 90,  # 90 gün sonra özetle
        "pattern_match_results": 30,  # 30 gün sonra özetle
        "signature_matches": 30,  # 30 gün sonra özetle
        "chat_messages": 7,       # 7 gün sonra sil (özetleme yok)
        "brain_learning_log": 60, # 60 gün sonra özetle
        "audit_records": 90,      # 90 gün sonra özetle
        "rca_results": 60,        # 60 gün sonra özetle
    }
    
    # Sadece silinecek tablolar (özetleme yok)
    DELETE_ONLY_TABLES = {"chat_messages", "agent_heartbeat", "state_history"}
    
    # Özetlenecek tablolar
    SUMMARIZE_TABLES = {"trades", "price_movements", "signals", "simulations", "pattern_records"}

    def __init__(self, pool: asyncpg.Pool):
        self.pool = pool
        self._lock = asyncio.Lock()

    async def ensure_summary_table(self):
        """history_summary tablosunu oluştur."""
        async with self.pool.acquire() as conn:
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS history_summary (
                    id SERIAL PRIMARY KEY,
                    table_name VARCHAR(50) NOT NULL,
                    time_bucket VARCHAR(50) NOT NULL,
                    symbol VARCHAR(20),
                    market_type VARCHAR(20) DEFAULT 'spot',
                    record_count INTEGER NOT NULL DEFAULT 0,
                    first_record_at TIMESTAMP,
                    last_record_at TIMESTAMP,
                    avg_price DECIMAL(20, 8) DEFAULT 0,
                    min_price DECIMAL(20, 8) DEFAULT 0,
                    max_price DECIMAL(20, 8) DEFAULT 0,
                    price_volatility DECIMAL(10, 6) DEFAULT 0,
                    total_change_pct DECIMAL(10, 4) DEFAULT 0,
                    avg_change_pct DECIMAL(10, 4) DEFAULT 0,
                    up_count INTEGER DEFAULT 0,
                    down_count INTEGER DEFAULT 0,
                    directional_bias DECIMAL(5, 4) DEFAULT 0,
                    total_volume DECIMAL(30, 8) DEFAULT 0,
                    avg_volume DECIMAL(20, 8) DEFAULT 0,
                    buy_volume_ratio DECIMAL(5, 4) DEFAULT 0,
                    success_rate DECIMAL(5, 4) DEFAULT 0,
                    avg_pnl_pct DECIMAL(10, 4) DEFAULT 0,
                    near_miss_rate DECIMAL(5, 4) DEFAULT 0,
                    horizon_distribution JSONB,
                    metadata JSONB,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(table_name, time_bucket, symbol, market_type)
                )
            """)
            await conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_history_summary_table 
                ON history_summary(table_name, symbol)
            """)
            await conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_history_summary_time 
                ON history_summary(first_record_at, last_record_at)
            """)

    async def summarize_trades(
        self,
        symbol: str,
        market_type: str,
        start_date: datetime,
        end_date: datetime,
    ) -> Optional[HistorySummary]:
        """trades tablosu için özet oluştur."""
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow("""
                SELECT 
                    COUNT(*) as record_count,
                    MIN(timestamp) as first_record_at,
                    MAX(timestamp) as last_record_at,
                    AVG(price) as avg_price,
                    MIN(price) as min_price,
                    MAX(price) as max_price,
                    STDDEV(price) as price_volatility,
                    SUM(quantity) as total_volume,
                    AVG(quantity) as avg_volume,
                    SUM(CASE WHEN side = 'buy' THEN quantity ELSE 0 END) / 
                        NULLIF(SUM(quantity), 0) as buy_volume_ratio
                FROM trades
                WHERE symbol = $1 
                  AND market_type = $2
                  AND timestamp >= $3 
                  AND timestamp < $4
            """, symbol, market_type, start_date, end_date)
            
            if not row or row['record_count'] == 0:
                return None

            time_bucket = f"{start_date.strftime('%Y-%m-%d')}_to_{end_date.strftime('%Y-%m-%d')}"
            
            return HistorySummary(
                table_name="trades",
                time_bucket=time_bucket,
                symbol=symbol,
                market_type=market_type,
                record_count=row['record_count'],
                first_record_at=row['first_record_at'],
                last_record_at=row['last_record_at'],
                avg_price=float(row['avg_price'] or 0),
                min_price=float(row['min_price'] or 0),
                max_price=float(row['max_price'] or 0),
                price_volatility=float(row['price_volatility'] or 0),
                total_volume=float(row['total_volume'] or 0),
                avg_volume=float(row['avg_volume'] or 0),
                buy_volume_ratio=float(row['buy_volume_ratio'] or 0),
            )

    async def summarize_price_movements(
        self,
        symbol: str,
        market_type: str,
        start_date: datetime,
        end_date: datetime,
    ) -> Optional[HistorySummary]:
        """price_movements tablosu için özet oluştur."""
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow("""
                SELECT 
                    COUNT(*) as record_count,
                    MIN(start_time) as first_record_at,
                    MAX(end_time) as last_record_at,
                    AVG(start_price) as avg_price,
                    MIN(start_price) as min_price,
                    MAX(end_price) as max_price,
                    SUM(change_pct) as total_change_pct,
                    AVG(change_pct) as avg_change_pct,
                    STDDEV(change_pct) as price_volatility,
                    SUM(CASE WHEN direction = 'up' THEN 1 ELSE 0 END) as up_count,
                    SUM(CASE WHEN direction = 'down' THEN 1 ELSE 0 END) as down_count,
                    SUM(volume) as total_volume,
                    AVG(volume) as avg_volume,
                    SUM(COALESCE(buy_volume, 0)) / NULLIF(SUM(volume), 0) as buy_volume_ratio
                FROM price_movements
                WHERE symbol = $1 
                  AND market_type = $2
                  AND start_time >= $3 
                  AND start_time < $4
            """, symbol, market_type, start_date, end_date)
            
            if not row or row['record_count'] == 0:
                return None

            time_bucket = f"{start_date.strftime('%Y-%m-%d')}_to_{end_date.strftime('%Y-%m-%d')}"
            up_count = int(row['up_count'] or 0)
            down_count = int(row['down_count'] or 0)
            total = up_count + down_count
            directional_bias = (up_count - down_count) / max(total, 1)
            
            return HistorySummary(
                table_name="price_movements",
                time_bucket=time_bucket,
                symbol=symbol,
                market_type=market_type,
                record_count=row['record_count'],
                first_record_at=row['first_record_at'],
                last_record_at=row['last_record_at'],
                avg_price=float(row['avg_price'] or 0),
                min_price=float(row['min_price'] or 0),
                max_price=float(row['max_price'] or 0),
                price_volatility=float(row['price_volatility'] or 0),
                total_change_pct=float(row['total_change_pct'] or 0),
                avg_change_pct=float(row['avg_change_pct'] or 0),
                up_count=up_count,
                down_count=down_count,
                directional_bias=directional_bias,
                total_volume=float(row['total_volume'] or 0),
                avg_volume=float(row['avg_volume'] or 0),
                buy_volume_ratio=float(row['buy_volume_ratio'] or 0),
            )

    async def summarize_signals(
        self,
        symbol: str,
        market_type: str,
        start_date: datetime,
        end_date: datetime,
    ) -> Optional[HistorySummary]:
        """signals tablosu için özet oluştur."""
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow("""
                SELECT 
                    COUNT(*) as record_count,
                    MIN(timestamp) as first_record_at,
                    MAX(timestamp) as last_record_at,
                    AVG(price) as avg_price,
                    MIN(price) as min_price,
                    MAX(price) as max_price,
                    AVG(confidence) as avg_confidence,
                    SUM(CASE WHEN status = 'completed' THEN 1 ELSE 0 END)::float / 
                        NULLIF(COUNT(*), 0) as success_rate
                FROM signals
                WHERE symbol = $1 
                  AND market_type = $2
                  AND timestamp >= $3 
                  AND timestamp < $4
            """, symbol, market_type, start_date, end_date)
            
            if not row or row['record_count'] == 0:
                return None

            time_bucket = f"{start_date.strftime('%Y-%m-%d')}_to_{end_date.strftime('%Y-%m-%d')}"
            
            return HistorySummary(
                table_name="signals",
                time_bucket=time_bucket,
                symbol=symbol,
                market_type=market_type,
                record_count=row['record_count'],
                first_record_at=row['first_record_at'],
                last_record_at=row['last_record_at'],
                avg_price=float(row['avg_price'] or 0),
                min_price=float(row['min_price'] or 0),
                max_price=float(row['max_price'] or 0),
                success_rate=float(row['success_rate'] or 0),
                metadata={"avg_confidence": float(row['avg_confidence'] or 0)},
            )

    async def summarize_simulations(
        self,
        symbol: str,
        market_type: str,
        start_date: datetime,
        end_date: datetime,
    ) -> Optional[HistorySummary]:
        """simulations tablosu için özet oluştur."""
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow("""
                SELECT 
                    COUNT(*) as record_count,
                    MIN(entry_time) as first_record_at,
                    MAX(COALESCE(exit_time, entry_time)) as last_record_at,
                    AVG(entry_price) as avg_price,
                    MIN(entry_price) as min_price,
                    MAX(entry_price) as max_price,
                    SUM(CASE WHEN pnl_pct > 0 THEN 1 ELSE 0 END)::float / 
                        NULLIF(COUNT(*) FILTER (WHERE status = 'closed'), 0) as success_rate,
                    AVG(pnl_pct) FILTER (WHERE status = 'closed') as avg_pnl_pct,
                    SUM(CASE WHEN side = 'long' THEN 1 ELSE 0 END) as up_count,
                    SUM(CASE WHEN side = 'short' THEN 1 ELSE 0 END) as down_count
                FROM simulations
                WHERE symbol = $1 
                  AND market_type = $2
                  AND entry_time >= $3 
                  AND entry_time < $4
            """, symbol, market_type, start_date, end_date)
            
            if not row or row['record_count'] == 0:
                return None

            time_bucket = f"{start_date.strftime('%Y-%m-%d')}_to_{end_date.strftime('%Y-%m-%d')}"
            up_count = int(row['up_count'] or 0)
            down_count = int(row['down_count'] or 0)
            total = up_count + down_count
            directional_bias = (up_count - down_count) / max(total, 1)
            
            return HistorySummary(
                table_name="simulations",
                time_bucket=time_bucket,
                symbol=symbol,
                market_type=market_type,
                record_count=row['record_count'],
                first_record_at=row['first_record_at'],
                last_record_at=row['last_record_at'],
                avg_price=float(row['avg_price'] or 0),
                min_price=float(row['min_price'] or 0),
                max_price=float(row['max_price'] or 0),
                up_count=up_count,
                down_count=down_count,
                directional_bias=directional_bias,
                success_rate=float(row['success_rate'] or 0),
                avg_pnl_pct=float(row['avg_pnl_pct'] or 0),
            )

    async def save_summary(self, summary: HistorySummary) -> int:
        """Özeti history_summary tablosuna kaydet."""
        async with self.pool.acquire() as conn:
            return await conn.fetchval("""
                INSERT INTO history_summary (
                    table_name, time_bucket, symbol, market_type,
                    record_count, first_record_at, last_record_at,
                    avg_price, min_price, max_price, price_volatility,
                    total_change_pct, avg_change_pct, up_count, down_count,
                    directional_bias, total_volume, avg_volume, buy_volume_ratio,
                    success_rate, avg_pnl_pct, near_miss_rate,
                    horizon_distribution, metadata
                ) VALUES (
                    $1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13,
                    $14, $15, $16, $17, $18, $19, $20, $21, $22, $23, $24
                )
                ON CONFLICT (table_name, time_bucket, symbol, market_type)
                DO UPDATE SET
                    record_count = EXCLUDED.record_count,
                    avg_price = EXCLUDED.avg_price,
                    success_rate = EXCLUDED.success_rate,
                    avg_pnl_pct = EXCLUDED.avg_pnl_pct
                RETURNING id
            """,
            summary.table_name, summary.time_bucket, summary.symbol, summary.market_type,
            summary.record_count, summary.first_record_at, summary.last_record_at,
            summary.avg_price, summary.min_price, summary.max_price, summary.price_volatility,
            summary.total_change_pct, summary.avg_change_pct, summary.up_count, summary.down_count,
            summary.directional_bias, summary.total_volume, summary.avg_volume, summary.buy_volume_ratio,
            summary.success_rate, summary.avg_pnl_pct, summary.near_miss_rate,
            json.dumps(summary.horizon_distribution), json.dumps(summary.metadata),
            )

    async def delete_old_records(
        self,
        table_name: str,
        timestamp_column: str,
        cutoff: datetime,
        symbol: Optional[str] = None,
        market_type: Optional[str] = None,
        batch_size: int = 5000,
    ) -> int:
        """Eski kayıtları sil (batch halinde)."""
        total_deleted = 0
        async with self.pool.acquire() as conn:
            while True:
                if symbol and market_type:
                    deleted = await conn.fetchval(f"""
                        WITH to_delete AS (
                            SELECT id FROM {table_name}
                            WHERE {timestamp_column} < $1
                              AND symbol = $2
                              AND market_type = $3
                            LIMIT $4
                        )
                        DELETE FROM {table_name}
                        WHERE id IN (SELECT id FROM to_delete)
                        RETURNING id
                    """, cutoff, symbol, market_type, batch_size)
                else:
                    deleted = await conn.fetchval(f"""
                        WITH to_delete AS (
                            SELECT id FROM {table_name}
                            WHERE {timestamp_column} < $1
                            LIMIT $2
                        )
                        DELETE FROM {table_name}
                        WHERE id IN (SELECT id FROM to_delete)
                        RETURNING id
                    """, cutoff, batch_size)
                
                if not deleted:
                    break
                    
                total_deleted += 1
                await asyncio.sleep(0.1)  # CPU/IO pressure azalt
                
        return total_deleted


class StorageManager:
    """
    Ana Depolama Yöneticisi — 70GB Pruning Stratejisi
    ==================================================
    Disk kullanımını izler, eşik aşıldığında otomatik pruning başlatır.
    Asenkron çalışır, sistem donmaz.
    """
    
    # Eşik değerleri
    THRESHOLD_BYTES = int(os.getenv("QUENBOT_STORAGE_THRESHOLD_GB", "70")) * (1024**3)
    SCAN_INTERVAL_SECONDS = int(os.getenv("QUENBOT_STORAGE_SCAN_INTERVAL", "3600"))  # 1 saat
    MIN_FREE_BYTES = int(os.getenv("QUENBOT_MIN_FREE_GB", "20")) * (1024**3)  # Min 20GB boş
    
    # Monitör edilecek path (PostgreSQL data)
    MONITOR_PATH = os.getenv("QUENBOT_STORAGE_MONITOR_PATH", "/var/lib/docker")

    def __init__(self, pool: asyncpg.Pool):
        self.pool = pool
        self.archiver = DataArchiver(pool)
        self.event_bus = get_event_bus()
        self._running = False
        self._task: Optional[asyncio.Task] = None
        self._last_scan: Optional[datetime] = None
        self._last_prune: Optional[PruneSummary] = None
        self._metrics: Optional[StorageMetrics] = None
        self._lock = asyncio.Lock()

    async def initialize(self):
        """StorageManager'ı başlat."""
        await self.archiver.ensure_summary_table()
        logger.info(
            f"📦 StorageManager initialized: "
            f"threshold={self.THRESHOLD_BYTES / (1024**3):.1f}GB, "
            f"scan_interval={self.SCAN_INTERVAL_SECONDS}s"
        )

    async def start(self):
        """Arka plan izleme döngüsünü başlat."""
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._monitor_loop())
        logger.info("📦 StorageManager background monitor started")

    async def stop(self):
        """İzleme döngüsünü durdur."""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("📦 StorageManager stopped")

    async def _monitor_loop(self):
        """Ana izleme döngüsü — sonsuz çalışır."""
        while self._running:
            try:
                metrics = await self.scan_storage()
                
                if metrics.over_threshold:
                    logger.warning(
                        f"⚠️ Storage threshold exceeded: "
                        f"{metrics.used_bytes / (1024**3):.1f}GB / "
                        f"{self.THRESHOLD_BYTES / (1024**3):.1f}GB"
                    )
                    await self.run_pruning()
                
                await asyncio.sleep(self.SCAN_INTERVAL_SECONDS)
                
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"StorageManager monitor error: {e}")
                await asyncio.sleep(60)  # Hata durumunda 1dk bekle

    async def scan_storage(self) -> StorageMetrics:
        """Disk kullanımını tara."""
        try:
            # Disk alanı kontrolü
            stat = shutil.disk_usage(self.MONITOR_PATH)
            
            # Veritabanı boyutu
            db_size = await self._get_database_size()
            
            metrics = StorageMetrics(
                total_bytes=stat.total,
                used_bytes=stat.used,
                free_bytes=stat.free,
                usage_percent=(stat.used / stat.total) * 100,
                db_size_bytes=db_size,
                threshold_bytes=self.THRESHOLD_BYTES,
                over_threshold=stat.used >= self.THRESHOLD_BYTES or stat.free < self.MIN_FREE_BYTES,
                scan_time=datetime.utcnow(),
            )
            
            self._metrics = metrics
            self._last_scan = datetime.utcnow()
            
            logger.info(
                f"📊 Storage scan: "
                f"used={metrics.used_bytes / (1024**3):.1f}GB, "
                f"free={metrics.free_bytes / (1024**3):.1f}GB, "
                f"db={db_size / (1024**3):.2f}GB"
            )
            
            return metrics
            
        except Exception as e:
            logger.error(f"Storage scan error: {e}")
            return StorageMetrics()

    async def _get_database_size(self) -> int:
        """PostgreSQL veritabanı boyutunu al."""
        try:
            async with self.pool.acquire() as conn:
                row = await conn.fetchrow("""
                    SELECT pg_database_size(current_database()) as size
                """)
                return int(row['size']) if row else 0
        except Exception as e:
            logger.error(f"Database size query error: {e}")
            return 0

    async def run_pruning(self) -> PruneSummary:
        """Veri pruning işlemini çalıştır."""
        async with self._lock:
            summary = PruneSummary(
                status=PruneStatus.SCANNING,
                started_at=datetime.utcnow(),
            )
            
            try:
                # Event yayınla
                await self.event_bus.publish(Event(
                    type=EventType.SYSTEM_ALERT,
                    source="storage_manager",
                    data={"action": "pruning_started", "metrics": self._metrics.to_dict() if self._metrics else {}},
                ))
                
                # Tablo boyutlarını al
                table_sizes = await self._get_table_sizes()
                logger.info(f"📦 Table sizes: {table_sizes}")
                
                # Özetleme tabloları için işle
                summary.status = PruneStatus.SUMMARIZING
                for table_name in self.archiver.SUMMARIZE_TABLES:
                    if table_name not in table_sizes:
                        continue
                    
                    retention_days = self.archiver.TABLE_RETENTION.get(table_name, 30)
                    cutoff = datetime.utcnow() - timedelta(days=retention_days)
                    
                    # Symbol ve market_type listesini al
                    symbols = await self._get_distinct_symbols(table_name, cutoff)
                    
                    for symbol, market_type in symbols:
                        # 7 günlük bloklara ayır ve özetle
                        start_date = cutoff - timedelta(days=7)
                        end_date = cutoff
                        
                        # Özetleme fonksiyonunu seç
                        if table_name == "trades":
                            summary_obj = await self.archiver.summarize_trades(
                                symbol, market_type, start_date, end_date
                            )
                        elif table_name == "price_movements":
                            summary_obj = await self.archiver.summarize_price_movements(
                                symbol, market_type, start_date, end_date
                            )
                        elif table_name == "signals":
                            summary_obj = await self.archiver.summarize_signals(
                                symbol, market_type, start_date, end_date
                            )
                        elif table_name == "simulations":
                            summary_obj = await self.archiver.summarize_simulations(
                                symbol, market_type, start_date, end_date
                            )
                        else:
                            continue
                        
                        if summary_obj:
                            await self.archiver.save_summary(summary_obj)
                            summary.summaries_created += 1
                            summary.rows_summarized += summary_obj.record_count
                    
                    summary.tables_processed.append(table_name)
                
                # Pruning
                summary.status = PruneStatus.PRUNING
                initial_size = await self._get_database_size()
                
                for table_name, retention_days in self.archiver.TABLE_RETENTION.items():
                    cutoff = datetime.utcnow() - timedelta(days=retention_days)
                    timestamp_col = self._get_timestamp_column(table_name)
                    
                    if timestamp_col:
                        deleted = await self.archiver.delete_old_records(
                            table_name, timestamp_col, cutoff
                        )
                        summary.rows_deleted += deleted
                        logger.info(f"🗑 Pruned {table_name}: {deleted} rows deleted")
                
                # VACUUM ANALYZE
                await self._vacuum_tables()
                
                final_size = await self._get_database_size()
                summary.bytes_freed = max(0, initial_size - final_size)
                
                summary.status = PruneStatus.COMPLETED
                summary.completed_at = datetime.utcnow()
                
                logger.info(
                    f"✅ Pruning completed: "
                    f"{summary.rows_deleted} rows deleted, "
                    f"{summary.summaries_created} summaries created, "
                    f"{summary.bytes_freed / (1024**2):.1f}MB freed"
                )
                
                # Event yayınla
                await self.event_bus.publish(Event(
                    type=EventType.SYSTEM_ALERT,
                    source="storage_manager",
                    data={"action": "pruning_completed", "summary": summary.to_dict()},
                ))
                
            except Exception as e:
                summary.status = PruneStatus.FAILED
                summary.errors.append(str(e))
                summary.completed_at = datetime.utcnow()
                logger.error(f"Pruning failed: {e}")
            
            self._last_prune = summary
            return summary

    async def _get_table_sizes(self) -> Dict[str, int]:
        """Tablo boyutlarını al."""
        try:
            async with self.pool.acquire() as conn:
                rows = await conn.fetch("""
                    SELECT 
                        relname as table_name,
                        pg_total_relation_size(relid) as size
                    FROM pg_catalog.pg_statio_user_tables
                    ORDER BY pg_total_relation_size(relid) DESC
                """)
                return {row['table_name']: row['size'] for row in rows}
        except Exception as e:
            logger.error(f"Table sizes query error: {e}")
            return {}

    async def _get_distinct_symbols(
        self, 
        table_name: str, 
        before: datetime
    ) -> List[Tuple[str, str]]:
        """Tablodaki benzersiz symbol/market_type çiftlerini al."""
        try:
            timestamp_col = self._get_timestamp_column(table_name)
            if not timestamp_col:
                return []
            
            async with self.pool.acquire() as conn:
                rows = await conn.fetch(f"""
                    SELECT DISTINCT symbol, COALESCE(market_type, 'spot') as market_type
                    FROM {table_name}
                    WHERE {timestamp_col} < $1
                """, before)
                return [(row['symbol'], row['market_type']) for row in rows]
        except Exception as e:
            logger.error(f"Distinct symbols query error for {table_name}: {e}")
            return []

    def _get_timestamp_column(self, table_name: str) -> Optional[str]:
        """Tablo için timestamp sütununu belirle."""
        timestamp_columns = {
            "trades": "timestamp",
            "price_movements": "start_time",
            "signals": "timestamp",
            "simulations": "entry_time",
            "pattern_records": "created_at",
            "historical_signatures": "created_at",
            "pattern_match_results": "created_at",
            "signature_matches": "created_at",
            "chat_messages": "created_at",
            "brain_learning_log": "created_at",
            "audit_records": "created_at",
            "rca_results": "created_at",
            "state_history": "timestamp",
            "agent_heartbeat": "last_heartbeat",
        }
        return timestamp_columns.get(table_name)

    async def _vacuum_tables(self):
        """VACUUM ANALYZE çalıştır."""
        try:
            async with self.pool.acquire() as conn:
                for table_name in self.archiver.TABLE_RETENTION.keys():
                    try:
                        await conn.execute(f"VACUUM ANALYZE {table_name}")
                    except Exception:
                        pass  # Bazı tablolar olmayabilir
        except Exception as e:
            logger.warning(f"Vacuum error: {e}")

    def get_status(self) -> Dict[str, Any]:
        """Mevcut durum bilgisini döndür."""
        return {
            "running": self._running,
            "threshold_gb": self.THRESHOLD_BYTES / (1024**3),
            "scan_interval_seconds": self.SCAN_INTERVAL_SECONDS,
            "last_scan": self._last_scan.isoformat() if self._last_scan else None,
            "metrics": self._metrics.to_dict() if self._metrics else None,
            "last_prune": self._last_prune.to_dict() if self._last_prune else None,
        }

    async def force_prune(self) -> PruneSummary:
        """Manuel pruning tetikle."""
        logger.info("🔧 Manual pruning triggered")
        return await self.run_pruning()

    async def get_history_summaries(
        self,
        table_name: Optional[str] = None,
        symbol: Optional[str] = None,
        limit: int = 100,
    ) -> List[Dict[str, Any]]:
        """history_summary kayıtlarını döndür."""
        try:
            async with self.pool.acquire() as conn:
                query = "SELECT * FROM history_summary WHERE 1=1"
                params = []
                
                if table_name:
                    params.append(table_name)
                    query += f" AND table_name = ${len(params)}"
                if symbol:
                    params.append(symbol.upper())
                    query += f" AND symbol = ${len(params)}"
                
                query += f" ORDER BY created_at DESC LIMIT {limit}"
                
                rows = await conn.fetch(query, *params)
                return [dict(row) for row in rows]
        except Exception as e:
            logger.error(f"Get history summaries error: {e}")
            return []


# Singleton instance
_storage_manager: Optional[StorageManager] = None


def get_storage_manager(pool: asyncpg.Pool = None) -> StorageManager:
    """StorageManager singleton'ı al veya oluştur."""
    global _storage_manager
    if _storage_manager is None and pool:
        _storage_manager = StorageManager(pool)
    return _storage_manager


async def init_storage_manager(pool: asyncpg.Pool) -> StorageManager:
    """StorageManager'ı başlat ve döndür."""
    global _storage_manager
    _storage_manager = StorageManager(pool)
    await _storage_manager.initialize()
    return _storage_manager
