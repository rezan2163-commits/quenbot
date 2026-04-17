"""
Metrics Exporter — Phase 5
===========================
Prometheus text-format exporter. Tüm intel modüllerinden `metrics()` çıktısını
toplar ve `/metrics` endpoint'inde yayınlar. Ayrı port (varsayılan 9108) —
ana API trafiğini etkilemez.

Flag: `METRICS_EXPORTER_ENABLED` (default OFF).

Güvenlik: modül veya port yoksa bootstrap sessizce atlar. Hiçbir metrik
toplama hata durumunda bile istisna üretmez.
"""
from __future__ import annotations

import logging
import time
from typing import Any, Callable, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


def _sanitize_name(name: str) -> str:
    return "".join(c if c.isalnum() or c == "_" else "_" for c in name)


def _fmt_value(v: Any) -> Optional[float]:
    try:
        if isinstance(v, bool):
            return 1.0 if v else 0.0
        if v is None:
            return None
        return float(v)
    except Exception:
        return None


class MetricsExporter:
    def __init__(
        self,
        port: int = 9108,
        host: str = "0.0.0.0",
        sources: Optional[List[Tuple[str, Callable[[], Dict[str, Any]]]]] = None,
    ) -> None:
        self.port = int(port)
        self.host = host
        self._sources: List[Tuple[str, Callable[[], Dict[str, Any]]]] = list(sources or [])
        self._runner = None
        self._site = None
        self._started_ts: float = 0.0
        self._scrape_count = 0
        self._scrape_errors = 0

    def register(self, namespace: str, metrics_fn: Callable[[], Dict[str, Any]]) -> None:
        self._sources.append((namespace, metrics_fn))

    async def start(self) -> None:
        try:
            from aiohttp import web
        except Exception as e:
            logger.warning("metrics_exporter: aiohttp yok — başlatılmıyor (%s)", e)
            return

        async def handle(_req):
            self._scrape_count += 1
            try:
                body = self._render()
                return web.Response(text=body, content_type="text/plain; version=0.0.4")
            except Exception as e:
                self._scrape_errors += 1
                logger.debug("metrics render hata: %s", e)
                return web.Response(text=f"# error: {e}\n", status=500)

        app = web.Application()
        app.router.add_get("/metrics", handle)
        self._runner = web.AppRunner(app)
        await self._runner.setup()
        self._site = web.TCPSite(self._runner, self.host, self.port)
        try:
            await self._site.start()
            self._started_ts = time.time()
            logger.info("📊 Metrics exporter :%d/metrics (namespaces=%d)",
                        self.port, len(self._sources))
        except Exception as e:
            logger.warning("metrics_exporter port %d kullanılamıyor: %s", self.port, e)

    async def stop(self) -> None:
        try:
            if self._runner:
                await self._runner.cleanup()
        except Exception:
            pass

    def _render(self) -> str:
        lines: List[str] = []
        lines.append(f"# HELP quenbot_exporter_up Exporter running")
        lines.append(f"# TYPE quenbot_exporter_up gauge")
        lines.append(f"quenbot_exporter_up 1")
        lines.append(f"quenbot_exporter_started_ts {self._started_ts}")
        lines.append(f"quenbot_exporter_scrape_total {self._scrape_count}")
        lines.append(f"quenbot_exporter_scrape_errors {self._scrape_errors}")
        for namespace, fn in self._sources:
            try:
                data = fn() or {}
            except Exception as e:
                self._scrape_errors += 1
                lines.append(f"# error in {namespace}: {e}")
                continue
            for key, val in data.items():
                v = _fmt_value(val)
                if v is None:
                    continue
                metric = _sanitize_name(key)
                lines.append(f"quenbot_{metric} {v}")
        return "\n".join(lines) + "\n"

    def metrics(self) -> Dict[str, Any]:
        return {
            "metrics_exporter_scrape_total": self._scrape_count,
            "metrics_exporter_scrape_errors": self._scrape_errors,
            "metrics_exporter_sources": len(self._sources),
            "metrics_exporter_port": self.port,
        }

    async def health_check(self) -> Dict[str, Any]:
        return {
            "healthy": self._started_ts > 0,
            "port": self.port,
            "sources": [n for n, _ in self._sources],
            "scrapes": self._scrape_count,
            "errors": self._scrape_errors,
        }


_exporter: Optional[MetricsExporter] = None


def get_metrics_exporter(*args, **kwargs) -> MetricsExporter:
    global _exporter
    if _exporter is None:
        _exporter = MetricsExporter(*args, **kwargs)
    return _exporter


def _reset_metrics_exporter_for_tests() -> None:
    global _exporter
    _exporter = None
