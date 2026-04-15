from __future__ import annotations

import asyncio
import json
import logging
import threading
from typing import Any, Callable, Dict, Optional

from websocket import WebSocketApp

logger = logging.getLogger("quenbot.websocket_client")


class WebSocketClientBridge:
    """Threaded websocket-client adapter for low-latency exchange feeds.

    Existing agents are asyncio-based; this bridge pushes raw messages into an
    asyncio queue without rewriting the rest of the pipeline.
    """

    def __init__(
        self,
        url: str,
        *,
        loop: asyncio.AbstractEventLoop,
        name: str,
        subscribe_payload: Optional[Dict[str, Any]] = None,
        parser: Optional[Callable[[str], Optional[Dict[str, Any]]]] = None,
    ):
        self.url = url
        self.loop = loop
        self.name = name
        self.subscribe_payload = subscribe_payload
        self.parser = parser
        self.queue: asyncio.Queue[Dict[str, Any]] = asyncio.Queue(maxsize=2000)
        self._app: Optional[WebSocketApp] = None
        self._thread: Optional[threading.Thread] = None
        self._running = False
        self._last_error: Optional[str] = None

    def start(self):
        if self._running:
            return
        self._running = True
        self._app = WebSocketApp(
            self.url,
            on_open=self._on_open,
            on_message=self._on_message,
            on_error=self._on_error,
            on_close=self._on_close,
        )
        self._thread = threading.Thread(target=self._app.run_forever, daemon=True, name=f"{self.name}-ws")
        self._thread.start()

    def stop(self):
        self._running = False
        if self._app:
            self._app.close()

    def _on_open(self, ws):
        if self.subscribe_payload:
            ws.send(json.dumps(self.subscribe_payload, ensure_ascii=True))
        logger.info("websocket-client bridge opened: %s", self.name)

    def _on_message(self, ws, message: str):
        if not self._running:
            return
        payload = self.parser(message) if self.parser else {"raw": message}
        if payload is None:
            return
        asyncio.run_coroutine_threadsafe(self._safe_put(payload), self.loop)

    async def _safe_put(self, payload: Dict[str, Any]):
        try:
            self.queue.put_nowait(payload)
        except asyncio.QueueFull:
            logger.debug("websocket-client queue full, dropping oldest payload")
            try:
                _ = self.queue.get_nowait()
            except asyncio.QueueEmpty:
                pass
            self.queue.put_nowait(payload)

    def _on_error(self, ws, error: Any):
        self._last_error = str(error)
        logger.debug("websocket-client error [%s]: %s", self.name, error)

    def _on_close(self, ws, status_code, msg):
        logger.info("websocket-client bridge closed [%s] code=%s msg=%s", self.name, status_code, msg)

    def stats(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "url": self.url,
            "running": self._running,
            "queued": self.queue.qsize(),
            "last_error": self._last_error,
        }