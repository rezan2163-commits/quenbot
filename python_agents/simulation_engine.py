"""
Simulation Engine — Hedef Kartı Yaşam Döngüsü Yönetimi
========================================================
Paper trading simülasyonları için çok-horizon hedef kartları oluşturur,
gerçek zamanlı fiyat takibi yapar, near-miss tespiti ve öğrenme arşivi yönetir.

MİMARİ KONUM: Ghost Simulator ile entegre çalışır
- INPUT  ← GemmaDecisionCore kararları, sinyal verileri, anlık fiyatlar
- OUTPUT → TargetCard yaşam döngüsü, near-miss tespiti, AdaptiveEngine feedback

HEDEF KARTI KURALLARI:
- Her coin için 1 saatte max 2 yeni hedef belirlenebilir (eski hedefler geçersiz olmalı)
- 4 zaman ufku: 15m, 1h, 4h, 24h — en yüksek olasılıklı ufuk = Ana Hedef
- Near Miss: Fiyat hedefe %0.5 yaklaşıp geri dönerse kayıt edilir
- Süre dolduğunda veya hedef vurulduğunda → Öğrenme Arşivi'ne aktarılır
"""
import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from typing import Any, Dict, List, Optional, Set, Tuple

from event_bus import Event, EventType, get_event_bus

logger = logging.getLogger(__name__)


class HorizonStatus(str, Enum):
    ACTIVE = "active"
    HIT = "hit"
    MISSED = "missed"
    NEAR_MISS = "near_miss"
    EXPIRED = "expired"


class CardStatus(str, Enum):
    LIVE = "live"
    COMPLETED = "completed"
    EXPIRED = "expired"
    SUPERSEDED = "superseded"  # Aynı coin'de yeni kart açılınca eski kart


# Horizon tanımları: (label, dakika, target çarpanı)
HORIZONS = [
    ("15m", 15, 1.0),
    ("1h", 60, 1.3),
    ("4h", 240, 1.8),
    ("24h", 1440, 2.5),
]

# Near-miss eşiği: hedefe %0.5 yaklaşma
NEAR_MISS_THRESHOLD = 0.005


@dataclass
class HorizonTarget:
    """Tek bir zaman ufku hedefi."""
    label: str
    eta_minutes: int
    target_pct: float
    target_price: float
    status: HorizonStatus = HorizonStatus.ACTIVE
    strength: float = 0.0  # Bu ufuk için olasılık gücü
    closest_approach_pct: float = 0.0  # Hedefe en yakın yaklaşma
    hit_time: Optional[datetime] = None
    near_miss: bool = False
    near_miss_pct: float = 0.0  # Near-miss sırasındaki mesafe

    def to_dict(self) -> Dict[str, Any]:
        return {
            "label": self.label,
            "eta_minutes": self.eta_minutes,
            "target_pct": self.target_pct,
            "target_price": self.target_price,
            "status": self.status.value,
            "strength": self.strength,
            "closest_approach_pct": self.closest_approach_pct,
            "hit_time": self.hit_time.isoformat() + "Z" if self.hit_time else None,
            "near_miss": self.near_miss,
            "near_miss_pct": self.near_miss_pct,
        }


@dataclass
class TargetCard:
    """
    Hedef Kartı — bir coin için çok-horizon simülasyon takip nesnesi.

    Yaşam döngüsü:
    1. LIVE: Kart oluşturulur, horizonlar aktif
    2. Fiyat takibi: Her tick'te horizonlar kontrol edilir
    3. HIT/MISS/NEAR_MISS: Horizon durumu güncellenir
    4. COMPLETED/EXPIRED: Tüm horizonlar kapanınca veya max süre dolunca
    5. Öğrenme Arşivi'ne aktarılır
    """
    id: str
    symbol: str
    direction: str  # "long" | "short"
    entry_price: float
    entry_time: datetime
    signal_id: Optional[int] = None
    simulation_id: Optional[int] = None
    confidence: float = 0.0
    status: CardStatus = CardStatus.LIVE
    primary_horizon: str = "15m"  # En yüksek olasılıklı horizon
    horizons: List[HorizonTarget] = field(default_factory=list)
    max_expiry: Optional[datetime] = None  # En uzun horizon + buffer
    metadata: Dict[str, Any] = field(default_factory=dict)
    close_reason: str = ""
    close_time: Optional[datetime] = None
    peak_price: float = 0.0  # Yön doğrultusunda en uç fiyat
    peak_pnl_pct: float = 0.0  # En yüksek PnL %

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "symbol": self.symbol,
            "direction": self.direction,
            "entry_price": self.entry_price,
            "entry_time": self.entry_time.isoformat() + "Z",
            "signal_id": self.signal_id,
            "simulation_id": self.simulation_id,
            "confidence": self.confidence,
            "status": self.status.value,
            "primary_horizon": self.primary_horizon,
            "horizons": [h.to_dict() for h in self.horizons],
            "max_expiry": self.max_expiry.isoformat() + "Z" if self.max_expiry else None,
            "close_reason": self.close_reason,
            "close_time": self.close_time.isoformat() + "Z" if self.close_time else None,
            "peak_price": self.peak_price,
            "peak_pnl_pct": self.peak_pnl_pct,
            "metadata": self.metadata,
        }

    @property
    def is_live(self) -> bool:
        return self.status == CardStatus.LIVE

    @property
    def active_horizons(self) -> List[HorizonTarget]:
        return [h for h in self.horizons if h.status == HorizonStatus.ACTIVE]

    @property
    def hit_count(self) -> int:
        return sum(1 for h in self.horizons if h.status == HorizonStatus.HIT)

    @property
    def near_miss_count(self) -> int:
        return sum(1 for h in self.horizons if h.near_miss)


class SimulationEngine:
    """
    Simülasyon Motoru — Hedef Kartı Yaşam Döngüsü
    ================================================
    Kartların açılması, anlık fiyat takibi, near-miss tespiti,
    süre aşımı yönetimi ve öğrenme arşivine aktarım.

    Rate Limit: Bir coin için 1 saatte max 2 yeni hedef kartı.
    Eski kart geçersiz olmadan yeni kart açılamaz.
    """

    MAX_CARDS_PER_HOUR = 2
    MAX_LIVE_CARDS = 20  # Toplam aktif kart limiti

    def __init__(self, decision_core=None):
        self.decision_core = decision_core
        self.event_bus = get_event_bus()
        # Aktif kartlar: card_id → TargetCard
        self._cards: Dict[str, TargetCard] = {}
        # Arşiv: kapatılan kartlar (son N)
        self._archive: List[Dict[str, Any]] = []
        self._archive_max = 200
        # Rate limiting: symbol → [(timestamp, card_id), ...]
        self._card_history: Dict[str, List[Tuple[float, str]]] = {}
        # İstatistikler
        self._stats = {
            "total_cards_created": 0,
            "total_hits": 0,
            "total_misses": 0,
            "total_near_misses": 0,
            "total_expired": 0,
            "total_superseded": 0,
        }

    # ─── Kart Oluşturma ───

    def create_target_card(
        self,
        *,
        symbol: str,
        direction: str,
        entry_price: float,
        confidence: float,
        target_pct: float,
        signal_id: Optional[int] = None,
        simulation_id: Optional[int] = None,
        data_density: float = 0.5,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Optional[TargetCard]:
        """
        Yeni hedef kartı oluştur.

        Rate Limit: Aynı coin için 1 saatte max 2 kart.
        Eski aktif kartlar superseded olarak işaretlenir.
        """
        symbol = symbol.upper()

        # ─── Rate limit kontrolü ───
        if not self._check_rate_limit(symbol):
            logger.info(
                f"🚫 TargetCard rate limit: {symbol} son 1 saatte "
                f"zaten {self.MAX_CARDS_PER_HOUR} kart açıldı"
            )
            return None

        # ─── Toplam kart limiti ───
        live_count = sum(1 for c in self._cards.values() if c.is_live)
        if live_count >= self.MAX_LIVE_CARDS:
            logger.info(f"🚫 TargetCard limit: {live_count}/{self.MAX_LIVE_CARDS} aktif kart")
            return None

        # ─── Aynı coin'deki eski kartları superseded yap ───
        self._supersede_existing_cards(symbol)

        # ─── Horizonları oluştur ───
        horizons = self._build_horizons(
            entry_price=entry_price,
            direction=direction,
            target_pct=target_pct,
            confidence=confidence,
            data_density=data_density,
        )

        # ─── Ana hedef: en yüksek strength'e sahip horizon ───
        primary = max(horizons, key=lambda h: h.strength)

        now = datetime.utcnow()
        max_eta = max(h.eta_minutes for h in horizons)
        card_id = f"tc:{symbol}:{int(now.timestamp())}"

        card = TargetCard(
            id=card_id,
            symbol=symbol,
            direction=direction,
            entry_price=entry_price,
            entry_time=now,
            signal_id=signal_id,
            simulation_id=simulation_id,
            confidence=confidence,
            primary_horizon=primary.label,
            horizons=horizons,
            max_expiry=now + timedelta(minutes=max_eta + 5),
            peak_price=entry_price,
            metadata=metadata or {},
        )

        self._cards[card_id] = card

        # Rate limit kaydı
        history = self._card_history.setdefault(symbol, [])
        history.append((time.time(), card_id))

        self._stats["total_cards_created"] += 1

        logger.info(
            f"🎯 TargetCard oluşturuldu: {symbol} {direction} "
            f"@ ${entry_price:,.2f} | Ana hedef: {primary.label} "
            f"(%{primary.target_pct * 100:.1f}) | "
            f"{len(horizons)} horizon aktif"
        )

        return card

    def _build_horizons(
        self,
        *,
        entry_price: float,
        direction: str,
        target_pct: float,
        confidence: float,
        data_density: float,
    ) -> List[HorizonTarget]:
        """4 zaman ufku hedefi oluştur. Veri yoğunluğu ve güvene göre filtrele."""
        base_target = max(abs(target_pct), 0.02)
        strength_base = min(max(confidence * 0.7 + data_density * 0.3, 0.0), 1.0)

        horizons: List[HorizonTarget] = []

        for label, eta_minutes, multiplier in HORIZONS:
            # Uzun vadeli horizonlar için minimum güç eşiği
            required_strength = {
                15: 0.0,
                60: 0.30,
                240: 0.40,
                1440: 0.50,
            }.get(eta_minutes, 0.30)

            if strength_base < required_strength:
                continue

            # Horizon'a özel strength: kısa vadeli daha güvenilir
            decay = {15: 1.0, 60: 0.9, 240: 0.75, 1440: 0.6}.get(eta_minutes, 0.7)
            horizon_strength = strength_base * decay

            horizon_target = min(max(base_target * multiplier, 0.02), 0.25)
            if direction == "long":
                target_price = entry_price * (1.0 + horizon_target)
            else:
                target_price = entry_price * (1.0 - horizon_target)

            horizons.append(HorizonTarget(
                label=label,
                eta_minutes=eta_minutes,
                target_pct=horizon_target,
                target_price=target_price,
                strength=round(horizon_strength, 4),
            ))

        # En az 15m horizon olmalı
        if not horizons:
            target_price = entry_price * (1.0 + base_target) if direction == "long" else entry_price * (1.0 - base_target)
            horizons.append(HorizonTarget(
                label="15m",
                eta_minutes=15,
                target_pct=base_target,
                target_price=target_price,
                strength=round(strength_base, 4),
            ))

        return horizons

    # ─── Fiyat Takibi ───

    def tick(self, symbol: str, current_price: float) -> List[Dict[str, Any]]:
        """
        Anlık fiyat güncellemesi — tüm aktif kartları kontrol et.

        Returns:
            Durum değişen kartların event listesi
        """
        events: List[Dict[str, Any]] = []
        now = datetime.utcnow()

        for card in list(self._cards.values()):
            if not card.is_live or card.symbol != symbol.upper():
                continue

            # Peak price takibi
            if card.direction == "long" and current_price > card.peak_price:
                card.peak_price = current_price
            elif card.direction == "short" and (card.peak_price == 0 or current_price < card.peak_price):
                card.peak_price = current_price

            # Peak PnL
            if card.direction == "long":
                card.peak_pnl_pct = max(card.peak_pnl_pct, (current_price - card.entry_price) / card.entry_price)
            else:
                card.peak_pnl_pct = max(card.peak_pnl_pct, (card.entry_price - current_price) / card.entry_price)

            # Her horizon'u kontrol et
            for horizon in card.horizons:
                if horizon.status != HorizonStatus.ACTIVE:
                    continue

                # Süre kontrolü
                horizon_expiry = card.entry_time + timedelta(minutes=horizon.eta_minutes)
                is_expired = now >= horizon_expiry

                # Hedefe yakınlık hesapla
                if card.direction == "long":
                    progress = (current_price - card.entry_price) / max(card.entry_price, 1e-8)
                    distance_to_target = (horizon.target_price - current_price) / max(horizon.target_price, 1e-8)
                else:
                    progress = (card.entry_price - current_price) / max(card.entry_price, 1e-8)
                    distance_to_target = (current_price - horizon.target_price) / max(horizon.target_price, 1e-8)

                # En yakın yaklaşma güncelle
                approach = progress / max(horizon.target_pct, 1e-8)
                horizon.closest_approach_pct = max(horizon.closest_approach_pct, approach)

                # ─── Hedef vuruldu mu? ───
                target_hit = False
                if card.direction == "long":
                    target_hit = current_price >= horizon.target_price
                else:
                    target_hit = current_price <= horizon.target_price

                if target_hit:
                    horizon.status = HorizonStatus.HIT
                    horizon.hit_time = now
                    self._stats["total_hits"] += 1
                    events.append({
                        "type": "horizon_hit",
                        "card_id": card.id,
                        "symbol": card.symbol,
                        "horizon": horizon.label,
                        "target_price": horizon.target_price,
                        "current_price": current_price,
                        "elapsed_minutes": (now - card.entry_time).total_seconds() / 60,
                    })
                    logger.info(
                        f"🎯 Horizon HIT: {card.symbol} {horizon.label} "
                        f"hedef ${horizon.target_price:,.4f} vuruldu @ ${current_price:,.4f}"
                    )
                    continue

                # ─── Near-miss kontrolü ───
                if not horizon.near_miss and abs(distance_to_target) <= NEAR_MISS_THRESHOLD:
                    horizon.near_miss = True
                    horizon.near_miss_pct = abs(distance_to_target)
                    self._stats["total_near_misses"] += 1
                    events.append({
                        "type": "near_miss",
                        "card_id": card.id,
                        "symbol": card.symbol,
                        "horizon": horizon.label,
                        "distance_pct": abs(distance_to_target),
                        "target_price": horizon.target_price,
                        "current_price": current_price,
                    })
                    logger.info(
                        f"⚡ Near-miss: {card.symbol} {horizon.label} "
                        f"hedefe %{abs(distance_to_target) * 100:.2f} kaldı"
                    )

                # ─── Süre doldu ───
                if is_expired:
                    if horizon.near_miss:
                        horizon.status = HorizonStatus.NEAR_MISS
                    else:
                        horizon.status = HorizonStatus.MISSED
                        self._stats["total_misses"] += 1
                    events.append({
                        "type": "horizon_expired",
                        "card_id": card.id,
                        "symbol": card.symbol,
                        "horizon": horizon.label,
                        "near_miss": horizon.near_miss,
                        "closest_approach_pct": horizon.closest_approach_pct,
                    })

            # ─── Kart bütünlüğü: tüm horizonlar kapandı mı? ───
            if not card.active_horizons:
                self._complete_card(card, "all_horizons_resolved")
                events.append({
                    "type": "card_completed",
                    "card_id": card.id,
                    "symbol": card.symbol,
                    "hits": card.hit_count,
                    "near_misses": card.near_miss_count,
                    "total_horizons": len(card.horizons),
                })

            # ─── Max süre doldu (24h + buffer) ───
            elif card.max_expiry and now >= card.max_expiry:
                # Kalan aktif horizonları expired yap
                for h in card.active_horizons:
                    if h.near_miss:
                        h.status = HorizonStatus.NEAR_MISS
                    else:
                        h.status = HorizonStatus.MISSED
                        self._stats["total_misses"] += 1
                self._complete_card(card, "max_expiry")
                self._stats["total_expired"] += 1
                events.append({
                    "type": "card_expired",
                    "card_id": card.id,
                    "symbol": card.symbol,
                })

        return events

    def tick_all(self, prices: Dict[str, float]) -> List[Dict[str, Any]]:
        """Tüm aktif semboller için fiyat güncellemesi."""
        all_events: List[Dict[str, Any]] = []
        for symbol, price in prices.items():
            if price > 0:
                all_events.extend(self.tick(symbol, price))
        return all_events

    # ─── Kart Yaşam Döngüsü ───

    def _complete_card(self, card: TargetCard, reason: str):
        """Kartı tamamla ve öğrenme arşivine aktar."""
        card.status = CardStatus.COMPLETED
        card.close_reason = reason
        card.close_time = datetime.utcnow()

        # Öğrenme arşivine ekle
        archive_entry = card.to_dict()
        archive_entry["learning"] = {
            "hit_rate": card.hit_count / max(len(card.horizons), 1),
            "near_miss_rate": card.near_miss_count / max(len(card.horizons), 1),
            "peak_pnl_pct": card.peak_pnl_pct,
            "duration_minutes": (card.close_time - card.entry_time).total_seconds() / 60,
            "primary_hit": any(
                h.status == HorizonStatus.HIT and h.label == card.primary_horizon
                for h in card.horizons
            ),
        }
        self._archive.append(archive_entry)
        if len(self._archive) > self._archive_max:
            self._archive = self._archive[-self._archive_max:]

        # AdaptiveEngine feedback
        if self.decision_core and hasattr(self.decision_core, "adaptive"):
            was_success = card.hit_count > 0
            self.decision_core.adaptive.record_outcome(
                "success" if was_success else "failure"
            )
            self.decision_core.adaptive._adjust_internal_parameters(
                "success" if was_success else "failure"
            )

            # Near-miss ise sensitivity artır (sıkı eşik → daha fazla near-miss → fırsatları kaçırıyoruz)
            if card.near_miss_count > 0 and card.hit_count == 0:
                adaptive = self.decision_core.adaptive
                adaptive._nudge("sensitivity", 0.01)
                adaptive._nudge("similarity_threshold", -0.005)
                logger.info(
                    f"🔧 Near-miss feedback: {card.symbol} — "
                    f"sensitivity +0.01, threshold -0.005"
                )

        logger.info(
            f"📦 TargetCard tamamlandı: {card.symbol} | "
            f"Sonuç: {card.hit_count}/{len(card.horizons)} hit, "
            f"{card.near_miss_count} near-miss | "
            f"Peak PnL: {card.peak_pnl_pct:+.2%} | "
            f"Sebep: {reason}"
        )

    def _supersede_existing_cards(self, symbol: str):
        """Aynı coin'deki eski aktif kartları geçersiz kıl."""
        for card in list(self._cards.values()):
            if card.symbol == symbol and card.is_live:
                card.status = CardStatus.SUPERSEDED
                card.close_reason = "superseded_by_new_card"
                card.close_time = datetime.utcnow()
                # Kalan aktif horizonları expired yap
                for h in card.active_horizons:
                    h.status = HorizonStatus.EXPIRED
                self._stats["total_superseded"] += 1

                # Arşive ekle
                archive_entry = card.to_dict()
                archive_entry["learning"] = {
                    "hit_rate": card.hit_count / max(len(card.horizons), 1),
                    "near_miss_rate": card.near_miss_count / max(len(card.horizons), 1),
                    "peak_pnl_pct": card.peak_pnl_pct,
                    "superseded": True,
                }
                self._archive.append(archive_entry)

                logger.info(f"♻️ TargetCard superseded: {card.id}")

    # ─── Rate Limiting ───

    def _check_rate_limit(self, symbol: str) -> bool:
        """Bir coin için son 1 saatte max MAX_CARDS_PER_HOUR kart."""
        history = self._card_history.get(symbol, [])
        cutoff = time.time() - 3600  # 1 saat
        # Eskilerini temizle
        recent = [(ts, cid) for ts, cid in history if ts >= cutoff]
        self._card_history[symbol] = recent
        return len(recent) < self.MAX_CARDS_PER_HOUR

    # ─── Sorgulama ───

    def get_live_cards(self) -> List[Dict[str, Any]]:
        """Aktif kartları döndür (dashboard için)."""
        return [
            card.to_dict()
            for card in self._cards.values()
            if card.is_live
        ]

    def get_card(self, card_id: str) -> Optional[Dict[str, Any]]:
        """Belirli bir kartı getir."""
        card = self._cards.get(card_id)
        return card.to_dict() if card else None

    def get_archive(self, limit: int = 50) -> List[Dict[str, Any]]:
        """Öğrenme arşivinden son N kartı getir."""
        return self._archive[-limit:]

    def get_stats(self) -> Dict[str, Any]:
        """Motor istatistikleri."""
        live_cards = sum(1 for c in self._cards.values() if c.is_live)
        return {
            **self._stats,
            "live_cards": live_cards,
            "archive_size": len(self._archive),
            "hit_rate": (
                self._stats["total_hits"]
                / max(self._stats["total_hits"] + self._stats["total_misses"], 1)
            ),
        }

    def get_symbol_cards(self, symbol: str) -> List[Dict[str, Any]]:
        """Belirli sembol için kartları getir."""
        return [
            card.to_dict()
            for card in self._cards.values()
            if card.symbol == symbol.upper()
        ]

    def cleanup_stale(self):
        """Kapalı kartları bellekten temizle (24 saatten eski)."""
        cutoff = datetime.utcnow() - timedelta(hours=24)
        to_remove = [
            cid for cid, card in self._cards.items()
            if not card.is_live and card.close_time and card.close_time < cutoff
        ]
        for cid in to_remove:
            del self._cards[cid]


# ─── Singleton ───
_engine: Optional[SimulationEngine] = None


def get_simulation_engine(**kwargs) -> SimulationEngine:
    global _engine
    if _engine is None:
        _engine = SimulationEngine(**kwargs)
    return _engine
