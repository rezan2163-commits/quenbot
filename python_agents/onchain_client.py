"""
onchain_client.py — On-chain veri çekici (§8)
===============================================
ETHERSCAN / BSCSCAN / BTC mempool / funding / OI için ince bir aiohttp
polling istemcisi. API anahtarları yoksa disabled flag ile boş çıktı.
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

try:
    import aiohttp  # type: ignore
    _HAS_AIOHTTP = True
except Exception:
    aiohttp = None  # type: ignore
    _HAS_AIOHTTP = False


@dataclass
class OnChainSnapshot:
    ts: float = 0.0
    stablecoin_mint_1h: float = 0.0   # USD
    cex_inflow_1h: float = 0.0        # USD
    btc_mempool_pending: float = 0.0  # count
    funding_rate_8h: float = 0.0
    open_interest: float = 0.0
    disabled: bool = True
    sources: Dict[str, bool] = field(default_factory=dict)


class OnChainClient:
    def __init__(
        self,
        poll_interval_sec: float = 60.0,
        etherscan_api_key: Optional[str] = None,
        bscscan_api_key: Optional[str] = None,
    ) -> None:
        self.poll_interval = float(poll_interval_sec)
        self.etherscan_key = etherscan_api_key or os.getenv("ETHERSCAN_API_KEY")
        self.bscscan_key = bscscan_api_key or os.getenv("BSCSCAN_API_KEY")
        self._session: Any = None
        self._snapshot = OnChainSnapshot()
        self._stats = {"fetches": 0, "errors": 0, "backoff_sec": 0.0}
        self._running = False

    async def start(self) -> None:
        if not _HAS_AIOHTTP:
            logger.info("OnChainClient: aiohttp unavailable, disabled")
            return
        if not (self.etherscan_key or self.bscscan_key):
            logger.info("OnChainClient: no API keys set, disabled")
            return
        self._running = True
        self._session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10))
        asyncio.create_task(self._poll_loop())

    async def stop(self) -> None:
        self._running = False
        if self._session is not None:
            try:
                await self._session.close()
            except Exception:
                pass
            self._session = None

    async def _poll_loop(self) -> None:
        backoff = 1.0
        while self._running:
            try:
                await self._poll_once()
                self._stats["fetches"] += 1
                backoff = 1.0
                self._stats["backoff_sec"] = 0.0
                await asyncio.sleep(self.poll_interval)
            except Exception as e:
                self._stats["errors"] += 1
                logger.debug("OnChain poll error: %s (backoff=%0.1fs)", e, backoff)
                self._stats["backoff_sec"] = backoff
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 300.0)

    async def _poll_once(self) -> None:
        # Minimal placeholder — gerçek entegrasyon PR2'de genişletilecek.
        snap = OnChainSnapshot(ts=time.time(), disabled=False)
        snap.sources = {
            "etherscan": bool(self.etherscan_key),
            "bscscan": bool(self.bscscan_key),
        }
        self._snapshot = snap

    def snapshot(self) -> OnChainSnapshot:
        return self._snapshot

    def inject_snapshot(self, snap: OnChainSnapshot) -> None:
        """Test-only enjeksiyon."""
        self._snapshot = snap

    def metrics(self) -> Dict[str, Any]:
        return {
            "onchain_fetches_total": self._stats["fetches"],
            "onchain_errors_total": self._stats["errors"],
            "onchain_backoff_sec": self._stats["backoff_sec"],
            "onchain_enabled": not self._snapshot.disabled,
        }


_instance: Optional[OnChainClient] = None


def get_onchain_client(**kwargs: Any) -> OnChainClient:
    global _instance
    if _instance is None:
        _instance = OnChainClient(**kwargs)
    return _instance


def _reset_for_tests() -> None:
    global _instance
    _instance = None
