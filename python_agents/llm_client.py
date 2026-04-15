"""
QuenBot V2 — LLM Client Module
SuperGemma-26B GGUF backend via llama-cpp-python.
Ollama yerine doğrudan GGUF inference yapılır.
Mevcut arayüz (generate/chat/health_check) korunur — llm_bridge uyumlu.
"""

import asyncio
import json
import logging
import os
import time
from typing import Optional
from dataclasses import dataclass, field

logger = logging.getLogger("quenbot.llm_client")

# -------------------------------------------------------------------
# Defaults tuned for 16 vCPU / 32 GB RAM — SuperGemma-26B GGUF
# -------------------------------------------------------------------
DEFAULT_MODEL = os.getenv("QUENBOT_LLM_MODEL", "supergemma-26b")
DEFAULT_TIMEOUT = int(os.getenv("QUENBOT_LLM_TIMEOUT", "90"))
DEFAULT_MAX_TOKENS = int(os.getenv("QUENBOT_LLM_MAX_TOKENS", "512"))
DEFAULT_MAX_PROMPT_CHARS = int(os.getenv("QUENBOT_LLM_MAX_PROMPT_CHARS", "6000"))
DEFAULT_MAX_RETRIES = int(os.getenv("QUENBOT_LLM_MAX_RETRIES", "1"))
DEFAULT_CONCURRENCY = int(os.getenv("QUENBOT_LLM_CONCURRENCY", "1"))
DEFAULT_NUM_THREAD = int(os.getenv("QUENBOT_LLM_NUM_THREAD", "14"))
DEFAULT_NUM_CTX = int(os.getenv("QUENBOT_LLM_NUM_CTX", "8192"))


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
            if cleaned.startswith("```"):
                lines = cleaned.split("\n")
                lines = [l for l in lines if not l.strip().startswith("```")]
                cleaned = "\n".join(lines)
            return json.loads(cleaned)
        except (json.JSONDecodeError, ValueError):
            start = cleaned.find("{") if cleaned else -1
            end = cleaned.rfind("}") + 1 if cleaned else 0
            if start >= 0 and end > start:
                try:
                    return json.loads(cleaned[start:end])
                except (json.JSONDecodeError, ValueError):
                    pass
            return None


class LLMClient:
    """
    SuperGemma-26B GGUF client — llm_bridge uyumlu arayüz.
    Dahili olarak gguf_engine.GGUFEngine kullanır.
    """

    def __init__(
        self,
        base_url: str = "",
        model: str = DEFAULT_MODEL,
        timeout: int = DEFAULT_TIMEOUT,
        max_tokens: int = DEFAULT_MAX_TOKENS,
        max_prompt_chars: int = DEFAULT_MAX_PROMPT_CHARS,
        max_retries: int = DEFAULT_MAX_RETRIES,
    ):
        self.model = model
        self.timeout = timeout
        self.max_tokens = max_tokens
        self.max_prompt_chars = max_prompt_chars
        self.max_retries = max_retries
        self.num_thread: int = DEFAULT_NUM_THREAD
        self.num_ctx: int = DEFAULT_NUM_CTX
        self._engine = None
        self._total_calls = 0
        self._total_errors = 0
        self._total_latency_ms = 0.0

    def _get_engine(self):
        """Lazy import to avoid circular deps."""
        if self._engine is None:
            from gguf_engine import get_gguf_engine
            self._engine = get_gguf_engine()
        return self._engine

    async def close(self):
        if self._engine:
            await self._engine.close()

    def _trim_prompt(self, text: str) -> str:
        """Trim prompt to fit within max chars to prevent OOM."""
        if len(text) <= self.max_prompt_chars:
            return text
        head_size = self.max_prompt_chars // 2
        tail_size = self.max_prompt_chars - head_size - 50
        trimmed = (
            text[:head_size]
            + "\n\n[... trimmed for memory constraints ...]\n\n"
            + text[-tail_size:]
        )
        logger.debug("Prompt trimmed: %d -> %d chars", len(text), len(trimmed))
        return trimmed

    async def health_check(self) -> bool:
        """Check if GGUF model is loaded and responsive."""
        try:
            engine = self._get_engine()
            if not engine._initialized:
                ok = await engine.initialize()
                if ok:
                    self.model = engine._model_name
                return ok
            self.model = engine._model_name
            return True
        except Exception:
            return False

    async def list_models(self) -> list[str]:
        """List available GGUF model (single model architecture)."""
        engine = self._get_engine()
        if engine._initialized:
            return [engine._model_name]
        return []

    async def ensure_model(self) -> bool:
        """Ensure GGUF model is loaded."""
        engine = self._get_engine()
        ok = await engine.initialize()
        if ok:
            self.model = engine._model_name
        return ok

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
        Generate a response from SuperGemma-26B GGUF.
        Arayüz Ollama client ile aynı kalır — llm_bridge uyumlu.
        """
        engine = self._get_engine()

        effective_timeout = timeout_override or self.timeout
        effective_max_tokens = int(max_tokens_override or self.max_tokens)
        effective_retries = self.max_retries if max_retries_override is None else max(0, int(max_retries_override))

        for attempt in range(effective_retries + 1):
            t0 = time.monotonic()

            gguf_resp = await engine.generate(
                prompt=prompt,
                system=system,
                temperature=temperature,
                json_mode=json_mode,
                max_tokens=effective_max_tokens,
                timeout_override=effective_timeout,
                prefer_fast_fail=prefer_fast_fail,
            )

            elapsed_ms = (time.monotonic() - t0) * 1000
            self._total_calls += 1
            self._total_latency_ms += elapsed_ms

            if gguf_resp.success:
                return LLMResponse(
                    text=gguf_resp.text,
                    model=gguf_resp.model,
                    total_duration_ms=gguf_resp.total_duration_ms,
                    prompt_eval_count=gguf_resp.prompt_tokens,
                    eval_count=gguf_resp.completion_tokens,
                    success=True,
                )

            if attempt < effective_retries:
                logger.warning(
                    "GGUF inference attempt %d/%d failed: %s",
                    attempt + 1, effective_retries + 1, gguf_resp.error
                )
                await asyncio.sleep(2)
                continue

            self._total_errors += 1
            return LLMResponse(
                text="",
                model=gguf_resp.model,
                success=False,
                error=gguf_resp.error,
            )

        return LLMResponse(text="", model=self.model, success=False, error="Unknown")

    async def chat(
        self,
        messages: list[dict],
        temperature: float = 0.3,
        json_mode: bool = False,
    ) -> LLMResponse:
        """Chat-style API (converts to generate internally)."""
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
        engine_stats = {}
        if self._engine:
            engine_stats = self._engine.get_stats()
        return {
            "total_calls": self._total_calls,
            "total_errors": self._total_errors,
            "avg_latency_ms": round(avg_latency, 1),
            "model": self.model,
            "backend": "SuperGemma-26B (GGUF / llama-cpp-python)",
            "concurrency": DEFAULT_CONCURRENCY,
            "num_thread": DEFAULT_NUM_THREAD,
            "num_ctx": DEFAULT_NUM_CTX,
            **engine_stats,
        }


# Singleton instance
_client: Optional[LLMClient] = None


def get_llm_client(**kwargs) -> LLMClient:
    """Get or create the singleton LLM client."""
    global _client
    if _client is None:
        _client = LLMClient(**kwargs)
    return _client
