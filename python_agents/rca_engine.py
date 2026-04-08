"""
RCA Engine - Root Cause Analysis
==================================
Başarısız trade'lerin NEDEN başarısız olduğunu sınıflandırır.
Auditor agent tarafından kullanılır.
"""
import logging
from datetime import datetime
from typing import Dict, Any, List, Optional

logger = logging.getLogger(__name__)

# Failure categories
FAILURE_TYPES = {
    'FALSE_BREAKOUT': 'Fiyat breakout gibi göründü ama geri döndü',
    'LIQUIDITY_TRAP': 'Düşük hacimde sahte hareket (wick/sweep)',
    'TREND_REVERSAL': 'Ana trend tersine döndü',
    'LOW_VOLUME_NOISE': 'Düşük hacim, anlamsız fiyat hareketi',
    'STOP_HUNT': 'Hızlı wick ile SL tetiklendi, sonra asıl yöne gitti',
    'OVEREXTENDED': 'RSI/momentum aşırı bölgede sinyal (overbought short vb.)',
    'BAD_TIMING': 'Signal sonrası çok geç pozisyon açıldı',
    'HIGH_SPREAD': 'Yüksek volatilite spread etkisi',
    'UNKNOWN': 'Sınıflandırılamadı',
}


class RCAEngine:
    """Root Cause Analysis - Başarısız trade analizi"""

    def __init__(self, db):
        self.db = db
        self.failure_stats: Dict[str, int] = {k: 0 for k in FAILURE_TYPES}
        self.total_analyzed = 0

    async def analyze_failure(self, simulation: Dict[str, Any],
                                signal: Optional[Dict] = None) -> Dict[str, Any]:
        """
        Tek bir başarısız simülasyonu analiz et.
        Returns: {failure_type, confidence, explanation, recommendations}
        """
        try:
            entry_price = float(simulation.get('entry_price', 0))
            exit_price = float(simulation.get('exit_price', 0))
            side = simulation.get('side', 'long')
            symbol = simulation.get('symbol', '')
            pnl_pct = float(simulation.get('pnl_pct', 0))
            entry_time = simulation.get('entry_time')
            exit_time = simulation.get('exit_time')

            metadata = simulation.get('metadata', {})
            if isinstance(metadata, str):
                import json
                metadata = json.loads(metadata)

            # Context verileri topla
            context = await self._gather_context(symbol, entry_time, exit_time, side)

            # Failure classification
            failure_type, confidence, explanation = self._classify_failure(
                entry_price, exit_price, side, pnl_pct, metadata, context
            )

            # Recommendations
            recommendations = self._generate_recommendations(failure_type, context, metadata)

            self.failure_stats[failure_type] = self.failure_stats.get(failure_type, 0) + 1
            self.total_analyzed += 1

            result = {
                'failure_type': failure_type,
                'failure_description': FAILURE_TYPES.get(failure_type, ''),
                'confidence': confidence,
                'explanation': explanation,
                'recommendations': recommendations,
                'context': {
                    'symbol': symbol,
                    'side': side,
                    'entry_price': entry_price,
                    'exit_price': exit_price,
                    'pnl_pct': pnl_pct,
                    'signal_type': metadata.get('signal_type', 'unknown'),
                },
            }

            logger.info(f"🔍 RCA: {symbol} → {failure_type} (conf={confidence:.2f})")
            return result

        except Exception as e:
            logger.error(f"RCA analysis error: {e}")
            return {
                'failure_type': 'UNKNOWN',
                'confidence': 0,
                'explanation': f'Analiz hatası: {str(e)[:100]}',
                'recommendations': [],
            }

    async def _gather_context(self, symbol: str, entry_time, exit_time,
                                side: str) -> Dict[str, Any]:
        """Trade etrafındaki market context verileri"""
        context = {
            'pre_volume': 0,
            'post_volume': 0,
            'price_range': 0,
            'max_adverse': 0,
            'recovery_pct': 0,
        }
        try:
            if entry_time and exit_time:
                # Entry öncesi trade'leri
                pre_trades = await self.db.get_recent_trades(symbol, limit=100)
                if pre_trades:
                    prices = [float(t['price']) for t in pre_trades]
                    volumes = [float(t['quantity']) * float(t['price']) for t in pre_trades]
                    context['pre_volume'] = sum(volumes)
                    context['price_range'] = (max(prices) - min(prices)) / max(min(prices), 1e-8)

                    # Max adverse excursion
                    entry_idx = 0
                    if side == 'long':
                        context['max_adverse'] = (min(prices[entry_idx:]) - prices[entry_idx]) / max(prices[entry_idx], 1e-8)
                    else:
                        context['max_adverse'] = (max(prices[entry_idx:]) - prices[entry_idx]) / max(prices[entry_idx], 1e-8)
        except Exception as e:
            logger.debug(f"Context gather error: {e}")

        return context

    def _classify_failure(self, entry: float, exit: float, side: str,
                           pnl_pct: float, metadata: Dict,
                           context: Dict) -> tuple:
        """
        Failure tipini belirle.
        Returns: (failure_type, confidence, explanation)
        """
        signal_type = metadata.get('signal_type', '').lower()
        confidence_val = metadata.get('signal_confidence', 0)
        bootstrap = metadata.get('bootstrap', False)

        price_range = context.get('price_range', 0)
        max_adverse = context.get('max_adverse', 0)
        pre_volume = context.get('pre_volume', 0)

        # 1. STOP_HUNT: Wick > 2x normal range, sonra recovery
        if abs(max_adverse) > price_range * 2 and abs(pnl_pct) < 5:
            return 'STOP_HUNT', 0.7, (
                f"Fiyat entry'den {'aşağı' if side == 'long' else 'yukarı'} "
                f"sert bir wick attı (max adverse: {max_adverse:.4f}). "
                f"SL tetiklendikten sonra fiyat dönmüş olabilir."
            )

        # 2. LOW_VOLUME_NOISE: Düşük hacim
        if pre_volume < 1000:  # Çok düşük hacim
            return 'LOW_VOLUME_NOISE', 0.65, (
                f"İşlem hacmi çok düşük (${pre_volume:.0f}). "
                f"Düşük hacimde fiyat hareketleri güvenilir değil."
            )

        # 3. FALSE_BREAKOUT: Similarity-based signal ama ters hareket
        if 'similarity' in signal_type or 'evolutionary' in signal_type:
            return 'FALSE_BREAKOUT', 0.6, (
                f"Geçmiş pattern'a benzeyen hareket tekrar etmedi. "
                f"Pattern similarity yüksek ama piyasa farklı tepki verdi."
            )

        # 4. OVEREXTENDED: Momentum signal, zaten aşırı hareket sonrası
        if 'momentum' in signal_type and abs(pnl_pct) > 2:
            return 'OVEREXTENDED', 0.55, (
                f"Momentum sinyali zaten uzanmış bir hareketten sonra geldi. "
                f"Fiyat aşırı alım/satım bölgesinde dönüş yaptı."
            )

        # 5. TREND_REVERSAL: Brain sinyali ama ana trend değişmiş
        if 'brain' in signal_type:
            return 'TREND_REVERSAL', 0.5, (
                f"Brain pattern eşleşmesi buldu ama piyasa modu değişmiş. "
                f"Makro trend tersine döndü."
            )

        # 6. BAD_TIMING: Bootstrap/düşük güven
        if bootstrap or confidence_val < 0.4:
            return 'BAD_TIMING', 0.45, (
                f"Düşük güvenli sinyal ({confidence_val:.2f}). "
                f"Bootstrap modda zamanlama eksikliği normal."
            )

        return 'UNKNOWN', 0.3, f'PnL: {pnl_pct:.2f}% | Signal: {signal_type}'

    def _generate_recommendations(self, failure_type: str,
                                    context: Dict, metadata: Dict) -> List[str]:
        """Failure tipine göre öneriler"""
        recs = {
            'FALSE_BREAKOUT': [
                'Similarity threshold artır (daha seçici ol)',
                'Volume confirmation ekle (breakout + volume)',
                'ATR-based SL kullan (daha geniş)',
            ],
            'LIQUIDITY_TRAP': [
                'Minimum volume filtresi ekle',
                'Sadece yüksek hacimli çiftlerde trade aç',
                'Order book depth kontrolü ekle',
            ],
            'TREND_REVERSAL': [
                'Daha uzun timeframe trend filtresi ekle',
                'Brain pattern\'larını macro trend ile filtrele',
                'Trailing SL kullan',
            ],
            'LOW_VOLUME_NOISE': [
                'Minimum volume threshold yükselt',
                f'Bu coin için min volume: ${context.get("pre_volume", 0)*3:.0f}',
                'Düşük hacimli dönemlerde sinyal üretme',
            ],
            'STOP_HUNT': [
                'SL\'yi ATR-based yap (sabit % yerine)',
                'Partial SL kullan (yarı pozisyonu kapat)',
                'Wick zonu dışına SL koy',
            ],
            'OVEREXTENDED': [
                'RSI filtresi ekle (>70 long açma, <30 short açma)',
                'Momentum sinyallerinde mean-reversion riski kontrol et',
                'TP\'yi daralt (erken kar al)',
            ],
            'BAD_TIMING': [
                'Bootstrap modda daha az agresif ol',
                'Sinyal gecikmesini azalt',
                'Minimum güven eşiğini yükselt',
            ],
            'UNKNOWN': [
                'Daha fazla veri toplanması gerekiyor',
                'Manuel inceleme önerilir',
            ],
        }
        return recs.get(failure_type, recs['UNKNOWN'])

    async def batch_analyze(self, simulations: List[Dict]) -> Dict[str, Any]:
        """
        Toplu failure analizi.
        Returns: kategori bazlı istatistikler ve genel öneriler.
        """
        results = []
        for sim in simulations:
            if float(sim.get('pnl', 0)) < 0:
                result = await self.analyze_failure(sim)
                results.append(result)

        if not results:
            return {'total': 0, 'categories': {}, 'top_recommendations': []}

        # Kategori bazlı toplam
        categories = {}
        all_recs = []
        for r in results:
            ft = r['failure_type']
            if ft not in categories:
                categories[ft] = {'count': 0, 'total_loss': 0, 'avg_confidence': 0}
            categories[ft]['count'] += 1
            categories[ft]['total_loss'] += abs(r['context'].get('pnl_pct', 0))
            categories[ft]['avg_confidence'] += r['confidence']
            all_recs.extend(r['recommendations'])

        for cat in categories.values():
            if cat['count'] > 0:
                cat['avg_confidence'] /= cat['count']
                cat['avg_loss'] = cat['total_loss'] / cat['count']

        # En sık öneriler
        rec_counts = {}
        for rec in all_recs:
            rec_counts[rec] = rec_counts.get(rec, 0) + 1
        top_recs = sorted(rec_counts.items(), key=lambda x: x[1], reverse=True)[:5]

        return {
            'total_analyzed': len(results),
            'categories': categories,
            'top_recommendations': [r[0] for r in top_recs],
            'failure_distribution': {k: v['count'] for k, v in categories.items()},
        }

    def get_stats(self) -> Dict[str, Any]:
        """RCA istatistikleri"""
        return {
            'total_analyzed': self.total_analyzed,
            'failure_distribution': dict(self.failure_stats),
            'top_failure': max(self.failure_stats, key=self.failure_stats.get) if self.total_analyzed > 0 else None,
        }
