"""
Chat Engine - Katman 5: Arayuz
==============================
QuenBot'un kullaniciyla hizli ve dogal konustugu katman.
"""

import asyncio
import json
import logging
import os
import re
import time
from typing import Any, Dict, List

logger = logging.getLogger(__name__)

ACTIVE_LLM_MODEL = os.getenv("QUENBOT_LLM_MODEL", "supergemma-26b")
CHAT_CACHE_TTL = int(os.getenv("QUENBOT_CHAT_CACHE_TTL", "8"))
HEALTH_CACHE_TTL = int(os.getenv("QUENBOT_HEALTH_CACHE_TTL", "20"))
MAX_CONTEXT_CHARS = int(os.getenv("QUENBOT_CHAT_CONTEXT_CHARS", "1800"))
CHAT_TIMEOUT = int(os.getenv("QUENBOT_CHAT_FULL_TIMEOUT", "30"))
QUICK_CHAT_TIMEOUT = int(os.getenv("QUENBOT_CHAT_QUICK_TIMEOUT", "15"))
CHAT_QUICK_MAX_TOKENS = int(os.getenv("QUENBOT_CHAT_QUICK_MAX_TOKENS", "140"))
CHAT_FULL_MAX_TOKENS = int(os.getenv("QUENBOT_CHAT_FULL_MAX_TOKENS", "280"))
# Gemma 3 12B model needs ~5-15s for quality responses
CHAT_MAX_TOTAL_LATENCY = float(os.getenv("QUENBOT_CHAT_MAX_TOTAL_LATENCY", "25"))

# Dedicated chat LLM lane — completely separate from the decision/pattern LLM pool.
# Prevents chat from ever competing with 18-second decision calls.
CHAT_DEDICATED_MODEL = os.getenv("QUENBOT_CHAT_MODEL", ACTIVE_LLM_MODEL)
CHAT_DEDICATED_TIMEOUT = int(os.getenv("QUENBOT_CHAT_LLM_TIMEOUT", "25"))
CHAT_DEDICATED_NUM_THREAD = int(os.getenv("QUENBOT_CHAT_LLM_NUM_THREAD", "8"))
CHAT_DEDICATED_NUM_CTX = int(os.getenv("QUENBOT_CHAT_LLM_NUM_CTX", "4096"))

SYSTEM_PROMPT = """Sen QuenBot'sun.

KIMLIGIN:
- Sen kullaniciyla gercekten konusan merkezi zekasin.
- Scout veri toplar, PatternMatcher benzerlik bulur, Brain ogrenir, GemmaDecisionCore karar verir,
    Strategist sinyal uretir, MAMIS mikro-yapi istihbarati saglar, GhostSimulator paper trade yapar, Auditor sistemi gelistirir.
- Tum bu akisin amaci risk kontrollu, geri beslemeli, cok katmanli trading zekasi kurmaktir.

STRATEJI OZETIN:
- Scout spot ve vadeli trade akisini toplar; Binance L2 bookTicker ile best bid/ask mikro-yapi akisina da bakar.
- MAMIS event-bar, OFI, VPIN ve CVD hesaplar; iceberg, spoofing ve market-maker paterni arar.
- Strategist klasik pattern, momentum ve signature sinyallerini MAMIS mikro-yapi sinyalleriyle agirlikli ensemble kuraliyla birlestirir.
- Ghost minimum hedef getirili paper trade ile sonucu geri bildirir.
- Auditor hatalari bulur ve sistemi duzeltir.
- Sen tum bunlarin ustunde sistemi anlayan, kullaniciya dogal Turkce ile yanit veren modelsin.

METRIK YORUM KURALI:
- `toplam_trade` metrigini sadece kapanan simulasyon/paper-trade olarak yorumla.
- Ham borsa trade akislarini (tick) gercek islem adedi gibi anlatma.
- `risk_red` sayisini "acilan riskli pozisyon" diye degil, "risk filtresinde elenen sinyal" olarak anlat.
- MAMIS metriklerini soyle yorumla: OFI likidite dengesini, VPIN toksik akisi, CVD agresif alici/satici baskisini gosterir.
- Sistem paper-trade odaklidir; gercek para islemi varsayimi yapma.

KONUSMA TARZI:
- Turkce, dogal, net, kisa ve kendinden emin.
- JSON verme.
- Gereksiz rapor dili kullanma.
- Veri yoksa uydurma yapma; eksik veriyi acikca soyle.
"""

DIRECT_COMMAND_SYSTEM_PROMPT = """Sen QuenBot'un dogrudan komut yorumlayicisisin.

GOREVIN:
- Kullanicinin mesajini yorumla.
- Sadece desteklenen komutlari JSON aksiyon listesine donustur.
- Desteklenen aksiyonlar: watchlist_add, watchlist_remove, risk_update, master_directive_update, system_mode_update, cleanup_run, system_diagnostic, symbol_analysis, code_change_request.

CIKTI JSON SEMASI:
{
    "reply": "kisa Turkce cevap",
    "actions": [
        {"type": "watchlist_add", "symbols": ["BTCUSDT", "ETHUSDT"]},
        {"type": "watchlist_remove", "symbols": ["SOLUSDT"]},
        {"type": "risk_update", "changes": {"max_daily_trades": 12, "max_open_positions": 4, "max_daily_loss_pct": -3, "max_drawdown_pct": -8}},
        {"type": "master_directive_update", "text": "sadece yuksek hacimli coinlerde calis"},
        {"type": "system_mode_update", "mode": "PRODUCTION"},
        {"type": "cleanup_run", "dry_run": true},
        {"type": "system_diagnostic"},
        {"type": "symbol_analysis", "symbol": "BTCUSDT"},
        {"type": "code_change_request", "prompt": "dashboard chat paneline yeni buton ekle", "mode": "preview"}
    ]
}

KURALLAR:
- Sadece gecerli JSON don.
- Soru/sohbet ise actions bos dizi olsun.
- Desteklenmeyen komutlarda aksiyon uretme.
- Symbol alanlarini buyuk harf ve USDT sonekli don.
- Risk degisikliklerinde sadece sayisal deger kullan.
- mode alani sadece BOOTSTRAP, LEARNING, WARMUP, PRODUCTION veya AUTO olsun.
- Temizlik isteginde varsayilan dry_run=true olsun; kullanici acikca uygula/sil/calistor derse false kullan.
- MAMIS, order-book, OFI, VPIN, CVD veya iceberg/spoofing ile ilgili sorular sohbet sorusudur; aksiyon uretme.
- Kod, dosya, dashboard, API, strateji mantigi, bileşen veya bug fix taleplerinde code_change_request kullan.
- code_change_request.mode degeri preview veya apply olsun; kullanici acikca uygula/calistir derse apply sec.
"""


class ChatEngine:
    """Katman 5 - hizli, cache'li, dogal sohbet motoru."""

    def __init__(self, db, brain, agents: Dict[str, Any] = None):
        self.db = db
        self.brain = brain
        self.agents = agents or {}
        self.state_tracker = None
        self.risk_manager = None
        self.rca_engine = None
        self._snapshot_cache: Dict[str, Any] = {}
        self._snapshot_cache_at = 0.0
        self._health_cache: Dict[str, str] = {}
        self._health_cache_at = 0.0
        # Dedicated chat client — own semaphore, never competes with decision LLM
        self._chat_client = None

    def register_agent(self, name: str, agent):
        self.agents[name] = agent

    def get_chat_model_name(self) -> str:
        if self._chat_client is not None and getattr(self._chat_client, "model", None):
            return str(self._chat_client.model)
        return (CHAT_DEDICATED_MODEL or ACTIVE_LLM_MODEL).strip() or ACTIVE_LLM_MODEL

    def get_assistant_identity(self) -> Dict[str, str]:
        return {
            "name": "SuperGemma Command",
            "model": self.get_chat_model_name(),
            "role": "direct_operator",
        }

    def _get_chat_client(self):
        """Return (and lazily create) a dedicated LLMClient for chat.

        This client has its own asyncio.Semaphore that is completely independent
        from the shared decision/pattern LLM semaphore.  Chat calls will never
        be blocked by an 18-second decision evaluation.
        """
        if self._chat_client is None:
            from llm_client import LLMClient
            model = CHAT_DEDICATED_MODEL.strip() or ACTIVE_LLM_MODEL
            client = LLMClient(
                model=model,
                timeout=CHAT_DEDICATED_TIMEOUT,
                max_tokens=CHAT_FULL_MAX_TOKENS,
                max_retries=0,
            )
            # Replace the default shared semaphore with a private one (1 slot)
            client._semaphore = asyncio.Semaphore(1)
            # Override inference params for the lighter chat model
            client.num_thread = CHAT_DEDICATED_NUM_THREAD
            client.num_ctx = CHAT_DEDICATED_NUM_CTX
            self._chat_client = client
            logger.info(
                "Chat dedicated LLM client created: model=%s timeout=%ss ctx=%s threads=%s",
                model, CHAT_DEDICATED_TIMEOUT, CHAT_DEDICATED_NUM_CTX, CHAT_DEDICATED_NUM_THREAD,
            )
        return self._chat_client

    async def respond(self, message: str, routed_actions: List[Dict[str, Any]] | None = None) -> str:
        msg = message.strip()
        if not msg:
            return "Mesaj bos."

        try:
            t0 = time.monotonic()
            lightweight = self._is_lightweight_message(msg)
            context = await self._collect_context(msg, lightweight=lightweight)
            if routed_actions:
                context = f"{context}\n\nUYGULANAN KOMUTLAR:\n{self._format_routed_actions(routed_actions)}"
            elapsed = time.monotonic() - t0
            budget_left = max(8.0, CHAT_MAX_TOTAL_LATENCY - elapsed)  # Minimum 8s budget
            return await self._ask_gemma(
                msg,
                context,
                lightweight=lightweight,
                budget_seconds=budget_left,
            )
        except Exception as exc:
            import traceback
            logger.error("Chat error: %s\n%s", exc, traceback.format_exc())
            return "Modelden yanit alirken teknik bir sorun oldu. Lutfen tekrar dener misin?"

    async def interpret_direct_command(self, message: str) -> Dict[str, Any]:
        msg = (message or "").strip()
        if not msg:
            return {"reply": "", "actions": []}

        client = self._get_chat_client()
        response = await client.generate(
            prompt=(
                f"Kullanici mesaji:\n{msg}\n\n"
                "Mesaji desteklenen QuenBot aksiyonlarina cevir."
            ),
            system=DIRECT_COMMAND_SYSTEM_PROMPT,
            temperature=0.05,
            json_mode=True,
            timeout_override=20,  # Gemma 3 12B için yeterli
            prefer_fast_fail=False,
            max_tokens_override=220,
            max_retries_override=0,
        )

        if not response.success:
            return {"reply": "", "actions": []}

        data = response.as_json() or {}
        if not isinstance(data, dict):
            return {"reply": "", "actions": []}

        actions = data.get("actions", [])
        if not isinstance(actions, list):
            actions = []

        reply = data.get("reply", "")
        if not isinstance(reply, str):
            reply = ""

        return {"reply": reply.strip(), "actions": actions[:8]}

    def build_command_response(self, routed_actions: List[Dict[str, Any]]) -> str:
        if not routed_actions:
            return "Komut uygulanmadi."

        lines: List[str] = []
        for action in routed_actions[:5]:
            action_type = action.get("type")
            if action_type == "watchlist_add":
                lines.append(f"Watchlist'e eklendi: {', '.join(action.get('symbols', [])[:8])}.")
            elif action_type == "watchlist_remove":
                lines.append(f"Watchlist'ten cikarildi: {', '.join(action.get('symbols', [])[:8])}.")
            elif action_type == "risk_update":
                changes = action.get("changes", {})
                rendered = ", ".join(f"{k}={v}" for k, v in changes.items())
                lines.append(f"Risk limitleri guncellendi: {rendered}.")
            elif action_type == "master_directive_update":
                lines.append(f"Ana direktif guncellendi: {str(action.get('text', ''))[:120]}.")
            elif action_type == "system_mode_update":
                forced = "kilitlendi" if action.get("forced") else "otomatikte"
                lines.append(f"Sistem modu {action.get('mode', '?')} olarak ayarlandi, mod {forced}.")
            elif action_type == "cleanup_run":
                if action.get("dry_run", True):
                    lines.append(
                        f"Temizlik raporu hazir: {action.get('stale_count', 0)} stale manifest bulundu, silme yapilmadi."
                    )
                else:
                    lines.append(
                        f"Temizlik calisti: {action.get('deleted_count', 0)} manifest silindi, {action.get('stale_count', 0)} stale kayit tarandi."
                    )
            elif action_type == "system_diagnostic":
                summary = action.get("summary", {})
                components = action.get("components", {})
                healthy_count = sum(1 for ok in components.values() if ok)
                lines.append(
                    f"Diagnostik tamam: mod={action.get('system_mode', '?')}, LLM={action.get('llm_model', '?')}, saglikli bilesen={healthy_count}/{len(components)}. Aktif sinyal={summary.get('active_signals', 0)}, acik simulasyon={summary.get('open_simulations', 0)}."
                )
            elif action_type == "symbol_analysis":
                overall = action.get("overall_signal", {})
                direction = overall.get("direction", "neutral")
                confidence = overall.get("confidence", 0)
                lines.append(
                    f"{action.get('symbol', '?')} analizi hazir: yon={direction}, guven={confidence}, fiyat={action.get('latest_price', 0)}. Son 24 saatte {action.get('recent_movement_count', 0)} hareket kaydi bulundu."
                )
            elif action_type == "code_change_request":
                extra = []
                if action.get("status"):
                    extra.append(f"durum={action.get('status')}")
                if action.get("selected_files"):
                    extra.append(f"dosya={', '.join(action.get('selected_files', [])[:4])}")
                if action.get("clarification"):
                    extra.append(f"not={str(action.get('clarification'))[:120]}")
                extra_text = (" " + " | ".join(extra)) if extra else ""
                lines.append(
                    f"Kod operatoru gorevi olusturuldu: {str(action.get('task_id', '?'))} mod={str(action.get('mode', 'preview'))}.{extra_text} {str(action.get('summary', ''))[:160]}"
                )
            else:
                lines.append(f"Komut uygulandi: {action_type}.")

        return " ".join(lines)

    async def _collect_context(self, user_msg: str, lightweight: bool = False) -> str:
        snapshot = await self._get_snapshot(user_msg, lightweight=lightweight)
        parts: List[str] = []

        summary = snapshot.get("summary", {})
        parts.append(
            "SISTEM OZETI:\n"
            f"- kapanan_sim_trade={summary.get('strategy_closed_trades', summary.get('total_trades', 0)):,}\n"
            f"- aktif_sinyal={summary.get('active_signals', 0)}\n"
            f"- acik_sim={summary.get('open_simulations', 0)}\n"
            f"- win_rate=%{summary.get('win_rate', 0):.1f}\n"
            f"- sim_pnl=${summary.get('total_pnl', 0):.2f}"
        )

        brain = snapshot.get("brain", {})
        parts.append(
            "BEYIN:\n"
            f"- pattern={brain.get('total_patterns', 0)}\n"
            f"- dogruluk=%{brain.get('accuracy', 0) * 100:.1f}"
        )

        if lightweight:
            context = "\n\n".join(parts)
            if len(context) > MAX_CONTEXT_CHARS:
                context = context[:MAX_CONTEXT_CHARS] + "\n\n[context kisaltildi]"
            return context

        if snapshot.get("state"):
            parts.append(f"MOD: {snapshot['state'].get('mode', 'BOOTSTRAP')}")

        signal_flow = snapshot.get("signal_flow", {})
        if signal_flow:
            window_h = signal_flow.get("window_hours", 0)
            parts.append(
                "SINYAL AKISI:\n"
                f"- son_{window_h}s_uretim={signal_flow.get('total', 0)}\n"
                f"- bekleyen={signal_flow.get('pending', 0)}\n"
                f"- islenen={signal_flow.get('processed', 0)}\n"
                f"- riskte_elenen={signal_flow.get('risk_rejected', 0)}"
            )

            latest = signal_flow.get("latest")
            if latest:
                parts.append(
                    "SON SINYAL:\n"
                    f"- {latest.get('symbol', '?')} {latest.get('signal_type', '?')}\n"
                    f"- durum={latest.get('status', '?')} conf={float(latest.get('confidence', 0) or 0) * 100:.0f}%"
                )

        if snapshot.get("risk"):
            risk = snapshot["risk"]
            parts.append(
                "RISK:\n"
                f"- gunluk_pnl={risk.get('daily_pnl', 0):.2f}%\n"
                f"- drawdown={risk.get('drawdown', 0)}\n"
                f"- cooldown={'aktif' if risk.get('cooldown_active') else 'yok'}"
            )

        prices = snapshot.get("prices", {})
        if prices:
            parts.append(
                "FIYATLAR:\n" + "\n".join(
                    f"- {symbol}: ${price:,.2f}" for symbol, price in prices.items()
                )
            )

        open_sims = snapshot.get("open_simulations", [])
        if open_sims:
            sim_lines = []
            for sim in open_sims[:4]:
                symbol = sim.get("symbol", "?")
                entry = float(sim.get("entry_price", 0) or 0)
                side = sim.get("side", "?")
                current = float(prices.get(symbol, 0) or 0)
                pnl = ""
                if entry and current:
                    pnl_pct = ((current - entry) / entry * 100) if side == "long" else ((entry - current) / entry * 100)
                    pnl = f" {pnl_pct:+.2f}%"
                sim_lines.append(f"- {symbol} {side} @{entry:,.2f}{pnl}")
            parts.append("ACIK POZISYONLAR:\n" + "\n".join(sim_lines))

        pending = snapshot.get("pending_signals", [])
        if pending:
            signal_lines = []
            for signal in pending[:4]:
                confidence = float(signal.get("confidence", 0) or 0) * 100
                signal_lines.append(f"- {signal.get('symbol', '?')} %{confidence:.0f}")
            parts.append("BEKLEYEN SINYALLER:\n" + "\n".join(signal_lines))

        if snapshot.get("agent_health"):
            parts.append(
                "AGENT DURUMU:\n" + "\n".join(
                    f"- {name}: {status}" for name, status in snapshot["agent_health"].items()
                )
            )

        context = "\n\n".join(parts)
        if len(context) > MAX_CONTEXT_CHARS:
            context = context[:MAX_CONTEXT_CHARS] + "\n\n[context kisaltildi]"
        return context

    async def _get_snapshot(self, user_msg: str, lightweight: bool = False) -> Dict[str, Any]:
        now = time.monotonic()
        if self._snapshot_cache and now - self._snapshot_cache_at <= CHAT_CACHE_TTL:
            snapshot = dict(self._snapshot_cache)
        else:
            summary, open_sims, pending = await asyncio.gather(
                self.db.get_dashboard_summary(),
                self.db.get_open_simulations(),
                self.db.get_pending_signals(),
            )
            snapshot = {
                "summary": summary,
                "open_simulations": open_sims[:5],
                "pending_signals": pending[:5],
                "signal_flow": await self.db.get_signal_pipeline_snapshot(hours=6),
                "brain": self.brain.get_brain_status() if self.brain else {},
                "risk": self.risk_manager.get_risk_summary() if self.risk_manager else {},
                "state": self.state_tracker.get_state_summary() if self.state_tracker else {},
            }
            self._snapshot_cache = dict(snapshot)
            self._snapshot_cache_at = now

        snapshot["prices"] = await self._get_prices(user_msg)
        snapshot["agent_health"] = {} if lightweight else await self._get_agent_health_snapshot()
        if lightweight:
            snapshot["open_simulations"] = []
            snapshot["pending_signals"] = []
        return snapshot

    async def _get_prices(self, user_msg: str) -> Dict[str, float]:
        from config import Config

        targets = self._extract_symbols(user_msg) or Config.WATCHLIST[:4]
        rows = await asyncio.gather(
            *[self.db.get_recent_trades(symbol, limit=1) for symbol in targets],
            return_exceptions=True,
        )
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

        if not self.agents:
            return {}

        results = await asyncio.gather(
            *[agent.health_check() for agent in self.agents.values()],
            return_exceptions=True,
        )
        health: Dict[str, str] = {}
        for name, result in zip(self.agents.keys(), results):
            if isinstance(result, Exception):
                health[name] = "bilinmiyor"
            else:
                health[name] = "aktif" if result.get("healthy") else "sorunlu"
        self._health_cache = dict(health)
        self._health_cache_at = now
        return health

    def _extract_symbols(self, msg: str) -> List[str]:
        from config import Config

        text = msg.upper()
        found: List[str] = []
        aliases = {
            "BTC": "BTCUSDT",
            "ETH": "ETHUSDT",
            "SOL": "SOLUSDT",
            "BNB": "BNBUSDT",
            "ADA": "ADAUSDT",
            "XRP": "XRPUSDT",
            "DOGE": "DOGEUSDT",
            "AVAX": "AVAXUSDT",
        }
        for symbol in Config.WATCHLIST:
            short = symbol.replace("USDT", "")
            if symbol in text or short in text:
                found.append(symbol)
        for alias, symbol in aliases.items():
            if alias in text and symbol not in found:
                found.append(symbol)
        return found[:4]

    def _format_routed_actions(self, routed_actions: List[Dict[str, Any]]) -> str:
        lines: List[str] = []
        for action in routed_actions[:6]:
            action_type = action.get("type", "?")
            if action_type in {"watchlist_add", "watchlist_remove"}:
                lines.append(f"- {action_type}: {', '.join(action.get('symbols', [])[:8])}")
            elif action_type == "risk_update":
                changes = action.get("changes", {})
                lines.append(f"- risk_update: {', '.join(f'{k}={v}' for k, v in changes.items())}")
            elif action_type == "master_directive_update":
                lines.append(f"- master_directive_update: {str(action.get('text', ''))[:120]}")
            elif action_type == "system_mode_update":
                lines.append(f"- system_mode_update: {action.get('mode', '?')}")
            elif action_type == "cleanup_run":
                lines.append(
                    f"- cleanup_run: dry_run={action.get('dry_run', True)} stale={action.get('stale_count', 0)} deleted={action.get('deleted_count', 0)}"
                )
            elif action_type == "system_diagnostic":
                summary = action.get("summary", {})
                lines.append(
                    f"- system_diagnostic: mode={action.get('system_mode', '?')} active={summary.get('active_signals', 0)} open={summary.get('open_simulations', 0)}"
                )
            elif action_type == "symbol_analysis":
                overall = action.get("overall_signal", {})
                lines.append(
                    f"- symbol_analysis: {action.get('symbol', '?')} dir={overall.get('direction', 'neutral')} conf={overall.get('confidence', 0)} price={action.get('latest_price', 0)}"
                )
            else:
                lines.append(f"- {action_type}")
        return "\n".join(lines) if lines else "- yok"

    async def _ask_gemma(
        self,
        user_msg: str,
        context: str,
        lightweight: bool = False,
        budget_seconds: float = 25.0,  # Gemma 3 12B için yeterli budget
    ) -> str:
        t0 = time.monotonic()

        # Use dedicated chat client — own semaphore, never blocked by long decision calls.
        client = self._get_chat_client()
        timeout_sec = max(8, int(min(QUICK_CHAT_TIMEOUT if lightweight else CHAT_DEDICATED_TIMEOUT, budget_seconds)))

        prompt = (
            f"Kullanici mesaji:\n{user_msg}\n\n"
            f"Sistem verisi:\n{context}\n\n"
            "Gorev: Kullaniciya dogal Turkce ile cevap ver. Kisa, net, icten ve sistemin sahibi gibi konus."
        )
        response = await client.generate(
            prompt=prompt,
            system=SYSTEM_PROMPT,
            temperature=0.18,
            json_mode=False,
            timeout_override=timeout_sec,
            # prefer_fast_fail=False: dedicated lane — no fast fail; let it wait its full timeout
            prefer_fast_fail=False,
            max_tokens_override=CHAT_QUICK_MAX_TOKENS if lightweight else CHAT_FULL_MAX_TOKENS,
            max_retries_override=0,
        )

        text = (response.text or "").strip()
        if response.success and text:
            logger.debug("Chat LLM answered in %.1fs", time.monotonic() - t0)
            return text

        # First attempt failed (timeout or error). One quick retry with a simpler prompt.
        elapsed = time.monotonic() - t0
        retry_budget = max(0.0, budget_seconds - elapsed)
        if retry_budget < 3.0:
            logger.debug("Chat budget exhausted (%.1fs), using context fallback", elapsed)
            return self._fast_context_fallback(context)

        retry_prompt = (
            f"Kullanici sorusu: {user_msg}\n"
            "Kisa, net, Turkce yanit ver. JSON kullanma."
        )
        retry = await client.generate(
            prompt=retry_prompt,
            system=SYSTEM_PROMPT,
            temperature=0.12,
            json_mode=False,
            timeout_override=max(3, int(retry_budget) - 1),
            prefer_fast_fail=False,
            max_tokens_override=CHAT_FULL_MAX_TOKENS,
            max_retries_override=0,
        )

        retry_text = (retry.text or "").strip()
        if retry.success and retry_text:
            return retry_text

        return self._fast_context_fallback(context)

    def _fast_context_fallback(self, context: str) -> str:
        """Return a short deterministic answer when model is busy to keep UX snappy."""
        active_signals = "?"
        pending = "?"
        risk_red = "?"
        for line in context.splitlines():
            if "aktif_sinyal=" in line:
                active_signals = line.split("aktif_sinyal=")[-1].strip()
            elif "- bekleyen=" in line:
                pending = line.split("=")[-1].strip()
            elif "- riskte_elenen=" in line:
                risk_red = line.split("=")[-1].strip()

        return (
            f"Sistem aktif. Bekleyen sinyal: {pending}, aktif sinyal: {active_signals}, "
            f"riskte elenen: {risk_red}. Model yogun oldugu icin hizli ozet dondurdum; "
            "istersen detayli analizi ikinci mesajda acayim."
        )

    def _is_lightweight_message(self, msg: str) -> bool:
        text = msg.lower().strip()
        if len(text) <= 24:
            return True
        return bool(re.search(r"^(selam|merhaba|sa|slm|naber|orada misin|hazir misin)$", text))

    def _fallback(self, msg: str) -> str:
        return "Model su an yanit uretemiyor. Lutfen tekrar dener misin?"
