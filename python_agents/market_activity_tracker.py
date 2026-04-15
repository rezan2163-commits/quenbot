"""
MarketActivityTracker — Low-Power Watch Mode & Threshold-Gated Wake-Up
=======================================================================
Piyasa aktivitesini izler, düşük aktivitede ajanları uyku moduna alır,
anlamlı hareket tespit edildiğinde event-driven olarak uyandırır.

AMAÇ:
- Piyasa durgunken CPU tüketimini %80+ azaltmak
- Ajanlar sadece anlamlı hareket olduğunda çalışsın
- Threshold-gated tetikleme ile gereksiz polling'i ortadan kaldırmak

MİMARİ:
  Scout PRICE_UPDATE → MarketActivityTracker
    → Fiyat değişimi < threshold?  → Low-Power mode (ajanlar uyur)
    → Fiyat değişimi >= threshold? → MARKET_ACTIVE event → ajanlar uyanır
    → Volume spike?               → MARKET_ACTIVE event → ajanlar uyanır
"""

import asyncio
import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, Optional, Set, Callable, Coroutine

from event_bus import Event, EventType, get_event_bus

logger = logging.getLogger(__name__)


class MarketMode(str, Enum):
    ACTIVE = "active"
    LOW_POWER = "low_power"
    HIBERNATING = "hibernating"


@dataclass
class SymbolActivity:
    """Tek bir sembolün aktivite durumu"""
    symbol: str
    last_price: float = 0.0
    anchor_price: float = 0.0  # Son anlamlı hareket fiyatı
    last_update_at: float = 0.0
    volume_1m: float = 0.0
    volume_anchor: float = 0.0  # Son 1dk ortalaması
    trade_count_1m: int = 0
    consecutive_quiet_periods: int = 0
    is_active: bool = False


class MarketActivityTracker:
    """
    Merkezi piyasa aktivite izleyici.
    
    Scout'tan gelen her PRICE_UPDATE'i analiz eder:
    - Fiyat değişimi threshold'u aşarsa → MARKET_ACTIVE yayınlar
    - Volume spike tespit ederse → MARKET_ACTIVE yayınlar
    - Belirli süre sessizlik → LOW_POWER moduna geçer
    
    Ajanlar bu tracker üzerinden asyncio.Event ile uyandırılır.
    """

    # Configurable thresholds
    PRICE_CHANGE_THRESHOLD = 0.003      # %0.3 fiyat değişimi = aktif
    VOLUME_SPIKE_MULTIPLIER = 2.0       # Normal hacmin 2x = spike
    QUIET_PERIOD_SECONDS = 60           # 60s hareketsizlik = 1 sessiz periyot
    LOW_POWER_AFTER_QUIET_PERIODS = 3   # 3 sessiz periyot = low-power
    HIBERNATE_AFTER_QUIET_PERIODS = 10  # 10 sessiz periyot = hibernation
    ACTIVITY_DECAY_SECONDS = 120        # 2dk sonra anchor güncelle

    def __init__(self):
        self.event_bus = get_event_bus()
        self._symbols: Dict[str, SymbolActivity] = {}
        self._mode = MarketMode.ACTIVE
        self._mode_changed_at = time.monotonic()

        # Asyncio Events for agent wake-up
        self._activity_event = asyncio.Event()
        self._activity_event.set()  # Start active

        # Stats
        self._total_updates = 0
        self._total_activations = 0
        self._total_quiet_periods = 0

        # Quiet period checker
        self._last_quiet_check = time.monotonic()

    async def initialize(self):
        """EventBus'a abone ol"""
        self.event_bus.subscribe(EventType.SCOUT_PRICE_UPDATE, self._on_price_update)
        logger.info("📊 MarketActivityTracker initialized — threshold=%.1f%%, quiet=%ds",
                     self.PRICE_CHANGE_THRESHOLD * 100, self.QUIET_PERIOD_SECONDS)

    async def _on_price_update(self, event: Event):
        """Her Scout fiyat güncellemesinde çağrılır"""
        data = event.data
        symbol = str(data.get("symbol", "")).upper()
        price = float(data.get("price", 0) or 0)
        quantity = float(data.get("quantity", 0) or 0)

        if not symbol or price <= 0:
            return

        self._total_updates += 1
        now = time.monotonic()

        # Sembol activity kaydını al/oluştur
        activity = self._symbols.get(symbol)
        if activity is None:
            activity = SymbolActivity(
                symbol=symbol,
                last_price=price,
                anchor_price=price,
                last_update_at=now,
            )
            self._symbols[symbol] = activity

        # Volume tracking (1dk pencere)
        if now - activity.last_update_at > 60:
            activity.volume_anchor = activity.volume_1m / max(1, (now - activity.last_update_at) / 60)
            activity.volume_1m = 0
            activity.trade_count_1m = 0

        activity.volume_1m += quantity
        activity.trade_count_1m += 1
        activity.last_update_at = now

        # Fiyat değişimi kontrolü
        old_price = activity.anchor_price
        if old_price > 0:
            change_pct = abs(price - old_price) / old_price
        else:
            change_pct = 0

        price_triggered = change_pct >= self.PRICE_CHANGE_THRESHOLD

        # Volume spike kontrolü
        volume_triggered = False
        if activity.volume_anchor > 0 and activity.volume_1m > 0:
            volume_ratio = activity.volume_1m / activity.volume_anchor
            volume_triggered = volume_ratio >= self.VOLUME_SPIKE_MULTIPLIER

        # Aktivite tetikleme
        if price_triggered or volume_triggered:
            activity.is_active = True
            activity.consecutive_quiet_periods = 0
            activity.anchor_price = price  # Anchor'u güncelle

            if self._mode != MarketMode.ACTIVE:
                old_mode = self._mode
                self._mode = MarketMode.ACTIVE
                self._mode_changed_at = now
                self._activity_event.set()
                self._total_activations += 1
                trigger = "price" if price_triggered else "volume"
                logger.info("⚡ Market ACTIVE — %s triggered by %s (%.2f%% change, mode was %s)",
                           symbol, trigger, change_pct * 100, old_mode.value)

                await self.event_bus.publish(Event(
                    type=EventType.SCOUT_ANOMALY,
                    source="market_activity_tracker",
                    data={
                        "event": "market_activated",
                        "symbol": symbol,
                        "trigger": trigger,
                        "change_pct": change_pct,
                        "previous_mode": old_mode.value,
                    },
                    priority=2,
                ))
        else:
            # Anchor decay — uzun süre küçük hareketlerde anchor'u yavaşça güncelle
            if now - activity.last_update_at > self.ACTIVITY_DECAY_SECONDS:
                activity.anchor_price = price

        activity.last_price = price

        # Periyodik sessizlik kontrolü (her 60s)
        if now - self._last_quiet_check >= self.QUIET_PERIOD_SECONDS:
            self._last_quiet_check = now
            await self._check_quiet_period()

    async def _check_quiet_period(self):
        """Tüm semboller sessiz mi kontrol et"""
        now = time.monotonic()
        any_active = False

        for activity in self._symbols.values():
            elapsed = now - activity.last_update_at
            if elapsed < self.QUIET_PERIOD_SECONDS and activity.is_active:
                any_active = True
            else:
                activity.is_active = False
                activity.consecutive_quiet_periods += 1

        if any_active:
            return

        # Tüm semboller sessiz
        self._total_quiet_periods += 1
        max_quiet = max((a.consecutive_quiet_periods for a in self._symbols.values()), default=0)

        if max_quiet >= self.HIBERNATE_AFTER_QUIET_PERIODS and self._mode != MarketMode.HIBERNATING:
            self._mode = MarketMode.HIBERNATING
            self._mode_changed_at = now
            self._activity_event.clear()
            logger.info("💤 Market HIBERNATING — all symbols quiet for %d periods", max_quiet)
        elif max_quiet >= self.LOW_POWER_AFTER_QUIET_PERIODS and self._mode == MarketMode.ACTIVE:
            self._mode = MarketMode.LOW_POWER
            self._mode_changed_at = now
            self._activity_event.clear()
            logger.info("🔋 Market LOW_POWER — all symbols quiet for %d periods", max_quiet)

    # ─── Public API for Agents ───

    @property
    def mode(self) -> MarketMode:
        return self._mode

    @property
    def is_active(self) -> bool:
        return self._mode == MarketMode.ACTIVE

    async def wait_for_activity(self, timeout: Optional[float] = None) -> bool:
        """
        Piyasa aktif olana kadar bekle.
        
        Ajanlar bu metodu polling döngüsü yerine kullanır:
          Eski: await asyncio.sleep(30)
          Yeni: await tracker.wait_for_activity(timeout=30)
        
        Returns:
            True = piyasa aktif, False = timeout (düşük güçte düzenli kontrol yap)
        """
        if self._activity_event.is_set():
            return True

        try:
            await asyncio.wait_for(self._activity_event.wait(), timeout=timeout)
            return True
        except asyncio.TimeoutError:
            return False

    def get_active_symbols(self) -> Set[str]:
        """Son 60 saniyede aktif olan sembolleri döndür"""
        now = time.monotonic()
        return {
            s.symbol for s in self._symbols.values()
            if s.is_active and (now - s.last_update_at) < self.QUIET_PERIOD_SECONDS
        }

    def get_symbol_activity(self, symbol: str) -> Optional[SymbolActivity]:
        return self._symbols.get(symbol.upper())

    def get_price(self, symbol: str) -> float:
        """Scout cache'inden en güncel fiyatı döndür — DB sorgusu gerektirmez"""
        activity = self._symbols.get(symbol.upper())
        return activity.last_price if activity else 0.0

    def get_all_prices(self) -> Dict[str, float]:
        """Tüm sembollerin güncel fiyatlarını döndür"""
        return {s.symbol: s.last_price for s in self._symbols.values() if s.last_price > 0}

    def get_stats(self) -> Dict:
        now = time.monotonic()
        active_count = sum(1 for s in self._symbols.values() if s.is_active)
        return {
            "mode": self._mode.value,
            "mode_duration_seconds": int(now - self._mode_changed_at),
            "total_symbols": len(self._symbols),
            "active_symbols": active_count,
            "total_updates": self._total_updates,
            "total_activations": self._total_activations,
            "total_quiet_periods": self._total_quiet_periods,
        }


# Singleton
_tracker: Optional[MarketActivityTracker] = None


def get_market_tracker() -> MarketActivityTracker:
    global _tracker
    if _tracker is None:
        _tracker = MarketActivityTracker()
    return _tracker
