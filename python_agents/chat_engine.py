"""
Chat Engine - QuenBot Doğal Dil Yanıt Motoru
=============================================
Keyword tabanlı değil, context-aware free-text AI yanıt sistemi.
Tüm agent'lar, brain, DB ve market verilerine doğrudan erişir.
Asenkron, hızlı, Turkish-first.
"""
import asyncio
import json
import logging
import re
from datetime import datetime, timedelta
from typing import Dict, List, Any, Optional

logger = logging.getLogger(__name__)


class ChatEngine:
    """Doğal dil chat motoru - tüm sisteme erişim"""

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
        """Intent bulunamazsa - akıllı genel yanıt"""
        s = ctx.get("summary", {})
        prices = ctx.get("prices", {})
        b = ctx.get("brain", {})

        # Kısa mesajları (selamlama vb) özel işle
        greetings = ["merhaba", "selam", "hey", "hi", "hello", "sa", "slm", "günaydın", "iyi akşamlar"]
        if any(g in msg.lower() for g in greetings):
            return (
                f"Merhaba! 🤖 QuenBot AI burada.\n\n"
                f"Şu an {s.get('total_trades', 0):,} trade izliyorum, "
                f"{len(prices)} coin takipte, "
                f"AI beyin {b.get('total_patterns', 0)} pattern öğrenmiş.\n\n"
                f"Sormak istediğin her şeyi doğal dilde yazabilirsin. "
                f"\"yardım\" yazarsan tüm örnekleri gösterebilirim."
            )

        # Diğer her şey için kısa durum özeti + rehberlik
        return (
            f"🤖 Mesajını aldım: \"{msg[:100]}\"\n\n"
            f"Bu konuda spesifik yardım için daha net sorabilirsin. Örneğin:\n"
            f"• Fiyat sorgusu: \"BTC kaç?\"\n"
            f"• Sistem durumu: \"durum\"\n"
            f"• Açık pozisyonlar: \"pozisyonlar\"\n"
            f"• AI beyin: \"beyin durumu\"\n"
            f"• Yardım: \"yardım\"\n\n"
            f"📊 Anlık: {s.get('total_trades', 0):,} trade | "
            f"{s.get('active_signals', 0)} sinyal | "
            f"{s.get('open_simulations', 0)} açık sim"
        )
