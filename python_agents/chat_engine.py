"""
Chat Engine - QuenBot Doğal Dil Yanıt Motoru
=============================================
Gemma LLM destekli serbest komut sistemi.
Kullanıcının doğal dilde verdiği her komutu Gemma yorumlar,
ilgili agentlara iş atar ve sonucu raporlar.
Turkish-first, asenkron, context-aware.
"""
import asyncio
import json
import logging
import re
import time
from datetime import datetime, timedelta
from typing import Dict, List, Any, Optional

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


# ─── Gemma Director System Prompt ───
DIRECTOR_SYSTEM_PROMPT = """Sen QuenBot AI'ın Müdürüsün. Kullanıcının doğal dilde verdiği her komutu anlayıp ilgili agentlara iş dağıtırsın.

SİSTEM AGENTLARI:
- Scout: Piyasa verisi toplama, anomali tespiti, coin takibi
- Strategist: Sinyal üretimi, strateji analizi, teknik göstergeler
- GhostSimulator: Paper trading simülasyonu, pozisyon yönetimi
- Auditor: Kalite kontrol, hata analizi, performans değerlendirme
- Brain: Pattern öğrenme, tahmin, merkezi zeka koordinasyonu
- RiskManager: Risk yönetimi (stop loss, position sizing, drawdown limitleri)
- StateTracker: Bot modu (BOOTSTRAP/LEARNING/WARMUP/PRODUCTION)

YAPABILECEĞIN EYLEMLER:
1. strategy_update: Strateji parametrelerini değiştir (agresif/defansif/dengeli)
2. risk_update: Risk parametrelerini güncelle (stop loss, take profit, max trade vs.)
3. watchlist_add: Coin takibe al
4. watchlist_remove: Coin takipten çıkar
5. analyze_symbol: Belirli bir coini detaylı analiz et
6. force_scan: Tüm coinleri acil tara
7. close_position: Açık simülasyonu kapat
8. set_mode: Bot modunu değiştir
9. brain_insight: Brain'den mevcut piyasa değerlendirmesi iste
10. status_report: Detaylı sistem raporu oluştur
11. general_chat: Genel sohbet / bilgi verme

KULLANICI MESAJI ANALİZİ:
- Kullanıcı Türkçe veya İngilizce yazabilir
- Doğal dilde verilen komutları çözümle
- Birden fazla eylem gerekiyorsa tümünü listele

CEVAP FORMATI (JSON):
{
  "understood": true,
  "user_intent": "kullanıcının ne istediğinin kısa özeti",
  "actions": [
    {
      "type": "eylem_tipi",
      "target": "hedef agent veya sembol",
      "params": {},
      "explanation": "neden bu eylemi yapıyoruz"
    }
  ],
  "response_to_user": "Kullanıcıya Türkçe kısa bilgi"
}"""


class ChatEngine:
    """Gemma LLM destekli doğal dil chat motoru - tüm sisteme erişim"""

    def __init__(self, db, brain, agents: Dict[str, Any] = None):
        self.db = db
        self.brain = brain
        self.agents = agents or {}
        self.state_tracker = None
        self.risk_manager = None
        self.rca_engine = None
        self._context_cache: Dict[str, Any] = {}
        self._cache_time: Optional[datetime] = None
        self._cache_ttl = 10  # saniye

    def register_agent(self, name: str, agent):
        self.agents[name] = agent

    async def _refresh_context(self, force=False):
        """Sistem context'ini önbelleğe al (hızlı yanıt için)"""
        now = datetime.utcnow()
        if not force and self._cache_time and (now - self._cache_time).seconds < self._cache_ttl:
            return
        try:
            summary = await self.db.get_dashboard_summary()
            prices_raw = await self._get_live_prices()
            brain_status = self.brain.get_brain_status() if self.brain else {}
            open_sims = await self.db.get_open_simulations()
            pending_signals = await self.db.get_pending_signals()
            agent_health = {}
            for name, agent in self.agents.items():
                try:
                    agent_health[name] = await agent.health_check()
                except:
                    agent_health[name] = {"healthy": False}

            self._context_cache = {
                "summary": summary,
                "prices": prices_raw,
                "brain": brain_status,
                "open_sims": open_sims,
                "pending_signals": pending_signals,
                "agent_health": agent_health,
                "timestamp": now.isoformat(),
            }
            self._cache_time = now
        except Exception as e:
            logger.error(f"Context refresh error: {e}")

    async def _get_live_prices(self) -> Dict[str, float]:
        """Son fiyatları çek"""
        prices = {}
        try:
            from config import Config
            for sym in Config.WATCHLIST:
                trades = await self.db.get_recent_trades(sym, limit=1)
                if trades:
                    prices[sym] = float(trades[0]['price'])
        except:
            pass
        return prices

    async def respond(self, message: str) -> str:
        """Ana yanıt metodu - doğal dil girişi, akıllı yanıt"""
        msg = message.strip()
        if not msg:
            return "Mesaj boş. Ne sormak istiyorsun?"

        # Context'i güncelle (cache ile hızlı)
        await self._refresh_context()
        ctx = self._context_cache
        msg_lower = msg.lower()

        try:
            # İntent analizi - birden fazla intent destekle
            intents = self._detect_intents(msg_lower)

            if not intents:
                # Genel soru - tüm sistemi özetle
                return await self._general_response(msg, ctx)

            parts = []
            for intent in intents:
                part = await self._handle_intent(intent, msg, msg_lower, ctx)
                if part:
                    parts.append(part)

            if parts:
                return "\n\n".join(parts)

            return await self._general_response(msg, ctx)

        except Exception as e:
            logger.error(f"Chat respond error: {e}")
            return f"Bir hata oluştu: {str(e)[:200]}"

    def _detect_intents(self, msg: str) -> List[str]:
        """Mesajdan intent'leri çıkar"""
        intents = []
        patterns = {
            "price": r"fiyat|price|kaç|ne\s*kadar|kur|dolar|usd",
            "status": r"durum|status|nasıl|naber|sistem|genel|çalış",
            "signal": r"sinyal|signal|alarm|uyarı|fırsat",
            "sim": r"simülasyon|simulation|sim|trade|pozisyon|işlem|açık|ghost",
            "brain": r"beyin|brain|öğren|learn|zeka|akıl|pattern|tahmin|doğruluk|accuracy",
            "flow": r"order\s*flow|akış|alış.*satış|satış.*alış|baskı|pressure|hacim|volume",
            "watchlist": r"watchlist|izleme|liste|coin|takip|ekle|kaldır|sil",
            "agent": r"agent|bot|sağlık|health|scout|strategist|auditor",
            "perf": r"performans|başarı|kazanç|kayıp|win|loss|kar|zarar|pnl",
            "pattern_match": r"pattern\s*match|eşleş|benzerlik|euclidean|similarity|kalıp\s*eşleş",
            "help": r"yardım|help|ne yapabil|komut|command",
            "market": r"piyasa|market|trend|yüksel|düş|boğa|ayı|bull|bear",
            "data": r"veri|data|trade.*sayı|kaç.*trade|istatistik|stat",
            "config": r"ayar|config|threshold|eşik|parametre|setting",
            "risk": r"risk|risik|drawdown|kayıp\s*limit|günlük\s*limit|cooldown|pozisyon\s*boyut|kelly",
            "state": r"state|durum|mod|mode|bootstrap|learning|warmup|production|faz",
            "rca": r"rca|root.*cause|neden.*başarısız|neden.*kaybetti|hata.*analiz|failure.*analysis",
        }
        for intent, pattern in patterns.items():
            if re.search(pattern, msg):
                intents.append(intent)
        return intents

    def _extract_symbols(self, msg: str) -> List[str]:
        """Mesajdan coin sembollerini çıkar"""
        from config import Config
        symbols = []
        msg_upper = msg.upper()
        for sym in Config.WATCHLIST:
            short = sym.replace("USDT", "")
            if short in msg_upper or sym in msg_upper:
                symbols.append(sym)
        # Kısa adlarla da eşle
        aliases = {"BTC": "BTCUSDT", "ETH": "ETHUSDT", "BNB": "BNBUSDT",
                   "SOL": "SOLUSDT", "ADA": "ADAUSDT", "DOT": "DOTUSDT",
                   "LINK": "LINKUSDT", "LTC": "LTCUSDT", "XRP": "XRPUSDT", "BCH": "BCHUSDT",
                   "BITCOIN": "BTCUSDT", "ETHEREUM": "ETHUSDT", "SOLANA": "SOLUSDT",
                   "RIPPLE": "XRPUSDT", "CARDANO": "ADAUSDT", "POLKADOT": "DOTUSDT"}
        for alias, sym in aliases.items():
            if alias in msg_upper and sym not in symbols:
                symbols.append(sym)
        return symbols

    async def _handle_intent(self, intent: str, msg: str, msg_lower: str, ctx: dict) -> Optional[str]:
        handlers = {
            "price": self._handle_price,
            "status": self._handle_status,
            "signal": self._handle_signal,
            "sim": self._handle_sim,
            "brain": self._handle_brain,
            "flow": self._handle_flow,
            "watchlist": self._handle_watchlist,
            "agent": self._handle_agent,
            "perf": self._handle_perf,
            "pattern_match": self._handle_pattern_match,
            "help": self._handle_help,
            "market": self._handle_market,
            "data": self._handle_data,
            "config": self._handle_config,
            "risk": self._handle_risk,
            "state": self._handle_state,
            "rca": self._handle_rca,
        }
        handler = handlers.get(intent)
        if handler:
            return await handler(msg, msg_lower, ctx)
        return None

    async def _handle_price(self, msg, msg_lower, ctx) -> str:
        symbols = self._extract_symbols(msg_lower)
        prices = ctx.get("prices", {})

        if symbols:
            lines = ["💰 **Fiyat Bilgisi**\n"]
            for sym in symbols:
                p = prices.get(sym)
                if p:
                    # Son hareket bilgisi
                    movements = await self.db.get_recent_movements(sym, hours=1)
                    change_info = ""
                    if movements:
                        last_m = movements[0]
                        change_info = f" | Değişim: {float(last_m['change_pct'])*100:.2f}%"
                    lines.append(f"**{sym.replace('USDT', '')}/USDT**: ${p:,.2f}{change_info}")
                else:
                    lines.append(f"**{sym}**: Veri yok")
            return "\n".join(lines)
        else:
            if not prices:
                return "💰 Henüz fiyat verisi toplanmadı. Scout agent veri çekmeye devam ediyor..."
            lines = ["💰 **Tüm Fiyatlar**\n"]
            for sym, p in sorted(prices.items()):
                lines.append(f"• **{sym.replace('USDT', '')}**: ${p:,.2f}")
            return "\n".join(lines)

    async def _handle_status(self, msg, msg_lower, ctx) -> str:
        s = ctx.get("summary", {})
        b = ctx.get("brain", {})
        ah = ctx.get("agent_health", {})
        prices = ctx.get("prices", {})

        agents_ok = sum(1 for v in ah.values() if v.get("healthy"))
        agents_total = len(ah)

        lines = [
            "📊 **QuenBot Sistem Durumu**\n",
            f"**Veri**: {s.get('total_trades', 0):,} trade kayıtlı",
            f"**Sinyaller**: {s.get('active_signals', 0)} aktif bekleyen",
            f"**Simülasyonlar**: {s.get('open_simulations', 0)} açık pozisyon",
            f"**PnL**: ${s.get('total_pnl', 0):.2f} (kapalı sim. toplamı)",
            f"**Win Rate**: %{s.get('win_rate', 0):.1f}",
            f"**AI Brain**: {b.get('total_patterns', 0)} pattern | %{b.get('accuracy', 0)*100:.0f} doğruluk",
            f"**Agent'lar**: {agents_ok}/{agents_total} aktif",
            f"**İzlenen Coin**: {len(prices)} adet",
            f"\nTüm sistemler {'✅ çalışıyor' if agents_ok == agents_total else '⚠ kısmen çalışıyor'}",
        ]
        return "\n".join(lines)

    async def _handle_signal(self, msg, msg_lower, ctx) -> str:
        signals = ctx.get("pending_signals", [])
        symbols = self._extract_symbols(msg_lower)
        if symbols:
            signals = [s for s in signals if s['symbol'] in symbols]

        if not signals:
            return "📡 Şu an aktif sinyal bulunmuyor. Strategist agent piyasayı analiz etmeye devam ediyor."

        lines = [f"📡 **{len(signals)} Aktif Sinyal**\n"]
        for s in signals[:10]:
            conf = float(s.get('confidence', 0)) * 100
            meta = s.get('metadata', {})
            if isinstance(meta, str):
                meta = json.loads(meta)
            direction = meta.get('position_bias', '?')
            tf = meta.get('timeframe', '')
            lines.append(
                f"• **{s['symbol']}** | {direction.upper()} | "
                f"Güven: %{conf:.0f} | ${float(s['price']):,.2f} | "
                f"{s.get('signal_type', '')} {f'({tf})' if tf else ''}"
            )
        return "\n".join(lines)

    async def _handle_sim(self, msg, msg_lower, ctx) -> str:
        open_sims = ctx.get("open_sims", [])
        symbols = self._extract_symbols(msg_lower)
        if symbols:
            open_sims = [s for s in open_sims if s['symbol'] in symbols]

        lines = []
        if open_sims:
            lines.append(f"👻 **{len(open_sims)} Açık Simülasyon**\n")
            for s in open_sims[:10]:
                entry = float(s.get('entry_price', 0))
                tp = float(s.get('take_profit', 0))
                sl = float(s.get('stop_loss', 0))
                current = ctx.get('prices', {}).get(s['symbol'])
                pnl_str = ""
                if current:
                    if s['side'] == 'long':
                        unrealized_pnl = (current - entry) / entry * 100
                    else:
                        unrealized_pnl = (entry - current) / entry * 100
                    pnl_str = f" | Anlık: {'🟢' if unrealized_pnl > 0 else '🔴'}{unrealized_pnl:+.2f}%"
                lines.append(
                    f"• **{s['symbol']}** {s['side'].upper()} @ ${entry:,.2f} "
                    f"(TP: ${tp:,.2f} | SL: ${sl:,.2f}){pnl_str}"
                )
        else:
            lines.append("👻 Şu an açık simülasyon yok.")

        # Kapalı simülasyon özeti
        try:
            closed = await self.db.get_closed_simulations(limit=5)
            if closed:
                lines.append(f"\n📋 **Son {len(closed)} Kapatılmış:**")
                for c in closed:
                    pnl = float(c.get('pnl', 0))
                    pnl_pct = float(c.get('pnl_pct', 0))
                    emoji = "✅" if pnl > 0 else "❌"
                    lines.append(f"  {emoji} {c['symbol']} {c['side']} | PnL: ${pnl:.2f} ({pnl_pct:+.2f}%)")
        except:
            pass

        return "\n".join(lines)

    async def _handle_brain(self, msg, msg_lower, ctx) -> str:
        b = ctx.get("brain", {})
        lines = [
            "🧠 **AI Beyin Durumu**\n",
            f"**Öğrenilen Pattern**: {b.get('total_patterns', 0)}",
            f"**Tahmin Doğruluğu**: %{b.get('accuracy', 0)*100:.1f}",
            f"**Tahmin İstatistikleri**: {b.get('prediction_stats', {}).get('correct', 0)} doğru / {b.get('prediction_stats', {}).get('total', 0)} toplam",
        ]

        # Öğrenme ağırlıkları
        weights = b.get('learning_weights', {})
        if weights:
            lines.append(f"\n**Öğrenme Ağırlıkları**:")
            for k, v in weights.items():
                lines.append(f"  • {k}: {v:.2f}")

        # Sinyal tipi başarıları
        sig_scores = b.get('signal_type_scores', {})
        if sig_scores:
            lines.append(f"\n**Sinyal Tipi Başarıları**:")
            for sig_type, scores in sig_scores.items():
                acc = scores.get('accuracy', 0)
                total = scores.get('total', 0)
                avg_pnl = scores.get('avg_pnl', 0)
                lines.append(f"  • {sig_type}: %{acc*100:.0f} başarı ({total} sinyal, ort PnL: {avg_pnl:.2f}%)")

        last_update = b.get('last_learning_update')
        if last_update:
            lines.append(f"\n🕐 Son öğrenme: {last_update}")

        return "\n".join(lines)

    async def _handle_flow(self, msg, msg_lower, ctx) -> str:
        symbols = self._extract_symbols(msg_lower)
        from config import Config
        target_symbols = symbols if symbols else Config.WATCHLIST[:5]

        lines = ["⚡ **Order Flow Analizi (Son 30dk)**\n"]
        for sym in target_symbols:
            try:
                trades = await self.db.get_recent_trades(sym, limit=500, market_type='spot')
                if not trades:
                    continue
                cutoff = datetime.utcnow() - timedelta(minutes=30)
                recent = [t for t in trades if t['timestamp'] >= cutoff]
                if not recent:
                    continue
                buy_vol = sum(float(t['quantity']) * float(t['price']) for t in recent if t['side'] == 'buy')
                sell_vol = sum(float(t['quantity']) * float(t['price']) for t in recent if t['side'] == 'sell')
                total = buy_vol + sell_vol
                if total > 0:
                    buy_pct = buy_vol / total * 100
                    if buy_pct > 60:
                        pressure = "🟢 Güçlü Alış"
                    elif buy_pct > 55:
                        pressure = "🟢 Hafif Alış"
                    elif buy_pct < 40:
                        pressure = "🔴 Güçlü Satış"
                    elif buy_pct < 45:
                        pressure = "🔴 Hafif Satış"
                    else:
                        pressure = "⚪ Dengeli"
                    lines.append(
                        f"**{sym.replace('USDT', '')}**: {pressure} "
                        f"(Alış: ${buy_vol:,.0f} [{buy_pct:.0f}%] | "
                        f"Satış: ${sell_vol:,.0f} [{100-buy_pct:.0f}%] | "
                        f"{len(recent)} trade)"
                    )
            except Exception as e:
                logger.debug(f"Flow error {sym}: {e}")

        if len(lines) == 1:
            lines.append("Henüz yeterli trade verisi yok.")
        return "\n".join(lines)

    async def _handle_watchlist(self, msg, msg_lower, ctx) -> str:
        # Ekleme isteği kontrolü
        add_match = re.search(r"ekle\s+(\w+)", msg_lower)
        remove_match = re.search(r"(?:kaldır|sil|çıkar)\s+(\w+)", msg_lower)

        if add_match:
            sym = add_match.group(1).upper()
            if not sym.endswith("USDT"):
                sym += "USDT"
            # Scout agent üzerinden anında takip başlat
            scout = self.agents.get('Scout')
            if scout and hasattr(scout, 'add_symbol_live'):
                await scout.add_symbol_live(sym)
                return (f"✅ **{sym}** izleme listesine eklendi ve anında veri çekimi başlatıldı!\n"
                        f"📊 Scout agent canlı olarak takip ediyor.")
            else:
                result = await self.db.add_user_watchlist(sym)
                if result:
                    return f"✅ **{sym}** izleme listesine eklendi. Scout agent yakında veri çekmeye başlayacak."
                return f"⚠ {sym} eklenemedi (zaten var veya hata)."

        if remove_match:
            sym = remove_match.group(1).upper()
            if not sym.endswith("USDT"):
                sym += "USDT"
            result = await self.db.remove_user_watchlist(sym)
            if result:
                return f"✅ **{sym}** izleme listesinden çıkarıldı."
            return f"⚠ {sym} listede bulunamadı."

        # Listeleme
        from config import Config
        user_wl = await self.db.get_user_watchlist()
        lines = ["📋 **İzleme Listesi**\n"]
        if user_wl:
            lines.append(f"**Özel Liste ({len(user_wl)} sembol):**")
            for w in user_wl:
                price = ctx.get('prices', {}).get(w['symbol'], 0)
                price_str = f" → ${price:,.2f}" if price else ""
                lines.append(f"  • {w['symbol']} ({w['exchange']}/{w['market_type']}){price_str}")
        lines.append(f"\n**Varsayılan Config ({len(Config.WATCHLIST)}):** {', '.join(Config.WATCHLIST)}")
        lines.append(f"\n💡 Eklemek için: \"watchlist ekle AVAXUSDT\"")
        lines.append(f"💡 Kaldırmak için: \"watchlist kaldır ADAUSDT\"")
        return "\n".join(lines)

    async def _handle_agent(self, msg, msg_lower, ctx) -> str:
        ah = ctx.get("agent_health", {})
        lines = ["🤖 **Agent Durumları**\n"]
        for name, health in ah.items():
            healthy = health.get("healthy", False)
            status = "✅ Aktif" if healthy else "❌ Sorunlu"
            details = []
            if "trade_counter" in health:
                details.append(f"{health['trade_counter']} trade")
            if "active_connections" in health:
                details.append(f"{health['active_connections']} bağlantı")
            if "signals_generated" in health:
                details.append(f"{health['signals_generated']} sinyal")
            if "active_simulations" in health:
                details.append(f"{health['active_simulations']} açık sim")
            if "win_rate" in health:
                details.append(f"WR: %{health['win_rate']:.0f}")
            if "audit_count" in health:
                details.append(f"#{health['audit_count']} audit")
            if "brain_connected" in health:
                details.append(f"Brain: {'✓' if health['brain_connected'] else '✗'}")
            detail_str = f" ({', '.join(details)})" if details else ""
            lines.append(f"• **{name}**: {status}{detail_str}")

        return "\n".join(lines)

    async def _handle_perf(self, msg, msg_lower, ctx) -> str:
        try:
            closed = await self.db.get_closed_simulations(limit=200)
            if not closed:
                return "📈 Henüz kapatılmış simülasyon yok. Ghost simulator sinyal bekliyor."

            wins = [s for s in closed if float(s.get('pnl', 0)) > 0]
            losses = [s for s in closed if float(s.get('pnl', 0)) <= 0]
            total_pnl = sum(float(s.get('pnl', 0)) for s in closed)
            avg_pnl_pct = sum(float(s.get('pnl_pct', 0)) for s in closed) / len(closed)
            best = max(closed, key=lambda s: float(s.get('pnl_pct', 0)))
            worst = min(closed, key=lambda s: float(s.get('pnl_pct', 0)))

            b = ctx.get("brain", {})
            lines = [
                "📈 **Bot Performans Raporu**\n",
                f"**Toplam Simülasyon**: {len(closed)}",
                f"**Kazanç**: {len(wins)} ✅ | **Kayıp**: {len(losses)} ❌",
                f"**Win Rate**: %{len(wins)/len(closed)*100:.1f}",
                f"**Toplam PnL**: ${total_pnl:,.2f}",
                f"**Ortalama PnL**: %{avg_pnl_pct:.2f}",
                f"**En İyi**: {best['symbol']} +{float(best.get('pnl_pct', 0)):.2f}%",
                f"**En Kötü**: {worst['symbol']} {float(worst.get('pnl_pct', 0)):.2f}%",
                f"\n**AI Doğruluğu**: %{b.get('accuracy', 0)*100:.1f} ({b.get('total_patterns', 0)} pattern)",
            ]
            return "\n".join(lines)
        except Exception as e:
            return f"⚠ Performans verisi alınamadı: {e}"

    async def _handle_pattern_match(self, msg, msg_lower, ctx) -> str:
        """Pattern match (Euclidean distance) durumunu göster"""
        try:
            lines = ["🎯 **Pattern Match Durumu**\n"]

            # PatternMatcher agent health
            pm_agent = self.agents.get('PatternMatcher')
            if pm_agent:
                health = await pm_agent.health_check()
                lines.append(f"**Agent**: {'✅ Çalışıyor' if health.get('healthy') else '❌ Sorunlu'}")
                lines.append(f"**Toplam Tarama**: {health.get('scan_count', 0)}")
                lines.append(f"**Toplam Eşleşme**: {health.get('match_count', 0)}")
                lines.append(f"**En İyi Benzerlik**: {health.get('best_similarity', 0):.4f}")
                lines.append(f"**Toplam Karşılaştırma**: {health.get('total_comparisons', 0)}")
                lines.append(f"**Aktif Cooldown**: {health.get('active_cooldowns', 0)} sembol")
                lines.append(f"**Cache Signature**: {health.get('cached_signatures', 0)}")

                # Son eşleşmeler
                last_matches = health.get('last_matches', [])
                if last_matches:
                    lines.append("\n**Son Eşleşmeler:**")
                    for m in last_matches[-5:]:
                        emoji = "🟢" if m.get('direction') == 'up' else "🔴"
                        lines.append(
                            f"  {emoji} {m['symbol']} [{m['timeframe']}] "
                            f"sim={m['similarity']:.4f} → {m['direction']} "
                            f"{m.get('magnitude', 0):+.2%}"
                        )
                else:
                    lines.append("\nHenüz eşleşme yok — signature birikimi bekleniyor.")

            # Sembol bazlı deep analiz
            symbols = self._extract_symbols(msg_lower)
            if symbols and pm_agent:
                for sym in symbols[:2]:
                    analysis = await pm_agent.deep_analyze_symbol(sym)
                    overall = analysis.get('overall_signal', {})
                    if overall.get('matched_timeframes', 0) > 0:
                        lines.append(f"\n**{sym} Detaylı Analiz:**")
                        lines.append(f"  Genel Yön: {overall.get('direction', '?')}")
                        lines.append(f"  Güven: %{overall.get('confidence', 0)*100:.1f}")
                        lines.append(f"  Eşleşen TF: {overall.get('matched_timeframes', 0)}")
                        for tf, data in analysis.get('timeframes', {}).items():
                            if data.get('status') == 'matched':
                                lines.append(
                                    f"    {tf}: sim={data['best_similarity']:.4f} "
                                    f"→ {data['predicted_direction']} "
                                    f"{data.get('predicted_magnitude', 0):+.4f}"
                                )

            # Brain pattern match stats
            b = ctx.get("brain", {})
            pm_stats = b.get('pattern_match', {})
            if pm_stats:
                lines.append(f"\n**Brain Değerlendirmesi:**")
                lines.append(f"  Toplam değerlendirilen: {pm_stats.get('total_evaluated', 0)}")
                lines.append(f"  Onaylanan: {pm_stats.get('total_approved', 0)}")
                lines.append(f"  Veto edilen: {pm_stats.get('total_vetoed', 0)}")

            return "\n".join(lines)
        except Exception as e:
            return f"⚠ Pattern match bilgisi alınamadı: {e}"

    async def _handle_help(self, msg, msg_lower, ctx) -> str:
        return (
            "🤖 **QuenBot AI - Yardım**\n\n"
            "Bana doğal dilde her şeyi sorabilirsin. Örnekler:\n\n"
            "💰 **Fiyat**: \"BTC kaç?\", \"ETH fiyatı ne?\", \"tüm fiyatlar\"\n"
            "📊 **Durum**: \"sistem nasıl?\", \"genel durum\"\n"
            "📡 **Sinyal**: \"aktif sinyaller\", \"BTC sinyali var mı?\"\n"
            "👻 **Simülasyon**: \"açık pozisyonlar\", \"trade durumu\"\n"
            "🧠 **AI**: \"beyin nasıl?\", \"öğrenme durumu\", \"pattern sayısı\"\n"
            "⚡ **Order Flow**: \"BTC alış satış baskısı\", \"order flow\"\n"
            "📈 **Performans**: \"win rate ne?\", \"bot performansı\"\n"
            "🎯 **Pattern Match**: \"pattern eşleşme\", \"benzerlik durumu\"\n"
            "🤖 **Agent**: \"bot durumları\", \"scout sağlık\"\n"
            "📋 **Watchlist**: \"izleme listesi\", \"ekle AVAXUSDT\", \"kaldır ADA\"\n"
            "📊 **Veri**: \"kaç trade var?\", \"istatistikler\"\n"
            "📈 **Piyasa**: \"piyasa trendi\", \"yükselenler\"\n"
            "⚙️ **Ayar**: \"mevcut ayarlar\"\n"
        )

    async def _handle_market(self, msg, msg_lower, ctx) -> str:
        prices = ctx.get("prices", {})
        try:
            # Son 1 saatlik hareketleri topla
            movers = []
            from config import Config
            for sym in Config.WATCHLIST:
                movements = await self.db.get_recent_movements(sym, hours=1)
                if movements:
                    total_change = sum(float(m['change_pct']) for m in movements)
                    movers.append((sym, total_change, prices.get(sym, 0)))

            movers.sort(key=lambda x: abs(x[1]), reverse=True)

            bulls = [m for m in movers if m[1] > 0]
            bears = [m for m in movers if m[1] < 0]

            lines = ["📈 **Piyasa Özeti**\n"]
            if bulls:
                lines.append("🟢 **Yükselen:**")
                for sym, chg, price in bulls[:5]:
                    lines.append(f"  • {sym.replace('USDT', '')}: +{chg*100:.2f}% (${price:,.2f})")
            if bears:
                lines.append("🔴 **Düşen:**")
                for sym, chg, price in bears[:5]:
                    lines.append(f"  • {sym.replace('USDT', '')}: {chg*100:.2f}% (${price:,.2f})")

            if not movers:
                lines.append("Henüz yeterli hareket verisi yok.")

            # Genel trend
            if len(bulls) > len(bears):
                lines.append(f"\n📊 Genel Trend: 🟢 **Piyasa pozitif** ({len(bulls)} yükselen / {len(bears)} düşen)")
            elif len(bears) > len(bulls):
                lines.append(f"\n📊 Genel Trend: 🔴 **Piyasa negatif** ({len(bears)} düşen / {len(bulls)} yükselen)")
            else:
                lines.append(f"\n📊 Genel Trend: ⚪ **Kararsız piyasa**")

            return "\n".join(lines)
        except Exception as e:
            return f"⚠ Piyasa analizi alınamadı: {e}"

    async def _handle_data(self, msg, msg_lower, ctx) -> str:
        s = ctx.get("summary", {})
        try:
            stats = await self.db.get_brain_status_data()
            learning = stats.get('learning', {})
            lines = [
                "📊 **Veri İstatistikleri**\n",
                f"**Toplam Trade**: {s.get('total_trades', 0):,}",
                f"**Hareketler (24s)**: {s.get('recent_movements_24h', 0):,}",
                f"**Toplam Sinyal**: {s.get('active_signals', 0)} aktif",
                f"**Toplam Simülasyon**: {s.get('open_simulations', 0)} açık / {s.get('closed_simulations', 0) if 'closed_simulations' in s else '?'} kapalı",
                f"**Pattern Kayıtları**: {stats.get('pattern_count', 0):,}",
                f"**Öğrenme Kayıtları**: {learning.get('total', 0)} (doğruluk: %{learning.get('accuracy', 0):.1f})",
            ]
            return "\n".join(lines)
        except Exception as e:
            return f"⚠ Veri istatistiği alınamadı: {e}"

    async def _handle_config(self, msg, msg_lower, ctx) -> str:
        from config import Config
        lines = [
            "⚙️ **Mevcut Ayarlar**\n",
            f"**Hareket Eşiği**: %{Config.PRICE_MOVEMENT_THRESHOLD*100:.0f}",
            f"**Benzerlik Eşiği**: {Config.SIMILARITY_THRESHOLD}",
            f"**Take Profit**: %{Config.GHOST_TAKE_PROFIT_PCT*100:.0f}",
            f"**Stop Loss**: %{Config.GHOST_STOP_LOSS_PCT*100:.0f}",
            f"**Simülasyon Timeout**: {Config.SIMULATION_TIMEOUT_HOURS} saat",
            f"**Strateji Population**: {Config.STRATEGY_POPULATION_SIZE}",
            f"**Strateji Nesil**: {Config.STRATEGY_GENERATIONS}",
            f"**Min Ortalama Kar**: %{Config.STRATEGY_MIN_MEAN_PROFIT*100:.0f}",
            f"**Watchlist**: {', '.join(Config.WATCHLIST)}",
        ]
        return "\n".join(lines)

    async def _handle_risk(self, msg, msg_lower, ctx) -> str:
        """Risk yönetimi durumu"""
        if not self.risk_manager:
            return "🛡 Risk Manager henüz aktif değil."
        summary = self.risk_manager.get_risk_summary()
        lines = [
            "🛡 **Risk Yönetimi Durumu**\n",
            f"**Mod**: {summary['mode']}",
            f"**Günlük İşlem**: {summary['daily_trades']}",
            f"**Günlük PnL**: {summary['daily_pnl']:.2f}%",
            f"**Art Arda Kayıp**: {summary['consecutive_losses']}",
            f"**Drawdown**: {summary['drawdown']}",
            f"**Açık Pozisyon**: {summary['open_positions']}",
            f"**Cooldown**: {'⏳ Aktif' if summary['cooldown_active'] else '✅ Yok'}",
            f"**Min Güven Eşiği**: {summary['min_confidence']:.2f}",
        ]
        mp = summary.get('mode_params', {})
        if mp:
            lines.append(f"\n**Mode Parametreleri:**")
            lines.append(f"  TP: %{mp.get('take_profit_pct', 0)*100:.1f} | SL: %{mp.get('stop_loss_pct', 0)*100:.1f}")
            lines.append(f"  Similarity: {mp.get('similarity_threshold', 0)} | Min Profit: {mp.get('min_mean_profit', 0)}")
        return "\n".join(lines)

    async def _handle_state(self, msg, msg_lower, ctx) -> str:
        """Bot state/mode durumu"""
        if not self.state_tracker:
            return "📊 StateTracker henüz aktif değil."
        summary = self.state_tracker.get_state_summary()
        st = summary
        lines = [
            "📊 **Bot State Durumu**\n",
            f"**Mod**: {st.get('mode', 'BOOTSTRAP')}",
            f"**Toplam Trade**: {st.get('total_trades', 0)}",
            f"**Kümülatif PnL**: {st.get('cumulative_pnl', 0):.2f}%",
            f"**Günlük PnL**: {st.get('daily_pnl', 0):.2f}%",
            f"**Win Rate**: %{st.get('win_rate', 0):.1f}",
            f"**Drawdown**: {st.get('current_drawdown', 0):.2f}%",
            f"**En İyi Streak**: {st.get('best_streak', 0)} ✅",
            f"**En Kötü Streak**: {st.get('worst_streak', 0)} ❌",
            f"**Aktif Semboller**: {st.get('active_symbols', [])}",
        ]
        return "\n".join(lines)

    async def _handle_rca(self, msg, msg_lower, ctx) -> str:
        """RCA (Root Cause Analysis) sonuçları"""
        try:
            rca_stats = await self.db.get_rca_stats()
            if rca_stats['total'] == 0:
                return "🔍 Henüz RCA analizi yapılmamış. Auditor agent başarısız trade'leri analiz ettiğinde sonuçlar burada görünecek."

            lines = [
                f"🔍 **RCA Analiz Sonuçları** ({rca_stats['total']} analiz)\n",
            ]
            for ftype, count in rca_stats.get('distribution', {}).items():
                pct = count / rca_stats['total'] * 100
                lines.append(f"• **{ftype}**: {count} (%{pct:.0f})")

            if self.rca_engine:
                stats = self.rca_engine.get_stats()
                top = stats.get('top_failure')
                if top:
                    lines.append(f"\n⚠ En sık hata: **{top}**")

            return "\n".join(lines)
        except Exception as e:
            return f"🔍 RCA verisi alınamadı: {e}"

    async def _general_response(self, msg: str, ctx: dict) -> str:
        """Intent bulunamazsa — Gemma Müdür devreye girer."""
        # Kısa selamlama kontrolü (LLM çağırmaya gerek yok)
        greetings = ["merhaba", "selam", "hey", "hi", "hello", "sa", "slm", "günaydın", "iyi akşamlar"]
        if any(g in msg.lower() for g in greetings):
            s = ctx.get("summary", {})
            b = ctx.get("brain", {})
            prices = ctx.get("prices", {})
            return (
                f"Merhaba! 🤖 QuenBot AI burada.\n\n"
                f"Şu an {s.get('total_trades', 0):,} trade izliyorum, "
                f"{len(prices)} coin takipte, "
                f"AI beyin {b.get('total_patterns', 0)} pattern öğrenmiş.\n\n"
                f"Bana doğal dilde her şeyi söyleyebilirsin — "
                f"strateji değiştir, risk ayarla, coin analiz et, "
                f"ne istersen. Gemma anlayıp agentlara iletecek."
            )

        # ─── Gemma Director: serbest komutu LLM ile yorumla ───
        return await self._gemma_director(msg, ctx)

    async def _gemma_director(self, msg: str, ctx: dict) -> str:
        """Gemma LLM ile serbest komutu yorumla ve agentlara dağıt."""
        bridge = _get_llm_bridge()
        if bridge is None or not await bridge.is_available():
            return self._fallback_response(msg, ctx)

        # Sistem context'ini kompakt JSON olarak hazırla
        s = ctx.get("summary", {})
        b = ctx.get("brain", {})
        prices = ctx.get("prices", {})
        ah = ctx.get("agent_health", {})

        system_state = json.dumps({
            "prices": {k: round(v, 2) for k, v in list(prices.items())[:10]},
            "total_trades": s.get("total_trades", 0),
            "active_signals": s.get("active_signals", 0),
            "open_sims": s.get("open_simulations", 0),
            "win_rate": s.get("win_rate", 0),
            "total_pnl": round(s.get("total_pnl", 0), 2),
            "brain_patterns": b.get("total_patterns", 0),
            "brain_accuracy": round(b.get("accuracy", 0) * 100, 1),
            "agents_healthy": sum(1 for v in ah.values() if v.get("healthy")),
            "agents_total": len(ah),
            "state_mode": self.state_tracker.get_mode() if self.state_tracker else "?",
            "risk": self.risk_manager.get_risk_summary() if self.risk_manager else {},
        }, default=str, separators=(",", ":"))

        prompt = (
            f"KULLANICI KOMUTU: {msg}\n\n"
            f"SİSTEM DURUMU:\n{system_state[:2000]}\n\n"
            f"Bu komutu analiz et ve yapılacak eylemleri JSON olarak döndür."
        )

        try:
            from llm_client import get_llm_client
            client = get_llm_client()
            response = await client.generate(
                prompt=prompt,
                system=DIRECTOR_SYSTEM_PROMPT,
                temperature=0.3,
                json_mode=True,
                timeout_override=60,
            )

            if not response.success or not response.text.strip():
                return self._fallback_response(msg, ctx)

            result = response.as_json()
            if result is None:
                # JSON parse fallback
                text = response.text.strip()
                start = text.find("{")
                end = text.rfind("}") + 1
                if start >= 0 and end > start:
                    try:
                        result = json.loads(text[start:end])
                    except json.JSONDecodeError:
                        pass

            if result is None:
                # LLM text yanıt verdi ama JSON değil — direkt göster
                return f"🤖 {response.text.strip()}"

            # ─── Eylemleri uygula ───
            actions = result.get("actions", [])
            action_results = []
            for action in actions:
                action_result = await self._execute_action(action, ctx)
                if action_result:
                    action_results.append(action_result)

            # Kullanıcıya yanıt oluştur
            parts = []
            user_response = result.get("response_to_user", "")
            intent = result.get("user_intent", "")

            if intent:
                parts.append(f"🎯 **Anlaşılan**: {intent}")
            if user_response:
                parts.append(f"\n🤖 {user_response}")
            if action_results:
                parts.append(f"\n📋 **Yapılan İşlemler:**")
                for ar in action_results:
                    parts.append(f"  • {ar}")

            return "\n".join(parts) if parts else f"🤖 {user_response or 'Komut alındı.'}"

        except Exception as e:
            logger.error(f"Gemma director error: {e}")
            return self._fallback_response(msg, ctx)

    async def _execute_action(self, action: dict, ctx: dict) -> Optional[str]:
        """Gemma'nın belirlediği eylemi uygula."""
        action_type = action.get("type", "")
        target = action.get("target", "")
        params = action.get("params", {})
        explanation = action.get("explanation", "")

        try:
            if action_type == "strategy_update":
                return await self._action_strategy_update(params)

            elif action_type == "risk_update":
                return await self._action_risk_update(params)

            elif action_type == "watchlist_add":
                sym = target.upper()
                if not sym.endswith("USDT"):
                    sym += "USDT"
                scout = self.agents.get('Scout')
                if scout and hasattr(scout, 'add_symbol_live'):
                    await scout.add_symbol_live(sym)
                    return f"✅ {sym} izleme listesine eklendi ve takip başladı"
                else:
                    await self.db.add_user_watchlist(sym)
                    return f"✅ {sym} izleme listesine eklendi"

            elif action_type == "watchlist_remove":
                sym = target.upper()
                if not sym.endswith("USDT"):
                    sym += "USDT"
                await self.db.remove_user_watchlist(sym)
                return f"✅ {sym} izleme listesinden çıkarıldı"

            elif action_type == "analyze_symbol":
                sym = target.upper()
                if not sym.endswith("USDT"):
                    sym += "USDT"
                return await self._action_deep_analyze(sym, ctx)

            elif action_type == "force_scan":
                return "📡 Scout acil tarama başlatıldı — sonuçlar kısa sürede gelecek"

            elif action_type == "brain_insight":
                b = ctx.get("brain", {})
                return (f"🧠 Brain: {b.get('total_patterns', 0)} pattern, "
                        f"%{b.get('accuracy', 0)*100:.1f} doğruluk, "
                        f"son öğrenme: {b.get('last_learning_update', 'yok')}")

            elif action_type == "set_mode":
                mode = params.get("mode", "").upper()
                if self.state_tracker and mode in ("BOOTSTRAP", "LEARNING", "WARMUP", "PRODUCTION"):
                    self.state_tracker.state["mode_override"] = mode
                    return f"✅ Bot modu {mode} olarak ayarlandı"
                return f"⚠ Geçersiz mod: {mode}"

            elif action_type == "status_report":
                return await self._handle_status("", "", ctx)

            elif action_type == "general_chat":
                return None  # response_to_user zaten cevap içeriyor

            else:
                logger.debug(f"Unknown action type: {action_type}")
                return None

        except Exception as e:
            logger.error(f"Action execution error ({action_type}): {e}")
            return f"⚠ {action_type} hatası: {str(e)[:100]}"

    async def _action_strategy_update(self, params: dict) -> str:
        """Strateji parametrelerini güncelle."""
        from config import Config
        changes = []

        # Preset profiller
        profile = params.get("profile", "").lower()
        if profile == "aggressive" or profile == "agresif":
            Config.SIMILARITY_THRESHOLD = 0.3
            Config.GHOST_TAKE_PROFIT_PCT = 0.07
            Config.GHOST_STOP_LOSS_PCT = 0.04
            Config.STRATEGY_POPULATION_SIZE = 50
            Config.RISK_MAX_DAILY_TRADES = 30
            changes.append("Agresif profil: düşük eşik, geniş TP/SL, yüksek trade limiti")

        elif profile == "defensive" or profile == "defansif":
            Config.SIMILARITY_THRESHOLD = 0.55
            Config.GHOST_TAKE_PROFIT_PCT = 0.03
            Config.GHOST_STOP_LOSS_PCT = 0.02
            Config.STRATEGY_POPULATION_SIZE = 30
            Config.RISK_MAX_DAILY_TRADES = 10
            changes.append("Defansif profil: yüksek eşik, dar TP/SL, düşük trade limiti")

        elif profile == "balanced" or profile == "dengeli":
            Config.SIMILARITY_THRESHOLD = 0.4
            Config.GHOST_TAKE_PROFIT_PCT = 0.05
            Config.GHOST_STOP_LOSS_PCT = 0.03
            Config.STRATEGY_POPULATION_SIZE = 40
            Config.RISK_MAX_DAILY_TRADES = 20
            changes.append("Dengeli profil: varsayılan parametreler")

        # Bireysel parametre güncellemeleri
        if "similarity_threshold" in params:
            val = float(params["similarity_threshold"])
            Config.SIMILARITY_THRESHOLD = max(0.1, min(0.9, val))
            changes.append(f"Similarity eşiği: {Config.SIMILARITY_THRESHOLD}")

        if "take_profit" in params:
            val = float(params["take_profit"])
            Config.GHOST_TAKE_PROFIT_PCT = max(0.01, min(0.20, val))
            changes.append(f"Take profit: %{Config.GHOST_TAKE_PROFIT_PCT*100:.1f}")

        if "stop_loss" in params:
            val = float(params["stop_loss"])
            Config.GHOST_STOP_LOSS_PCT = max(0.005, min(0.10, val))
            changes.append(f"Stop loss: %{Config.GHOST_STOP_LOSS_PCT*100:.1f}")

        # Directive store'a kaydet (kalıcı)
        if changes:
            try:
                from directive_store import get_directive_store
                store = get_directive_store()
                await store.set_master_directive(
                    f"Strateji güncellemesi ({datetime.utcnow().strftime('%H:%M')}): " +
                    "; ".join(changes)
                )
            except Exception:
                pass

        return "⚙️ Strateji güncellendi: " + "; ".join(changes) if changes else "⚙️ Parametre değişikliği belirtilmedi"

    async def _action_risk_update(self, params: dict) -> str:
        """Risk parametrelerini güncelle."""
        from config import Config
        changes = []

        if "max_daily_trades" in params:
            Config.RISK_MAX_DAILY_TRADES = int(params["max_daily_trades"])
            changes.append(f"Günlük max trade: {Config.RISK_MAX_DAILY_TRADES}")

        if "max_daily_loss" in params:
            Config.RISK_MAX_DAILY_LOSS_PCT = float(params["max_daily_loss"])
            changes.append(f"Günlük max kayıp: %{Config.RISK_MAX_DAILY_LOSS_PCT}")

        if "max_drawdown" in params:
            Config.RISK_MAX_DRAWDOWN_PCT = float(params["max_drawdown"])
            changes.append(f"Max drawdown: %{Config.RISK_MAX_DRAWDOWN_PCT}")

        if "max_open_positions" in params:
            Config.RISK_MAX_OPEN_POSITIONS = int(params["max_open_positions"])
            changes.append(f"Max açık pozisyon: {Config.RISK_MAX_OPEN_POSITIONS}")

        return "🛡 Risk güncellendi: " + "; ".join(changes) if changes else "🛡 Risk parametresi belirtilmedi"

    async def _action_deep_analyze(self, symbol: str, ctx: dict) -> str:
        """Bir coini tüm agentlardan geçirerek derin analiz yap."""
        parts = [f"🔍 **{symbol} Derin Analiz**\n"]

        # Fiyat
        price = ctx.get("prices", {}).get(symbol)
        if price:
            parts.append(f"💰 Fiyat: ${price:,.2f}")

        # Order flow
        try:
            trades = await self.db.get_recent_trades(symbol, limit=500, market_type='spot')
            if trades:
                cutoff = datetime.utcnow() - timedelta(minutes=30)
                recent = [t for t in trades if t['timestamp'] >= cutoff]
                if recent:
                    buy_vol = sum(float(t['quantity']) * float(t['price']) for t in recent if t['side'] == 'buy')
                    sell_vol = sum(float(t['quantity']) * float(t['price']) for t in recent if t['side'] == 'sell')
                    total = buy_vol + sell_vol
                    if total > 0:
                        buy_pct = buy_vol / total * 100
                        parts.append(f"⚡ Order Flow: Alış %{buy_pct:.0f} / Satış %{100-buy_pct:.0f} ({len(recent)} trade)")
        except Exception:
            pass

        # Sinyaller
        signals = ctx.get("pending_signals", [])
        sym_signals = [s for s in signals if s.get("symbol") == symbol]
        if sym_signals:
            for s in sym_signals[:3]:
                conf = float(s.get('confidence', 0)) * 100
                meta = s.get('metadata', {})
                if isinstance(meta, str):
                    try: meta = json.loads(meta)
                    except: meta = {}
                direction = meta.get('position_bias', '?')
                parts.append(f"📡 Sinyal: {direction.upper()} (güven %{conf:.0f})")

        # Açık simülasyon
        open_sims = ctx.get("open_sims", [])
        sym_sims = [s for s in open_sims if s.get("symbol") == symbol]
        if sym_sims:
            for s in sym_sims:
                entry = float(s.get('entry_price', 0))
                parts.append(f"👻 Açık Sim: {s['side'].upper()} @ ${entry:,.2f}")

        # PatternMatcher derin analizi
        pm_agent = self.agents.get("PatternMatcher")
        if pm_agent:
            try:
                pm = await pm_agent.deep_analyze_symbol(symbol)
                overall = pm.get("overall_signal", {})
                if overall.get("matched_timeframes", 0) > 0:
                    parts.append(
                        f"🎯 PatternMatcher: {overall.get('direction', '?').upper()} "
                        f"(güven %{overall.get('confidence', 0) * 100:.1f}, "
                        f"avg sim={overall.get('avg_similarity', 0):.4f}, "
                        f"TF={overall.get('matched_timeframes', 0)})"
                    )
                else:
                    parts.append("🎯 PatternMatcher: Eşik üstü eşleşme yok")
            except Exception as e:
                logger.debug(f"PatternMatcher deep analyze error: {e}")

        # Brain pattern eşleşmesi
        b = ctx.get("brain", {})
        parts.append(f"🧠 Brain: {b.get('total_patterns', 0)} pattern, %{b.get('accuracy', 0)*100:.1f} doğruluk")

        # LLM derin analiz iste
        bridge = _get_llm_bridge()
        if bridge and await bridge.is_available():
            try:
                llm_result = await bridge.brain_predict_with_context(
                    symbol=symbol,
                    snapshot_data={"price": price, "symbol": symbol},
                    matching_patterns=[],
                    avg_similarity=0,
                    indicators={},
                )
                if llm_result and llm_result.get("_parsed"):
                    pred = llm_result.get("prediction", "?")
                    conf = llm_result.get("confidence", 0)
                    risk = llm_result.get("risk_assessment", "")
                    factors = llm_result.get("key_factors", [])
                    parts.append(f"\n🤖 **Gemma Analizi:**")
                    parts.append(f"  Tahmin: {pred.upper()} (güven: %{conf*100:.0f})")
                    if risk:
                        parts.append(f"  Risk: {risk}")
                    if factors:
                        parts.append(f"  Faktörler: {', '.join(factors[:5])}")
            except Exception as e:
                logger.debug(f"LLM analyze error: {e}")

        return "\n".join(parts)

    def _fallback_response(self, msg: str, ctx: dict) -> str:
        """LLM kullanılamadığında basit fallback."""
        s = ctx.get("summary", {})
        return (
            f"🤖 Mesajını aldım: \"{msg[:100]}\"\n\n"
            f"⚠ LLM şu an yanıt üretemiyor (degraded mode).\n"
            f"Keyword tabanlı komutlar kullanabilirsin:\n"
            f"• \"durum\" — sistem durumu\n"
            f"• \"fiyat BTC\" — fiyat sorgula\n"
            f"• \"sinyaller\" — aktif sinyaller\n"
            f"• \"yardım\" — tüm komutlar\n\n"
            f"📊 Anlık: {s.get('total_trades', 0):,} trade | "
            f"{s.get('active_signals', 0)} sinyal | "
            f"{s.get('open_simulations', 0)} açık sim"
        )
