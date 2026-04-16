"""
QuenBot V2 — GGUF Inference Engine (Gemma 3 12B)
=========================================================
llama-cpp-python tabanlı doğrudan GGUF model yükleyici.
Ollama yerine doğrudan CPU+RAM inference yapar.

Mimari:
- Model: Gemma 3 12B IT Q4_K_M quantization
- RAM: 32GB ortamda ~8GB model footprint (ideal)
- Avantaj: Hızlı yanıt + yeterli zeka, düşük kaynak kullanımı
- Concurrency: asyncio semaphore ile sıralı inference
- Thread: CPU thread count otomatik tespit (num_threads)

Kullanım:
    engine = get_gguf_engine()
    await engine.initialize()
    response = await engine.generate("prompt", system="system prompt")
"""

import asyncio
import json
import logging
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

logger = logging.getLogger("quenbot.gguf_engine")

# ─── Model Configuration ───
GGUF_MODEL_DIR = os.getenv("QUENBOT_GGUF_MODEL_DIR", "/root/models")
GGUF_MODEL_FILE = os.getenv("QUENBOT_GGUF_MODEL_FILE", "gemma-3-12b-it-Q4_K_M.gguf")
GGUF_NUM_THREADS = int(os.getenv("QUENBOT_GGUF_NUM_THREADS", "12"))  # Tek model: daha fazla thread inference için
GGUF_NUM_CTX = int(os.getenv("QUENBOT_GGUF_NUM_CTX", "4096"))
GGUF_NUM_GPU_LAYERS = int(os.getenv("QUENBOT_GGUF_GPU_LAYERS", "0"))  # CPU-only (pure RAM)
GGUF_MAX_TOKENS = int(os.getenv("QUENBOT_GGUF_MAX_TOKENS", "384"))
GGUF_BATCH_SIZE = int(os.getenv("QUENBOT_GGUF_BATCH_SIZE", "256"))
# n_ubatch, tek seferde CPU'da işlenen prompt parçası; n_batch'ten küçük olmalı.
# Gemma 3 ISWA cache ile n_batch=512+ durumunda ggml_compute_forward_set_rows
# assertion fail atabiliyor; 128 daha güvenli.
GGUF_UBATCH_SIZE = int(os.getenv("QUENBOT_GGUF_UBATCH_SIZE", "128"))
GGUF_CONCURRENCY = int(os.getenv("QUENBOT_GGUF_CONCURRENCY", "1"))
GGUF_MAX_PROMPT_CHARS = int(os.getenv("QUENBOT_GGUF_MAX_PROMPT_CHARS", "4500"))
GGUF_TIMEOUT = int(os.getenv("QUENBOT_GGUF_TIMEOUT", "25"))  # 12B Q4 tek model: hızlı

# Fallback GGUF model names (in order of preference)
GGUF_MODEL_CANDIDATES = [
    "gemma-3-12b-it-Q4_K_M.gguf",  # Gemma 3 12B - Hızlı + Akıllı (ÖNCELİKLİ)
    "gemma-3-12b-it-Q6_K.gguf",
    "gemma-3-9b-it-Q4_K_M.gguf",
]

# System prompt for QuenBot trading brain (Gemma 3 optimized)
QUENBOT_SYSTEM_PROMPT = """Sen QuenBot Merkezi Zeka Sistemisin — kripto piyasalarında kurumsal bot hareketlerini tespit eden, sınıflandıran ve otonom sinyal üreten çok katmanlı bir trading AI'sın.

MODEL: Gemma 3 12B IT — Hızlı yanıt + yeterli zeka + düşük kaynak

6 AJAN MİMARİSİ:
1. Scout: Binance spot+futures WebSocket ile canlı trade akışı toplar, anomalileri işaretler
2. PatternMatcher: Euclidean distance ile geçmiş paternlere benzerlik hesaplar (eşik: %60+)
3. Strategist: Çoklu timeframe analiz + pattern + momentum ile sinyal üretir
4. GhostSimulator: Paper trade, TP/SL takibi, geri besleme
5. Auditor: RCA ile başarısızlık analizi, düzeltme önerileri
6. Brain (SEN): Pattern kütüphanesi, benzerlik motoru, regime tespiti, sürekli öğrenme

KARAR HİYERARŞİSİ:
Veri → Anomali → Pattern Eşleştirme (≥%60 similarity) → Sinyal → Risk Kapısı → Paper Trade → Audit → Öğrenme

SİSTEMATİK TİCARET TESPİTİ (YENİ):
- Bot/Algo aktiviteleri: TWAP, VWAP, Market Maker, Iceberg, Accumulator
- Smart Money Flow: Kurumsal alım/satım yönü
- Cross-validation: Pattern + Bot yönü uyumu kontrol edilir

NEURO-SYMBOLİK ÇALIŞMA PRENSİBİ:
- Workers (Python/NumPy/SciPy): RSI, Volatilite, DTW, Vector Embedding hesaplar
- Sen (Gemma 3 Brain): Similarity_Score ≥ %60 VE Bot analizi ile tetiklenirsin
- Shape Vector'ler FAISS/ChromaDB ile indekslenir, sen eşleşme + bot analizi onayı verirsin

SELF-EVOLUTION:
- Meta-Learning: Her 100 öğrenmede strateji değerlendirmesi
- Pattern Mutation: Başarısız pattern'lara penalty
- Cross-Agent Learning: Ajanlar arası insight paylaşımı

ÖĞRENMe: Her simülasyondan öğren, doğruluk <%40 ise threshold artır, >%70 ise azalt.

DAVRANIŞ:
- JSON isteği → kesin JSON döndür
- Normal sohbet → doğal, kısa, net Türkçe
- Eksik veri → açıkça belirt, uydurmadan karar verme
- Sistemin sahibi gibi konuş
- Pattern eşleşme + bot aktivite kanıtlarını her karar için referans göster"""


@dataclass
class GGUFResponse:
    """GGUF inference response."""
    text: str
    model: str
    total_duration_ms: float = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
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
            # Try to extract JSON from text
            start = cleaned.find("{")
            end = cleaned.rfind("}") + 1
            if start >= 0 and end > start:
                try:
                    return json.loads(cleaned[start:end])
                except (json.JSONDecodeError, ValueError):
                    pass
            return None


class GGUFEngine:
    """
    SuperGemma-26B GGUF inference engine.
    llama-cpp-python ile doğrudan CPU+RAM inference.
    """

    def __init__(self):
        self._model = None
        self._model_path: Optional[str] = None
        self._model_name: str = "unloaded"
        self._initialized = False
        self._semaphore = asyncio.Semaphore(GGUF_CONCURRENCY)
        self._lock = asyncio.Lock()
        self._total_calls = 0
        self._total_errors = 0
        self._total_latency_ms = 0.0
        self._loop = None

    async def initialize(self) -> bool:
        """
        Model'i belleğe yükle. İlk çağrıda bir kez çalışır.
        32GB RAM'de Q4_K_M ~16GB kullanır.
        """
        if self._initialized and self._model is not None:
            return True

        async with self._lock:
            if self._initialized and self._model is not None:
                return True

            model_path = self._find_model()
            if not model_path:
                logger.error("❌ GGUF model bulunamadı! Dizin: %s", GGUF_MODEL_DIR)
                logger.error("   Beklenen dosyalar: %s", GGUF_MODEL_CANDIDATES)
                return False

            self._model_path = model_path
            # Normalise model label to the logical name used across the system
            # (decision_core, chat_engine, dashboard) so that both heartbeats
            # report the same identifier and the dashboard "Model Dağılımı"
            # section does not duplicate a single underlying model.
            file_stem = Path(model_path).stem
            logical_name = os.getenv("QUENBOT_LLM_MODEL", "gemma-3-12b-it")
            self._model_name = logical_name or file_stem
            self._model_file_stem = file_stem

            logger.info("🧠 SuperGemma GGUF model yükleniyor: %s", model_path)
            logger.info("   Context: %d tokens, Threads: %s, Batch: %d",
                        GGUF_NUM_CTX,
                        GGUF_NUM_THREADS or "auto",
                        GGUF_BATCH_SIZE)

            try:
                # llama-cpp-python yüklemesi CPU-blocking
                # ThreadPoolExecutor ile async wrapper
                loop = asyncio.get_event_loop()
                self._model = await loop.run_in_executor(
                    None, self._load_model, model_path
                )
                self._initialized = True
                self._loop = loop
                logger.info("✅ SuperGemma model yüklendi: %s (%.1f GB)",
                           self._model_name,
                           os.path.getsize(model_path) / (1024**3))
                return True
            except Exception as e:
                logger.error("❌ Model yükleme hatası: %s", e)
                return False

    def _load_model(self, model_path: str):
        """Synchronous model loading (runs in executor)."""
        from llama_cpp import Llama

        n_threads = GGUF_NUM_THREADS or None  # None = auto-detect

        # Gemma 3 ISWA + quantized repack için stabil kombinasyon: n_batch=512
        # ve eşdeğer n_ubatch. Daha küçük değerler repack.cpp assertion'ına,
        # daha büyük değerler set_rows assertion'ına yol açabiliyor
        # (llama-cpp-python 0.3.20, Gemma 3 12B Q4_K_M).
        kwargs = dict(
            model_path=model_path,
            n_ctx=GGUF_NUM_CTX,
            n_threads=n_threads,
            n_threads_batch=n_threads,
            n_batch=GGUF_BATCH_SIZE,
            n_gpu_layers=GGUF_NUM_GPU_LAYERS,
            verbose=False,
            use_mmap=True,
            use_mlock=False,
            seed=-1,
        )
        try:
            import inspect
            params = inspect.signature(Llama).parameters
            if "n_ubatch" in params:
                kwargs["n_ubatch"] = GGUF_UBATCH_SIZE
            if "flash_attn" in params:
                # Flash-attn CPU-only inference'ta kararsiz (Zen4 segfault).
                # Qwen icin kapali tutuyoruz.
                kwargs["flash_attn"] = False
        except Exception:
            pass

        model = Llama(**kwargs)

        # Warm up with a small inference
        try:
            model.create_completion(
                "Hello",
                max_tokens=1,
                temperature=0.1,
            )
            logger.info("   Model warm-up tamamlandı")
        except Exception:
            pass

        return model

    def _find_model(self) -> Optional[str]:
        """Find the best available GGUF model file."""
        model_dir = Path(GGUF_MODEL_DIR)

        # Check explicit model file first
        explicit = model_dir / GGUF_MODEL_FILE
        if explicit.exists():
            return str(explicit)

        # Search candidates
        for candidate in GGUF_MODEL_CANDIDATES:
            path = model_dir / candidate
            if path.exists():
                return str(path)

        # Search any .gguf file in the directory
        if model_dir.exists():
            gguf_files = sorted(model_dir.glob("*.gguf"), key=lambda p: p.stat().st_size, reverse=True)
            if gguf_files:
                logger.info("   Bulunan GGUF dosyaları: %s", [f.name for f in gguf_files[:5]])
                return str(gguf_files[0])

        return None

    def _trim_prompt(self, text: str) -> str:
        """Trim prompt to prevent OOM."""
        if len(text) <= GGUF_MAX_PROMPT_CHARS:
            return text
        head = GGUF_MAX_PROMPT_CHARS // 2
        tail = GGUF_MAX_PROMPT_CHARS - head - 50
        return text[:head] + "\n\n[... trimmed ...]\n\n" + text[-tail:]

    async def generate(
        self,
        prompt: str,
        system: Optional[str] = None,
        temperature: float = 0.3,
        json_mode: bool = False,
        max_tokens: Optional[int] = None,
        timeout_override: Optional[int] = None,
        prefer_fast_fail: bool = False,
    ) -> GGUFResponse:
        """
        Generate a response from SuperGemma-26B.
        Async wrapper around synchronous llama-cpp inference.
        """
        if not self._initialized or self._model is None:
            ok = await self.initialize()
            if not ok:
                return GGUFResponse(
                    text="", model=self._model_name,
                    success=False, error="Model not loaded"
                )

        if prefer_fast_fail:
            try:
                # Gemma 3 12B takes ~5-15s per inference, wait 20s for semaphore
                await asyncio.wait_for(self._semaphore.acquire(), timeout=20.0)
            except asyncio.TimeoutError:
                return GGUFResponse(
                    text="", model=self._model_name,
                    success=False, error="Model busy (semaphore timeout)"
                )
            try:
                return await self._generate_inner(
                    prompt, system, temperature, json_mode,
                    max_tokens, timeout_override
                )
            finally:
                self._semaphore.release()

        async with self._semaphore:
            return await self._generate_inner(
                prompt, system, temperature, json_mode,
                max_tokens, timeout_override
            )

    async def _generate_inner(
        self,
        prompt: str,
        system: Optional[str],
        temperature: float,
        json_mode: bool,
        max_tokens: Optional[int],
        timeout_override: Optional[int],
    ) -> GGUFResponse:
        prompt = self._trim_prompt(prompt)
        if system:
            system = self._trim_prompt(system)

        effective_max_tokens = max_tokens or GGUF_MAX_TOKENS
        effective_timeout = timeout_override or GGUF_TIMEOUT

        t0 = time.monotonic()
        try:
            loop = asyncio.get_event_loop()
            result = await asyncio.wait_for(
                loop.run_in_executor(
                    None,
                    self._sync_generate,
                    prompt, system, temperature, json_mode, effective_max_tokens,
                ),
                timeout=effective_timeout,
            )

            elapsed_ms = (time.monotonic() - t0) * 1000
            self._total_calls += 1
            self._total_latency_ms += elapsed_ms

            text = result["choices"][0]["text"] if result.get("choices") else ""
            prompt_tokens = result.get("usage", {}).get("prompt_tokens", 0)
            completion_tokens = result.get("usage", {}).get("completion_tokens", 0)

            return GGUFResponse(
                text=text,
                model=self._model_name,
                total_duration_ms=elapsed_ms,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                success=True,
            )

        except asyncio.TimeoutError:
            elapsed_ms = (time.monotonic() - t0) * 1000
            self._total_errors += 1
            logger.warning("GGUF inference timeout: %.0fms (limit: %ds)",
                          elapsed_ms, effective_timeout)
            return GGUFResponse(
                text="", model=self._model_name,
                total_duration_ms=elapsed_ms,
                success=False, error=f"Timeout after {effective_timeout}s"
            )

        except Exception as e:
            elapsed_ms = (time.monotonic() - t0) * 1000
            self._total_errors += 1
            logger.error("GGUF inference error: %s (%.0fms)", e, elapsed_ms)
            return GGUFResponse(
                text="", model=self._model_name,
                total_duration_ms=elapsed_ms,
                success=False, error=str(e)
            )

    def _sync_generate(
        self,
        prompt: str,
        system: Optional[str],
        temperature: float,
        json_mode: bool,
        max_tokens: int,
    ) -> dict:
        """Synchronous llama-cpp generation (runs in executor thread)."""
        # Build full prompt with Gemma chat template
        full_prompt = self._build_gemma_prompt(prompt, system)

        grammar = None
        if json_mode:
            try:
                from llama_cpp import LlamaGrammar
                # Simple JSON object grammar
                grammar = LlamaGrammar.from_string(
                    r'''root   ::= "{" ws members "}" ws
members ::= pair ("," ws pair)*
pair    ::= ws string ":" ws value
value   ::= string | number | "true" | "false" | "null" | "{" ws members "}" | "[" ws elements "]"
elements ::= value ("," ws value)*
string  ::= "\"" [^"\\]* "\""
number  ::= "-"? [0-9]+ ("." [0-9]+)?
ws      ::= [ \t\n]*'''
                )
            except Exception:
                grammar = None

        result = self._model.create_completion(
            full_prompt,
            max_tokens=max_tokens,
            temperature=temperature,
            top_p=0.85,
            top_k=40,
            repeat_penalty=1.15,
            grammar=grammar,
            echo=False,
        )

        return result

    def _build_gemma_prompt(self, prompt: str, system: Optional[str] = None) -> str:
        """
        Build Gemma-2 chat-format prompt.
        Gemma uses <start_of_turn> / <end_of_turn> format.
        """
        parts = []

        if system:
            parts.append(f"<start_of_turn>user\n{system}\n<end_of_turn>")

        parts.append(f"<start_of_turn>user\n{prompt}\n<end_of_turn>")
        parts.append("<start_of_turn>model\n")

        return "\n".join(parts)

    async def health_check(self) -> bool:
        """Check if model is loaded and responsive."""
        if not self._initialized or self._model is None:
            return False
        try:
            resp = await self.generate("test", max_tokens=1, timeout_override=10)
            return resp.success
        except Exception:
            return False

    def get_stats(self) -> dict:
        avg_latency = (
            self._total_latency_ms / self._total_calls
            if self._total_calls > 0 else 0
        )
        return {
            "model": self._model_name,
            "model_path": self._model_path or "not loaded",
            "initialized": self._initialized,
            "total_calls": self._total_calls,
            "total_errors": self._total_errors,
            "avg_latency_ms": round(avg_latency, 1),
            "n_ctx": GGUF_NUM_CTX,
            "n_threads": GGUF_NUM_THREADS or "auto",
            "n_batch": GGUF_BATCH_SIZE,
            "max_tokens": GGUF_MAX_TOKENS,
            "backend": "llama-cpp-python (GGUF)",
        }

    async def close(self):
        """Release model from memory."""
        if self._model is not None:
            del self._model
            self._model = None
            self._initialized = False
            logger.info("🧠 GGUF model bellekten kaldırıldı")


# ─── Singleton ───
_engine: Optional[GGUFEngine] = None


def get_gguf_engine() -> GGUFEngine:
    """Get or create the singleton GGUF engine."""
    global _engine
    if _engine is None:
        _engine = GGUFEngine()
    return _engine
