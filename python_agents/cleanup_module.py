from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Dict, List

logger = logging.getLogger("quenbot.cleanup")


class CleanupModule:
    def __init__(self):
        configured = os.getenv("QUENBOT_ACTIVE_MODELS", "")
        active = [m.strip() for m in configured.split(",") if m.strip()]
        if not active:
            active = [
                os.getenv("QUENBOT_LLM_MODEL", "qwen3:8b"),
                os.getenv("QUENBOT_DECISION_MODEL", os.getenv("QUENBOT_LLM_MODEL", "qwen3:8b")),
                os.getenv("QUENBOT_CHAT_MODEL", os.getenv("QUENBOT_LLM_MODEL", "qwen3:8b")),
            ]
        self.active_models = sorted(set(active))
        self.ollama_root = Path(os.getenv("QUENBOT_OLLAMA_ROOT", str(Path.home() / ".ollama" / "models")))

    def scan(self) -> Dict[str, List[str]]:
        manifests = self.ollama_root / "manifests"
        blobs = self.ollama_root / "blobs"
        keep: List[str] = []
        remove: List[str] = []
        if manifests.exists():
            for item in manifests.rglob("*"):
                if not item.is_file():
                    continue
                path_text = str(item)
                if any(model.replace(":", "/") in path_text for model in self.active_models):
                    keep.append(path_text)
                else:
                    remove.append(path_text)
        return {
            "active_models": self.active_models,
            "keep_manifests": keep,
            "stale_manifests": remove,
            "blob_root": [str(blobs)] if blobs.exists() else [],
        }

    def cleanup(self, dry_run: bool = True) -> Dict[str, List[str]]:
        report = self.scan()
        deleted: List[str] = []
        if not dry_run:
            for file_path in report["stale_manifests"]:
                try:
                    Path(file_path).unlink(missing_ok=True)
                    deleted.append(file_path)
                except Exception as exc:
                    logger.warning("Cleanup failed for %s: %s", file_path, exc)
        report["deleted"] = deleted
        report["dry_run"] = [str(dry_run)]
        return report