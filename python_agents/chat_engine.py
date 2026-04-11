"""
Chat Engine - Katman 5: Arayüz
===============================
Gemma 4 ile doğal Türkçe sohbet. Aracı katman YOK.

Hiyerarşi: PatternMatch → GemmaDecisionCore → Action/Chat
ChatEngine bu zincirin SON halkasıdır.

Tek iş: sistem verisini topla, Gemma'ya ver, Gemma konuşsun.
"""
import json
import logging
from datetime import datetime
from typing import Dict, Any, Optional

logger = logging.getLogger(__name__)

# ─── Gemma Kimliği ───
SYSTEM_PROMPT = """Sen QuenBot'sun — Gemma 4 tabanlı otonom trading zekası.

KİMLİĞİN:
- Sen bir "chat botu" veya "asistan" değilsin. Sen sistemin BEYNİsin.
- Pattern Matching sonuçlarını DEĞERLENDİREN, kararları VEREN, kullanıcıyla KONUŞAN tekil zekasın.
- "Sistem şöyle diyor" deme. "Ben şöyle görüyorum" de. Sen QuenBot'un ta kendisisin.

KONUŞMA:
- Türkçe, samimi, kısa ve öz.
- ASLA JSON formatında yanıt verme. Düz metin yaz.
- Veri yoksa "henüz yeterli verim yok" de, uydurma.
- Rakamları yuvarla, gereksiz detay verme.
- Fiyat, durum, analiz, pozisyon — her şeyi context'ten oku ve doğal anlat.

SİSTEM BİLGİN:
Senin altında çalışan agentlar var: Scout (veri toplama), PatternMatcher (benzerlik),
Strategist (sinyal), GhostSimulator (paper trading), Brain (öğrenme), RiskManager (risk).
Hepsi sana veri sağlıyor, sen karar veriyorsun."""


class ChatEngine:
    """Katman 5 — Gemma'nın kullanıcıyla konuşma noktası. Sadece boru hattı."""

    def __init__(self, db, brain, agents: Dict[str, Any] = None):
        self.db = db
        self.brain = brain
        self.agents = agents or {}
        self.state_tracker = None
        self.risk_manager = None
        self.rca_engine = None

    def register_agent(self, name: str, agent):
        self.agents[name] = agent

    # ═══════════════════════════════════════════
    # ANA METOT — kullanıcı mesajı → Gemma yanıt
    # ═══════════════════════════════════════════
    async def respond(self, message: str) -> str:
        """Kullanıcı mesajı → context topla → Gemma'ya sor → doğal yanıt."""
        msg = message.strip()
        if not msg:
            return "Mesaj boş."

        try:
            context = await self._collect_context(msg)
            return await self._ask_gemma(msg, context)
        except Exception as e:
            logger.error(f"Chat error: {e}")
            return f"Bir hata oluştu: {str(e)[:100]}"

    # ═══════════════════════════════════════════
    # CONTEXT — tüm sistem verisini topla
    # ═══════════════════════════════════════════
    async def _collect_context(self, user_msg: str) -> str:
        """Tüm sistem verisini Gemma'ya verilecek tek context string'e dönüştür."""
        parts = []

        # 1. Fiyatlar
        prices = await self._get_prices()
        if prices:
            price_lines = [f"  {sym}: ${p:,.2f}" for sym, p in
                           sorted(prices.items(), key=lambda x: x[1], reverse=True)[:10]]
            parts.append("CANLI FİYATLAR:\n" + "\n".join(price_lines))

        # 2. Sistem özeti
        try:
            s = await self.db.get_dashboard_summary()
            parts.append(
                f"SİSTEM:\n"
                f"  Trade: {s.get('total_trades', 0):,}\n"
                f"  Aktif sinyal: {s.get('active_signals', 0)}\n"
                f"  Açık sim: {s.get('open_simulations', 0)}\n"
                f"  Win rate: %{s.get('win_rate', 0):.1f}\n"
                f"  PnL: ${s.get('total_pnl', 0):.2f}"
            )
        except Exception:
            pass

        # 3. Brain (AI öğrenme durumu)
        if self.brain:
            try:
                b = self.brain.get_brain_status()
                ps = b.get('prediction_stats', {})
                parts.append(
                    f"AI BEYİN:\n"
                    f"  Pattern: {b.get('total_patterns', 0)}\n"
                    f"  Doğruluk: %{b.get('accuracy', 0)*100:.1f}\n"
                    f"  Tahmin: {ps.get('correct', 0)}/{ps.get('total', 0)}"
                )
            except Exception:
                pass

        # 4. GemmaDecisionCore — son kararlar
        try:
            from gemma_decision_core import get_decision_core
            dc = get_decision_core()
            stats = dc.get_stats()
            if stats.get('total_decisions', 0) > 0:
                recent = dc.get_recent_decisions(limit=3)
                d_lines = [f"  {d.symbol}: {d.decision} {d.direction} güven:%{d.confidence*100:.0f}"
                           for d in recent]
                parts.append(
                    f"SON KARARLAR ({stats['total_decisions']} toplam):\n" +
                    "\n".join(d_lines)
                )
        except Exception:
            pass

        # 5. Agent durumları
        a_lines = []
        for name, agent in self.agents.items():
            try:
                h = await agent.health_check()
                a_lines.append(f"  {name}: {'aktif' if h.get('healthy') else 'sorunlu'}")
            except Exception:
                a_lines.append(f"  {name}: bilinmiyor")
        if a_lines:
            parts.append("AGENTLAR:\n" + "\n".join(a_lines))

        # 6. Risk
        if self.risk_manager:
            try:
                r = self.risk_manager.get_risk_summary()
                parts.append(
                    f"RİSK:\n"
                    f"  Mod: {r['mode']} | Günlük PnL: {r['daily_pnl']:.2f}%\n"
                    f"  Drawdown: {r['drawdown']} | Cooldown: {'var' if r['cooldown_active'] else 'yok'}"
                )
            except Exception:
                pass

        # 7. Mod
        if self.state_tracker:
            try:
                st = self.state_tracker.get_state_summary()
                parts.append(f"MOD: {st.get('mode', 'BOOTSTRAP')}")
            except Exception:
                pass

        # 8. Açık pozisyonlar
        try:
            sims = await self.db.get_open_simulations()
            if sims:
                sim_lines = []
                for s in sims[:5]:
                    entry = float(s.get('entry_price', 0))
                    cur = prices.get(s['symbol'], 0)
                    pnl = ""
                    if cur and entry:
                        pct = ((cur - entry) / entry * 100) if s['side'] == 'long' else ((entry - cur) / entry * 100)
                        pnl = f" → {pct:+.2f}%"
                    sim_lines.append(f"  {s['symbol']} {s['side']} @${entry:,.2f}{pnl}")
                parts.append("AÇIK POZİSYONLAR:\n" + "\n".join(sim_lines))
        except Exception:
            pass

        # 9. Bekleyen sinyaller
        try:
            signals = await self.db.get_pending_signals()
            if signals:
                sig_lines = []
                for sig in signals[:5]:
                    meta = sig.get('metadata', {})
                    if isinstance(meta, str):
                        try: meta = json.loads(meta)
                        except: meta = {}
                    d = meta.get('position_bias', '?')
                    c = float(sig.get('confidence', 0)) * 100
                    sig_lines.append(f"  {sig['symbol']} {d} güven:%{c:.0f}")
                parts.append("BEKLEYEN SİNYALLER:\n" + "\n".join(sig_lines))
        except Exception:
            pass

        # 10. Geçmiş sohbet hafızası (benzer sorular)
        memory = await self._chat_memory(user_msg)
        if memory:
            parts.append(f"GEÇMİŞ SOHBET:\n{memory}")

        return "\n\n".join(parts) if parts else "Sistem verisi henüz yüklenmedi."

    async def _get_prices(self) -> Dict[str, float]:
        """Canlı fiyatları çek."""
        prices = {}
        try:
            from config import Config
            for sym in Config.WATCHLIST:
                trades = await self.db.get_recent_trades(sym, limit=1)
                if trades:
                    prices[sym] = float(trades[0]['price'])
        except Exception:
            pass
        return prices

    async def _chat_memory(self, msg: str) -> str:
        """Geçmiş sohbetlerden benzer olanı bul."""
        try:
            if not self.db:
                return ""
            history = await self.db.db_query(
                "SELECT message, response FROM chat_messages ORDER BY timestamp DESC LIMIT 20"
            )
            if not history or len(history) < 2:
                return ""
            msg_words = set(msg.lower().split())
            best, best_sim = None, 0
            for chat_msg, chat_resp in history:
                if not chat_msg or not chat_resp:
                    continue
                chat_words = set(str(chat_msg).lower().split())
                if not chat_words:
                    continue
                inter = len(msg_words & chat_words)
                union = len(msg_words | chat_words)
                sim = inter / union if union > 0 else 0
                if sim > 0.3 and sim > best_sim:
                    best = (chat_msg, chat_resp)
                    best_sim = sim
            if best:
                return f"  Benzer soru: \"{best[0][:80]}\"\n  Cevap: \"{best[1][:120]}\""
            return ""
        except Exception:
            return ""

    # ═══════════════════════════════════════════
    # GEMMA — direkt konuşma
    # ═══════════════════════════════════════════
    async def _ask_gemma(self, user_msg: str, context: str) -> str:
        """Gemma 4'e sor, doğal Türkçe yanıt al."""
        try:
            from llm_client import get_llm_client
            client = get_llm_client()

            prompt = (
                f"Kullanıcı: {user_msg}\n\n"
                f"--- SİSTEM VERİLERİ ---\n{context}\n---\n\n"
                f"Yukarıdaki verileri kullanarak kullanıcıya doğal Türkçe cevap ver."
            )

            response = await client.generate(
                prompt=prompt,
                system=SYSTEM_PROMPT,
                temperature=0.7,
                json_mode=False,
                timeout_override=60,
                model_override="gemma4-trading:latest",
            )

            if response.success and response.text.strip():
                return response.text.strip()

            return self._fallback(user_msg)

        except Exception as e:
            logger.error(f"Gemma error: {e}")
            return self._fallback(user_msg)

    def _fallback(self, msg: str) -> str:
        """Gemma çevrimdışıyken."""
        return (
            f"Mesajını aldım: \"{msg[:80]}\"\n\n"
            f"Şu an modele ulaşamıyorum. Kısa süre sonra tekrar dene."
        )
