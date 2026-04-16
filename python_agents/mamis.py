from __future__ import annotations

import abc
import asyncio
import logging
import math
import os
from collections import deque
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Deque, Dict, List, Optional

import numpy as np

from agent_base import AgentBase
from event_bus import Event, EventType, get_event_bus
from systematic_trade_detector import get_systematic_detector

logger = logging.getLogger("quenbot.mamis")


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _serialize_value(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, dict):
        return {key: _serialize_value(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_serialize_value(item) for item in value]
    if isinstance(value, tuple):
        return [_serialize_value(item) for item in value]
    return value


@dataclass
class MicrostructureTick:
    symbol: str
    exchange: str
    market_type: str
    price: float
    quantity: float
    side: str
    timestamp: datetime
    trade_id: Optional[str] = None


@dataclass
class OrderBookUpdate:
    symbol: str
    bid_price: float
    ask_price: float
    bid_size: float
    ask_size: float
    event_type: str = "quote"
    order_size: float = 0.0
    order_side: str = "unknown"
    timestamp: datetime = field(default_factory=utc_now)


@dataclass
class EventBar:
    symbol: str
    exchange: str
    market_type: str
    bar_index: int
    started_at: datetime
    ended_at: datetime
    tick_count: int
    total_volume: float
    buy_volume: float
    sell_volume: float
    open_price: float
    high_price: float
    low_price: float
    close_price: float
    vwap: float
    delta_volume: float
    cumulative_volume_delta: float
    ofi: float
    ofi_normalized: float
    vpin: float
    volatility: float
    price_range_bps: float
    spread_bps: float
    cancel_to_trade_ratio: float
    book_pressure: float
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return _serialize_value(asdict(self))


@dataclass
class MAMISClassification:
    symbol: str
    pattern_type: str
    confidence: float
    direction_hint: str
    estimated_volatility: float
    reason: str
    event_bar: EventBar
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        payload = asdict(self)
        payload["event_bar"] = self.event_bar.to_dict()
        return _serialize_value(payload)


@dataclass
class MAMISSignal:
    timestamp: str
    symbol: str
    signal_direction: str
    confidence_score: float
    detected_pattern_type: str
    estimated_volatility: float
    position_size: float
    source: str = "mamis"
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return _serialize_value(asdict(self))


class BaseMicrostructureStrategy(abc.ABC):
    """Adapter for plugging microstructure intelligence into strategy pipelines."""

    @abc.abstractmethod
    def build_signal(self, classification: MAMISClassification) -> MAMISSignal:
        raise NotImplementedError


class VPINTracker:
    def __init__(self, bucket_volume: float, window_buckets: int):
        self.bucket_volume = max(bucket_volume, 1.0)
        self.window_buckets = max(window_buckets, 4)
        self._bucket_buy = 0.0
        self._bucket_sell = 0.0
        self._history: Deque[float] = deque(maxlen=self.window_buckets)

    def update(self, tick: MicrostructureTick) -> float:
        remaining = float(max(tick.quantity, 0.0))
        while remaining > 0:
            used = self._bucket_buy + self._bucket_sell
            capacity = self.bucket_volume - used
            take = min(capacity, remaining)
            if tick.side == "buy":
                self._bucket_buy += take
            else:
                self._bucket_sell += take
            remaining -= take

            if self._bucket_buy + self._bucket_sell >= self.bucket_volume - 1e-9:
                toxicity = abs(self._bucket_buy - self._bucket_sell) / max(self.bucket_volume, 1e-9)
                self._history.append(float(toxicity))
                self._bucket_buy = 0.0
                self._bucket_sell = 0.0

        return self.current_value()

    def current_value(self) -> float:
        if not self._history:
            return 0.0
        return float(np.mean(list(self._history)))


class EventBarBuilder:
    def __init__(self, tick_threshold: int, volume_threshold: float):
        self.tick_threshold = max(int(tick_threshold), 50)
        self.volume_threshold = max(float(volume_threshold), 1.0)
        self._ticks: List[MicrostructureTick] = []
        self._book_updates: List[OrderBookUpdate] = []
        self._bar_index = 0
        self._current_volume = 0.0

    def add_trade_tick(self, tick: MicrostructureTick, cumulative_cvd: float, current_vpin: float) -> Optional[EventBar]:
        self._ticks.append(tick)
        self._current_volume += tick.quantity
        if len(self._ticks) >= self.tick_threshold or self._current_volume >= self.volume_threshold:
            return self._flush(cumulative_cvd=cumulative_cvd, current_vpin=current_vpin)
        return None

    def add_book_update(self, update: OrderBookUpdate):
        self._book_updates.append(update)

    def _flush(self, cumulative_cvd: float, current_vpin: float) -> Optional[EventBar]:
        if not self._ticks:
            return None

        ticks = self._ticks
        books = self._book_updates
        self._ticks = []
        self._book_updates = []
        self._current_volume = 0.0
        self._bar_index += 1

        prices = np.asarray([float(t.price) for t in ticks], dtype=np.float64)
        quantities = np.asarray([float(t.quantity) for t in ticks], dtype=np.float64)
        signs = np.asarray([1.0 if t.side == "buy" else -1.0 for t in ticks], dtype=np.float64)

        buy_volume = float(np.sum(quantities[signs > 0]))
        sell_volume = float(np.sum(quantities[signs < 0]))
        total_volume = float(np.sum(quantities))
        delta_volume = float(buy_volume - sell_volume)
        vwap = float(np.sum(prices * quantities) / max(total_volume, 1e-9))
        returns = np.diff(np.log(np.clip(prices, 1e-9, None))) if len(prices) > 1 else np.asarray([], dtype=np.float64)
        volatility = float(np.std(returns) * math.sqrt(max(len(returns), 1))) if returns.size else 0.0
        price_range_bps = float((np.max(prices) - np.min(prices)) / max(vwap, 1e-9) * 10000.0)

        ofi, spread_bps, cancel_to_trade_ratio, book_pressure = self._compute_book_metrics(books, delta_volume, total_volume, vwap)

        return EventBar(
            symbol=ticks[-1].symbol,
            exchange=ticks[-1].exchange,
            market_type=ticks[-1].market_type,
            bar_index=self._bar_index,
            started_at=ticks[0].timestamp,
            ended_at=ticks[-1].timestamp,
            tick_count=len(ticks),
            total_volume=total_volume,
            buy_volume=buy_volume,
            sell_volume=sell_volume,
            open_price=float(prices[0]),
            high_price=float(np.max(prices)),
            low_price=float(np.min(prices)),
            close_price=float(prices[-1]),
            vwap=vwap,
            delta_volume=delta_volume,
            cumulative_volume_delta=float(cumulative_cvd),
            ofi=float(ofi),
            ofi_normalized=float(ofi / max(total_volume, 1e-9)),
            vpin=float(current_vpin),
            volatility=volatility,
            price_range_bps=price_range_bps,
            spread_bps=spread_bps,
            cancel_to_trade_ratio=cancel_to_trade_ratio,
            book_pressure=book_pressure,
            metadata={
                "tick_threshold": self.tick_threshold,
                "volume_threshold": self.volume_threshold,
                "book_updates": len(books),
            },
        )

    def _compute_book_metrics(
        self,
        book_updates: List[OrderBookUpdate],
        delta_volume: float,
        total_volume: float,
        vwap: float,
    ) -> tuple[float, float, float, float]:
        if len(book_updates) < 2:
            return float(delta_volume), 0.0, 0.0, 0.0

        ofi = 0.0
        spreads: List[float] = []
        cancel_count = 0
        trade_count = 0
        near_mid_cancel_size = 0.0
        near_mid_add_size = 0.0

        prev = book_updates[0]
        for current in book_updates[1:]:
            ofi += (
                (1.0 if current.bid_price >= prev.bid_price else 0.0) * current.bid_size
                - (1.0 if current.bid_price <= prev.bid_price else 0.0) * prev.bid_size
                - (1.0 if current.ask_price <= prev.ask_price else 0.0) * current.ask_size
                + (1.0 if current.ask_price >= prev.ask_price else 0.0) * prev.ask_size
            )
            mid = max((current.bid_price + current.ask_price) / 2.0, 1e-9)
            spreads.append((current.ask_price - current.bid_price) / mid * 10000.0)

            if current.event_type == "cancel":
                cancel_count += 1
                if abs(current.bid_price - mid) / mid < 0.0005 or abs(current.ask_price - mid) / mid < 0.0005:
                    near_mid_cancel_size += current.order_size or max(current.bid_size, current.ask_size)
            elif current.event_type == "add":
                if abs(current.bid_price - mid) / mid < 0.0005 or abs(current.ask_price - mid) / mid < 0.0005:
                    near_mid_add_size += current.order_size or max(current.bid_size, current.ask_size)
            elif current.event_type == "trade":
                trade_count += 1

            prev = current

        cancel_to_trade_ratio = float(cancel_count / max(trade_count, 1))
        book_pressure = float((near_mid_add_size - near_mid_cancel_size) / max(total_volume, 1e-9))
        return float(ofi), float(np.mean(spreads) if spreads else 0.0), cancel_to_trade_ratio, book_pressure


class MarketMakerDetector:
    def detect(self, bar: EventBar) -> Optional[Dict[str, Any]]:
        balance = 1.0 - abs(bar.delta_volume) / max(bar.total_volume, 1e-9)
        low_spread = bar.spread_bps > 0 and bar.spread_bps <= 2.5
        high_turnover = bar.tick_count >= 80
        low_vol = bar.volatility <= 0.0025
        if balance >= 0.82 and high_turnover and (low_spread or low_vol):
            confidence = min(0.95, 0.45 * balance + 0.25 * min(bar.tick_count / 200.0, 1.0) + 0.30 * (1.0 - min(bar.volatility / 0.004, 1.0)))
            return {
                "pattern_type": "market_maker_detected",
                "confidence": float(confidence),
                "direction_hint": "neutral",
                "reason": "balanced_two_sided_liquidity",
            }
        return None


class SpoofingLayeringDetector:
    def detect(self, bar: EventBar) -> Optional[Dict[str, Any]]:
        if bar.cancel_to_trade_ratio <= 3.0:
            return None
        confidence = min(0.97, 0.35 * min(bar.cancel_to_trade_ratio / 8.0, 1.0) + 0.35 * min(abs(bar.book_pressure), 1.0) + 0.30 * min(abs(bar.ofi_normalized) * 2.5, 1.0))
        direction_hint = "short" if bar.book_pressure < 0 else "long"
        return {
            "pattern_type": "spoofing_detected",
            "confidence": float(confidence),
            "direction_hint": direction_hint,
            "reason": "cancel_to_trade_ratio_spike",
        }


class IcebergDetector:
    def detect(self, bar: EventBar) -> Optional[Dict[str, Any]]:
        aggressive_delta = abs(bar.delta_volume) / max(bar.total_volume, 1e-9)
        stagnant_price = bar.price_range_bps <= 8.0
        if stagnant_price and aggressive_delta >= 0.60:
            confidence = min(0.96, 0.50 * aggressive_delta + 0.30 * (1.0 - min(bar.price_range_bps / 8.0, 1.0)) + 0.20 * min(bar.vpin / 0.8, 1.0))
            direction_hint = "long" if bar.delta_volume > 0 else "short"
            return {
                "pattern_type": "iceberg_detected",
                "confidence": float(confidence),
                "direction_hint": direction_hint,
                "reason": "price_stability_vs_cvd_aggression",
            }
        return None


class MAMISSentinelAgent(AgentBase):
    def __init__(self, event_bus, forensic_queue: asyncio.Queue):
        super().__init__("mamis_sentinel")
        self.event_bus = event_bus
        self.forensic_queue = forensic_queue
        self.tick_queue: asyncio.Queue = asyncio.Queue(maxsize=10000)
        self.book_queue: asyncio.Queue = asyncio.Queue(maxsize=4000)
        self.bar_builder = EventBarBuilder(
            tick_threshold=int(os.getenv("QUENBOT_MAMIS_TICK_BAR_SIZE", "180")),
            volume_threshold=float(os.getenv("QUENBOT_MAMIS_VOLUME_BAR_SIZE", "1500")),
        )
        self.vpin_tracker = VPINTracker(
            bucket_volume=float(os.getenv("QUENBOT_MAMIS_VPIN_BUCKET_VOLUME", "2200")),
            window_buckets=int(os.getenv("QUENBOT_MAMIS_VPIN_WINDOW", "24")),
        )
        self.ofi_threshold = float(os.getenv("QUENBOT_MAMIS_OFI_THRESHOLD", "0.18"))
        self.vpin_threshold = float(os.getenv("QUENBOT_MAMIS_VPIN_THRESHOLD", "0.62"))
        self.cvd_threshold = float(os.getenv("QUENBOT_MAMIS_CVD_THRESHOLD", "0.52"))
        self._cumulative_cvd: Dict[str, float] = {}
        self._bars_completed = 0
        self._anomalies = 0
        self._last_bar: Optional[EventBar] = None
        self._recent_bars: Deque[Dict[str, Any]] = deque(maxlen=32)

    async def initialize(self) -> None:
        self.running = False

    async def start(self) -> None:
        self.running = True
        while self.running:
            tick_task = asyncio.create_task(self.tick_queue.get())
            book_task = asyncio.create_task(self.book_queue.get())
            try:
                done, pending = await asyncio.wait({tick_task, book_task}, return_when=asyncio.FIRST_COMPLETED)
            except asyncio.CancelledError:
                tick_task.cancel()
                book_task.cancel()
                raise
            for task in pending:
                task.cancel()

            for task in done:
                payload = task.result()
                if payload is None:
                    return
                if isinstance(payload, MicrostructureTick):
                    await self._handle_tick(payload)
                elif isinstance(payload, OrderBookUpdate):
                    self.bar_builder.add_book_update(payload)

    async def stop(self) -> None:
        self.running = False
        try:
            self.tick_queue.put_nowait(None)
        except Exception:
            pass
        try:
            self.book_queue.put_nowait(None)
        except Exception:
            pass

    async def ingest_trade_tick(self, tick: MicrostructureTick):
        await self.tick_queue.put(tick)

    async def ingest_book_update(self, update: OrderBookUpdate):
        await self.book_queue.put(update)

    async def _handle_tick(self, tick: MicrostructureTick):
        self.last_activity = tick.timestamp
        sign = 1.0 if tick.side == "buy" else -1.0
        self._cumulative_cvd[tick.symbol] = self._cumulative_cvd.get(tick.symbol, 0.0) + sign * tick.quantity
        current_vpin = self.vpin_tracker.update(tick)
        bar = self.bar_builder.add_trade_tick(
            tick,
            cumulative_cvd=self._cumulative_cvd[tick.symbol],
            current_vpin=current_vpin,
        )
        if not bar:
            return

        self._bars_completed += 1
        self._last_bar = bar
        self._recent_bars.appendleft(bar.to_dict())
        await self.event_bus.publish(Event(
            type=EventType.MICROSTRUCTURE_BAR,
            source=self.name,
            data=bar.to_dict(),
        ))

        toxic_cvd = abs(bar.delta_volume) / max(bar.total_volume, 1e-9)
        is_anomaly = (
            abs(bar.ofi_normalized) >= self.ofi_threshold or
            bar.vpin >= self.vpin_threshold or
            toxic_cvd >= self.cvd_threshold
        )
        if not is_anomaly:
            return

        self._anomalies += 1
        payload = {
            "bar": bar,
            "trigger": {
                "ofi": round(bar.ofi_normalized, 4),
                "vpin": round(bar.vpin, 4),
                "cvd_toxicity": round(toxic_cvd, 4),
            },
        }
        await self.forensic_queue.put(payload)
        await self.event_bus.publish(Event(
            type=EventType.MICROSTRUCTURE_ALERT,
            source=self.name,
            data={
                "symbol": bar.symbol,
                "bar_index": bar.bar_index,
                "trigger": payload["trigger"],
            },
        ))

    async def health_check(self) -> Dict[str, Any]:
        return {
            "healthy": self.running,
            "bars_completed": self._bars_completed,
            "anomalies": self._anomalies,
            "queue_depth": self.tick_queue.qsize(),
            "last_symbol": self._last_bar.symbol if self._last_bar else None,
        }

    def recent_bars(self) -> List[Dict[str, Any]]:
        return list(self._recent_bars)


class MAMISForensicAgent(AgentBase):
    def __init__(self, event_bus, input_queue: asyncio.Queue, strategist_queue: asyncio.Queue):
        super().__init__("mamis_forensic")
        self.event_bus = event_bus
        self.input_queue = input_queue
        self.strategist_queue = strategist_queue
        self.market_maker_detector = MarketMakerDetector()
        self.spoofing_detector = SpoofingLayeringDetector()
        self.iceberg_detector = IcebergDetector()
        self._classified = 0
        self._last_classification: Optional[MAMISClassification] = None
        self._recent_classifications: Deque[Dict[str, Any]] = deque(maxlen=32)

    async def initialize(self) -> None:
        self.running = False

    async def start(self) -> None:
        self.running = True
        while self.running:
            try:
                payload = await self.input_queue.get()
            except asyncio.CancelledError:
                raise
            if payload is None:
                return
            await self._analyze(payload)

    async def stop(self) -> None:
        self.running = False
        try:
            self.input_queue.put_nowait(None)
        except Exception:
            pass

    async def _analyze(self, payload: Dict[str, Any]):
        bar: EventBar = payload["bar"]
        self.last_activity = utc_now()
        candidates = [
            self.iceberg_detector.detect(bar),
            self.spoofing_detector.detect(bar),
            self.market_maker_detector.detect(bar),
        ]
        candidates = [c for c in candidates if c]

        if not candidates:
            direction_hint = "long" if bar.delta_volume > 0 else "short"
            candidates = [{
                "pattern_type": "toxic_flow_detected",
                "confidence": float(min(0.85, 0.35 + abs(bar.ofi_normalized) + 0.35 * bar.vpin)),
                "direction_hint": direction_hint,
                "reason": "ofi_vpin_cvd_alignment",
            }]

        best = max(candidates, key=lambda item: float(item.get("confidence", 0.0)))
        classification = MAMISClassification(
            symbol=bar.symbol,
            pattern_type=str(best["pattern_type"]),
            confidence=float(best["confidence"]),
            direction_hint=str(best["direction_hint"]),
            estimated_volatility=float(bar.volatility),
            reason=str(best["reason"]),
            event_bar=bar,
            metadata={
                "trigger": payload.get("trigger", {}),
                "spread_bps": bar.spread_bps,
                "cancel_to_trade_ratio": bar.cancel_to_trade_ratio,
            },
        )
        self._classified += 1
        self._last_classification = classification
        self._recent_classifications.appendleft(classification.to_dict())
        await self.strategist_queue.put(classification)
        await self.event_bus.publish(Event(
            type=EventType.MICROSTRUCTURE_CLASSIFIED,
            source=self.name,
            data=classification.to_dict(),
        ))

    async def health_check(self) -> Dict[str, Any]:
        return {
            "healthy": self.running,
            "classified": self._classified,
            "last_pattern": self._last_classification.pattern_type if self._last_classification else None,
        }

    def recent_classifications(self) -> List[Dict[str, Any]]:
        return list(self._recent_classifications)


class MAMISStrategistAgent(AgentBase, BaseMicrostructureStrategy):
    def __init__(self, event_bus, input_queue: asyncio.Queue):
        AgentBase.__init__(self, "mamis_strategist")
        self.event_bus = event_bus
        self.input_queue = input_queue
        self.base_position_size = float(os.getenv("QUENBOT_MAMIS_BASE_POSITION_SIZE", "250"))
        self._signals = 0
        self._last_signal: Optional[MAMISSignal] = None
        self._recent_signals: Deque[Dict[str, Any]] = deque(maxlen=32)

    async def initialize(self) -> None:
        self.running = False

    async def start(self) -> None:
        self.running = True
        while self.running:
            try:
                classification = await self.input_queue.get()
            except asyncio.CancelledError:
                raise
            if classification is None:
                return
            signal = self.build_signal(classification)
            self.last_activity = utc_now()
            self._signals += 1
            self._last_signal = signal
            self._recent_signals.appendleft(signal.to_dict())
            await self.event_bus.publish(Event(
                type=EventType.MICROSTRUCTURE_SIGNAL,
                source=self.name,
                data=signal.to_dict(),
            ))

    async def stop(self) -> None:
        self.running = False
        try:
            self.input_queue.put_nowait(None)
        except Exception:
            pass

    def build_signal(self, classification: MAMISClassification) -> MAMISSignal:
        bar = classification.event_bar
        direction = classification.direction_hint.lower()
        if classification.pattern_type == "market_maker_detected":
            direction = "neutral"

        volatility = max(classification.estimated_volatility, 0.0005)
        confidence = min(
            0.99,
            0.55 * classification.confidence +
            0.25 * min(abs(bar.ofi_normalized) * 2.0, 1.0) +
            0.20 * min(bar.vpin / 0.8, 1.0),
        )
        position_size = self.base_position_size * confidence * min(2.8, 0.012 / volatility)
        position_size = float(max(25.0, min(position_size, 1500.0)))

        return MAMISSignal(
            timestamp=utc_now().isoformat(),
            symbol=classification.symbol,
            signal_direction=direction,
            confidence_score=float(round(confidence, 4)),
            detected_pattern_type=classification.pattern_type,
            estimated_volatility=float(round(volatility, 6)),
            position_size=float(round(position_size, 2)),
            metadata={
                "reason": classification.reason,
                "ofi": round(bar.ofi_normalized, 4),
                "vpin": round(bar.vpin, 4),
                "cvd": round(bar.cumulative_volume_delta, 4),
                "price_range_bps": round(bar.price_range_bps, 4),
                "spread_bps": round(bar.spread_bps, 4),
                "bar_index": bar.bar_index,
            },
        )

    async def health_check(self) -> Dict[str, Any]:
        return {
            "healthy": self.running,
            "signals": self._signals,
            "last_signal": self._last_signal.to_dict() if self._last_signal else None,
        }

    def recent_signals(self) -> List[Dict[str, Any]]:
        return list(self._recent_signals)


class MAMISOrchestrator:
    def __init__(self):
        self.event_bus = get_event_bus()
        self._forensic_queue: asyncio.Queue = asyncio.Queue(maxsize=1024)
        self._strategist_queue: asyncio.Queue = asyncio.Queue(maxsize=1024)
        self.sentinel = MAMISSentinelAgent(self.event_bus, self._forensic_queue)
        self.forensic = MAMISForensicAgent(self.event_bus, self._forensic_queue, self._strategist_queue)
        self.strategist = MAMISStrategistAgent(self.event_bus, self._strategist_queue)
        self.running = False
        self._subscribed = False

    async def initialize(self):
        await asyncio.gather(
            self.sentinel.initialize(),
            self.forensic.initialize(),
            self.strategist.initialize(),
        )
        if not self._subscribed:
            self.event_bus.subscribe(EventType.SCOUT_PRICE_UPDATE, self._handle_trade_event)
            self.event_bus.subscribe(EventType.ORDER_BOOK_UPDATE, self._handle_book_event)
            self._subscribed = True

    async def start(self):
        self.running = True
        await asyncio.gather(
            self.sentinel.start(),
            self.forensic.start(),
            self.strategist.start(),
        )

    async def stop(self):
        self.running = False
        await asyncio.gather(
            self.sentinel.stop(),
            self.forensic.stop(),
            self.strategist.stop(),
        )

    async def _handle_trade_event(self, event: Event):
        data = event.data or {}
        try:
            tick = MicrostructureTick(
                symbol=str(data.get("symbol", "")).upper(),
                exchange=str(data.get("exchange", "mixed")),
                market_type=str(data.get("market_type", "spot")),
                price=float(data.get("price", 0) or 0),
                quantity=float(data.get("quantity", 0) or 0),
                side=str(data.get("side", "buy")).lower(),
                timestamp=datetime.fromisoformat(data["timestamp"]) if isinstance(data.get("timestamp"), str) else data.get("timestamp") or utc_now(),
                trade_id=str(data.get("trade_id")) if data.get("trade_id") else None,
            )
        except Exception:
            return

        if tick.price <= 0 or tick.quantity <= 0 or not tick.symbol:
            return
        
        # Systematic Trade Detector'a da gönder
        systematic_detector = get_systematic_detector()
        systematic_detector.ingest_trade({
            "symbol": tick.symbol,
            "price": tick.price,
            "quantity": tick.quantity,
            "side": tick.side,
            "timestamp": tick.timestamp,
            "trade_id": tick.trade_id,
        })
        
        await self.sentinel.ingest_trade_tick(tick)

    async def _handle_book_event(self, event: Event):
        data = event.data or {}
        try:
            update = OrderBookUpdate(
                symbol=str(data.get("symbol", "")).upper(),
                bid_price=float(data.get("bid_price", 0) or 0),
                ask_price=float(data.get("ask_price", 0) or 0),
                bid_size=float(data.get("bid_size", 0) or 0),
                ask_size=float(data.get("ask_size", 0) or 0),
                event_type=str(data.get("event_type", "quote")),
                order_size=float(data.get("order_size", 0) or 0),
                order_side=str(data.get("order_side", "unknown")),
                timestamp=datetime.fromisoformat(data["timestamp"]) if isinstance(data.get("timestamp"), str) else data.get("timestamp") or utc_now(),
            )
        except Exception:
            return
        await self.sentinel.ingest_book_update(update)

    async def health_check(self) -> Dict[str, Any]:
        sentinel, forensic, strategist = await asyncio.gather(
            self.sentinel.health_check(),
            self.forensic.health_check(),
            self.strategist.health_check(),
        )
        healthy = all(item.get("healthy") for item in [sentinel, forensic, strategist])
        return {
            "healthy": healthy,
            "sentinel": sentinel,
            "forensic": forensic,
            "strategist": strategist,
        }

    def get_dashboard_payload(self) -> Dict[str, Any]:
        return {
            "bars": self.sentinel.recent_bars(),
            "classifications": self.forensic.recent_classifications(),
            "signals": self.strategist.recent_signals(),
        }
