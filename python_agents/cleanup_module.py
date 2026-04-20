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
                os.getenv("QUENBOT_LLM_MODEL", "gemma-3-12b-it"),
                os.getenv("QUENBOT_DECISION_MODEL", os.getenv("QUENBOT_LLM_MODEL", "gemma-3-12b-it")),
            ]
        gguf_file = os.getenv("QUENBOT_GGUF_MODEL_FILE", "").strip()
        server_alias = os.getenv("QUENBOT_LLM_SERVER_MODEL", "").strip()
        if gguf_file:
            active.append(gguf_file)
            active.append(Path(gguf_file).stem)
        if server_alias:
            active.append(server_alias)
        self.active_models = sorted({m.strip() for m in active if m.strip()})
        self.gguf_model_dir = Path(os.getenv("QUENBOT_GGUF_MODEL_DIR", "/root/models"))

    def scan(self) -> Dict[str, List[str]]:
        model_dir = self.gguf_model_dir
        keep: List[str] = []
        remove: List[str] = []
        active_tokens = [m.casefold() for m in self.active_models]
        if model_dir.exists():
            for item in model_dir.glob("*.gguf"):
                item_name = item.name.casefold()
                item_stem = item.stem.casefold()
                if any(token in item_name or token in item_stem for token in active_tokens):
                    keep.append(str(item))
                else:
                    remove.append(str(item))
        return {
            "active_models": self.active_models,
            "keep_models": keep,
            "stale_models": remove,
            "model_dir": str(model_dir),
        }

    def cleanup(self, dry_run: bool = True) -> Dict[str, List[str]]:
        report = self.scan()
        deleted: List[str] = []
        if not dry_run:
            for file_path in report["stale_models"]:
                try:
                    Path(file_path).unlink(missing_ok=True)
                    deleted.append(file_path)
                except Exception as exc:
                    logger.warning("Cleanup failed for %s: %s", file_path, exc)
        report["deleted"] = deleted
        report["dry_run"] = [str(dry_run)]
        return report