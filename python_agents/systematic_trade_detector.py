"""
Systematic Trade Detector — Bot/Algo Trading Pattern Analyzer
=============================================================
Gerçekleşen alım-satım defterlerinden sistematik veya bot kaynaklı işlemleri tespit eder.
Fiyata yön veren algoritmik aktiviteleri belirler ve Gemma'ya zengin context sağlar.

MİMARİ KONUM: Intelligence Layer (Pre-Decision Enhancement)
- INPUT  ← Raw trades, Order Book updates, MAMIS bars
- OUTPUT → Bot signature, systematic activity score, predicted direction

KULLANIM ALANLARI:
1. Market Maker Tespiti — iki taraflı likidite sağlayan botların tespiti
2. Trend Follower Botu — momentum/trend takip eden algoritmalar
3. Arbitraj Botu — cross-exchange/pairs arbitrajcıları
4. Accumulation/Distribution — büyük oyuncuların sessiz birikimi
5. Spoofing/Layering — manipülatif emir stratejileri
6. TWAP/VWAP Execution — kurumsal icra algoritmaları
"""
import asyncio
import logging
import math
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Deque, Dict, List, Optional, Tuple

import numpy as np

logger = logging.getLogger("quenbot.systematic_detector")


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class TradeCluster:
    """Benzer özelliklere sahip trade grubu."""
    start_time: datetime
    end_time: datetime
    trade_count: int
    total_volume: float
    avg_price: float
    price_std: float
    side_ratio: float  # buy_volume / total_volume
    avg_interval_ms: float
    interval_std_ms: float
    signature_hash: str = ""


@dataclass
class BotSignature:
    """Tespit edilen bot/algo imzası."""
    signature_type: str  # market_maker, trend_follower, accumulator, twap, iceberg, spoofing
    confidence: float
    direction_bias: str  # long, short, neutral
    estimated_volume_remaining: float
    avg_trade_size: float
    trade_interval_ms: float
    price_impact_bps: float
    first_seen: datetime
    last_seen: datetime
    trade_count: int
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "signature_type": self.signature_type,
            "confidence": round(self.confidence, 4),
            "direction_bias": self.direction_bias,
            "estimated_volume_remaining": round(self.estimated_volume_remaining, 4),
            "avg_trade_size": round(self.avg_trade_size, 4),
            "trade_interval_ms": round(self.trade_interval_ms, 2),
            "price_impact_bps": round(self.price_impact_bps, 4),
            "first_seen": self.first_seen.isoformat(),
            "last_seen": self.last_seen.isoformat(),
            "trade_count": self.trade_count,
            "metadata": self.metadata,
        }


@dataclass
class SystematicActivityReport:
    """Sembol için sistematik aktivite raporu."""
    symbol: str
    timestamp: datetime
    total_trades_analyzed: int
    systematic_trade_ratio: float
    dominant_bot_type: Optional[str]
    bot_signatures: List[BotSignature]
    predicted_price_direction: str
    direction_confidence: float
    estimated_price_impact_bps: float
    accumulation_score: float  # -1 (distribution) to +1 (accumulation)
    smart_money_flow: float  # -1 (outflow) to +1 (inflow)
    retail_vs_institutional: float  # 0 (retail) to 1 (institutional)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "symbol": self.symbol,
            "timestamp": self.timestamp.isoformat(),
            "total_trades_analyzed": self.total_trades_analyzed,
            "systematic_trade_ratio": round(self.systematic_trade_ratio, 4),
            "dominant_bot_type": self.dominant_bot_type,
            "bot_signatures": [s.to_dict() for s in self.bot_signatures],
            "predicted_price_direction": self.predicted_price_direction,
            "direction_confidence": round(self.direction_confidence, 4),
            "estimated_price_impact_bps": round(self.estimated_price_impact_bps, 4),
            "accumulation_score": round(self.accumulation_score, 4),
            "smart_money_flow": round(self.smart_money_flow, 4),
            "retail_vs_institutional": round(self.retail_vs_institutional, 4),
            "metadata": self.metadata,
        }


class TradeIntervalAnalyzer:
    """
    Trade interval pattern analyzer — Bot imzalarını interval düzeninden tespit eder.
    
    Botlar genellikle:
    - Sabit interval'lerle işlem yapar (TWAP/VWAP)
    - Çok düşük interval std'ye sahiptir
    - Belirli zaman dilimlerinde yoğunlaşır
    """
    
    MIN_TRADES_FOR_ANALYSIS = 10
    BOT_INTERVAL_CV_THRESHOLD = 0.15  # Coefficient of Variation < 0.15 = bot
    HUMAN_INTERVAL_CV_MIN = 0.40  # CV > 0.40 = likely human
    
    @staticmethod
    def analyze_intervals(timestamps: List[datetime]) -> Dict[str, Any]:
        """Trade timestamp'larından interval analizi çıkar."""
        if len(timestamps) < TradeIntervalAnalyzer.MIN_TRADES_FOR_ANALYSIS:
            return {
                "is_systematic": False,
                "confidence": 0.0,
                "avg_interval_ms": 0.0,
                "interval_cv": 0.0,
                "pattern_type": "insufficient_data",
            }
        
        # Interval'leri hesapla
        intervals_ms = []
        for i in range(1, len(timestamps)):
            delta = (timestamps[i] - timestamps[i-1]).total_seconds() * 1000
            if delta > 0:
                intervals_ms.append(delta)
        
        if len(intervals_ms) < 5:
            return {
                "is_systematic": False,
                "confidence": 0.0,
                "avg_interval_ms": 0.0,
                "interval_cv": 0.0,
                "pattern_type": "insufficient_intervals",
            }
        
        intervals = np.array(intervals_ms)
        avg_interval = float(np.mean(intervals))
        std_interval = float(np.std(intervals))
        cv = std_interval / max(avg_interval, 1.0)  # Coefficient of variation
        
        # Periyodik pattern tespiti (FFT)
        if len(intervals) >= 32:
            fft_result = np.abs(np.fft.fft(intervals - avg_interval))
            dominant_freq_power = float(np.max(fft_result[1:len(fft_result)//2]))
            total_power = float(np.sum(fft_result[1:]))
            periodicity_score = dominant_freq_power / max(total_power, 1e-9)
        else:
            periodicity_score = 0.0
        
        # Bot confidence hesapla
        cv_score = max(0, 1.0 - cv / TradeIntervalAnalyzer.HUMAN_INTERVAL_CV_MIN)
        is_systematic = cv < TradeIntervalAnalyzer.BOT_INTERVAL_CV_THRESHOLD
        confidence = cv_score * 0.6 + periodicity_score * 0.4 if is_systematic else cv_score * 0.3
        
        # Pattern tipi
        if cv < 0.08:
            pattern_type = "twap_vwap"  # Çok düzenli = TWAP/VWAP
        elif cv < 0.15:
            pattern_type = "algorithmic"  # Düzenli = Algo
        elif cv < 0.30:
            pattern_type = "semi_systematic"  # Yarı sistematik
        else:
            pattern_type = "organic"  # İnsan veya rastgele
        
        return {
            "is_systematic": is_systematic,
            "confidence": min(confidence, 1.0),
            "avg_interval_ms": avg_interval,
            "interval_cv": cv,
            "interval_std_ms": std_interval,
            "periodicity_score": periodicity_score,
            "pattern_type": pattern_type,
        }


class TradeSizeAnalyzer:
    """
    Trade size pattern analyzer — Büyüklük dağılımından bot/kurumsal tespit.
    
    Kurumsal işlemler:
    - Round lot'lar (100, 500, 1000 birim)
    - Benzer büyüklükte tekrarlayan işlemler
    - Iceberg pattern (küçük görünen büyük emirler)
    """
    
    ROUND_LOT_TOLERANCE = 0.02  # %2 tolerans
    
    @staticmethod
    def analyze_sizes(quantities: List[float], values: List[float]) -> Dict[str, Any]:
        """Trade büyüklüklerinden pattern analizi çıkar."""
        if len(quantities) < 5:
            return {
                "is_institutional": False,
                "round_lot_ratio": 0.0,
                "avg_size": 0.0,
                "size_cv": 0.0,
                "iceberg_probability": 0.0,
            }
        
        qty_arr = np.array(quantities)
        val_arr = np.array(values)
        
        avg_qty = float(np.mean(qty_arr))
        std_qty = float(np.std(qty_arr))
        cv_qty = std_qty / max(avg_qty, 1e-9)
        
        avg_val = float(np.mean(val_arr))
        median_val = float(np.median(val_arr))
        
        # Round lot analizi
        round_lots = [10, 25, 50, 100, 250, 500, 1000, 2500, 5000, 10000]
        round_lot_count = 0
        for qty in quantities:
            for lot in round_lots:
                if abs(qty - lot) / max(lot, 1e-9) < TradeSizeAnalyzer.ROUND_LOT_TOLERANCE:
                    round_lot_count += 1
                    break
        round_lot_ratio = round_lot_count / len(quantities)
        
        # Iceberg pattern (küçük trade'ler ama yüksek frekans)
        small_trade_count = sum(1 for v in values if v < median_val * 0.5)
        small_trade_ratio = small_trade_count / len(values)
        iceberg_prob = small_trade_ratio * 0.5 + (1.0 - cv_qty) * 0.5 if cv_qty < 0.3 else 0.0
        
        # Kurumsal scoring
        is_institutional = round_lot_ratio > 0.3 or (cv_qty < 0.2 and avg_val > 1000)
        
        return {
            "is_institutional": is_institutional,
            "round_lot_ratio": round_lot_ratio,
            "avg_size": avg_qty,
            "avg_value": avg_val,
            "size_cv": cv_qty,
            "iceberg_probability": iceberg_prob,
            "small_trade_ratio": small_trade_ratio,
        }


class DirectionalFlowAnalyzer:
    """
    Order flow direction analyzer — Smart money flow ve yön tahmini.
    
    Analiz faktörleri:
    - Net buy/sell volume
    - Trade size weighted direction
    - Price level'da yoğunlaşma
    - Agresif vs pasif işlemler
    """
    
    @staticmethod
    def analyze_flow(
        trades: List[Dict[str, Any]],
        current_price: float,
    ) -> Dict[str, Any]:
        """Trade akışından yön ve smart money flow analizi."""
        if len(trades) < 10:
            return {
                "net_flow": 0.0,
                "smart_money_flow": 0.0,
                "predicted_direction": "neutral",
                "direction_confidence": 0.0,
                "accumulation_score": 0.0,
            }
        
        buy_volume = 0.0
        sell_volume = 0.0
        buy_value = 0.0
        sell_value = 0.0
        large_buy_value = 0.0
        large_sell_value = 0.0
        
        # Büyük trade eşiği (üst %20)
        values = [float(t.get('quantity', 0)) * float(t.get('price', 0)) for t in trades]
        large_threshold = np.percentile(values, 80) if values else 0
        
        for trade in trades:
            qty = float(trade.get('quantity', 0))
            price = float(trade.get('price', 0))
            side = trade.get('side', 'unknown')
            value = qty * price
            
            if side == 'buy':
                buy_volume += qty
                buy_value += value
                if value >= large_threshold:
                    large_buy_value += value
            else:
                sell_volume += qty
                sell_value += value
                if value >= large_threshold:
                    large_sell_value += value
        
        total_volume = buy_volume + sell_volume
        total_value = buy_value + sell_value
        
        # Net flow
        net_flow = (buy_value - sell_value) / max(total_value, 1e-9)
        
        # Smart money flow (büyük işlemlerin yönü)
        large_total = large_buy_value + large_sell_value
        smart_money_flow = (large_buy_value - large_sell_value) / max(large_total, 1e-9) if large_total > 0 else 0
        
        # Accumulation/Distribution score
        # Yükselen fiyatta satış = distribution, düşen fiyatta alış = accumulation
        prices = [float(t.get('price', current_price)) for t in trades]
        price_trend = (prices[-1] - prices[0]) / max(prices[0], 1e-9) if prices else 0
        
        if price_trend > 0 and net_flow < 0:
            accumulation_score = -0.5 - abs(net_flow) * 0.5  # Distribution
        elif price_trend < 0 and net_flow > 0:
            accumulation_score = 0.5 + abs(net_flow) * 0.5  # Accumulation
        else:
            accumulation_score = net_flow * 0.5
        
        # Yön tahmini
        combined_signal = net_flow * 0.4 + smart_money_flow * 0.6
        if combined_signal > 0.15:
            direction = "long"
            confidence = min(abs(combined_signal) + 0.3, 1.0)
        elif combined_signal < -0.15:
            direction = "short"
            confidence = min(abs(combined_signal) + 0.3, 1.0)
        else:
            direction = "neutral"
            confidence = 0.3
        
        return {
            "net_flow": net_flow,
            "smart_money_flow": smart_money_flow,
            "predicted_direction": direction,
            "direction_confidence": confidence,
            "accumulation_score": accumulation_score,
            "buy_volume_ratio": buy_volume / max(total_volume, 1e-9),
            "large_trade_ratio": (large_buy_value + large_sell_value) / max(total_value, 1e-9),
        }


class SystematicTradeDetector:
    """
    Ana Systematic Trade Detector — Bot/Algo trading pattern analyzer.
    
    Tüm alt analyzer'ları birleştirerek kapsamlı sistematik aktivite raporu üretir.
    Bu rapor GemmaDecisionCore'a zengin context olarak gönderilir.
    """
    
    # Trade buffer boyutları
    MAX_TRADES_PER_SYMBOL = 1000
    ANALYSIS_WINDOW_SECONDS = 300  # Son 5 dakika
    MIN_TRADES_FOR_REPORT = 20
    
    def __init__(self):
        self._trade_buffers: Dict[str, Deque[Dict]] = {}
        self._last_reports: Dict[str, SystematicActivityReport] = {}
        self._bot_signatures_history: Dict[str, List[BotSignature]] = {}
        self._stats = {
            "total_trades_processed": 0,
            "reports_generated": 0,
            "bots_detected": 0,
        }
    
    def ingest_trade(self, trade: Dict[str, Any]) -> None:
        """Trade verisini buffer'a ekle."""
        symbol = trade.get("symbol", "UNKNOWN")
        
        if symbol not in self._trade_buffers:
            self._trade_buffers[symbol] = deque(maxlen=self.MAX_TRADES_PER_SYMBOL)
        
        self._trade_buffers[symbol].append({
            "timestamp": trade.get("timestamp", utc_now()),
            "price": float(trade.get("price", 0)),
            "quantity": float(trade.get("quantity", 0)),
            "side": trade.get("side", "unknown"),
            "trade_id": trade.get("trade_id"),
        })
        
        self._stats["total_trades_processed"] += 1
    
    def analyze_symbol(self, symbol: str) -> Optional[SystematicActivityReport]:
        """Sembol için sistematik aktivite analizi yap."""
        if symbol not in self._trade_buffers:
            return None
        
        trades = list(self._trade_buffers[symbol])
        
        # Sadece son 5 dk'lık trade'leri filtrele
        cutoff = utc_now() - timedelta(seconds=self.ANALYSIS_WINDOW_SECONDS)
        recent_trades = [
            t for t in trades
            if isinstance(t.get("timestamp"), datetime) and t["timestamp"] >= cutoff
        ]
        
        if len(recent_trades) < self.MIN_TRADES_FOR_REPORT:
            return None
        
        # Alt analizleri çalıştır
        timestamps = [t["timestamp"] for t in recent_trades if isinstance(t.get("timestamp"), datetime)]
        quantities = [t["quantity"] for t in recent_trades]
        values = [t["quantity"] * t["price"] for t in recent_trades]
        current_price = recent_trades[-1]["price"] if recent_trades else 0
        
        interval_analysis = TradeIntervalAnalyzer.analyze_intervals(timestamps)
        size_analysis = TradeSizeAnalyzer.analyze_sizes(quantities, values)
        flow_analysis = DirectionalFlowAnalyzer.analyze_flow(recent_trades, current_price)
        
        # Bot signature'ları tespit et
        bot_signatures = self._detect_bot_signatures(
            symbol, recent_trades, interval_analysis, size_analysis, flow_analysis
        )
        
        # Sistematik trade oranı
        systematic_ratio = self._calculate_systematic_ratio(
            interval_analysis, size_analysis, len(bot_signatures)
        )
        
        # Dominant bot tipi
        dominant_bot = None
        if bot_signatures:
            dominant_bot = max(bot_signatures, key=lambda s: s.confidence).signature_type
        
        # Retail vs Institutional
        retail_vs_inst = 0.0
        if size_analysis["is_institutional"]:
            retail_vs_inst = 0.7 + size_analysis["round_lot_ratio"] * 0.3
        elif interval_analysis["is_systematic"]:
            retail_vs_inst = 0.4 + interval_analysis["confidence"] * 0.3
        
        report = SystematicActivityReport(
            symbol=symbol,
            timestamp=utc_now(),
            total_trades_analyzed=len(recent_trades),
            systematic_trade_ratio=systematic_ratio,
            dominant_bot_type=dominant_bot,
            bot_signatures=bot_signatures,
            predicted_price_direction=flow_analysis["predicted_direction"],
            direction_confidence=flow_analysis["direction_confidence"],
            estimated_price_impact_bps=self._estimate_price_impact(bot_signatures, flow_analysis),
            accumulation_score=flow_analysis["accumulation_score"],
            smart_money_flow=flow_analysis["smart_money_flow"],
            retail_vs_institutional=retail_vs_inst,
            metadata={
                "interval_analysis": interval_analysis,
                "size_analysis": size_analysis,
                "flow_analysis": flow_analysis,
            },
        )
        
        self._last_reports[symbol] = report
        self._stats["reports_generated"] += 1
        
        return report
    
    def _detect_bot_signatures(
        self,
        symbol: str,
        trades: List[Dict],
        interval_analysis: Dict,
        size_analysis: Dict,
        flow_analysis: Dict,
    ) -> List[BotSignature]:
        """Trade verilerinden bot imzalarını tespit et."""
        signatures = []
        
        if not trades:
            return signatures
        
        first_time = trades[0].get("timestamp", utc_now())
        last_time = trades[-1].get("timestamp", utc_now())
        avg_trade_size = sum(t["quantity"] for t in trades) / len(trades)
        avg_interval = interval_analysis.get("avg_interval_ms", 0)
        
        # TWAP/VWAP Bot Detection
        if interval_analysis.get("pattern_type") == "twap_vwap":
            signatures.append(BotSignature(
                signature_type="twap_vwap",
                confidence=interval_analysis["confidence"],
                direction_bias=flow_analysis["predicted_direction"],
                estimated_volume_remaining=avg_trade_size * 50,  # Tahmin
                avg_trade_size=avg_trade_size,
                trade_interval_ms=avg_interval,
                price_impact_bps=2.0,  # TWAP genelde az etki
                first_seen=first_time,
                last_seen=last_time,
                trade_count=len(trades),
                metadata={"interval_cv": interval_analysis.get("interval_cv", 0)},
            ))
        
        # Market Maker Detection
        buy_ratio = flow_analysis.get("buy_volume_ratio", 0.5)
        if 0.45 < buy_ratio < 0.55 and interval_analysis.get("is_systematic", False):
            mm_confidence = (1.0 - abs(buy_ratio - 0.5) * 10) * interval_analysis.get("confidence", 0)
            if mm_confidence > 0.5:
                signatures.append(BotSignature(
                    signature_type="market_maker",
                    confidence=mm_confidence,
                    direction_bias="neutral",
                    estimated_volume_remaining=0,
                    avg_trade_size=avg_trade_size,
                    trade_interval_ms=avg_interval,
                    price_impact_bps=0.5,
                    first_seen=first_time,
                    last_seen=last_time,
                    trade_count=len(trades),
                    metadata={"balance_ratio": 1.0 - abs(buy_ratio - 0.5) * 2},
                ))
        
        # Accumulator Bot Detection
        if abs(flow_analysis.get("accumulation_score", 0)) > 0.5:
            acc_score = flow_analysis["accumulation_score"]
            signatures.append(BotSignature(
                signature_type="accumulator" if acc_score > 0 else "distributor",
                confidence=abs(acc_score),
                direction_bias="long" if acc_score > 0 else "short",
                estimated_volume_remaining=avg_trade_size * 100,
                avg_trade_size=avg_trade_size,
                trade_interval_ms=avg_interval,
                price_impact_bps=5.0 * abs(acc_score),
                first_seen=first_time,
                last_seen=last_time,
                trade_count=len(trades),
                metadata={"accumulation_score": acc_score},
            ))
        
        # Iceberg Detection
        if size_analysis.get("iceberg_probability", 0) > 0.4:
            signatures.append(BotSignature(
                signature_type="iceberg",
                confidence=size_analysis["iceberg_probability"],
                direction_bias=flow_analysis["predicted_direction"],
                estimated_volume_remaining=avg_trade_size * 200,  # Iceberg büyük
                avg_trade_size=avg_trade_size,
                trade_interval_ms=avg_interval,
                price_impact_bps=8.0,  # Iceberg yüksek etki
                first_seen=first_time,
                last_seen=last_time,
                trade_count=len(trades),
                metadata={"small_trade_ratio": size_analysis.get("small_trade_ratio", 0)},
            ))
        
        # Trend Follower Detection (momentum takip)
        if interval_analysis.get("is_systematic") and abs(flow_analysis.get("net_flow", 0)) > 0.3:
            direction = "long" if flow_analysis["net_flow"] > 0 else "short"
            signatures.append(BotSignature(
                signature_type="trend_follower",
                confidence=min(abs(flow_analysis["net_flow"]) + 0.2, 0.9),
                direction_bias=direction,
                estimated_volume_remaining=avg_trade_size * 30,
                avg_trade_size=avg_trade_size,
                trade_interval_ms=avg_interval,
                price_impact_bps=3.0,
                first_seen=first_time,
                last_seen=last_time,
                trade_count=len(trades),
                metadata={"net_flow": flow_analysis["net_flow"]},
            ))
        
        self._stats["bots_detected"] += len(signatures)
        return signatures
    
    def _calculate_systematic_ratio(
        self,
        interval_analysis: Dict,
        size_analysis: Dict,
        bot_count: int,
    ) -> float:
        """Sistematik trade oranını hesapla."""
        interval_score = interval_analysis.get("confidence", 0) if interval_analysis.get("is_systematic") else 0
        size_score = 0.5 if size_analysis.get("is_institutional") else 0
        bot_score = min(bot_count * 0.2, 0.6)
        
        return min(interval_score * 0.4 + size_score * 0.3 + bot_score * 0.3, 1.0)
    
    def _estimate_price_impact(
        self,
        signatures: List[BotSignature],
        flow_analysis: Dict,
    ) -> float:
        """Tahmini fiyat etkisini hesapla (bps)."""
        if not signatures:
            return abs(flow_analysis.get("smart_money_flow", 0)) * 5.0
        
        max_impact = max(s.price_impact_bps for s in signatures)
        flow_impact = abs(flow_analysis.get("smart_money_flow", 0)) * 3.0
        
        return max_impact * 0.7 + flow_impact * 0.3
    
    def get_last_report(self, symbol: str) -> Optional[SystematicActivityReport]:
        """Son raporu döndür."""
        return self._last_reports.get(symbol)
    
    def get_stats(self) -> Dict[str, Any]:
        """İstatistikleri döndür."""
        return dict(self._stats)


# Global singleton
_systematic_detector: Optional[SystematicTradeDetector] = None


def get_systematic_detector() -> SystematicTradeDetector:
    """Singleton systematic detector instance."""
    global _systematic_detector
    if _systematic_detector is None:
        _systematic_detector = SystematicTradeDetector()
    return _systematic_detector
