"""
QuenBot V2 — LLM Client Module
Backend-agnostic async client for local or remote Gemma inference.

Supported backends:
- ollama: existing Ollama HTTP API
- openai: OpenAI-compatible servers such as llama.cpp server, vLLM, TGI gateways
"""

import asyncio
import json
import logging
import os
import time
from dataclasses import dataclass
from typing import Optional

import aiohttp

logger = logging.getLogger("quenbot.llm_client")

DEFAULT_BACKEND = os.getenv("QUENBOT_LLM_BACKEND", "ollama").lower()
DEFAULT_BASE_URL = os.getenv("QUENBOT_LLM_BASE_URL", "http://localhost:11434")
DEFAULT_MODEL = os.getenv("QUENBOT_LLM_MODEL", "quenbot-brain")
DEFAULT_API_KEY = os.getenv("QUENBOT_LLM_API_KEY", "")
MODEL_CANDIDATES = [
    "gemma4-trading",
    "gemma-2",
    "gemma-2-2b-it",
    "gemma-2-9b-it",
    "gemma3:4b-it-q4_K_M",
    "quenbot-brain",
    "gemma:7b",
    "qwen3:1.7b",
]
DEFAULT_TIMEOUT = int(os.getenv("QUENBOT_LLM_TIMEOUT", "90"))
DEFAULT_MAX_TOKENS = int(os.getenv("QUENBOT_LLM_MAX_TOKENS", "1024"))
DEFAULT_MAX_PROMPT_CHARS = int(os.getenv("QUENBOT_LLM_MAX_PROMPT_CHARS", "8000"))
DEFAULT_MAX_RETRIES = int(os.getenv("QUENBOT_LLM_MAX_RETRIES", "2"))
CPU_COUNT = os.cpu_count() or 4
DEFAULT_NUM_THREAD = int(os.getenv("QUENBOT_LLM_NUM_THREAD", str(max(4, min(12, CPU_COUNT - 1)))))
DEFAULT_NUM_CTX = int(os.getenv("QUENBOT_LLM_NUM_CTX", "4096"))
DEFAULT_CONCURRENCY = int(os.getenv("QUENBOT_LLM_CONCURRENCY", str(max(1, min(3, CPU_COUNT // 4)))))


@dataclass
class LLMResponse:
    text: str
    model: str
    total_duration_ms: float = 0
    prompt_eval_count: int = 0
    eval_count: int = 0
    success: bool = True
    error: Optional[str] = None

    def as_json(self) -> Optional[dict]:
        try:
            cleaned = self.text.strip()
            if cleaned.startswith("```"):
                lines = cleaned.split("\n")
                lines = [line for line in lines if not line.strip().startswith("```")]
                cleaned = "\n".join(lines)
            return json.loads(cleaned)
        except (json.JSONDecodeError, ValueError):
            return None


class _BaseBackend:
    backend_name = "base"

    def __init__(
        self,
        base_url: str,
        model: str,
        timeout: int,
        max_tokens: int,
        max_prompt_chars: int,
        max_retries: int,
        api_key: str = "",
    ):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout = timeout
        self.max_tokens = max_tokens
        self.max_prompt_chars = max_prompt_chars
        self.max_retries = max_retries
        self.api_key = api_key
        self._session: Optional[aiohttp.ClientSession] = None
        self._total_calls = 0
        self._total_errors = 0
        self._total_latency_ms = 0.0

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            connector = aiohttp.TCPConnector(limit=8, force_close=False)
            timeout = aiohttp.ClientTimeout(total=self.timeout + 30)
            self._session = aiohttp.ClientSession(connector=connector, timeout=timeout)
        return self._session

    async def close(self):
        if self._session and not self._session.closed:
            await self._session.close()
            self._session = None

    def _trim_prompt(self, text: Optional[str]) -> Optional[str]:
        if not text or len(text) <= self.max_prompt_chars:
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

    def _record_success(self, elapsed_ms: float):
        self._total_calls += 1
        self._total_latency_ms += elapsed_ms

    def _record_error(self):
        self._total_errors += 1

    def get_stats(self) -> dict:
        avg_latency = self._total_latency_ms / self._total_calls if self._total_calls else 0
        return {
            "backend": self.backend_name,
            "total_calls": self._total_calls,
            "total_errors": self._total_errors,
            "avg_latency_ms": round(avg_latency, 1),
            "model": self.model,
            "base_url": self.base_url,
        }


class _OllamaBackend(_BaseBackend):
    backend_name = "ollama"

    async def health_check(self) -> bool:
        try:
            session = await self._get_session()
            async with session.get(
                f"{self.base_url}/api/tags",
                timeout=aiohttp.ClientTimeout(total=5),
            ) as resp:
                if resp.status != 200:
                    return False
                data = await resp.json()
                available = [model.get("name", "") for model in data.get("models", [])]
                if not available:
                    logger.warning("Ollama running but no models installed")
                    return False
                logger.info("Ollama models available: %s", available)
                self._select_model(available)
                return True
        except Exception:
            return False

    async def list_models(self) -> list[str]:
        try:
            session = await self._get_session()
            async with session.get(
                f"{self.base_url}/api/tags",
                timeout=aiohttp.ClientTimeout(total=5),
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    return [model.get("name", "") for model in data.get("models", [])]
        except Exception:
            pass
        return []

    def _select_model(self, models: list[str]) -> bool:
        for candidate in [self.model, *MODEL_CANDIDATES]:
            for available in models:
                if available == candidate or available.startswith(candidate + ":"):
                    self.model = available
                    return True
        if models:
            self.model = models[0]
            return True
        return False

    async def ensure_model(self) -> bool:
        models = await self.list_models()
        if models:
            return self._select_model(models)
        return False

    async def generate(
        self,
        prompt: str,
        system: Optional[str] = None,
        temperature: float = 0.3,
        json_mode: bool = False,
        timeout_override: Optional[int] = None,
        model_override: Optional[str] = None,
    ) -> LLMResponse:
        prompt = self._trim_prompt(prompt) or ""
        system = self._trim_prompt(system)
        payload = {
            "model": model_override or self.model,
            "prompt": prompt,
            "stream": False,
            "options": {
                "temperature": temperature,
                "top_p": 0.85,
                "top_k": 30,
                "repeat_penalty": 1.15,
                "num_predict": self.max_tokens,
                "num_ctx": DEFAULT_NUM_CTX,
                "num_thread": DEFAULT_NUM_THREAD,
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
                        raise RuntimeError(f"Ollama HTTP {resp.status}: {error_text[:200]}")
                    data = await resp.json()
                elapsed_ms = (time.monotonic() - t0) * 1000
                self._record_success(elapsed_ms)
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
                logger.warning("LLM timeout (attempt %d/%d, %.0fms)", attempt + 1, self.max_retries + 1, elapsed_ms)
                if attempt == self.max_retries:
                    self._record_error()
                    return LLMResponse(text="", model=self.model, success=False, error=f"Timeout after {effective_timeout}s")
                await asyncio.sleep(2)
            except Exception as exc:
                logger.error("LLM error (attempt %d/%d): %s", attempt + 1, self.max_retries + 1, str(exc))
                if attempt == self.max_retries:
                    self._record_error()
                    return LLMResponse(text="", model=self.model, success=False, error=str(exc))
                await asyncio.sleep(2)
        return LLMResponse(text="", model=self.model, success=False, error="Unknown")


class _OpenAICompatBackend(_BaseBackend):
    backend_name = "openai-compatible"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if not self.base_url.endswith("/v1"):
            self.base_url = self.base_url + "/v1"

    def _headers(self) -> dict:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    async def health_check(self) -> bool:
        models = await self.list_models()
        if not models:
            return False
        logger.info("OpenAI-compatible models available: %s", models)
        await self.ensure_model()
        return True

    async def list_models(self) -> list[str]:
        try:
            session = await self._get_session()
            async with session.get(
                f"{self.base_url}/models",
                headers=self._headers(),
                timeout=aiohttp.ClientTimeout(total=5),
            ) as resp:
                if resp.status != 200:
                    return []
                data = await resp.json()
                return [item.get("id", "") for item in data.get("data", []) if item.get("id")]
        except Exception:
            return []

    async def ensure_model(self) -> bool:
        models = await self.list_models()
        if not models:
            return False
        for candidate in [self.model, *MODEL_CANDIDATES]:
            if candidate in models:
                self.model = candidate
                return True
        self.model = models[0]
        return True

    def _extract_message_text(self, data: dict) -> str:
        choices = data.get("choices", [])
        if not choices:
            return ""
        message = choices[0].get("message", {})
        content = message.get("content", "")
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts = []
            for item in content:
                if isinstance(item, dict) and item.get("type") == "text":
                    parts.append(item.get("text", ""))
            return "\n".join(part for part in parts if part)
        return str(content)

    async def generate(
        self,
        prompt: str,
        system: Optional[str] = None,
        temperature: float = 0.3,
        json_mode: bool = False,
        timeout_override: Optional[int] = None,
        model_override: Optional[str] = None,
    ) -> LLMResponse:
        prompt = self._trim_prompt(prompt) or ""
        system = self._trim_prompt(system)
        messages = []
        if system:
            system_text = system
            if json_mode:
                system_text += "\nYanıtın tek bir geçerli JSON nesnesi olsun."
            messages.append({"role": "system", "content": system_text})
        elif json_mode:
            messages.append({"role": "system", "content": "Yanıtın tek bir geçerli JSON nesnesi olsun."})
        messages.append({"role": "user", "content": prompt})

        payload = {
            "model": model_override or self.model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": self.max_tokens,
            "stream": False,
        }
        if json_mode:
            payload["response_format"] = {"type": "json_object"}

        effective_timeout = timeout_override or self.timeout
        for attempt in range(self.max_retries + 1):
            t0 = time.monotonic()
            try:
                session = await self._get_session()
                async with session.post(
                    f"{self.base_url}/chat/completions",
                    headers=self._headers(),
                    json=payload,
                    timeout=aiohttp.ClientTimeout(total=effective_timeout),
                ) as resp:
                    if resp.status != 200:
                        error_text = await resp.text()
                        raise RuntimeError(f"OpenAI-compatible HTTP {resp.status}: {error_text[:200]}")
                    data = await resp.json()
                elapsed_ms = (time.monotonic() - t0) * 1000
                self._record_success(elapsed_ms)
                usage = data.get("usage", {})
                return LLMResponse(
                    text=self._extract_message_text(data),
                    model=(data.get("model") or payload["model"]),
                    total_duration_ms=elapsed_ms,
                    prompt_eval_count=usage.get("prompt_tokens", 0),
                    eval_count=usage.get("completion_tokens", 0),
                    success=True,
                )
            except asyncio.TimeoutError:
                elapsed_ms = (time.monotonic() - t0) * 1000
                logger.warning("LLM timeout (attempt %d/%d, %.0fms)", attempt + 1, self.max_retries + 1, elapsed_ms)
                if attempt == self.max_retries:
                    self._record_error()
                    return LLMResponse(text="", model=self.model, success=False, error=f"Timeout after {effective_timeout}s")
                await asyncio.sleep(2)
            except Exception as exc:
                logger.error("LLM error (attempt %d/%d): %s", attempt + 1, self.max_retries + 1, str(exc))
                if attempt == self.max_retries:
                    self._record_error()
                    return LLMResponse(text="", model=self.model, success=False, error=str(exc))
                await asyncio.sleep(2)
        return LLMResponse(text="", model=self.model, success=False, error="Unknown")


class LLMClient:
    """Facade client that keeps the existing call surface while allowing backend swaps."""

    def __init__(
        self,
        base_url: str = DEFAULT_BASE_URL,
        model: str = DEFAULT_MODEL,
        timeout: int = DEFAULT_TIMEOUT,
        max_tokens: int = DEFAULT_MAX_TOKENS,
        max_prompt_chars: int = DEFAULT_MAX_PROMPT_CHARS,
        max_retries: int = DEFAULT_MAX_RETRIES,
        backend: str = DEFAULT_BACKEND,
        api_key: str = DEFAULT_API_KEY,
    ):
        self.backend_name = backend.lower()
        self._concurrency = DEFAULT_CONCURRENCY
        self._semaphore = asyncio.Semaphore(self._concurrency)
        backend_kwargs = {
            "base_url": base_url,
            "model": model,
            "timeout": timeout,
            "max_tokens": max_tokens,
            "max_prompt_chars": max_prompt_chars,
            "max_retries": max_retries,
            "api_key": api_key,
        }
        if self.backend_name == "ollama":
            self._backend = _OllamaBackend(**backend_kwargs)
        elif self.backend_name in {"openai", "openai-compatible", "llamacpp", "vllm"}:
            self._backend = _OpenAICompatBackend(**backend_kwargs)
        else:
            raise ValueError(f"Unsupported LLM backend: {self.backend_name}")

    @property
    def model(self) -> str:
        return self._backend.model

    @model.setter
    def model(self, value: str):
        self._backend.model = value

    @property
    def base_url(self) -> str:
        return self._backend.base_url

    async def close(self):
        await self._backend.close()

    async def health_check(self) -> bool:
        return await self._backend.health_check()

    async def list_models(self) -> list[str]:
        return await self._backend.list_models()

    async def ensure_model(self) -> bool:
        return await self._backend.ensure_model()

    async def generate(
        self,
        prompt: str,
        system: Optional[str] = None,
        temperature: float = 0.3,
        json_mode: bool = False,
        timeout_override: Optional[int] = None,
        model_override: Optional[str] = None,
    ) -> LLMResponse:
        async with self._semaphore:
            return await self._backend.generate(
                prompt=prompt,
                system=system,
                temperature=temperature,
                json_mode=json_mode,
                timeout_override=timeout_override,
                model_override=model_override,
            )

    async def chat(
        self,
        messages: list[dict],
        temperature: float = 0.3,
        json_mode: bool = False,
    ) -> LLMResponse:
        system_parts = []
        prompt_parts = []
        for message in messages:
            role = message.get("role", "user")
            content = message.get("content", "")
            if role == "system":
                system_parts.append(content)
            elif role == "assistant":
                prompt_parts.append(f"Assistant: {content}")
            else:
                prompt_parts.append(content)
        return await self.generate(
            prompt="\n\n".join(prompt_parts),
            system="\n\n".join(system_parts) if system_parts else None,
            temperature=temperature,
            json_mode=json_mode,
        )

    def get_stats(self) -> dict:
        stats = self._backend.get_stats()
        stats["configured_backend"] = self.backend_name
        stats["concurrency"] = self._concurrency
        stats["num_thread"] = DEFAULT_NUM_THREAD
        stats["num_ctx"] = DEFAULT_NUM_CTX
        return stats


_client: Optional[LLMClient] = None


def get_llm_client(**kwargs) -> LLMClient:
    global _client
    if _client is None:
        _client = LLMClient(**kwargs)
    return _client