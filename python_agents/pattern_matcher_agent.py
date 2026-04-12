"""
PatternMatcherAgent — QuenBot V2 Euclidean Distance Pattern Matcher
=====================================================================
N-noktalı fiyat vektörleri üzerinde Euclidean distance ile geçmiş
önemli olaylara (historical_signatures) benzerlik hesaplar.

Akış:
  Scout (WebSocket) → DB (trades) → PatternMatcher
    → historical_signatures sorgusu
    → Euclidean distance hesaplama
    → similarity > 0.90 ⇒ PATTERN_MATCH event → Brain → RiskManager → Strategist

Benzerlik formülü:
  distance = ||V_current - V_historical|| (L2 norm)
  similarity = 1 / (1 + distance)   [0..1 aralığı, 1 = birebir eşleşme]

Karar mekanizması:
  Eşleşen signature'ın yönü (direction) ve büyüklüğü (change_pct)
  doğrudan tahmin olarak kullanılır. Birden fazla eşleşme varsa,
  similarity-weighted average uygulanır.
"""

import asyncio
import json
import logging
import time
from datetime import datetime, timedelta
from typing import Dict, List, Any, Optional, Tuple

import numpy as np

from config import Config
from database import Database
from event_bus import get_event_bus, Event, EventType

logger = logging.getLogger(__name__)

# Lazy LLM bridge import
_llm_bridge = None
def _get_llm_bridge():
    global _llm_bridge
    if _llm_bridge is None:
        try:
            from llm_bridge import get_llm_bridge
            _llm_bridge = get_llm_bridge()
        except Exception:
            _llm_bridge = None
    return _llm_bridge


# ─── Configuration ───
SIMILARITY_THRESHOLD = 0.90        # Minimum similarity to trigger a match
SCAN_INTERVAL_SECONDS = 300        # 5 dakikada bir tarama
VECTOR_POINTS = 60                 # N-point price vector (last N trade prices)
MIN_HISTORICAL_SIGNATURES = 3      # Minimum signatures needed for matching
MAX_MATCHES_PER_SCAN = 5           # Top-K matches to consider
TIMEFRAMES_TO_SCAN = ['5m', '15m', '1h']
COOLDOWN_SECONDS = 900             # 15dk cooldown — aynı sembol için çok sık sinyal önle


class PatternMatcherAgent:
    """
    Euclidean Distance tabanlı pattern eşleştirme ajanı.

    Scout'un topladığı real-time trade verilerinden N-noktalı fiyat vektörü
    oluşturur, DB'deki historical_signatures ile Euclidean distance hesaplar,
    eşik üstü benzerlik bulunursa Brain'e PATTERN_MATCH event'i gönderir.
    """

    def __init__(self, db: Database, brain=None):
        self.db = db
        self.brain = brain
        self.running = False
        self.last_activity: Optional[datetime] = None
        self.event_bus = get_event_bus()

        # Stats
        self.scan_count = 0
        self.match_count = 0
        self.total_comparisons = 0
        self.best_similarity_ever = 0.0
        self.last_matches: List[Dict] = []  # Son 20 eşleşme

        # Per-symbol cooldown tracker
        self._cooldowns: Dict[str, float] = {}

        # Signature cache (refresh periodically)
        self._signature_cache: Dict[str, List[Dict]] = {}
        self._cache_expiry: float = 0

    async def initialize(self):
        """Agent başlangıç — signature cache'i yükle"""
        await self._refresh_signature_cache()
        total = sum(len(v) for v in self._signature_cache.values())
        logger.info(f"🔍 PatternMatcher initialized — "
                    f"{total} historical signatures cached "
                    f"(threshold={SIMILARITY_THRESHOLD}, vector_points={VECTOR_POINTS})")

    async def start(self):
        """Ana döngü: periyodik olarak tüm watchlist sembollerini tara"""
        self.running = True
        logger.info("🔍 PatternMatcher agent started")

        while self.running:
            try:
                cycle_start = time.time()

                # Signature cache'i periyodik yenile (5 dakikada bir)
                if time.time() > self._cache_expiry:
                    await self._refresh_signature_cache()

                # Tüm watchlist sembollerini tara
                symbols = Config.WATCHLIST
                for symbol in symbols:
                    if not self.running:
                        break

                    # Cooldown kontrolü
                    if self._is_on_cooldown(symbol):
                        continue

                    await self._scan_symbol(symbol)
                    await asyncio.sleep(5)  # Semboller arası LLM boğulmasını önle

                self.scan_count += 1
                self.last_activity = datetime.utcnow()

                # Döngü süresi hesapla ve bekleme
                elapsed = time.time() - cycle_start
                wait = max(SCAN_INTERVAL_SECONDS - elapsed, 5)
                await asyncio.sleep(wait)

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"PatternMatcher cycle error: {e}")
                await asyncio.sleep(10)

        logger.info("🔍 PatternMatcher agent stopped")

    async def stop(self):
        self.running = False

    async def health_check(self) -> Dict[str, Any]:
        """Sağlık kontrolü"""
        total_sigs = sum(len(v) for v in self._signature_cache.values())
        return {
            'agent': 'PatternMatcher',
            'healthy': self.running and self.last_activity is not None,
            'running': self.running,
            'last_activity': self.last_activity.isoformat() if self.last_activity else None,
            'scan_count': self.scan_count,
            'match_count': self.match_count,
            'total_comparisons': self.total_comparisons,
            'best_similarity': round(self.best_similarity_ever, 4),
            'cached_signatures': total_sigs,
            'active_cooldowns': sum(1 for s, t in self._cooldowns.items()
                                    if time.time() < t),
            'last_matches': self.last_matches[-5:],
        }

    # ─── Core Logic ───

    async def _scan_symbol(self, symbol: str):
        """Tek bir sembol için pattern eşleştirme taraması"""
        for tf_key in TIMEFRAMES_TO_SCAN:
            try:
                # 1. Get historical signatures for this symbol+timeframe
                signatures = self._get_cached_signatures(symbol, tf_key)
                if len(signatures) < MIN_HISTORICAL_SIGNATURES:
                    continue

                # 2. Build current price vector from recent trades
                vector_result = await self._build_current_vector(symbol, tf_key)
                if not vector_result:
                    continue
                current_vector, current_price = vector_result
                if len(current_vector) < 10:
                    continue

                # 3. Compare against all historical signatures
                matches = self._find_euclidean_matches(current_vector, signatures)
                self.total_comparisons += len(signatures)

                if not matches:
                    continue

                # 4. Best match found — process it
                best_match = matches[0]
                similarity = best_match['similarity']

                if similarity > self.best_similarity_ever:
                    self.best_similarity_ever = similarity

                if similarity >= SIMILARITY_THRESHOLD:
                    await self._handle_match(symbol, tf_key, current_vector,
                                             matches, best_match, current_price)

            except Exception as e:
                logger.debug(f"PatternMatcher scan error {symbol}/{tf_key}: {e}")

    async def _build_current_vector(self, symbol: str, tf_key: str) -> Optional[Tuple[List[float], float]]:
        """
        Son N trade'den normalize edilmiş fiyat vektörü oluştur.
        Scout'un _build_movement_vector() ile aynı formatta:
          vector[i] = (price[i] - price[0]) / price[0]
        """
        try:
            trades = await self.db.get_recent_trades(symbol, limit=VECTOR_POINTS * 2)
            if not trades or len(trades) < 10:
                return None

            # Trades are returned DESC, reverse for chronological order
            trades = list(reversed(trades))

            # Take last N prices
            prices = [float(t['price']) for t in trades[-VECTOR_POINTS:]]
            if len(prices) < 10:
                return None

            # Normalize: relative change from first price
            base = prices[0]
            if base <= 0:
                return None

            vector = [(p - base) / base for p in prices]
            return vector, float(prices[-1])

        except Exception as e:
            logger.debug(f"Build vector error {symbol}: {e}")
            return None

    def _find_euclidean_matches(self, current_vector: List[float],
                                 signatures: List[Dict]) -> List[Dict]:
        """
        Euclidean distance ile benzerlik hesapla.

        Vektör uzunlukları farklı olabilir — kısa olanı uzununa hizala
        (sondan kırp veya baştan pad).

        Similarity = 1 / (1 + distance)
        """
        current = np.array(current_vector, dtype=np.float64)
        results = []

        for sig in signatures:
            try:
                hist_vector = sig.get('pre_move_vector', [])
                if not hist_vector or len(hist_vector) < 5:
                    continue

                hist = np.array(hist_vector, dtype=np.float64)

                # Align vector lengths — use the shorter length
                min_len = min(len(current), len(hist))
                # Take the LAST min_len points (most recent)
                c = current[-min_len:]
                h = hist[-min_len:]

                # Euclidean distance
                distance = float(np.linalg.norm(c - h))

                # Similarity score [0, 1]
                similarity = 1.0 / (1.0 + distance)

                results.append({
                    'signature_id': sig.get('id'),
                    'symbol': sig.get('symbol'),
                    'timeframe': sig.get('timeframe'),
                    'direction': sig.get('direction'),
                    'change_pct': float(sig.get('change_pct', 0)),
                    'similarity': similarity,
                    'euclidean_distance': distance,
                    'vector_length': min_len,
                    'volume_profile': sig.get('volume_profile', {}),
                    'pre_move_indicators': sig.get('pre_move_indicators', {}),
                })

            except Exception as e:
                logger.debug(f"Euclidean calc error: {e}")
                continue

        # Sort by similarity descending
        results.sort(key=lambda x: x['similarity'], reverse=True)
        return results[:MAX_MATCHES_PER_SCAN]

    async def _handle_match(self, symbol: str, tf_key: str,
                             current_vector: List[float],
                             matches: List[Dict], best: Dict,
                             current_price: float):
        """
        Eşik üstü eşleşme bulundu — Brain'e bildir ve DB'ye kaydet.

        Karar mekanizması:
        - best match'in direction (up/down) → predicted direction
        - best match'in change_pct → predicted magnitude
        - Birden fazla eşleşme varsa weighted average
        """
        self.match_count += 1

        # Weighted prediction from top matches
        prediction = self._compute_weighted_prediction(matches)

        # Confidence: similarity * match_count factor
        match_factor = min(len(matches) / 3.0, 1.0)
        confidence = best['similarity'] * 0.7 + match_factor * 0.3

        logger.info(
            f"🎯 PATTERN MATCH [{tf_key}] {symbol} | "
            f"Similarity: {best['similarity']:.4f} | "
            f"Distance: {best['euclidean_distance']:.6f} | "
            f"Predicted: {prediction['direction']} {prediction['magnitude']:+.2%} | "
            f"Confidence: {confidence:.2%} | "
            f"Top {len(matches)} matches"
        )

        # Save to DB
        match_data = {
            'symbol': symbol,
            'timeframe': tf_key,
            'matched_signature_id': best['signature_id'],
            'similarity': best['similarity'],
            'euclidean_distance': best['euclidean_distance'],
            'matched_direction': best['direction'],
            'matched_change_pct': best['change_pct'],
            'predicted_direction': prediction['direction'],
            'predicted_magnitude': prediction['magnitude'],
            'current_vector': current_vector[-20:],  # Son 20 nokta kaydet
            'confidence': confidence,
            'current_price': current_price,
        }

        try:
            match_id = await self.db.insert_pattern_match_result(match_data)
            match_data['match_id'] = match_id
        except Exception as e:
            logger.error(f"Pattern match DB save error: {e}")
            match_id = None

        # Store in recent matches
        self.last_matches.append({
            'symbol': symbol,
            'timeframe': tf_key,
            'similarity': round(best['similarity'], 4),
            'direction': prediction['direction'],
            'magnitude': round(prediction['magnitude'], 4),
            'confidence': round(confidence, 4),
            'timestamp': datetime.utcnow().isoformat(),
        })
        if len(self.last_matches) > 20:
            self.last_matches = self.last_matches[-20:]

        # Set cooldown for this symbol
        self._cooldowns[symbol] = time.time() + COOLDOWN_SECONDS

        # Publish PATTERN_MATCH event → Brain
        await self.event_bus.publish(Event(
            type=EventType.PATTERN_MATCH,
            source="pattern_matcher",
            data={
                'match_id': match_id,
                'symbol': symbol,
                'timeframe': tf_key,
                'similarity': best['similarity'],
                'euclidean_distance': best['euclidean_distance'],
                'predicted_direction': prediction['direction'],
                'predicted_magnitude': prediction['magnitude'],
                'current_price': current_price,
                'confidence': confidence,
                'matched_signature_id': best['signature_id'],
                'matched_change_pct': best['change_pct'],
                'matched_direction': best['direction'],
                'match_count': len(matches),
                'top_matches': matches[:3],  # Top 3 detayı
                'indicators': best.get('pre_move_indicators', {}),
                'volume_profile': best.get('volume_profile', {}),
            },
        ))

    def _compute_weighted_prediction(self, matches: List[Dict]) -> Dict[str, Any]:
        """
        Birden fazla eşleşmeden similarity-weighted tahmin hesapla.

        direction: up/down — çoğunluk ve ağırlık
        magnitude: weighted average of change_pct values
        """
        if not matches:
            return {'direction': 'neutral', 'magnitude': 0.0}

        total_weight = 0.0
        weighted_change = 0.0
        up_weight = 0.0
        down_weight = 0.0

        for m in matches:
            w = m['similarity']
            total_weight += w
            weighted_change += m['change_pct'] * w

            if m['direction'] == 'up':
                up_weight += w
            else:
                down_weight += w

        if total_weight == 0:
            return {'direction': 'neutral', 'magnitude': 0.0}

        avg_change = weighted_change / total_weight
        direction = 'up' if up_weight > down_weight else 'down'

        return {
            'direction': direction,
            'magnitude': avg_change,
            'up_weight': up_weight,
            'down_weight': down_weight,
            'avg_change': avg_change,
        }

    # ─── Cache Management ───

    async def _refresh_signature_cache(self):
        """Historical signatures cache'ini yenile (5 dakikada bir)"""
        try:
            all_sigs = await self.db.get_historical_signatures(limit=1000)
            cache: Dict[str, List[Dict]] = {}

            for sig in all_sigs:
                # Parse JSONB fields if needed
                if isinstance(sig.get('pre_move_vector'), str):
                    sig['pre_move_vector'] = json.loads(sig['pre_move_vector'])
                if isinstance(sig.get('pre_move_indicators'), str):
                    sig['pre_move_indicators'] = json.loads(sig['pre_move_indicators'])
                if isinstance(sig.get('volume_profile'), str):
                    sig['volume_profile'] = json.loads(sig['volume_profile'])

                key = f"{sig['symbol']}_{sig['timeframe']}"
                if key not in cache:
                    cache[key] = []
                cache[key].append(sig)

            self._signature_cache = cache
            self._cache_expiry = time.time() + 300  # 5 minutes
            total = sum(len(v) for v in cache.values())
            logger.debug(f"🔍 Signature cache refreshed: {total} signatures, "
                        f"{len(cache)} symbol/timeframe groups")

        except Exception as e:
            logger.error(f"Signature cache refresh error: {e}")
            self._cache_expiry = time.time() + 60  # Retry in 1 min

    def _get_cached_signatures(self, symbol: str, tf_key: str) -> List[Dict]:
        """Cache'den signature'ları getir"""
        key = f"{symbol}_{tf_key}"
        sigs = self._signature_cache.get(key, [])

        # Cross-symbol matching: aynı timeframe'de başka sembollerden de bak
        # (en az MIN_HISTORICAL_SIGNATURES yoksa)
        if len(sigs) < MIN_HISTORICAL_SIGNATURES:
            for cache_key, cache_sigs in self._signature_cache.items():
                if cache_key.endswith(f"_{tf_key}") and cache_key != key:
                    sigs.extend(cache_sigs)
                    if len(sigs) >= MIN_HISTORICAL_SIGNATURES * 3:
                        break

        return sigs

    def _is_on_cooldown(self, symbol: str) -> bool:
        """Symbol cooldown kontrolü"""
        expiry = self._cooldowns.get(symbol, 0)
        return time.time() < expiry

    # ─── Deep Analysis (Brain ile birlikte) ───

    async def deep_analyze_symbol(self, symbol: str) -> Dict[str, Any]:
        """
        Tek bir sembol için derinlemesine pattern analizi.
        Chat engine'den veya Brain'den çağrılabilir.
        """
        results = {}
        for tf_key in TIMEFRAMES_TO_SCAN:
            vector_result = await self._build_current_vector(symbol, tf_key)
            if not vector_result:
                results[tf_key] = {'status': 'insufficient_data'}
                continue
            current_vector, _ = vector_result

            signatures = self._get_cached_signatures(symbol, tf_key)
            if not signatures:
                results[tf_key] = {'status': 'no_signatures'}
                continue

            matches = self._find_euclidean_matches(current_vector, signatures)
            if not matches:
                results[tf_key] = {'status': 'no_match', 'comparisons': len(signatures)}
                continue

            best = matches[0]
            prediction = self._compute_weighted_prediction(matches)

            results[tf_key] = {
                'status': 'matched' if best['similarity'] >= SIMILARITY_THRESHOLD else 'below_threshold',
                'best_similarity': round(best['similarity'], 4),
                'best_distance': round(best['euclidean_distance'], 6),
                'predicted_direction': prediction['direction'],
                'predicted_magnitude': round(prediction['magnitude'], 4),
                'match_count': len(matches),
                'comparisons': len(signatures),
                'top_matches': [{
                    'similarity': round(m['similarity'], 4),
                    'direction': m['direction'],
                    'change_pct': round(m['change_pct'], 4),
                } for m in matches[:3]],
            }

        return {
            'symbol': symbol,
            'timestamp': datetime.utcnow().isoformat(),
            'timeframes': results,
            'overall_signal': self._compute_overall_signal(results),
        }

    def _compute_overall_signal(self, timeframe_results: Dict) -> Dict[str, Any]:
        """Tüm timeframe sonuçlarından genel sinyal hesapla"""
        directions = []
        magnitudes = []
        similarities = []

        for tf, data in timeframe_results.items():
            if data.get('status') == 'matched':
                directions.append(data['predicted_direction'])
                magnitudes.append(data['predicted_magnitude'])
                similarities.append(data['best_similarity'])

        if not directions:
            return {'direction': 'neutral', 'confidence': 0, 'reason': 'no_matches'}

        up_count = directions.count('up')
        down_count = directions.count('down')
        direction = 'up' if up_count > down_count else 'down' if down_count > up_count else 'neutral'

        avg_sim = sum(similarities) / len(similarities)
        avg_mag = sum(magnitudes) / len(magnitudes)
        agreement = max(up_count, down_count) / len(directions)

        return {
            'direction': direction,
            'confidence': round(avg_sim * agreement, 4),
            'avg_similarity': round(avg_sim, 4),
            'avg_magnitude': round(avg_mag, 4),
            'timeframe_agreement': round(agreement, 2),
            'matched_timeframes': len(directions),
        }
