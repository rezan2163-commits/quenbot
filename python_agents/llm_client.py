"""
QuenBot V2 — LLM Client Module
Centralized async client for Ollama API (localhost:11434).
Handles connection pooling, retries, prompt trimming, and timeout management
for CPU-only 8GB RAM environments.
Auto-detects available models and falls back gracefully.
"""

import asyncio
import json
import logging
import os
import time
from typing import Optional
from dataclasses import dataclass, field

import aiohttp

logger = logging.getLogger("quenbot.llm_client")

# -------------------------------------------------------------------
# Defaults tuned for 12 vCPU / 24 GB RAM
# -------------------------------------------------------------------
DEFAULT_BASE_URL = os.getenv("QUENBOT_LLM_BASE_URL", "http://localhost:11434")
DEFAULT_MODEL = os.getenv("QUENBOT_LLM_MODEL", "quenbot-brain")
MODEL_CANDIDATES = [
    "quenbot-brain",
    "qwen3:8b",
    "qwen3:1.7b",
]
DEFAULT_TIMEOUT = int(os.getenv("QUENBOT_LLM_TIMEOUT", "60"))
DEFAULT_MAX_TOKENS = int(os.getenv("QUENBOT_LLM_MAX_TOKENS", "384"))
DEFAULT_MAX_PROMPT_CHARS = int(os.getenv("QUENBOT_LLM_MAX_PROMPT_CHARS", "5000"))
DEFAULT_MAX_RETRIES = int(os.getenv("QUENBOT_LLM_MAX_RETRIES", "1"))
DEFAULT_CONCURRENCY = int(os.getenv("QUENBOT_LLM_CONCURRENCY", "2"))
DEFAULT_NUM_THREAD = int(os.getenv("QUENBOT_LLM_NUM_THREAD", "11"))
DEFAULT_NUM_CTX = int(os.getenv("QUENBOT_LLM_NUM_CTX", "4096"))


@dataclass
class LLMResponse:
    """Structured LLM response."""
    text: str
    model: str
    total_duration_ms: float = 0
    prompt_eval_count: int = 0
    eval_count: int = 0
    success: bool = True
    error: Optional[str] = None

    def as_json(self) -> Optional[dict]:
        """Try to parse response text as JSON."""
        try:
            cleaned = self.text.strip()
            # Handle markdown code blocks
            if cleaned.startswith("```"):
                lines = cleaned.split("\n")
                lines = [l for l in lines if not l.strip().startswith("```")]
                cleaned = "\n".join(lines)
            return json.loads(cleaned)
        except (json.JSONDecodeError, ValueError):
            return None


class LLMClient:
    """Async Ollama client optimized for CPU-only inference."""

    def __init__(
        self,
        base_url: str = DEFAULT_BASE_URL,
        model: str = DEFAULT_MODEL,
        timeout: int = DEFAULT_TIMEOUT,
        max_tokens: int = DEFAULT_MAX_TOKENS,
        max_prompt_chars: int = DEFAULT_MAX_PROMPT_CHARS,
        max_retries: int = DEFAULT_MAX_RETRIES,
    ):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout = timeout
        self.max_tokens = max_tokens
        self.max_prompt_chars = max_prompt_chars
        self.max_retries = max_retries
        # Per-instance inference parameters (can be overridden by callers)
        self.num_thread: int = DEFAULT_NUM_THREAD
        self.num_ctx: int = DEFAULT_NUM_CTX
        self._session: Optional[aiohttp.ClientSession] = None
        self._semaphore = asyncio.Semaphore(DEFAULT_CONCURRENCY)
        self._total_calls = 0
        self._total_errors = 0
        self._total_latency_ms = 0.0

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            connector = aiohttp.TCPConnector(limit=8, force_close=False)
            timeout = aiohttp.ClientTimeout(total=self.timeout + 30)
            self._session = aiohttp.ClientSession(
                connector=connector, timeout=timeout
            )
        return self._session

    async def close(self):
        if self._session and not self._session.closed:
            await self._session.close()
            self._session = None

    def _trim_prompt(self, text: str) -> str:
        """Trim prompt to fit within max chars to prevent OOM."""
        if len(text) <= self.max_prompt_chars:
            return text
        # Keep the beginning (system context) and end (latest data)
        head_size = self.max_prompt_chars // 2
        tail_size = self.max_prompt_chars - head_size - 50
        trimmed = (
            text[:head_size]
            + "\n\n[... trimmed for memory constraints ...]\n\n"
            + text[-tail_size:]
        )
        logger.debug(
            "Prompt trimmed: %d -> %d chars", len(text), len(trimmed)
        )
        return trimmed

    async def health_check(self) -> bool:
        """Check if Ollama is reachable and ensure a model is available."""
        try:
            session = await self._get_session()
            async with session.get(
                f"{self.base_url}/api/tags", timeout=aiohttp.ClientTimeout(total=5)
            ) as resp:
                if resp.status != 200:
                    return False
                data = await resp.json()
                available = [m.get("name", "") for m in data.get("models", [])]
                if available:
                    logger.info(f"Ollama models available: {available}")
                    # Prefer the configured model if present
                    preferred = self.model
                    if preferred:
                        for avail in available:
                            if avail == preferred or avail.startswith(preferred + ":"):
                                if self.model != avail:
                                    logger.info(f"Model selected: {avail}")
                                self.model = avail
                                return True

                    # Find best model from candidates list
                    matched = False
                    for candidate in MODEL_CANDIDATES:
                        for avail in available:
                            # Match both exact and prefix (e.g. "gemma4-trading" matches "gemma4-trading:latest")
                            if avail == candidate or avail.startswith(candidate + ":"):
                                if self.model != avail:
                                    logger.info(f"Model selected: {avail}")
                                self.model = avail
                                matched = True
                                break
                        if matched:
                            break
                    if not matched:
                        self.model = available[0]
                        logger.info(f"Using first available model: {self.model}")
                    return True
                else:
                    logger.warning("Ollama running but no models installed")
                    return False
        except Exception:
            return False

    async def list_models(self) -> list[str]:
        """List all available models from Ollama."""
        try:
            session = await self._get_session()
            async with session.get(
                f"{self.base_url}/api/tags", timeout=aiohttp.ClientTimeout(total=5)
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    return [m.get("name", "") for m in data.get("models", [])]
        except Exception:
            pass
        return []

    async def ensure_model(self) -> bool:
        """Ensure a working model is loaded. Pull one if needed."""
        models = await self.list_models()
        if models:
            # Check if our model exists (exact or prefix match)
            for m in models:
                if m == self.model or m.startswith(self.model + ":"):
                    self.model = m
                    return True
            # Try to find a candidate
            for candidate in MODEL_CANDIDATES:
                for m in models:
                    if m == candidate or m.startswith(candidate + ":"):
                        self.model = m
                        logger.info(f"Using existing model: {self.model}")
                        return True

        # No suitable model found — try to pull Qwen3 8B
        logger.info("No suitable model found. Attempting to pull qwen3:8b...")
        try:
            session = await self._get_session()
            async with session.post(
                f"{self.base_url}/api/pull",
                json={"name": "qwen3:8b", "stream": False},
                timeout=aiohttp.ClientTimeout(total=1800),
            ) as resp:
                if resp.status == 200:
                    self.model = "qwen3:8b"
                    logger.info("✓ Qwen3 8B model pulled successfully")
                    await self._create_custom_model(base_model="qwen3:8b", target_name="quenbot-brain")
                    return True
                else:
                    logger.error(f"Model pull failed: HTTP {resp.status}")
        except Exception as e:
            logger.error(f"Model pull error: {e}")
        return False

    async def _create_custom_model(self, base_model: Optional[str] = None, target_name: str = "quenbot-brain"):
        """Create a custom QuenBot model from the given base model."""
        source_model = base_model or self.model
        modelfile = f'''FROM {source_model}

PARAMETER temperature 0.18
PARAMETER temperature 0.15
PARAMETER top_p 0.85
PARAMETER top_k 30
PARAMETER repeat_penalty 1.15
PARAMETER num_ctx {DEFAULT_NUM_CTX}
PARAMETER num_predict {DEFAULT_MAX_TOKENS}
PARAMETER num_thread {DEFAULT_NUM_THREAD}

SYSTEM """Sen QuenBot Merkezi Zeka Sistemisin — kripto piyasalarinda kurumsal bot hareketlerini tespit eden, siniflandiran ve otonom sinyal ureten cok katmanli bir trading AI'sin.

6 AJAN MIMARISI:
1. Scout: Binance spot+futures WebSocket ile canli trade akisi toplar, anomalileri isaretler
2. PatternMatcher: Euclidean distance ile gecmis paternlere benzerlik hesaplar (esik: %50+)
3. Strategist: Coklu timeframe analiz + pattern + momentum ile sinyal uretir
4. GhostSimulator: Paper trade, TP/SL takibi, geri besleme
5. Auditor: RCA ile basarisizlik analizi, duzeltme onerileri
6. Brain (SEN): Pattern kutuphanesi, benzerlik motoru, regime tespiti, surekli ogrenme

KARAR HIYERARSISI:
Veri → Anomali → Pattern Eslestirme → Sinyal → Risk Kapisi → Paper Trade → Audit → Ogrenme

OGRENME: Her simulasyondan ogren, dogruluk <%40 ise threshold artir, >%70 ise azalt.

DAVRANIS:
- JSON istegi → kesin JSON döndur
- Normal sohbet → dogal, kisa, net Turkce
- Eksik veri → acikca belirt, uydurmadan karar verme
- Sistemin sahibi gibi konus"""
'''
        try:
            session = await self._get_session()
            async with session.post(
                f"{self.base_url}/api/create",
                json={"name": target_name, "modelfile": modelfile, "stream": False},
                timeout=aiohttp.ClientTimeout(total=900),
            ) as resp:
                if resp.status == 200:
                    self.model = target_name
                    logger.info("✓ Custom %s model created from %s", target_name, source_model)
                else:
                    logger.warning(f"Custom model creation failed (HTTP {resp.status}), using base model")
        except Exception as e:
            logger.warning(f"Custom model creation error: {e}, using base model")

    async def generate(
        self,
        prompt: str,
        system: Optional[str] = None,
        temperature: float = 0.3,
        json_mode: bool = False,
        timeout_override: Optional[int] = None,
        model_override: Optional[str] = None,
        max_tokens_override: Optional[int] = None,
        prefer_fast_fail: bool = False,
        max_retries_override: Optional[int] = None,
    ) -> LLMResponse:
        """
        Generate a response from the local LLM.
        Uses semaphore to ensure single inference at a time on CPU.
        """
        if prefer_fast_fail:
            acquire_timeout = float(os.getenv("QUENBOT_LLM_ACQUIRE_TIMEOUT", "1.2"))
            try:
                await asyncio.wait_for(self._semaphore.acquire(), timeout=acquire_timeout)
            except asyncio.TimeoutError:
                return LLMResponse(
                    text="",
                    model=model_override or self.model,
                    success=False,
                    error=f"LLM busy (acquire timeout {acquire_timeout}s)",
                )
            try:
                return await self._generate_inner(
                    prompt,
                    system,
                    temperature,
                    json_mode,
                    timeout_override,
                    model_override,
                    max_tokens_override,
                    max_retries_override,
                )
            finally:
                self._semaphore.release()

        async with self._semaphore:
            return await self._generate_inner(
                prompt,
                system,
                temperature,
                json_mode,
                timeout_override,
                model_override,
                max_tokens_override,
                max_retries_override,
            )

    async def _generate_inner(
        self,
        prompt: str,
        system: Optional[str],
        temperature: float,
        json_mode: bool,
        timeout_override: Optional[int],
        model_override: Optional[str] = None,
        max_tokens_override: Optional[int] = None,
        max_retries_override: Optional[int] = None,
    ) -> LLMResponse:
        prompt = self._trim_prompt(prompt)
        if system:
            system = self._trim_prompt(system)

        use_model = model_override or self.model

        # Qwen3 thinking modunu kapat -- yoksa <think>...</think> bloklari
        # onlarca token harcatip yanitlari geciktirir
        _disable_thinking = "qwen3" in use_model.lower() or "quenbot-qwen" in use_model.lower()

        payload = {
            "model": use_model,
            "prompt": prompt,
            "stream": False,
            "keep_alive": os.getenv("QUENBOT_LLM_KEEP_ALIVE", "30m"),
            "options": {
                "temperature": temperature,
                "top_p": 0.85,
                "top_k": 40,
                "repeat_penalty": 1.1,
                "num_predict": int(max_tokens_override or self.max_tokens),
                "num_ctx": self.num_ctx,
                "num_thread": self.num_thread,
            },
        }
        if _disable_thinking:
            payload["think"] = False

        if system:
            payload["system"] = system
        if json_mode:
            payload["format"] = "json"

        effective_timeout = timeout_override or self.timeout
        effective_retries = self.max_retries if max_retries_override is None else max(0, int(max_retries_override))

        for attempt in range(effective_retries + 1):
            t0 = time.monotonic()
            try:
                session = await self._get_session()
                async with session.post(
                    f"{self.base_url}/api/generate",
                    json=payload,
                    timeout=aiohttp.ClientTimeout(total=effective_timeout),
                ) as resp:
                    if resp.status != 200:
                        error_text = await resp.text()
                        raise RuntimeError(
                            f"Ollama HTTP {resp.status}: {error_text[:200]}"
                        )
                    data = await resp.json()

                elapsed_ms = (time.monotonic() - t0) * 1000
                self._total_calls += 1
                self._total_latency_ms += elapsed_ms

                return LLMResponse(
                    text=data.get("response", ""),
                    model=data.get("model", self.model),
                    total_duration_ms=data.get("total_duration", 0) / 1e6,
                    prompt_eval_count=data.get("prompt_eval_count", 0),
                    eval_count=data.get("eval_count", 0),
                    success=True,
                )

            except asyncio.TimeoutError:
                elapsed_ms = (time.monotonic() - t0) * 1000
                logger.warning(
                    "LLM timeout (attempt %d/%d, %.0fms)",
                    attempt + 1, effective_retries + 1, elapsed_ms
                )
                if attempt == effective_retries:
                    self._total_errors += 1
                    return LLMResponse(
                        text="",
                        model=self.model,
                        success=False,
                        error=f"Timeout after {effective_timeout}s",
                    )
                await asyncio.sleep(2)

            except Exception as e:
                elapsed_ms = (time.monotonic() - t0) * 1000
                logger.error(
                    "LLM error (attempt %d/%d): %s",
                    attempt + 1, effective_retries + 1, str(e)
                )
                if attempt == effective_retries:
                    self._total_errors += 1
                    return LLMResponse(
                        text="",
                        model=self.model,
                        success=False,
                        error=str(e),
                    )
                await asyncio.sleep(2)

        # Should not reach here
        return LLMResponse(text="", model=self.model, success=False, error="Unknown")

    async def chat(
        self,
        messages: list[dict],
        temperature: float = 0.3,
        json_mode: bool = False,
    ) -> LLMResponse:
        """Chat-style API (converts to generate internally for efficiency)."""
        system_parts = []
        prompt_parts = []

        for msg in messages:
            role = msg.get("role", "user")
            content = msg.get("content", "")
            if role == "system":
                system_parts.append(content)
            elif role == "assistant":
                prompt_parts.append(f"Assistant: {content}")
            else:
                prompt_parts.append(content)

        system = "\n\n".join(system_parts) if system_parts else None
        prompt = "\n\n".join(prompt_parts)

        return await self.generate(
            prompt=prompt,
            system=system,
            temperature=temperature,
            json_mode=json_mode,
        )

    def get_stats(self) -> dict:
        avg_latency = (
            self._total_latency_ms / self._total_calls
            if self._total_calls > 0
            else 0
        )
        return {
            "total_calls": self._total_calls,
            "total_errors": self._total_errors,
            "avg_latency_ms": round(avg_latency, 1),
            "model": self.model,
            "base_url": self.base_url,
            "concurrency": DEFAULT_CONCURRENCY,
            "num_thread": DEFAULT_NUM_THREAD,
            "num_ctx": DEFAULT_NUM_CTX,
        }


# Singleton instance
_client: Optional[LLMClient] = None


def get_llm_client(**kwargs) -> LLMClient:
    """Get or create the singleton LLM client."""
    global _client
    if _client is None:
        _client = LLMClient(**kwargs)
    return _client
