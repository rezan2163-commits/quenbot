"""
Chat Engine - Katman 5: Arayuz
==============================
Gemma'nin kullaniciyla dogrudan konustugu katman.
Hedef: hizli, dogal ve sistemin tamamini bilen cevaplar.
"""
import asyncio
import json
import logging
import os
import re
import time
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)
STRICT_LLM = os.getenv("QUENBOT_LLM_STRICT", "1").lower() in {"1", "true", "yes", "on"}
ACTIVE_LLM_MODEL = os.getenv("QUENBOT_LLM_MODEL", "quenbot-brain")
FAST_LLM_MODEL = os.getenv("QUENBOT_LLM_FAST_MODEL", "qwen3:1.7b")
CHAT_CACHE_TTL = int(os.getenv("QUENBOT_CHAT_CACHE_TTL", "12"))
HEALTH_CACHE_TTL = int(os.getenv("QUENBOT_HEALTH_CACHE_TTL", "30"))
MAX_CONTEXT_CHARS = int(os.getenv("QUENBOT_CHAT_CONTEXT_CHARS", "3800"))
QUICK_CHAT_TIMEOUT = int(os.getenv("QUENBOT_CHAT_QUICK_TIMEOUT", "12"))
FULL_CHAT_TIMEOUT = int(os.getenv("QUENBOT_CHAT_FULL_TIMEOUT", "24"))

SYSTEM_PROMPT = """Sen QuenBot'sun.

ROLUN:
- Sen kullaniciya cevap veren yuzeysel bir chat botu degilsin.
- Sen sistemin merkezi zekasi, sefi ve operasyonel karar vericisisin.
- Scout, PatternMatcher, Brain, GemmaDecisionCore, Strategist, GhostSimulator, Auditor, RiskManager, StateTracker ve API akisini bilirsin.
- Sana verilen SISTEM DOSYASI statik bilgi, CANLI DURUM ise anlik operasyon bilgisidir.

KONUSMA KURALLARI:
- Turkce, net, dogal ve kendinden emin konus.
- Kullanici sorusunu tekrar etme.
- JSON, anahtar-deger listesi, rapor dump'i veya "kullanici soyle sordu" tarzi ifadeler kullanma.
- Statik mimari ve kod sorularinda "bilmiyorum" deme; SISTEM DOSYASI'ndan cevap ver.
- Canli veri gercekten yoksa sadece eksik olan kismi belirt.
- Cevabi genelde 2-5 cumlede ver. Gereksiz uzunluk yapma.
- Gerekiyorsa dosya veya ajan isimlerini dogrudan soyle.
"""


class ChatEngine:
    """Katman 5 - hizli, context-aware, Gemma odakli sohbet motoru."""

    def __init__(self, db, brain, agents: Dict[str, Any] = None):
        self.db = db
        self.brain = brain
        self.agents = agents or {}
        self.state_tracker = None
        self.risk_manager = None
        self.rca_engine = None
        self._snapshot_cache: Dict[str, Any] = {}
        self._snapshot_cache_at = 0.0
        self._health_cache: Dict[str, Any] = {}
        self._health_cache_at = 0.0
        self._system_dossier = self._build_system_dossier()

    def register_agent(self, name: str, agent):
        self.agents[name] = agent

    async def respond(self, message: str) -> str:
        msg = message.strip()
        if not msg:
            return "Mesaj bos."

        try:
            command_response = await self._try_operational_command(msg)
            if command_response:
                return command_response

            profile = self._analyze_request(msg)
            context = await self._collect_context(msg, profile)
            return await self._ask_gemma(msg, context, profile)
        except Exception as exc:
            logger.error("Chat error: %s", exc)
            return f"Bir hata olustu: {str(exc)[:120]}"

    def _analyze_request(self, msg: str) -> Dict[str, Any]:
        lowered = msg.lower()
        symbols = self._extract_symbols(msg)
        architecture = bool(re.search(r"mimari|katman|sistem|altyapi|kod|dosya|agent|ajan|gorev|gorevin|nasil calis|akis|strateji|plan|hangi model|model kullan|llm|local model|ollama|gemma", lowered))
        prices = bool(symbols or re.search(r"fiyat|price|kur|kac|ne kadar|btc|eth|sol|avax|doge|coin", lowered))
        signals = bool(re.search(r"sinyal|signal|firsat|alarm|uyari", lowered))
        positions = bool(re.search(r"pozisyon|simulasyon|trade|islem|acik pozisyon|pnl", lowered))
        learning = bool(re.search(r"ogrend|ogren|brain|beyin|pattern|dogruluk|neden|rca|hata|basari", lowered))
        agents = bool(re.search(r"agent|ajan|scout|strategist|ghost|auditor|patternmatcher|risk|state", lowered))
        history = bool(re.search(r"az once|demin|onceki|gecmis|hatirliyor|hatirla", lowered))
        greeting = self._is_greeting(msg)
        return {
            "symbols": symbols,
            "needs_prices": prices,
            "needs_signals": signals,
            "needs_positions": positions,
            "needs_learning": learning,
            "needs_agents": agents,
            "needs_history": history,
            "needs_dossier": architecture or greeting,
            "greeting": greeting,
        }

    async def _collect_context(self, user_msg: str, profile: Dict[str, Any]) -> str:
        snapshot = await self._get_live_snapshot(user_msg, profile)
        sections: List[str] = []

        if profile["needs_dossier"]:
            sections.append("SISTEM DOSYASI:\n" + self._system_dossier)

        live = self._format_live_snapshot(snapshot, profile)
        if live:
            sections.append("CANLI DURUM:\n" + live)

        if profile["needs_history"] and snapshot.get("chat_memory"):
            sections.append("SOHBET HAFIZASI:\n" + snapshot["chat_memory"])

        context = "\n\n".join(sections).strip()
        if len(context) > MAX_CONTEXT_CHARS:
            context = context[:MAX_CONTEXT_CHARS] + "\n\n[context kisaltildi]"
        return context or "CANLI DURUM: Kritik context yok."

    async def _get_live_snapshot(self, user_msg: str, profile: Dict[str, Any]) -> Dict[str, Any]:
        now = time.monotonic()
        if not self._snapshot_cache or now - self._snapshot_cache_at > CHAT_CACHE_TTL:
            summary, open_sims, pending_signals = await asyncio.gather(
                self.db.get_dashboard_summary(),
                self.db.get_open_simulations(),
                self.db.get_pending_signals(),
            )
            self._snapshot_cache = {
                "summary": summary,
                "open_simulations": open_sims[:5],
                "pending_signals": pending_signals[:5],
                "brain": self.brain.get_brain_status() if self.brain else {},
                "risk": self.risk_manager.get_risk_summary() if self.risk_manager else {},
                "state": self.state_tracker.get_state_summary() if self.state_tracker else {},
                "decision_core": self._get_decision_snapshot(),
            }
            self._snapshot_cache_at = now

        snapshot = dict(self._snapshot_cache)

        extras = []
        if profile["needs_prices"]:
            extras.append(self._get_price_snapshot(profile["symbols"]))
        else:
            extras.append(asyncio.sleep(0, result={}))

        if profile["needs_learning"]:
            extras.append(self.db.get_learning_stats())
            extras.append(self.db.get_rca_stats())
        else:
            extras.append(asyncio.sleep(0, result={}))
            extras.append(asyncio.sleep(0, result={}))

        if profile["needs_agents"]:
            extras.append(self._get_agent_health_snapshot())
        else:
            extras.append(asyncio.sleep(0, result={}))

        if profile["needs_history"]:
            extras.append(self._chat_memory(user_msg))
        else:
            extras.append(asyncio.sleep(0, result=""))

        prices, learning_stats, rca_stats, agent_health, chat_memory = await asyncio.gather(*extras)
        snapshot.update({
            "prices": prices,
            "learning": learning_stats,
            "rca": rca_stats,
            "agent_health": agent_health,
            "chat_memory": chat_memory,
        })
        return snapshot

    async def _get_price_snapshot(self, symbols: List[str]) -> Dict[str, float]:
        from config import Config
        targets = symbols or Config.WATCHLIST[:6]
        tasks = [self.db.get_recent_trades(symbol, limit=1) for symbol in targets]
        rows = await asyncio.gather(*tasks, return_exceptions=True)
        prices: Dict[str, float] = {}
        for symbol, trades in zip(targets, rows):
            if isinstance(trades, Exception) or not trades:
                continue
            prices[symbol] = float(trades[0]["price"])
        return prices

    async def _get_agent_health_snapshot(self) -> Dict[str, str]:
        now = time.monotonic()
        if self._health_cache and now - self._health_cache_at <= HEALTH_CACHE_TTL:
            return dict(self._health_cache)

        tasks = [agent.health_check() for agent in self.agents.values()]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        health: Dict[str, str] = {}
        for name, result in zip(self.agents.keys(), results):
            if isinstance(result, Exception):
                health[name] = "bilinmiyor"
            else:
                health[name] = "aktif" if result.get("healthy") else "sorunlu"
        self._health_cache = health
        self._health_cache_at = now
        return dict(health)

    def _get_decision_snapshot(self) -> Dict[str, Any]:
        try:
            from gemma_decision_core import get_decision_core
            core = get_decision_core()
            stats = core.get_stats()
            recent = core.get_recent_decisions(limit=2)
            return {"stats": stats, "recent": recent}
        except Exception:
            return {}

    def _format_live_snapshot(self, snapshot: Dict[str, Any], profile: Dict[str, Any]) -> str:
        lines: List[str] = []
        summary = snapshot.get("summary", {})
        brain = snapshot.get("brain", {})
        state = snapshot.get("state", {})
        risk = snapshot.get("risk", {})
        decision = snapshot.get("decision_core", {})

        lines.append(
            "Ozet: "
            f"trade={summary.get('total_trades', 0):,}, "
            f"aktif_sinyal={summary.get('active_signals', 0)}, "
            f"acik_pozisyon={summary.get('open_simulations', 0)}, "
            f"toplam_pnl=${summary.get('total_pnl', 0):.2f}"
        )
        lines.append(
            "Zeka: "
            f"pattern={brain.get('total_patterns', 0)}, "
            f"dogruluk=%{brain.get('accuracy', 0) * 100:.1f}, "
            f"mod={state.get('mode', 'bilinmiyor')}"
        )

        if risk:
            lines.append(
                "Risk: "
                f"gunluk_pnl={risk.get('daily_pnl', 0):.2f}%, "
                f"drawdown={risk.get('drawdown', 0)}, "
                f"cooldown={'aktif' if risk.get('cooldown_active') else 'yok'}"
            )

        recent = decision.get("recent", [])
        if recent:
            decision_parts = []
            for item in recent[:2]:
                decision_parts.append(
                    f"{item.get('symbol', '?')} {item.get('decision', '?')} {item.get('direction', '?')}"
                )
            lines.append("Son kararlar: " + ", ".join(decision_parts))

        if profile["needs_prices"] and snapshot.get("prices"):
            price_parts = [f"{symbol}=${price:,.2f}" for symbol, price in snapshot["prices"].items()]
            lines.append("Fiyatlar: " + ", ".join(price_parts))

        if profile["needs_signals"] and snapshot.get("pending_signals"):
            signal_parts = []
            for signal in snapshot["pending_signals"][:4]:
                meta = signal.get("metadata", {})
                if isinstance(meta, str):
                    try:
                        meta = json.loads(meta)
                    except Exception:
                        meta = {}
                signal_parts.append(
                    f"{signal.get('symbol', '?')} {meta.get('position_bias', '?')} guven=%{float(signal.get('confidence', 0)) * 100:.0f}"
                )
            lines.append("Sinyaller: " + ", ".join(signal_parts))

        if profile["needs_positions"] and snapshot.get("open_simulations"):
            sim_parts = []
            prices = snapshot.get("prices", {})
            for simulation in snapshot["open_simulations"][:4]:
                entry = float(simulation.get("entry_price", 0) or 0)
                current = float(prices.get(simulation.get("symbol"), 0) or 0)
                side = simulation.get("side", "?")
                pnl_part = ""
                if entry and current:
                    pct = ((current - entry) / entry * 100) if side == "long" else ((entry - current) / entry * 100)
                    pnl_part = f" {pct:+.2f}%"
                sim_parts.append(f"{simulation.get('symbol', '?')} {side} @{entry:,.2f}{pnl_part}")
            lines.append("Pozisyonlar: " + ", ".join(sim_parts))

        if profile["needs_learning"] and snapshot.get("learning"):
            learning = snapshot["learning"]
            rca = snapshot.get("rca", {})
            lines.append(
                "Ogrenme: "
                f"kayit={learning.get('total', 0)}, "
                f"basari=%{learning.get('accuracy', 0):.1f}, "
                f"ortalama_pnl={learning.get('avg_pnl', 0):.2f}"
            )
            if rca:
                lines.append(f"RCA: toplam={rca.get('total', 0)}, dagilim={rca.get('distribution', {})}")

        if profile["needs_agents"] and snapshot.get("agent_health"):
            lines.append(
                "Ajanlar: " + ", ".join(
                    f"{name}={status}" for name, status in snapshot["agent_health"].items()
                )
            )

        return "\n".join(lines)

    def _build_system_dossier(self) -> str:
        from config import Config
        from llm_client import (
            DEFAULT_BACKEND, DEFAULT_BASE_URL, DEFAULT_MODEL,
            DEFAULT_NUM_CTX, DEFAULT_NUM_THREAD, MODEL_CANDIDATES,
        )
        watchlist = ", ".join(Config.WATCHLIST)
        candidate_list = ", ".join(MODEL_CANDIDATES[:5])
        return (
            "Mimari: 5 katmanli yapi var. Katman1 Scout veri toplar; Katman2 PatternMatcher ve Brain ogrenme yapar; "
            "Katman3 GemmaDecisionCore nihai karari verir; Katman4 Strategist, RiskManager, GhostSimulator ve Auditor aksiyon/geri besleme tarafidir; "
            "Katman5 ChatEngine kullaniciyla konusur.\n"
            "Veri akisi: Scout -> PatternMatcher -> Brain -> GemmaDecisionCore -> Risk kontrolu -> Strategist/Ghost -> Auditor geri besleme.\n"
            "Kod haritasi: main.py orkestrasyon; database.py PostgreSQL erisim; scout_agent.py veri toplama; strategist_agent.py sinyal uretimi; "
            "ghost_simulator_agent.py paper trade; auditor_agent.py hata analizi; brain.py pattern hafiza ve ogrenme; gemma_decision_core.py merkezi karar; "
            "chat_engine.py dogal dil arayuzu; risk_manager.py limitler; state_tracker.py mod yonetimi; llm_client.py LLM baglantisi.\n"
            "Portlar: API 3001, Python directive/chat 3002, dashboard 5173, PostgreSQL 5432.\n"
            "Veritabani ana tablolar: trades, price_movements, signals, simulations, pattern_records, brain_learning_log, rca_results, agent_heartbeat, bot_state, state_history, chat_messages.\n"
            "Strateji omurgasi: pattern eslesme, momentum ve geri besleme ile risk kontrollu paper trading. Risk limitleri gunluk islem, drawdown, ard arda kayip ve acik pozisyon sinirlariyla korunur.\n"
            f"Izlenen semboller: {watchlist}.\n"
            "Not: Binance ve Bybit spot/futures global WS kaynaklari aktif; bolgesel erisim sorunu olursa fallback endpoint ve tunnel URL'leri kullanilir.\n"
            f"LLM yapisi: backend={DEFAULT_BACKEND}, url={DEFAULT_BASE_URL}, varsayilan model={DEFAULT_MODEL}, "
            f"context penceresi={DEFAULT_NUM_CTX} token, cpu_thread={DEFAULT_NUM_THREAD}. "
            f"Model oncelik sirasi: {candidate_list}... (Ollama'da bulunan ilk uygun model secilir). "
            "Aktif model /llm komutuyla veya API /api/llm/status endpoint'iyle sorgulanabilir. "
            "Sunucu: 24 GB RAM, 12 vCPU."
        )

    async def _chat_memory(self, msg: str) -> str:
        try:
            history = await self.db.get_chat_messages(limit=6)
            if len(history) < 2:
                return ""
            msg_words = set(msg.lower().split())
            best_text = ""
            best_score = 0.0
            for item in history:
                if item.get("role") != "user":
                    continue
                text = str(item.get("message", ""))
                words = set(text.lower().split())
                union = len(msg_words | words)
                if not union:
                    continue
                score = len(msg_words & words) / union
                if score > 0.34 and score > best_score:
                    best_score = score
                    best_text = text[:120]
            return f"Benzer onceki soru: {best_text}" if best_text else ""
        except Exception:
            return ""

    async def _ask_gemma(self, user_msg: str, context: str, profile: Dict[str, Any]) -> str:
        try:
            from llm_client import get_llm_client
            client = get_llm_client()
            sentence_budget = "2 cumle" if profile["greeting"] else "4 cumle"
            prompt = (
                f"Kullanici sorusu:\n{user_msg}\n\n"
                f"{context}\n\n"
                f"Gorev:\n"
                f"- Kullaniciya dogal Turkce ile cevap ver.\n"
                f"- Cevabi en fazla {sentence_budget} icinde tut.\n"
                f"- Statik sistem sorularini SISTEM DOSYASI'na dayanarak net cevapla.\n"
                f"- Canli veri sorularinda sadece ilgili olani soyle; gereksiz dump yapma.\n"
            )
            timeout = 18 if profile["greeting"] else 30
            response = await client.generate(
                prompt=prompt,
                system=SYSTEM_PROMPT,
                temperature=0.12,
                json_mode=False,
                timeout_override=min(timeout, FULL_CHAT_TIMEOUT),
                model_override=None,
            )
            text = (response.text or "").strip()
            if response.success and text and not self._needs_repair(text, profile):
                return text

            fast_model = os.getenv("QUENBOT_LLM_FAST_MODEL")
            if fast_model and fast_model != client.model:
                fast = await client.generate(
                    prompt=prompt,
                    system=SYSTEM_PROMPT,
                    temperature=0.1,
                    json_mode=False,
                    timeout_override=QUICK_CHAT_TIMEOUT,
                    model_override=fast_model,
                )
                fast_text = (fast.text or "").strip()
                if fast.success and fast_text and not self._needs_repair(fast_text, profile):
                    return fast_text

            repair = await client.generate(
                prompt=(
                    f"Asagidaki taslak yetersiz ya da yanlis:\n{text or '[bos]'}\n\n"
                    f"Kullanici sorusu: {user_msg}\n\n"
                    f"{context}\n\n"
                    "Yeni ve temiz cevap yaz. Turkce, dogal, net, tekrar yok, rapor dili yok, JSON yok."
                ),
                system=SYSTEM_PROMPT,
                temperature=0.08,
                json_mode=False,
                timeout_override=20,
                model_override=None,
            )
            repaired = (repair.text or "").strip()
            if repair.success and repaired and not self._needs_repair(repaired, profile):
                return repaired
            return self._fallback(user_msg)
        except Exception as exc:
            logger.error("Gemma error: %s", exc)
            return self._fallback(user_msg)

    async def _try_operational_command(self, msg: str) -> Optional[str]:
        """Execute deterministic operational commands without LLM roundtrip."""
        lowered = msg.lower().strip()
        if lowered in {"/help", "yardim", "komutlar"}:
            return (
                "Komutlar: /status, /llm, /watch add <SEMBOL>, /watch remove <SEMBOL>, "
                "/agent run strategist|auditor|pattern|ghost, /pattern <SEMBOL>, "
                "/directive <metin>, /model <model_adi>."
            )

        if lowered in {"/status", "durum", "sistem durumu", "ozet"}:
            profile = {
                "needs_prices": False,
                "needs_signals": True,
                "needs_positions": True,
                "needs_learning": False,
                "needs_agents": True,
            }
            snapshot = await self._get_live_snapshot(msg, profile)
            return self._format_live_snapshot(snapshot, profile)

        if lowered in {"/llm", "/llm durumu", "llm", "hangi model", "aktif model",
                       "llm bilgi", "model bilgi", "model durumu", "llm durumu"}:
            from llm_client import get_llm_client, DEFAULT_NUM_CTX, DEFAULT_NUM_THREAD
            client = get_llm_client()
            stats = client.get_stats()
            models = await client.list_models()
            model_list = ", ".join(models) if models else "yok"
            avg_ms = stats.get("avg_latency_ms", 0)
            return (
                f"Aktif model: {client.model} | "
                f"Backend: {stats.get('backend', '?')} | "
                f"URL: {stats.get('base_url', '?')} | "
                f"Ctx penceresi: {DEFAULT_NUM_CTX} token | "
                f"CPU thread: {DEFAULT_NUM_THREAD} | "
                f"Toplam cagri: {stats.get('total_calls', 0)} | "
                f"Ort gecikme: {avg_ms:.0f}ms | "
                f"Yuklu modeller: {model_list}"
            )

        if lowered.startswith("/directive ") or lowered.startswith("direktif "):
            text = msg.split(" ", 1)[1].strip() if " " in msg else ""
            if not text:
                return "Direktif metni bos olamaz."
            from directive_store import get_directive_store
            store = get_directive_store()
            await store.set_master_directive(text)
            return "Merkezi direktif guncellendi ve tum ajan LLM cagri zincirine uygulandi."

        if lowered.startswith("/model ") or lowered.startswith("model "):
            model_name = msg.split(" ", 1)[1].strip() if " " in msg else ""
            if not model_name:
                return "Model adi gerekli. Ornek: /model quenbot-brain:latest"
            from llm_client import get_llm_client
            client = get_llm_client()
            models = await client.list_models()
            if models and not any(m == model_name or m.startswith(model_name + ":") for m in models):
                return f"Model bulunamadi. Yuklu modeller: {', '.join(models)}"
            client.model = model_name
            return f"Aktif model guncellendi: {client.model}"

        watch_match = re.search(r"(?:/watch\s+)?(add|ekle|remove|sil)\s+([A-Za-z]{2,12}(?:USDT)?)", msg, re.IGNORECASE)
        if watch_match:
            action = watch_match.group(1).lower()
            symbol = self._normalize_symbol(watch_match.group(2))
            if action in {"add", "ekle"}:
                scout = self.agents.get("Scout")
                if not scout:
                    return "Scout ajanina erisilemiyor."
                await scout.add_symbol_live(symbol)
                return f"{symbol} watchlist'e eklendi ve Scout canli takip baslatti."
            await self.db.remove_user_watchlist(symbol, 'all', 'spot')
            await self.db.remove_user_watchlist(symbol, 'all', 'futures')
            return f"{symbol} watchlist'ten silindi."

        if lowered.startswith("/pattern ") or "pattern tara" in lowered:
            symbol = self._extract_command_symbol(msg)
            if not symbol:
                return "Pattern tarama icin sembol gerekli. Ornek: /pattern BTCUSDT"
            pm = self.agents.get("PatternMatcher")
            if not pm:
                return "PatternMatcher ajanina erisilemiyor."
            result = await pm.deep_analyze_symbol(symbol)
            return f"Pattern tarama tamamlandi: {symbol} | match={result.get('matches', 0)} | best={result.get('best_similarity', 0):.4f}"

        if lowered.startswith("/agent run ") or "analiz baslat" in lowered or "ajan calistir" in lowered:
            return await self._run_agent_command(lowered)

        return None

    async def _run_agent_command(self, lowered: str) -> str:
        if "strategist" in lowered:
            agent = self.agents.get("Strategist")
            if not agent:
                return "Strategist ajanina erisilemiyor."
            self._fire_and_forget(agent._analyze_strategies(), "strategist_manual_analyze")
            return "Strategist icin manuel analiz tetiklendi."

        if "auditor" in lowered:
            agent = self.agents.get("Auditor")
            if not agent:
                return "Auditor ajanina erisilemiyor."
            self._fire_and_forget(agent._analyze_failed_signals(), "auditor_manual_audit")
            return "Auditor icin manuel denetim tetiklendi."

        if "pattern" in lowered:
            agent = self.agents.get("PatternMatcher")
            if not agent:
                return "PatternMatcher ajanina erisilemiyor."
            self._fire_and_forget(agent._refresh_signature_cache(), "pattern_refresh_cache")
            return "PatternMatcher imza cache yenileme tetiklendi."

        if "ghost" in lowered:
            agent = self.agents.get("Ghost")
            if not agent:
                return "Ghost ajanina erisilemiyor."
            self._fire_and_forget(agent._process_pending_signals(), "ghost_manual_process")
            return "Ghost icin bekleyen sinyal isleme tetiklendi."

        return "Calistirilacak ajan belirtilmedi. Ornek: /agent run strategist"

    def _fire_and_forget(self, coro, tag: str):
        async def _runner():
            try:
                await coro
            except Exception as exc:
                logger.warning("Manual command task failed [%s]: %s", tag, exc)
        asyncio.create_task(_runner())

    def _normalize_symbol(self, raw: str) -> str:
        s = raw.upper().strip()
        return s if s.endswith("USDT") else f"{s}USDT"

    def _extract_command_symbol(self, msg: str) -> Optional[str]:
        m = re.search(r"([A-Za-z]{2,12}(?:USDT)?)", msg)
        if not m:
            return None
        return self._normalize_symbol(m.group(1))

    def _needs_repair(self, text: str, profile: Dict[str, Any]) -> bool:
        if not self._is_valid_natural_response(text):
            return True
        if profile["needs_dossier"] and re.search(r"bilmiyorum|emin degilim|bilgim yok", text, re.IGNORECASE):
            return True
        if re.search(r"kullanici|soru sordu|bildirmistir", text, re.IGNORECASE):
            return True
        return False

    def _extract_symbols(self, msg: str) -> List[str]:
        from config import Config
        lowered = msg.upper()
        symbols: List[str] = []
        aliases = {
            "BTC": "BTCUSDT",
            "ETH": "ETHUSDT",
            "BNB": "BNBUSDT",
            "SOL": "SOLUSDT",
            "ADA": "ADAUSDT",
            "DOT": "DOTUSDT",
            "LINK": "LINKUSDT",
            "LTC": "LTCUSDT",
            "XRP": "XRPUSDT",
            "BCH": "BCHUSDT",
            "DOGE": "DOGEUSDT",
            "AVAX": "AVAXUSDT",
            "APT": "APTUSDT",
            "OP": "OPUSDT",
            "PNUT": "PNUTUSDT",
            "HFT": "HFTUSDT",
            "CRV": "CRVUSDT",
            "LDO": "LDOUSDT",
            "BOME": "BOMEUSDT",
        }
        for symbol in Config.WATCHLIST:
            short_name = symbol.replace("USDT", "")
            if symbol in lowered or short_name in lowered:
                symbols.append(symbol)
        for alias, symbol in aliases.items():
            if alias in lowered and symbol not in symbols:
                symbols.append(symbol)
        return symbols[:6]

    def _is_greeting(self, msg: str) -> bool:
        lowered = msg.lower().strip()
        greetings = ["selam", "merhaba", "sa", "slm", "hey", "hi", "hello", "günaydın", "iyi akşamlar"]
        return any(lowered == item or lowered.startswith(item + " ") for item in greetings)

    def _is_valid_natural_response(self, text: str) -> bool:
        cleaned = text.strip()
        if not cleaned or cleaned[0] in "[{":
            return False
        if re.search(r'"(decision|confidence|direction|approved|action)"\s*:', cleaned, re.IGNORECASE):
            return False
        letters = sum(1 for char in cleaned if char.isalpha())
        noisy = sum(1 for char in cleaned if char in '{}[]`~|:_"')
        return letters >= 16 and noisy < letters

    def _fallback(self, msg: str) -> str:
        if STRICT_LLM:
            return "Gemma su an zamaninda net bir yanit uretemedi. Sistem strict modda oldugu icin tahmini cevap uretmiyorum."
        if self._is_greeting(msg):
            return "Merhaba. Buradayim ve sistemi izliyorum."
        return "Su an yanit motoru gecikti. Sorunu yeniden daha kisa sorarsan hizli cevap verebilirim."
