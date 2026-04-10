"""
QuenBot V2 — Directive Store
Persists 'Permanent Directives' from the dashboard Master Control.
Directives are prepended to every LLM call as master system instructions.
Uses a JSON file for lightweight persistence (no extra infra needed).
"""

import json
import logging
import os
import asyncio
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger("quenbot.directive_store")

DIRECTIVE_FILE = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "directives.json"
)


class DirectiveStore:
    """Thread-safe persistent directive storage."""

    def __init__(self, filepath: str = DIRECTIVE_FILE):
        self._filepath = filepath
        self._lock = asyncio.Lock()
        self._cache: Optional[dict] = None

    def _load_sync(self) -> dict:
        """Synchronous load for initialization."""
        if not os.path.exists(self._filepath):
            return {
                "master_directive": "",
                "agent_overrides": {},
                "history": [],
                "updated_at": None,
            }
        try:
            with open(self._filepath, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError) as e:
            logger.error("Failed to load directives: %s", e)
            return {
                "master_directive": "",
                "agent_overrides": {},
                "history": [],
                "updated_at": None,
            }

    def _save_sync(self, data: dict):
        """Synchronous save."""
        tmp_path = self._filepath + ".tmp"
        try:
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False, default=str)
            os.replace(tmp_path, self._filepath)
        except IOError as e:
            logger.error("Failed to save directives: %s", e)
            if os.path.exists(tmp_path):
                os.remove(tmp_path)

    async def _ensure_loaded(self) -> dict:
        if self._cache is None:
            self._cache = self._load_sync()
        return self._cache

    async def get_master_directive(self) -> str:
        """Get the current master directive text."""
        async with self._lock:
            data = await self._ensure_loaded()
            return data.get("master_directive", "")

    async def set_master_directive(self, text: str):
        """Set the master directive (prepended to all LLM calls)."""
        async with self._lock:
            data = await self._ensure_loaded()
            old = data.get("master_directive", "")

            # Record history (keep last 20)
            if old and old != text:
                history = data.get("history", [])
                history.append({
                    "previous": old[:500],
                    "changed_at": datetime.now(timezone.utc).isoformat(),
                })
                data["history"] = history[-20:]

            data["master_directive"] = text
            data["updated_at"] = datetime.now(timezone.utc).isoformat()
            self._cache = data
            self._save_sync(data)

        logger.info("Master directive updated (%d chars)", len(text))

    async def get_agent_override(self, agent_name: str) -> str:
        """Get agent-specific directive override."""
        async with self._lock:
            data = await self._ensure_loaded()
            return data.get("agent_overrides", {}).get(agent_name.lower(), "")

    async def set_agent_override(self, agent_name: str, text: str):
        """Set an agent-specific directive override."""
        async with self._lock:
            data = await self._ensure_loaded()
            if "agent_overrides" not in data:
                data["agent_overrides"] = {}
            data["agent_overrides"][agent_name.lower()] = text
            data["updated_at"] = datetime.now(timezone.utc).isoformat()
            self._cache = data
            self._save_sync(data)

        logger.info("Agent override updated: %s (%d chars)", agent_name, len(text))

    async def get_full_directive(self, agent_name: Optional[str] = None) -> str:
        """
        Get the combined directive for an agent:
        master directive + agent-specific override.
        """
        parts = []
        master = await self.get_master_directive()
        if master:
            parts.append(master)
        if agent_name:
            override = await self.get_agent_override(agent_name)
            if override:
                parts.append(f"\n[{agent_name.upper()} SPECIFIC]\n{override}")
        return "\n".join(parts)

    async def get_all(self) -> dict:
        """Get all directive data (for API/dashboard)."""
        async with self._lock:
            data = await self._ensure_loaded()
            return {
                "master_directive": data.get("master_directive", ""),
                "agent_overrides": data.get("agent_overrides", {}),
                "updated_at": data.get("updated_at"),
                "history_count": len(data.get("history", [])),
            }

    async def clear(self):
        """Clear all directives."""
        async with self._lock:
            self._cache = {
                "master_directive": "",
                "agent_overrides": {},
                "history": [],
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }
            self._save_sync(self._cache)
        logger.info("All directives cleared")


# Singleton
_store: Optional[DirectiveStore] = None


def get_directive_store() -> DirectiveStore:
    global _store
    if _store is None:
        _store = DirectiveStore()
    return _store
