"""
feature_store.py — Point-in-time güvenli özellik deposu
========================================================
Intel Upgrade Phase 1: Parquet + DuckDB tabanlı append-only özellik deposu.
`symbol/date=YYYY-MM-DD/hour=HH.parquet` şemasıyla diske yazar, DuckDB ile
hızlı PIT sorgu yapar. Hot-path'i ASLA bloklamaz (bounded queue +
`asyncio.create_task` ile fire-and-forget flush).

Matematiksel garanti:
    read_pit(as_of=t) asla ts > t olan bir satır döndürmez.
Bu garanti, walk-forward eğitim ve counterfactual öğrenmede leakage'ı
engeller (Lopez de Prado, AFML Chapter 4).

Graceful degradation: pyarrow/duckdb yoksa modül "disabled" raporlar,
import hataları ana ajan sürecini düşürmez.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, AsyncIterator, Deque, Dict, Iterable, Iterator, List, Optional

from collections import deque

logger = logging.getLogger(__name__)

# Optional deps — hata verirse store disabled olur
try:
    import pyarrow as pa  # type: ignore
    import pyarrow.parquet as pq  # type: ignore
    _PA_OK = True
except Exception as _e:
    pa = None  # type: ignore
    pq = None  # type: ignore
    _PA_OK = False
    logger.warning("pyarrow not available — FeatureStore disabled (%s)", _e)

try:
    import duckdb  # type: ignore
    _DUCK_OK = True
except Exception as _e:
    duckdb = None  # type: ignore
    _DUCK_OK = False
    logger.warning("duckdb not available — FeatureStore PIT queries slower (%s)", _e)

try:
    import pandas as pd  # type: ignore
    _PD_OK = True
except Exception:
    pd = None  # type: ignore
    _PD_OK = False


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class FeatureRecord:
    """Tek bir özellik satırı (sembol × timestamp × özellik dict)."""
    symbol: str
    ts: datetime
    features: Dict[str, Any] = field(default_factory=dict)

    def flatten(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {
            "symbol": self.symbol,
            "ts": self.ts if self.ts.tzinfo else self.ts.replace(tzinfo=timezone.utc),
        }
        for k, v in self.features.items():
            # Parquet dostu skalar/float'a çevir; None kabul
            if isinstance(v, (int, float, bool)) or v is None:
                out[k] = v
            elif isinstance(v, str):
                out[k] = v
            else:
                # karmaşık yapıyı JSON string olarak yaz
                try:
                    out[k] = json.dumps(v, default=str, ensure_ascii=False)
                except Exception:
                    out[k] = str(v)
        return out


class FeatureStore:
    """Parquet tabanlı append-only feature store.

    Dosya yerleşimi:
        {root}/symbol={SYMBOL}/date={YYYY-MM-DD}/hour={HH}.parquet

    Sözleşme:
        - write(): satırı bounded queue'ya koyar, ASLA bloklamaz
        - flush(): queue'yu N satır veya T saniyede diske yazar
        - read_pit(): as_of'u aşan satırları görmez (FILTER WHERE ts <= as_of)
        - replay(): kronolojik stream (walk-forward eğitim / geri doldurma)
    """

    def __init__(
        self,
        root: str,
        flush_seconds: float = 15.0,
        flush_rows: int = 2000,
        queue_max: int = 20000,
        enable_write: bool = True,
    ) -> None:
        self.root = Path(root)
        self.flush_seconds = max(1.0, float(flush_seconds))
        self.flush_rows = max(1, int(flush_rows))
        self.queue_max = max(1, int(queue_max))
        self.enable_write = bool(enable_write) and _PA_OK
        self._queue: Deque[FeatureRecord] = deque()
        self._queue_dropped = 0
        self._write_errors = 0
        self._total_written = 0
        self._last_flush_ts = time.time()
        self._flush_lock = asyncio.Lock()
        self._flusher_task: Optional[asyncio.Task] = None
        self._running = False
        # Per (symbol, date, hour) dosya bazlı pending buffer
        self._pending: Dict[str, List[Dict[str, Any]]] = {}

    # ──────────── lifecycle ────────────
    async def start(self) -> None:
        if not _PA_OK:
            logger.warning("FeatureStore.start(): pyarrow yok → pasif mod")
            return
        if self._running:
            return
        self.root.mkdir(parents=True, exist_ok=True)
        self._running = True
        self._flusher_task = asyncio.create_task(self._flusher_loop())
        logger.info("📦 FeatureStore başlatıldı: root=%s flush_rows=%d flush_s=%.1f",
                    self.root, self.flush_rows, self.flush_seconds)

    async def stop(self) -> None:
        self._running = False
        if self._flusher_task:
            try:
                self._flusher_task.cancel()
                await asyncio.wait_for(asyncio.shield(self._flusher_task), timeout=0.5)
            except Exception:
                pass
        await self.flush(force=True)

    # ──────────── public API ────────────
    async def write(self, symbol: str, ts: datetime, features: Dict[str, Any]) -> None:
        """Fire-and-forget append. Hot path'i asla bloklamaz."""
        if not self.enable_write:
            return
        if not symbol or not isinstance(ts, datetime):
            return
        if len(self._queue) >= self.queue_max:
            self._queue_dropped += 1
            if self._queue_dropped % 100 == 1:
                logger.warning("FeatureStore queue full → %d satır düştü", self._queue_dropped)
            return
        self._queue.append(FeatureRecord(symbol=symbol, ts=ts, features=features))
        # Eşik aşıldıysa async flush tetikle, ama bekleme
        if len(self._queue) >= self.flush_rows:
            asyncio.create_task(self._safe_flush())

    def read_pit(
        self,
        symbol: str,
        as_of: datetime,
        lookback: timedelta,
    ) -> "pd.DataFrame":
        """Point-in-time güvenli okuma. Ts > as_of olan hiçbir satır döndürülmez.

        Returns:
            DataFrame with columns [symbol, ts, <features...>].
            Boş DataFrame dönebilir; asla None.
        """
        if not _PA_OK or not _PD_OK:
            logger.debug("read_pit skipped — pyarrow/pandas yok")
            return pd.DataFrame() if _PD_OK else _EmptyDF()  # type: ignore
        as_of_utc = as_of if as_of.tzinfo else as_of.replace(tzinfo=timezone.utc)
        start_utc = as_of_utc - lookback
        files = self._collect_files(symbol, start_utc, as_of_utc)
        if not files:
            return pd.DataFrame()
        if _DUCK_OK:
            try:
                return self._read_via_duckdb(files, as_of_utc, start_utc)
            except Exception as e:
                logger.debug("duckdb PIT okuma başarısız, pandas'a düşüyorum: %s", e)
        return self._read_via_pandas(files, as_of_utc, start_utc)

    def replay(
        self,
        symbol: str,
        start: datetime,
        end: datetime,
    ) -> Iterator[Dict[str, Any]]:
        """Kronolojik stream — walk-forward replay için."""
        if not _PA_OK or not _PD_OK:
            return iter([])
        start_utc = start if start.tzinfo else start.replace(tzinfo=timezone.utc)
        end_utc = end if end.tzinfo else end.replace(tzinfo=timezone.utc)
        files = self._collect_files(symbol, start_utc, end_utc)
        if not files:
            return iter([])
        df = self._read_via_pandas(files, end_utc, start_utc)
        if df.empty:
            return iter([])
        return (row.to_dict() for _, row in df.iterrows())

    async def flush(self, force: bool = False) -> int:
        """Queue'yu diske yaz. Zorunlu değilse throttle'lı.

        Returns:
            Disk'e yazılan satır sayısı.
        """
        if not self.enable_write:
            return 0
        async with self._flush_lock:
            # Snapshot queue atomically
            snapshot: List[FeatureRecord] = []
            while self._queue:
                snapshot.append(self._queue.popleft())
            if not snapshot:
                return 0
            # Group by (symbol, date, hour)
            groups: Dict[str, List[Dict[str, Any]]] = {}
            for rec in snapshot:
                ts_utc = rec.ts if rec.ts.tzinfo else rec.ts.replace(tzinfo=timezone.utc)
                ts_utc = ts_utc.astimezone(timezone.utc)
                key = f"{rec.symbol}|{ts_utc.strftime('%Y-%m-%d')}|{ts_utc.strftime('%H')}"
                row = rec.flatten()
                # Normalize ts to ms precision UTC datetime (pyarrow-friendly)
                row["ts"] = ts_utc
                groups.setdefault(key, []).append(row)
            # Run blocking parquet write in thread pool
            total = 0
            try:
                total = await asyncio.to_thread(self._write_groups, groups)
                self._total_written += total
                self._last_flush_ts = time.time()
            except Exception as e:
                self._write_errors += 1
                logger.warning("FeatureStore flush error: %s", e)
            return total

    async def health_check(self) -> Dict[str, Any]:
        return {
            "healthy": self.enable_write,
            "pyarrow": _PA_OK,
            "duckdb": _DUCK_OK,
            "queue_size": len(self._queue),
            "queue_max": self.queue_max,
            "queue_dropped": self._queue_dropped,
            "total_written": self._total_written,
            "write_errors": self._write_errors,
            "last_flush_age_s": round(time.time() - self._last_flush_ts, 2),
            "root": str(self.root),
        }

    def metrics(self) -> Dict[str, Any]:
        return {
            "feature_store_queue": len(self._queue),
            "feature_store_dropped_total": self._queue_dropped,
            "feature_store_written_total": self._total_written,
            "feature_store_errors_total": self._write_errors,
        }

    # ──────────── internals ────────────
    async def _flusher_loop(self) -> None:
        while self._running:
            try:
                await asyncio.sleep(self.flush_seconds)
                await self._safe_flush()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.debug("feature_store flusher hiccup: %s", e)

    async def _safe_flush(self) -> None:
        try:
            await self.flush(force=False)
        except Exception as e:
            logger.warning("FeatureStore safe_flush error: %s", e)

    def _write_groups(self, groups: Dict[str, List[Dict[str, Any]]]) -> int:
        """Senkron parquet yazımı (thread pool'da çalışır)."""
        if not _PA_OK:
            return 0
        total = 0
        for key, rows in groups.items():
            if not rows:
                continue
            symbol, date_str, hour_str = key.split("|")
            path = self.root / f"symbol={symbol}" / f"date={date_str}" / f"hour={hour_str}.parquet"
            path.parent.mkdir(parents=True, exist_ok=True)
            table = pa.Table.from_pylist(rows)
            # Append semantics: eğer dosya varsa oku + concat + yeniden yaz
            # (Parquet native append olmadığı için; hourly partisyon ≤ birkaç bin satır kalır)
            if path.exists():
                try:
                    existing = pq.read_table(str(path))
                    table = pa.concat_tables([existing, table], promote_options="default")
                except Exception as e:
                    logger.debug("Mevcut parquet okunamadı, üzerine yazılıyor: %s", e)
            pq.write_table(table, str(path), compression="zstd")
            total += len(rows)
        return total

    def _collect_files(self, symbol: str, start_utc: datetime, end_utc: datetime) -> List[str]:
        base = self.root / f"symbol={symbol}"
        if not base.exists():
            return []
        out: List[str] = []
        # Iterate date=YYYY-MM-DD directories in range
        cur = start_utc.replace(minute=0, second=0, microsecond=0)
        end_floor = end_utc.replace(minute=0, second=0, microsecond=0)
        # Walk date+hour dirs; cheap because range is small (days × 24)
        while cur <= end_floor + timedelta(hours=1):
            date_dir = base / f"date={cur.strftime('%Y-%m-%d')}"
            if date_dir.exists():
                hour_file = date_dir / f"hour={cur.strftime('%H')}.parquet"
                if hour_file.exists():
                    out.append(str(hour_file))
            cur += timedelta(hours=1)
        return sorted(out)

    def _read_via_duckdb(self, files: List[str], as_of: datetime, start: datetime) -> "pd.DataFrame":
        assert _DUCK_OK and _PD_OK
        con = duckdb.connect(":memory:")
        try:
            # DuckDB parquet reader handles list of files
            files_list = "[" + ",".join(f"'{f}'" for f in files) + "]"
            query = (
                f"SELECT * FROM read_parquet({files_list}) "
                f"WHERE ts <= TIMESTAMPTZ '{as_of.isoformat()}' "
                f"AND ts >= TIMESTAMPTZ '{start.isoformat()}' "
                f"ORDER BY ts ASC"
            )
            return con.execute(query).fetch_df()
        finally:
            con.close()

    def _read_via_pandas(self, files: List[str], as_of: datetime, start: datetime) -> "pd.DataFrame":
        assert _PA_OK and _PD_OK
        frames: List["pd.DataFrame"] = []
        for f in files:
            try:
                df = pq.read_table(f).to_pandas()
                if df.empty:
                    continue
                # ts'yi UTC'ye zorla
                if "ts" in df.columns:
                    df["ts"] = pd.to_datetime(df["ts"], utc=True, errors="coerce")
                    df = df[(df["ts"] >= start) & (df["ts"] <= as_of)]
                    if not df.empty:
                        frames.append(df)
            except Exception as e:
                logger.debug("parquet okuma atlandı (%s): %s", f, e)
        if not frames:
            return pd.DataFrame()
        out = pd.concat(frames, ignore_index=True)
        out.sort_values("ts", inplace=True, kind="mergesort")  # stable
        return out.reset_index(drop=True)


class _EmptyDF:
    """pyarrow/pandas olmadığında read_pit için minimum placeholder."""
    empty = True
    columns: List[str] = []
    def __len__(self) -> int: return 0
    def __iter__(self): return iter([])
    def to_dict(self, *_a, **_kw): return {}


# ─────────── module-level singleton ───────────
_store: Optional[FeatureStore] = None


def get_feature_store(
    root: Optional[str] = None,
    flush_seconds: float = 15.0,
    flush_rows: int = 2000,
    queue_max: int = 20000,
    enable_write: bool = True,
) -> FeatureStore:
    """Singleton accessor. root ilk çağrıda bağlanır."""
    global _store
    if _store is None:
        _store = FeatureStore(
            root=root or "python_agents/.feature_store",
            flush_seconds=flush_seconds,
            flush_rows=flush_rows,
            queue_max=queue_max,
            enable_write=enable_write,
        )
    return _store
