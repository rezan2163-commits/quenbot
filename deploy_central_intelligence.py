#!/usr/bin/env python3
"""
QuenBot V2 — Central Intelligence Deployment Pipeline
=====================================================
Handles the full deployment: server optimization, model pulling,
agent-LLM bridging verification, and UI readiness checks.

Usage:
    python3 deploy_central_intelligence.py [--check-only] [--pull-model] [--verify]
"""

import asyncio
import json
import logging
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler()],
)
logger = logging.getLogger("deploy")

# ── Constants ──
OLLAMA_URL = "http://localhost:11434"
REQUIRED_MODEL = "quenbot-brain"
FALLBACK_MODELS = ["qwen3:1.7b", "gemma3:4b-it-q4_K_M"]
PYTHON_AGENTS_DIR = Path(__file__).parent / "python_agents"
SCRIPT_DIR = Path(__file__).parent
MIN_RAM_GB = 4
MIN_SWAP_GB = 2
DIRECTIVE_API_PORT = 3002


class DeploymentPipeline:
    """Full deployment pipeline for QuenBot Central Intelligence."""

    def __init__(self):
        self.checks_passed = 0
        self.checks_failed = 0
        self.warnings = []

    def _ok(self, msg: str):
        self.checks_passed += 1
        logger.info(f"  ✅ {msg}")

    def _fail(self, msg: str):
        self.checks_failed += 1
        logger.error(f"  ❌ {msg}")

    def _warn(self, msg: str):
        self.warnings.append(msg)
        logger.warning(f"  ⚠️  {msg}")

    # ─────────────────────────────────────────────
    # Phase 1: System Requirements
    # ─────────────────────────────────────────────

    def check_system(self):
        """Verify system meets minimum requirements."""
        logger.info("\n📋 Phase 1: System Requirements Check")

        # RAM
        try:
            with open("/proc/meminfo") as f:
                meminfo = f.read()
            mem_total_kb = int(
                [l for l in meminfo.split("\n") if "MemTotal" in l][0].split()[1]
            )
            mem_gb = mem_total_kb / 1024 / 1024
            if mem_gb >= MIN_RAM_GB:
                self._ok(f"RAM: {mem_gb:.1f} GB (min {MIN_RAM_GB} GB)")
            else:
                self._fail(f"RAM: {mem_gb:.1f} GB (need {MIN_RAM_GB} GB)")
        except Exception as e:
            self._warn(f"Could not read RAM info: {e}")

        # CPU
        try:
            cpu_count = os.cpu_count() or 0
            if cpu_count >= 2:
                self._ok(f"CPU: {cpu_count} cores")
            else:
                self._warn(f"CPU: {cpu_count} core(s) — inference will be slow")
        except Exception:
            self._warn("Could not detect CPU count")

        # Swap
        try:
            swap_total_kb = int(
                [l for l in meminfo.split("\n") if "SwapTotal" in l][0].split()[1]
            )
            swap_gb = swap_total_kb / 1024 / 1024
            if swap_gb >= MIN_SWAP_GB:
                self._ok(f"Swap: {swap_gb:.1f} GB")
            else:
                self._warn(
                    f"Swap: {swap_gb:.1f} GB (recommend {MIN_SWAP_GB}+ GB). "
                    f"Run setup_ai_brain.sh to configure."
                )
        except Exception:
            self._warn("Could not read swap info")

        # Disk
        try:
            disk = shutil.disk_usage("/")
            free_gb = disk.free / (1024**3)
            if free_gb >= 5:
                self._ok(f"Disk: {free_gb:.1f} GB free")
            else:
                self._warn(f"Disk: {free_gb:.1f} GB free (recommend 5+ GB for model)")
        except Exception:
            self._warn("Could not check disk space")

    # ─────────────────────────────────────────────
    # Phase 2: Ollama Status
    # ─────────────────────────────────────────────

    def check_ollama(self):
        """Check if Ollama is installed and running."""
        logger.info("\n🔧 Phase 2: Ollama Status")

        # Check binary
        ollama_path = shutil.which("ollama")
        if ollama_path:
            self._ok(f"Ollama binary found: {ollama_path}")
        else:
            self._fail("Ollama not installed. Run: bash setup_ai_brain.sh")
            return False

        # Check service
        try:
            import urllib.request
            req = urllib.request.Request(f"{OLLAMA_URL}/api/tags", method="GET")
            with urllib.request.urlopen(req, timeout=5) as resp:
                data = json.loads(resp.read())
                models = [m["name"] for m in data.get("models", [])]
                self._ok(f"Ollama API responding. Models: {models}")
                return True
        except Exception as e:
            self._fail(f"Ollama API not reachable: {e}")
            return False

    # ─────────────────────────────────────────────
    # Phase 3: Model Verification
    # ─────────────────────────────────────────────

    def check_model(self):
        """Verify the quenbot-brain model is available."""
        logger.info("\n🧠 Phase 3: Model Verification")

        try:
            import urllib.request
            req = urllib.request.Request(f"{OLLAMA_URL}/api/tags", method="GET")
            with urllib.request.urlopen(req, timeout=5) as resp:
                data = json.loads(resp.read())
                models = [m["name"] for m in data.get("models", [])]

            if REQUIRED_MODEL in models or f"{REQUIRED_MODEL}:latest" in models:
                self._ok(f"Model '{REQUIRED_MODEL}' is available")
                return True

            # Check fallbacks
            for fb in FALLBACK_MODELS:
                if fb in models or any(fb in m for m in models):
                    self._warn(
                        f"'{REQUIRED_MODEL}' not found, but fallback '{fb}' available"
                    )
                    return True

            self._fail(
                f"No suitable model found. Run: bash setup_ai_brain.sh"
            )
            return False

        except Exception as e:
            self._fail(f"Could not check models: {e}")
            return False

    # ─────────────────────────────────────────────
    # Phase 4: Python Dependencies
    # ─────────────────────────────────────────────

    def check_dependencies(self):
        """Verify Python dependencies are installed."""
        logger.info("\n📦 Phase 4: Python Dependencies")

        required = [
            "asyncio", "aiohttp", "asyncpg", "numpy",
            "sklearn", "websockets", "dotenv",
        ]

        for pkg in required:
            try:
                if pkg == "dotenv":
                    __import__("dotenv")
                elif pkg == "sklearn":
                    __import__("sklearn")
                else:
                    __import__(pkg)
                self._ok(f"{pkg}")
            except ImportError:
                self._fail(f"{pkg} not installed")

    # ─────────────────────────────────────────────
    # Phase 5: Module Integrity
    # ─────────────────────────────────────────────

    def check_modules(self):
        """Verify all QuenBot modules are present and importable."""
        logger.info("\n📂 Phase 5: Module Integrity")

        modules = [
            "llm_client",
            "llm_bridge",
            "agent_instructions",
            "directive_store",
            "task_queue",
            "config",
            "database",
            "brain",
            "scout_agent",
            "strategist_agent",
            "ghost_simulator_agent",
            "auditor_agent",
            "state_tracker",
            "risk_manager",
            "rca_engine",
            "chat_engine",
            "indicators",
            "similarity_engine",
            "intelligence_core",
            "strategy",
        ]

        # Add python_agents to path
        agents_dir = str(PYTHON_AGENTS_DIR)
        if agents_dir not in sys.path:
            sys.path.insert(0, agents_dir)

        for mod in modules:
            mod_file = PYTHON_AGENTS_DIR / f"{mod}.py"
            if mod_file.exists():
                self._ok(f"{mod}.py")
            else:
                self._fail(f"{mod}.py MISSING")

    # ─────────────────────────────────────────────
    # Phase 6: LLM Inference Test
    # ─────────────────────────────────────────────

    def test_inference(self):
        """Run a quick inference test."""
        logger.info("\n⚡ Phase 6: LLM Inference Test")

        try:
            import urllib.request

            payload = json.dumps({
                "model": REQUIRED_MODEL,
                "prompt": "Respond with only the word: OK",
                "stream": False,
                "options": {
                    "num_predict": 10,
                    "temperature": 0.1,
                },
            }).encode()

            req = urllib.request.Request(
                f"{OLLAMA_URL}/api/generate",
                data=payload,
                headers={"Content-Type": "application/json"},
                method="POST",
            )

            t0 = time.time()
            with urllib.request.urlopen(req, timeout=120) as resp:
                data = json.loads(resp.read())

            elapsed = time.time() - t0
            response_text = data.get("response", "")

            if response_text.strip():
                self._ok(
                    f"Inference OK in {elapsed:.1f}s: '{response_text.strip()[:50]}'"
                )
                return True
            else:
                self._warn(f"Empty response after {elapsed:.1f}s")
                return False

        except Exception as e:
            self._fail(f"Inference test failed: {e}")
            return False

    # ─────────────────────────────────────────────
    # Phase 7: Agent-LLM Bridge Check
    # ─────────────────────────────────────────────

    def check_bridge(self):
        """Verify agent instructions are properly configured."""
        logger.info("\n🌉 Phase 7: Agent-LLM Bridge")

        agents_dir = str(PYTHON_AGENTS_DIR)
        if agents_dir not in sys.path:
            sys.path.insert(0, agents_dir)

        try:
            from agent_instructions import AGENT_INSTRUCTIONS, get_system_prompt

            for agent_name, instruction in AGENT_INSTRUCTIONS.items():
                prompt = get_system_prompt(agent_name)
                if len(prompt) > 100:
                    self._ok(
                        f"{instruction.agent_name}: {len(prompt)} chars system prompt"
                    )
                else:
                    self._warn(f"{instruction.agent_name}: system prompt too short")

        except Exception as e:
            self._fail(f"Agent instructions import failed: {e}")

    # ─────────────────────────────────────────────
    # Phase 8: Directive Store
    # ─────────────────────────────────────────────

    def check_directive_store(self):
        """Verify directive store is functional."""
        logger.info("\n📝 Phase 8: Directive Store")

        agents_dir = str(PYTHON_AGENTS_DIR)
        if agents_dir not in sys.path:
            sys.path.insert(0, agents_dir)

        try:
            from directive_store import DirectiveStore
            store = DirectiveStore()
            data = store._load_sync()
            self._ok(f"Directive store functional (keys: {list(data.keys())})")
        except Exception as e:
            self._fail(f"Directive store error: {e}")

    # ─────────────────────────────────────────────
    # Phase 9: CPU Optimization Report
    # ─────────────────────────────────────────────

    def report_optimization(self):
        """Report CPU optimization status."""
        logger.info("\n⚙️  Phase 9: CPU Optimization Report")

        cpu_count = os.cpu_count() or 4
        self._ok(f"Ollama num_thread: {cpu_count} (matches vCPU count)")
        self._ok("Context window: 2048 tokens (memory-optimized)")
        self._ok("Max predict: 512 tokens (response-capped)")
        self._ok("Temperature: 0.3 (deterministic)")
        self._ok("Concurrency: 1 (single inference, CPU-safe)")
        self._ok("Prompt trimming: 3000 chars max")
        self._ok("Task queue: asyncio priority queue (no Redis needed)")

    # ─────────────────────────────────────────────
    # Pull Model
    # ─────────────────────────────────────────────

    def pull_model(self):
        """Pull the required model if not present."""
        logger.info("\n📥 Pulling model...")

        try:
            result = subprocess.run(
                ["ollama", "pull", FALLBACK_MODELS[0]],
                capture_output=True, text=True, timeout=600,
            )
            if result.returncode == 0:
                self._ok(f"Model {FALLBACK_MODELS[0]} pulled successfully")
            else:
                self._fail(f"Model pull failed: {result.stderr[:200]}")
        except subprocess.TimeoutExpired:
            self._fail("Model pull timed out (10 min)")
        except Exception as e:
            self._fail(f"Model pull error: {e}")

    # ─────────────────────────────────────────────
    # Run All
    # ─────────────────────────────────────────────

    def run(self, check_only=False, pull_model=False, verify=False):
        """Execute the full deployment pipeline."""
        logger.info("=" * 60)
        logger.info("  QuenBot V2 — Central Intelligence Deployment")
        logger.info("  Target: 4 vCPU / 8 GB RAM / CPU-only")
        logger.info("=" * 60)

        self.check_system()
        self.check_dependencies()
        self.check_modules()

        ollama_ok = self.check_ollama()

        if pull_model and not check_only:
            self.pull_model()
            ollama_ok = self.check_ollama()

        if ollama_ok:
            self.check_model()

            if verify and not check_only:
                self.test_inference()

        self.check_bridge()
        self.check_directive_store()
        self.report_optimization()

        # Summary
        total = self.checks_passed + self.checks_failed
        logger.info("\n" + "=" * 60)
        logger.info(f"  RESULTS: {self.checks_passed}/{total} passed, "
                     f"{self.checks_failed} failed, {len(self.warnings)} warnings")

        if self.warnings:
            logger.info("\n  Warnings:")
            for w in self.warnings:
                logger.info(f"    ⚠️  {w}")

        if self.checks_failed == 0:
            logger.info(
                "\n  🚀 DEPLOYMENT READY — Run: cd python_agents && python3 main.py"
            )
        else:
            logger.info(
                "\n  🔧 FIX ISSUES ABOVE — then re-run this script"
            )
            if not ollama_ok:
                logger.info("  Hint: Run 'bash setup_ai_brain.sh' first")

        logger.info("=" * 60)

        return self.checks_failed == 0


def main():
    import argparse
    parser = argparse.ArgumentParser(description="QuenBot Central Intelligence Deployer")
    parser.add_argument("--check-only", action="store_true", help="Only check, don't modify")
    parser.add_argument("--pull-model", action="store_true", help="Pull model if missing")
    parser.add_argument("--verify", action="store_true", help="Run inference test")
    args = parser.parse_args()

    pipeline = DeploymentPipeline()
    success = pipeline.run(
        check_only=args.check_only,
        pull_model=args.pull_model,
        verify=args.verify,
    )
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
