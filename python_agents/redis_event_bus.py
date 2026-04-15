from __future__ import annotations

import asyncio
import json
import logging
import os
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Dict, Optional

import redis.asyncio as redis

from qwen_models import CommunicationLogEntry

logger = logging.getLogger("quenbot.redis_bridge")


def _json_default(value: Any):
    if isinstance(value, datetime):
        return value.isoformat()
    if hasattr(value, "model_dump"):
        return value.model_dump()
    if hasattr(value, "value"):
        return value.value
    return str(value)


class RedisEventBridge:
    def __init__(self, local_bus=None):
        self.url = os.getenv("QUENBOT_REDIS_URL", "redis://127.0.0.1:6379/0")
        self.namespace = os.getenv("QUENBOT_REDIS_NAMESPACE", "quenbot")
        self.enabled = os.getenv("QUENBOT_ENABLE_REDIS", "1").lower() not in {"0", "false", "no"}
        self.local_bus = local_bus
        self.client: Optional[redis.Redis] = None
        self.pubsub = None
        self._subscriber_task: Optional[asyncio.Task] = None
        self._stats = {
            "connected": False,
            "published": 0,
            "received": 0,
            "last_error": None,
        }

    async def connect(self):
        if not self.enabled:
            return False
        try:
            self.client = redis.from_url(self.url, decode_responses=True)
            await self.client.ping()
            self._stats["connected"] = True
            logger.info("✓ Redis bridge connected: %s", self.url)
            return True
        except Exception as exc:
            self._stats["connected"] = False
            self._stats["last_error"] = str(exc)
            logger.warning("Redis bridge unavailable, local bus only: %s", exc)
            return False

    async def close(self):
        if self._subscriber_task:
            self._subscriber_task.cancel()
        if self.pubsub:
            await self.pubsub.close()
        if self.client:
            await self.client.aclose()
        self._stats["connected"] = False

    def _channel(self, suffix: str) -> str:
        return f"{self.namespace}.{suffix}"

    async def publish(self, channel: str, payload: Dict[str, Any]):
        if not self.client or not self._stats["connected"]:
            return False
        try:
            await self.client.publish(self._channel(channel), json.dumps(payload, default=_json_default, ensure_ascii=True))
            self._stats["published"] += 1
            return True
        except Exception as exc:
            self._stats["last_error"] = str(exc)
            logger.debug("Redis publish failed: %s", exc)
            return False

    async def mirror_event(self, event):
        payload = {
            "type": getattr(getattr(event, "type", None), "value", getattr(event, "type", "unknown")),
            "source": getattr(event, "source", "unknown"),
            "timestamp": getattr(event, "timestamp", datetime.now(timezone.utc).timestamp()),
            "priority": getattr(event, "priority", 0),
            "data": getattr(event, "data", {}),
        }
        await self.publish("events", payload)

    async def publish_command(self, entry: CommunicationLogEntry):
        await self.publish("commands", entry.model_dump(mode="json"))

    async def publish_directive(self, payload: Dict[str, Any]):
        await self.publish("directives", payload)

    async def start_listener(
        self,
        handler: Callable[[str, Dict[str, Any]], Awaitable[None]],
        channels: Optional[list[str]] = None,
    ):
        if not self.client or not self._stats["connected"]:
            return
        if self._subscriber_task and not self._subscriber_task.done():
            return

        async def _runner():
            subscribed = channels or ["commands", "directives"]
            self.pubsub = self.client.pubsub()
            await self.pubsub.subscribe(*[self._channel(name) for name in subscribed])
            while True:
                message = await self.pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0)
                if not message:
                    await asyncio.sleep(0.1)
                    continue
                try:
                    channel_name = str(message.get("channel", "")).split(".")[-1]
                    payload = json.loads(message.get("data") or "{}")
                    self._stats["received"] += 1
                    await handler(channel_name, payload)
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    self._stats["last_error"] = str(exc)
                    logger.debug("Redis listener message failed: %s", exc)

        self._subscriber_task = asyncio.create_task(_runner())

    def get_stats(self) -> Dict[str, Any]:
        return dict(self._stats)


_bridge: Optional[RedisEventBridge] = None


def get_redis_bridge(local_bus=None) -> RedisEventBridge:
    global _bridge
    if _bridge is None:
        _bridge = RedisEventBridge(local_bus=local_bus)
    return _bridge