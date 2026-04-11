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
import time
import subprocess
import shutil
from typing import Optional
from dataclasses import dataclass, field

import aiohttp

logger = logging.getLogger("quenbot.llm_client")

# -------------------------------------------------------------------
# Defaults tuned for 12 vCPU / 24 GB RAM
# Gemma 4 Trading model with 5 strategic enhancements
# -------------------------------------------------------------------
DEFAULT_BASE_URL = "http://localhost:11434"
DEFAULT_MODEL = "gemma4-trading"
MODEL_CANDIDATES = ["gemma4-trading", "quenbot-brain", "gemma:7b", "gemma3:4b-it-q4_K_M", "gemma3", "qwen3:1.7b"]
DEFAULT_TIMEOUT = 90           # faster CPU = shorter timeout
DEFAULT_MAX_TOKENS = 1024      # longer responses with more RAM
DEFAULT_MAX_PROMPT_CHARS = 8000  # 3x more context fits in 24GB
DEFAULT_MAX_RETRIES = 2
DEFAULT_CONCURRENCY = 3        # parallel inferences with 12 vCPU + 24GB RAM


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
                    # If current model is not available, find best match
                    if not any(self.model in m for m in available):
                        for candidate in MODEL_CANDIDATES:
                            if any(candidate in m for m in available):
                                old_model = self.model
                                self.model = candidate
                                logger.info(f"Model switched: {old_model} → {self.model}")
                                break
                        else:
                            # Use whatever is available
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
            # Check if our model exists
            if any(self.model in m for m in models):
                return True
            # Try to find a candidate
            for candidate in MODEL_CANDIDATES:
                if any(candidate in m for m in models):
                    self.model = candidate
                    logger.info(f"Using existing model: {self.model}")
                    return True

        # No suitable model found — try to pull Gemma
        logger.info("No suitable model found. Attempting to pull gemma3:4b-it-q4_K_M...")
        try:
            session = await self._get_session()
            async with session.post(
                f"{self.base_url}/api/pull",
                json={"name": "gemma3:4b-it-q4_K_M", "stream": False},
                timeout=aiohttp.ClientTimeout(total=600),  # 10 min for download
            ) as resp:
                if resp.status == 200:
                    self.model = "gemma3:4b-it-q4_K_M"
                    logger.info("✓ Gemma 3 4B model pulled successfully")
                    # Now create custom quenbot-brain from it
                    await self._create_custom_model()
                    return True
                else:
                    logger.error(f"Model pull failed: HTTP {resp.status}")
        except Exception as e:
            logger.error(f"Model pull error: {e}")
        return False

    async def _create_custom_model(self):
        """Create the quenbot-brain custom model from the active base model."""
        modelfile = f"""FROM {self.model}

PARAMETER temperature 0.3
PARAMETER top_p 0.85
PARAMETER top_k 30
PARAMETER repeat_penalty 1.15
PARAMETER num_ctx 8192
PARAMETER num_predict 1024
PARAMETER num_thread 10

SYSTEM \"\"\"You are QuenBot Central Intelligence, a specialized cryptocurrency trading analysis AI.
You operate as part of a multi-agent trading system with the following agents:
- Scout: Market data collection and anomaly detection
- Strategist: Signal generation and pattern analysis
- Ghost Simulator: Paper trading and backtesting
- Auditor: Quality control and root cause analysis
- Brain: Pattern learning and prediction

You provide structured, data-driven analysis. Always respond in valid JSON when requested.
Be concise. Focus on actionable insights. Never hallucinate data.\"\"\"
"""
        try:
            session = await self._get_session()
            async with session.post(
                f"{self.base_url}/api/create",
                json={"name": "quenbot-brain", "modelfile": modelfile, "stream": False},
                timeout=aiohttp.ClientTimeout(total=120),
            ) as resp:
                if resp.status == 200:
                    self.model = "quenbot-brain"
                    logger.info("✓ Custom quenbot-brain model created from " + self.model)
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
    ) -> LLMResponse:
        """
        Generate a response from the local LLM.
        Uses semaphore to ensure single inference at a time on CPU.
        """
        async with self._semaphore:
            return await self._generate_inner(
                prompt, system, temperature, json_mode, timeout_override
            )

    async def _generate_inner(
        self,
        prompt: str,
        system: Optional[str],
        temperature: float,
        json_mode: bool,
        timeout_override: Optional[int],
    ) -> LLMResponse:
        prompt = self._trim_prompt(prompt)
        if system:
            system = self._trim_prompt(system)

        payload = {
            "model": self.model,
            "prompt": prompt,
            "stream": False,
            "options": {
                "temperature": temperature,
                "top_p": 0.85,
                "top_k": 30,
                "repeat_penalty": 1.15,
                "num_predict": self.max_tokens,
                "num_ctx": 8192,
                "num_thread": 10,
            },
        }

        if system:
            payload["system"] = system
        if json_mode:
            payload["format"] = "json"

        effective_timeout = timeout_override or self.timeout

        for attempt in range(self.max_retries + 1):
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
                    attempt + 1, self.max_retries + 1, elapsed_ms
                )
                if attempt == self.max_retries:
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
                    attempt + 1, self.max_retries + 1, str(e)
                )
                if attempt == self.max_retries:
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
        }


# Singleton instance
_client: Optional[LLMClient] = None


def get_llm_client(**kwargs) -> LLMClient:
    """Get or create the singleton LLM client."""
    global _client
    if _client is None:
        _client = LLMClient(**kwargs)
    return _client
