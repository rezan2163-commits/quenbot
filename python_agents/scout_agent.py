import asyncio
from asyncio import QueueEmpty
import json
import logging
import os
import time
import contextlib
from datetime import datetime, timedelta
from typing import Dict, List, Any, Optional

import numpy as np
import websockets
import aiohttp

from config import Config
from database import Database
from indicators import compute_all_indicators
from similarity_engine import cosine_sim
from vector_memory import get_vector_store
from websocket_ingestion import WebSocketClientBridge
from event_bus import Event, EventType, get_event_bus

logger = logging.getLogger(__name__)

# Lazy LLM bridge import
_llm_bridge = None
def _get_llm_bridge():
    global _llm_bridge
    if _llm_bridge is None:
        try:
            from llm_bridge import get_llm_bridge
            _llm_bridge = get_llm_bridge()
        except Exception:
            _llm_bridge = None
    return _llm_bridge

# Multi-timeframe windows for movement detection (minutes)
MOVEMENT_TIMEFRAMES = {
    '5m': 5,
    '15m': 15,
    '1h': 60,
}

# Minimum change to capture a historical signature
SIGNATURE_THRESHOLD = 0.02  # 2% (per strategy requirement)

class ScoutAgent:
    def __init__(self, db: Database, brain=None):
        self.db = db
        self.brain = brain
        self.running = False
        self.connections: Dict[str, Any] = {}
        self.last_activity = None
        self.price_cache: Dict[str, float] = {}
        self.http_session: Optional[aiohttp.ClientSession] = None
        self.last_rest_fetch: Dict[str, datetime] = {}
        self._active_watchlist: List[str] = []
        self.trade_counter = 0
        self.vector_store = get_vector_store()
        self._ws_client_enabled = False
        self._ws_client_bridges: List[WebSocketClientBridge] = []
        self.event_bus = get_event_bus()
        self._orderbook_state: Dict[str, Dict[str, float]] = {}
        self._bybit_ws_attempts: Dict[str, int] = {'spot': 0, 'futures': 0}
        self._retry_counts: Dict[str, int] = {}
        self._exchange_last_trade_at: Dict[str, datetime] = {}
        self._last_bybit_rest_fetch_at: Dict[str, datetime] = {}
        self._bybit_stale_after_seconds = max(4, int(os.getenv("QUENBOT_BYBIT_STALE_AFTER_SECONDS", "8")))
        self._bybit_fast_poll_seconds = max(2, int(os.getenv("QUENBOT_BYBIT_FAST_POLL_SECONDS", "6")))
        self._bybit_rest_symbol_cooldown_seconds = max(2, int(os.getenv("QUENBOT_BYBIT_REST_SYMBOL_COOLDOWN_SECONDS", "4")))
        self.trade_ingest_queue: asyncio.Queue[Dict[str, Any]] = asyncio.Queue(maxsize=max(5000, Config.SCOUT_TRADE_QUEUE_SIZE))
        self._trade_ingest_workers: List[asyncio.Task] = []
        self._trade_queue_drops = 0
        # Per-symbol flood protection: queue >80% dolu ve aynı sembol var → skip
        self._trade_queue_symbol_last_at: Dict[str, float] = {}
        self._trade_queue_symbol_cooldown = float(os.getenv("QUENBOT_TRADE_QUEUE_SYMBOL_COOLDOWN_MS", "200")) / 1000.0
        self._last_trade_timeout_log_at: Optional[datetime] = None

    async def initialize(self):
        """Initialize the scout agent."""
        logger.info("Initializing Scout Agent...")
        self.http_session = aiohttp.ClientSession()
        await self._refresh_watchlist()
        for symbol in self._active_watchlist:
            self.price_cache[symbol] = 0.0
            self.last_rest_fetch[symbol] = datetime.utcnow()

        self._ws_client_enabled = str(os.getenv('QUENBOT_USE_WS_CLIENT_BRIDGE', '0')).lower() in {'1', 'true', 'yes', 'on'}
        self._ensure_trade_ingest_workers()
        if self._ws_client_enabled and self._active_watchlist:
            loop = asyncio.get_running_loop()
            streams = "/".join([f"{symbol.lower()}@trade" for symbol in self._active_watchlist])
            url = f"wss://stream.binance.com:443/stream?streams={streams}"
            bridge = WebSocketClientBridge(
                url,
                loop=loop,
                name="binance-spot-bridge",
                parser=lambda message: {"raw": message, "market_type": "spot"},
            )
            self._ws_client_bridges = [bridge]
            for item in self._ws_client_bridges:
                item.start()
            logger.info("✓ websocket-client bridge enabled for Scout (%s symbols)", len(self._active_watchlist))

    def _ensure_trade_ingest_workers(self):
        if self._trade_ingest_workers:
            return
        worker_count = max(2, Config.SCOUT_TRADE_INGEST_WORKERS)
        for worker_index in range(worker_count):
            self._trade_ingest_workers.append(
                asyncio.create_task(self._trade_ingest_worker(worker_index), name=f"scout-trade-worker-{worker_index}")
            )

    async def _trade_ingest_worker(self, worker_index: int):
        while True:
            try:
                trade = await self.trade_ingest_queue.get()
                batch = [trade]
                while len(batch) < max(1, Config.SCOUT_TRADE_BATCH_SIZE):
                    try:
                        batch.append(self.trade_ingest_queue.get_nowait())
                    except QueueEmpty:
                        break

                for item in batch:
                    await self._persist_trade(item)

                for _ in batch:
                    self.trade_ingest_queue.task_done()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.debug(f"Scout trade ingest worker {worker_index} error: {e}")

    def _queue_trade(self, trade_data: Dict[str, Any]):
        symbol = str(trade_data.get('symbol', '') or '').upper()
        queue_size = self.trade_ingest_queue.maxsize
        # Per-symbol flood gate: queue >80% dolu ve bu sembolün son eklenmesi çok yakınsa, skip
        if queue_size > 0 and self.trade_ingest_queue.qsize() >= int(queue_size * 0.80):
            now_f = time.monotonic()
            last_sym = self._trade_queue_symbol_last_at.get(symbol, 0.0)
            if now_f - last_sym < self._trade_queue_symbol_cooldown:
                self._trade_queue_drops += 1
                return
        try:
            self.trade_ingest_queue.put_nowait(trade_data)
            if symbol:
                self._trade_queue_symbol_last_at[symbol] = time.monotonic()
        except asyncio.QueueFull:
            self._trade_queue_drops += 1
            try:
                _ = self.trade_ingest_queue.get_nowait()
                self.trade_ingest_queue.task_done()
            except QueueEmpty:
                pass
            try:
                self.trade_ingest_queue.put_nowait(trade_data)
            except asyncio.QueueFull:
                logger.debug("Scout trade queue full after drop; skipping trade")

    async def _persist_trade(self, trade_data: Dict[str, Any]):
        retry_count = int(trade_data.get('_retry_count', 0) or 0)
        try:
            inserted_id = await self.db.insert_trade(trade_data)
        except asyncio.TimeoutError:
            if retry_count < 2:
                retry_trade = dict(trade_data)
                retry_trade['_retry_count'] = retry_count + 1
                self._queue_trade(retry_trade)

            now = datetime.utcnow()
            if (self._last_trade_timeout_log_at is None or
                    (now - self._last_trade_timeout_log_at).total_seconds() >= 30):
                self._last_trade_timeout_log_at = now
                logger.warning(
                    "Scout trade insert timeout; queue_depth=%s drops=%s retry=%s",
                    self.trade_ingest_queue.qsize(),
                    self._trade_queue_drops,
                    retry_count,
                )
            return
        except Exception as e:
            logger.debug(f"Scout trade persist skipped: {e}")
            return

        if inserted_id:
            self.trade_counter += 1
            await self._publish_trade_update(trade_data)

    async def _refresh_watchlist(self):
        """Kullanıcı watchlist'ini DB'den yükle - sadece user_watchlist tablosu kullanılır"""
        try:
            user_wl = await self.db.get_user_watchlist()
            user_symbols = [str(w.get('symbol', '')).upper() for w in (user_wl or []) if w.get('symbol')]
            
            # Watchlist sınırlandırması: Kalite > Nicelik — yüksek hacimli semboller önce
            # QUENBOT_SCOUT_MAX_WATCHLIST=8 (varsayılan) → queue_depth 40k → ~1k, latency 34s → ~2s
            max_watchlist = int(os.getenv("QUENBOT_SCOUT_MAX_WATCHLIST", "8"))
            priority_symbols = ['BTCUSDT', 'ETHUSDT', 'SOLUSDT', 'BNBUSDT', 'XRPUSDT', 'DOTUSDT', 'AVAXUSDT', 'LINKUSDT']
            user_set = set(user_symbols)
            filtered = [s for s in priority_symbols if s in user_set]  # Priority semboller (high volume)
            filtered += [s for s in sorted(user_symbols) if s not in filtered]  # Geri kalanlar
            self._active_watchlist = filtered[:max_watchlist]
            
            logger.info(f"📋 Active watchlist: {len(self._active_watchlist)} coins (max {max_watchlist}) -> {self._active_watchlist}{'...' if len(self._active_watchlist) > max_watchlist else ''}")
        except Exception as e:
            logger.error(f"Watchlist refresh error: {e}")
            # Hata durumunda mevcut listeyi koru
            if not self._active_watchlist:
                self._active_watchlist = []

    def get_watchlist(self) -> List[str]:
        return self._active_watchlist if self._active_watchlist else []

    async def _publish_trade_update(self, trade_data: Dict[str, Any]):
        try:
            await self.event_bus.publish(Event(
                type=EventType.SCOUT_PRICE_UPDATE,
                source="scout",
                data={
                    "exchange": trade_data.get("exchange", "mixed"),
                    "market_type": trade_data.get("market_type", "spot"),
                    "symbol": trade_data.get("symbol", ""),
                    "price": float(trade_data.get("price", 0) or 0),
                    "quantity": float(trade_data.get("quantity", 0) or 0),
                    "side": trade_data.get("side", "buy"),
                    "timestamp": trade_data.get("timestamp").isoformat() if hasattr(trade_data.get("timestamp"), "isoformat") else trade_data.get("timestamp"),
                    "trade_id": trade_data.get("trade_id"),
                },
            ))
        except Exception as e:
            logger.debug(f"Scout trade publish skipped: {e}")

    async def _publish_orderbook_update(self, update: Dict[str, Any]):
        try:
            await self.event_bus.publish(Event(
                type=EventType.ORDER_BOOK_UPDATE,
                source="scout",
                data=update,
            ))
        except Exception as e:
            logger.debug(f"Scout order book publish skipped: {e}")

    async def start(self):
        """Start the scout agent."""
        self.running = True
        logger.info("Starting Scout Agent...")

        tasks = [
            self._monitor_binance_market('spot'),
            self._monitor_binance_market('futures'),
            self._monitor_bybit_market('spot'),
            self._monitor_bybit_market('futures'),
            self._rest_fallback_fetcher(),
            self._bybit_stale_recovery_loop(),
            self._price_movement_detector(),
            self._watchlist_refresher(),
        ]

        if self._ws_client_enabled:
            tasks.append(self._consume_ws_client_bridge())

        results = await asyncio.gather(*tasks, return_exceptions=True)
        for i, result in enumerate(results):
            if isinstance(result, Exception):
                logger.error(f"Scout sub-task {i} failed: {result}")
        # Let the orchestrator's resilient wrapper handle restart

    async def stop(self):
        """Stop the scout agent."""
        self.running = False
        logger.info("Stopping Scout Agent...")

        if self.http_session:
            await self.http_session.close()

        for exchange, connection in self.connections.items():
            try:
                if connection and not connection.closed:
                    await connection.close()
                    logger.info(f"Closed {exchange} connection")
            except Exception as e:
                logger.error(f"Error closing connection {exchange}: {e}")

        self.connections.clear()
        for bridge in self._ws_client_bridges:
            bridge.stop()
        for worker in self._trade_ingest_workers:
            worker.cancel()
        if self._trade_ingest_workers:
            await asyncio.gather(*self._trade_ingest_workers, return_exceptions=True)
        self._trade_ingest_workers.clear()

    async def _monitor_binance_market(self, market_type: str):
        """Monitor the Binance trade stream for a specific market type using 2026 API format."""
        if market_type == 'futures':
            ws_base = Config.BINANCE_FUTURES_WS_URL
            if not ws_base:
                logger.info(f"⚠ Binance futures using REST fallback (WebSocket endpoint not configured)")
                return
        else:
            ws_base = Config.BINANCE_SPOT_WS_URL
        
        while self.running:
            try:
                # Build streams for all watchlist symbols: btcusdt@trade, ethusdt@trade, etc.
                streams = "/".join(
                    [f"{symbol.lower()}@trade" for symbol in self.get_watchlist()] +
                    [f"{symbol.lower()}@bookTicker" for symbol in self.get_watchlist()]
                )
                uri = f"{ws_base}/{streams}"

                async with websockets.connect(
                    uri,
                    ping_interval=20,
                    ping_timeout=10,
                    close_timeout=4,
                    max_queue=max(1024, Config.BINANCE_WS_MAX_QUEUE),
                    compression=None,
                ) as websocket:
                    self.connections[f'binance_{market_type}'] = websocket
                    retry_key = f'binance_{market_type}'
                    self._retry_counts[retry_key] = 0
                    logger.info(f"✓ Connected to Binance {market_type.upper()} WebSocket")

                    async for message in websocket:
                        if not self.running:
                            break
                        await self._process_binance_message(message, market_type)
                        self.last_activity = datetime.utcnow()

            except (websockets.exceptions.ConnectionClosed,
                    websockets.exceptions.InvalidStatusCode,
                    ConnectionRefusedError, OSError) as e:
                retry_key = f'binance_{market_type}'
                self._retry_counts[retry_key] = self._retry_counts.get(retry_key, 0) + 1
                backoff = min(Config.get_agent_config('scout')['reconnect_delay'] * (2 ** min(self._retry_counts[retry_key] - 1, 4)), 45)
                logger.warning(f"✗ Binance {market_type} WS disconnected (#{self._retry_counts[retry_key]}): {e} — reconnecting in {backoff}s")
                await asyncio.sleep(backoff)
            except Exception as e:
                logger.error(f"✗ Binance {market_type} WebSocket error: {e}")
                await asyncio.sleep(Config.get_agent_config('scout')['reconnect_delay'])

    async def _monitor_bybit_market(self, market_type: str):
        """Monitor the Bybit trade stream using V5 API format."""
        while self.running:
            try:
                candidates = Config.get_bybit_ws_candidates(market_type)
                ws_url = candidates[self._bybit_ws_attempts.get(market_type, 0) % max(len(candidates), 1)]
                async with websockets.connect(
                    ws_url,
                    ping_interval=20,
                    ping_timeout=20,
                    close_timeout=4,
                    max_queue=max(1024, Config.BYBIT_WS_MAX_QUEUE),
                    compression=None,
                ) as websocket:
                    self.connections[f'bybit_{market_type}'] = websocket
                    retry_key = f'bybit_{market_type}'
                    self._retry_counts[retry_key] = 0
                    self._bybit_ws_attempts[market_type] = 0
                    logger.info(f"✓ Connected to Bybit {market_type.upper()} WebSocket: {ws_url}")

                    # Bybit V5 API: subscribe to publicTrade channel for each symbol
                    subscribe_msg = {
                        "op": "subscribe",
                        "args": [f"publicTrade.{symbol}" for symbol in self.get_watchlist()]
                    }
                    await websocket.send(json.dumps(subscribe_msg))
                    logger.info(f"Sent subscription to Bybit {market_type}: {len(self.get_watchlist())} symbols")

                    async for message in websocket:
                        if not self.running:
                            break
                        await self._process_bybit_message(message, market_type)
                        self.last_activity = datetime.utcnow()

            except (websockets.exceptions.ConnectionClosed,
                    websockets.exceptions.InvalidStatusCode,
                    ConnectionRefusedError, OSError) as e:
                retry_key = f'bybit_{market_type}'
                self._retry_counts[retry_key] = self._retry_counts.get(retry_key, 0) + 1
                self._bybit_ws_attempts[market_type] = self._bybit_ws_attempts.get(market_type, 0) + 1
                backoff = min(Config.get_agent_config('scout')['reconnect_delay'] * (2 ** min(self._retry_counts[retry_key] - 1, 3)), 20)
                logger.warning(f"✗ Bybit {market_type} WS disconnected (#{self._retry_counts[retry_key]}): {e} — reconnecting in {backoff}s")
                await asyncio.sleep(backoff)
            except Exception as e:
                self._bybit_ws_attempts[market_type] = self._bybit_ws_attempts.get(market_type, 0) + 1
                logger.error(f"✗ Bybit {market_type} WebSocket error: {e}")
                await asyncio.sleep(Config.get_agent_config('scout')['reconnect_delay'])

    async def _rest_fallback_fetcher(self):
        """Periodically fetch recent trades via REST API as fallback."""
        scout_config = Config.get_agent_config('scout')
        fetch_interval = scout_config.get('rest_fetch_interval_seconds', 30)
        self._bybit_rest_disabled = False  # Track Bybit REST disable state
        
        while self.running:
            try:
                await asyncio.sleep(fetch_interval)
                
                # Slow safety net for all exchanges. Fast Bybit recovery runs in a dedicated loop.
                active_symbols = self.get_watchlist()
                tasks = []
                for symbol in active_symbols:
                    tasks.append(self._fetch_binance_rest('spot', symbol))
                    tasks.append(self._fetch_binance_rest('futures', symbol))
                    if self._exchange_is_stale('bybit', 'spot'):
                        tasks.append(self._fetch_bybit_rest('spot', symbol))
                    if self._exchange_is_stale('bybit', 'futures'):
                        tasks.append(self._fetch_bybit_rest('futures', symbol))
                
                await asyncio.gather(*tasks, return_exceptions=True)
                logger.debug(f"REST API fallback fetch completed for {len(active_symbols)} symbols ({self.trade_counter} total trades)")
                
            except Exception as e:
                logger.error(f"REST fallback fetcher error: {e}")

    async def _fetch_binance_rest(self, market_type: str, symbol: str):
        """Fetch recent trades from Binance REST API."""
        try:
            endpoint = f"{Config.BINANCE_REST_API}/api/v3/trades"
            params = {"symbol": symbol, "limit": Config.get_agent_config('scout').get('rest_fetch_limit', 100)}
            
            async with self.http_session.get(endpoint, params=params, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                if resp.status == 200:
                    trades = await resp.json()
                    for trade in trades:
                        price = float(trade['price'])
                        quantity = float(trade['qty'])
                        side = 'sell' if trade['isBuyerMaker'] else 'buy'
                        timestamp = datetime.utcfromtimestamp(trade['time'] / 1000)
                        
                        trade_data = {
                            'exchange': 'binance',
                            'market_type': market_type,
                            'symbol': symbol,
                            'price': price,
                            'quantity': quantity,
                            'timestamp': timestamp,
                            'side': side,
                            'trade_id': f"binance_{market_type}_{symbol}_{trade['id']}"
                        }
                        
                        self.price_cache[symbol] = price
                        self._queue_trade(trade_data)
                    
                    logger.debug(f"Fetched {len(trades)} trades from Binance {market_type}: {symbol}")
                else:
                    logger.warning(f"Binance REST API error for {symbol} ({market_type}): status {resp.status}")
                    
        except Exception as e:
            logger.debug(f"Binance REST fetch error for {symbol} ({market_type}): {e}")

    async def _fetch_bybit_rest(self, market_type: str, symbol: str):
        """Fetch recent trades from Bybit REST API (V5 format)."""
        cooldown_key = f"{market_type}:{symbol}"
        now = datetime.utcnow()
        last_fetch = self._last_bybit_rest_fetch_at.get(cooldown_key)
        if last_fetch and (now - last_fetch).total_seconds() < self._bybit_rest_symbol_cooldown_seconds:
            return
        self._last_bybit_rest_fetch_at[cooldown_key] = now

        category = "spot" if market_type == "spot" else "linear"
        params = {
            "category": category,
            "symbol": symbol,
            "limit": Config.get_agent_config('scout').get('rest_fetch_limit', 100)
        }

        for base_url in Config.get_bybit_rest_candidates():
            try:
                endpoint = f"{base_url}/v5/market/recent-trade"
                async with self.http_session.get(endpoint, params=params, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                    if resp.status != 200:
                        logger.warning(f"Bybit REST API error for {symbol} ({market_type}) via {base_url}: status {resp.status}")
                        continue

                    data = await resp.json()
                    trades = data.get('result', {}).get('list', [])
                    for trade in trades:
                        price = float(trade['price'])
                        quantity = float(trade['size'])
                        side = 'buy' if trade['side'] == 'Buy' else 'sell'
                        timestamp = datetime.utcfromtimestamp(int(trade['time']) / 1000)

                        trade_data = {
                            'exchange': 'bybit',
                            'market_type': market_type,
                            'symbol': symbol,
                            'price': price,
                            'quantity': quantity,
                            'timestamp': timestamp,
                            'side': side,
                            'trade_id': f"bybit_{market_type}_{symbol}_{trade['execId']}"
                        }

                        self.price_cache[symbol] = price
                        self._exchange_last_trade_at[f"bybit:{market_type}"] = timestamp
                        self._queue_trade(trade_data)

                    logger.debug(f"Fetched {len(trades)} trades from Bybit {market_type}: {symbol} via {base_url}")
                    return
            except Exception as e:
                logger.debug(f"Bybit REST fetch error for {symbol} ({market_type}) via {base_url}: {e}")

    async def _process_binance_message(self, message: str, market_type: str):
        """Process Binance trade events from WebSocket."""
        try:
            payload = json.loads(message)
            
            # Binance sends trade data in 'data' field or directly
            data = payload.get('data') or payload
            
            # Check if this is a trade event
            if not isinstance(data, dict):
                return
                
            # Binance newer format sends event type differently
            event_type = data.get('e') or payload.get('e')
            if event_type == 'bookTicker':
                await self._process_binance_book_message(data, market_type)
                return
            if event_type not in ['trade', 'aggTrade']:
                return

            symbol = data.get('s')
            if not symbol or symbol not in self.get_watchlist():
                return

            try:
                price = float(data.get('p') or data.get('price', 0))
                quantity = float(data.get('q') or data.get('qty', 0))
                
                # Determine side
                if 'm' in data:
                    side = 'sell' if data['m'] else 'buy'
                else:
                    side = 'buy'  # default
                
                timestamp = datetime.utcfromtimestamp((data.get('T') or data.get('time', 0)) / 1000)

                trade_data = {
                    'exchange': 'binance',
                    'market_type': market_type,
                    'symbol': symbol,
                    'price': price,
                    'quantity': quantity,
                    'timestamp': timestamp,
                    'side': side,
                    'trade_id': f"binance_{market_type}_{symbol}_{data.get('t', int(timestamp.timestamp() * 1000))}"
                }

                self.price_cache[symbol] = price
                self._queue_trade(trade_data)
            except (ValueError, KeyError) as e:
                logger.warning(f"Failed to parse Binance trade data: {e}")

        except json.JSONDecodeError as e:
            logger.debug(f"JSON decode error in Binance message: {e}")
        except Exception as e:
            logger.error(f"Error processing Binance message: {e}")

    async def _process_binance_book_message(self, data: Dict[str, Any], market_type: str):
        try:
            symbol = str(data.get('s', '')).upper()
            if not symbol or symbol not in self.get_watchlist():
                return

            bid_price = float(data.get('b', 0) or 0)
            ask_price = float(data.get('a', 0) or 0)
            bid_size = float(data.get('B', 0) or 0)
            ask_size = float(data.get('A', 0) or 0)
            timestamp_ms = int(data.get('T') or data.get('E') or 0)
            ts = datetime.utcfromtimestamp(timestamp_ms / 1000) if timestamp_ms else datetime.utcnow()
            state_key = f"binance:{market_type}:{symbol}"
            previous = self._orderbook_state.get(state_key)

            if previous:
                mid = max((bid_price + ask_price) / 2.0, 1e-9)
                bid_delta = bid_size - previous.get('bid_size', 0.0)
                ask_delta = ask_size - previous.get('ask_size', 0.0)

                if abs(bid_delta) > 0:
                    await self._publish_orderbook_update({
                        'symbol': symbol,
                        'exchange': 'binance',
                        'market_type': market_type,
                        'bid_price': bid_price,
                        'ask_price': ask_price,
                        'bid_size': bid_size,
                        'ask_size': ask_size,
                        'order_size': abs(bid_delta),
                        'order_side': 'bid',
                        'event_type': 'add' if bid_delta > 0 else 'cancel',
                        'mid_price': mid,
                        'timestamp': ts.isoformat(),
                    })

                if abs(ask_delta) > 0:
                    await self._publish_orderbook_update({
                        'symbol': symbol,
                        'exchange': 'binance',
                        'market_type': market_type,
                        'bid_price': bid_price,
                        'ask_price': ask_price,
                        'bid_size': bid_size,
                        'ask_size': ask_size,
                        'order_size': abs(ask_delta),
                        'order_side': 'ask',
                        'event_type': 'add' if ask_delta > 0 else 'cancel',
                        'mid_price': mid,
                        'timestamp': ts.isoformat(),
                    })

            await self._publish_orderbook_update({
                'symbol': symbol,
                'exchange': 'binance',
                'market_type': market_type,
                'bid_price': bid_price,
                'ask_price': ask_price,
                'bid_size': bid_size,
                'ask_size': ask_size,
                'order_size': max(bid_size, ask_size),
                'order_side': 'both',
                'event_type': 'quote',
                'mid_price': (bid_price + ask_price) / 2.0 if bid_price and ask_price else 0.0,
                'timestamp': ts.isoformat(),
            })

            self._orderbook_state[state_key] = {
                'bid_price': bid_price,
                'ask_price': ask_price,
                'bid_size': bid_size,
                'ask_size': ask_size,
            }
        except Exception as e:
            logger.debug(f"Binance bookTicker parse error: {e}")

    async def _process_bybit_message(self, message: str, market_type: str):
        """Process Bybit trade messages from WebSocket (V5 API format)."""
        try:
            payload = json.loads(message)
            
            # Bybit V5 format: { "topic": "publicTrade.BTCUSDT", "data": [...], ...}
            topic = payload.get('topic', '')
            if not topic.startswith('publicTrade.'):
                return

            data_list = payload.get('data')
            if not data_list:
                return

            if not isinstance(data_list, list):
                data_list = [data_list]

            for trade in data_list:
                try:
                    symbol = trade.get('symbol')
                    if not symbol or symbol not in self.get_watchlist():
                        continue

                    price = float(trade.get('price', 0))
                    quantity = float(trade.get('size', trade.get('qty', 0)))
                    side = 'buy' if trade.get('side') == 'Buy' else 'sell'
                    timestamp = datetime.utcfromtimestamp(int(trade.get('time', 0)) / 1000)

                    trade_data = {
                        'exchange': 'bybit',
                        'market_type': market_type,
                        'symbol': symbol,
                        'price': price,
                        'quantity': quantity,
                        'timestamp': timestamp,
                        'side': side,
                        'trade_id': f"bybit_{market_type}_{symbol}_{trade.get('execId', int(timestamp.timestamp() * 1000))}"
                    }

                    self.price_cache[symbol] = price
                    self._exchange_last_trade_at[f"bybit:{market_type}"] = timestamp
                    self._queue_trade(trade_data)
                    logger.debug(f"Bybit {market_type} trade: {symbol} {side} @ {price} x {quantity}")

                except (ValueError, KeyError) as e:
                    logger.warning(f"Failed to parse Bybit trade data: {e}")

        except json.JSONDecodeError as e:
            logger.debug(f"JSON decode error in Bybit message: {e}")
        except Exception as e:
            logger.error(f"Error processing Bybit message: {e}")

    async def _price_movement_detector(self):
        """Detect significant price movements across multiple timeframes (5m–1d)."""
        while self.running:
            try:
                await asyncio.sleep(15)
                for market_type in Config.MARKET_TYPES:
                    for symbol in self.get_watchlist():
                        for tf_key, tf_minutes in MOVEMENT_TIMEFRAMES.items():
                            await self._check_price_movement_tf(symbol, market_type, tf_key, tf_minutes)
            except Exception as e:
                logger.error(f"Price movement detector error: {e}")
                await asyncio.sleep(30)

    async def _consume_ws_client_bridge(self):
        while self.running and self._ws_client_bridges:
            for bridge in self._ws_client_bridges:
                try:
                    payload = await asyncio.wait_for(bridge.queue.get(), timeout=2.0)
                    await self._process_binance_message(payload.get("raw", ""), payload.get("market_type", "spot"))
                except asyncio.TimeoutError:
                    continue
                except Exception as e:
                    logger.debug(f"websocket-client bridge consume error: {e}")

    async def _check_price_movement_tf(self, symbol: str, market_type: str,
                                        tf_key: str, tf_minutes: int):
        """Check for significant price movement in a specific timeframe window."""
        try:
            cutoff = datetime.utcnow() - timedelta(minutes=tf_minutes)
            trades = await self.db.get_trades_in_range(
                symbol, cutoff, datetime.utcnow(), market_type=market_type)
            if len(trades) < 10:
                return

            prices = [float(t['price']) for t in trades]
            start_price = prices[0]
            end_price = prices[-1]
            high_price = max(prices)
            low_price = min(prices)

            change_pct = (end_price - start_price) / max(start_price, 1e-8)
            abs_change = abs(change_pct)

            # Guard against bad ticks causing unrealistic percentage explosions.
            if abs_change > 100:
                logger.debug(f"Skipping outlier movement {symbol} {tf_key} ({market_type}): {change_pct:+.4f}")
                return

            if abs_change < Config.PRICE_MOVEMENT_THRESHOLD:
                return

            buy_volume = sum(float(t['quantity']) for t in trades if t['side'] == 'buy')
            sell_volume = sum(float(t['quantity']) for t in trades if t['side'] == 'sell')
            direction = 'long' if change_pct > 0 else 'short'
            total_volume = buy_volume + sell_volume
            aggressiveness = float(buy_volume / max(sell_volume, 1e-8)) if direction == 'long' \
                else float(sell_volume / max(buy_volume, 1e-8))
            aggressiveness = min(aggressiveness, 9999.99)  # cap for DB NUMERIC column

            norm_profile = self._build_movement_vector(prices)

            movement_data = {
                'exchange': trades[0].get('exchange', 'mixed'),
                'market_type': market_type,
                'symbol': symbol,
                'start_price': start_price,
                'end_price': end_price,
                'change_pct': min(abs_change, 999999.9999),
                'volume': total_volume,
                'buy_volume': buy_volume,
                'sell_volume': sell_volume,
                'direction': direction,
                'aggressiveness': aggressiveness,
                'start_time': trades[0]['timestamp'],
                'end_time': trades[-1]['timestamp'],
                't10_data': {
                    'trade_count': len(trades),
                    'average_price': sum(prices) / len(prices),
                    'total_volume': total_volume,
                    'buy_volume': float(buy_volume),
                    'sell_volume': float(sell_volume),
                    'direction': direction,
                    'price_profile': norm_profile,
                    'high': high_price,
                    'low': low_price,
                    'timeframe': tf_key,
                }
            }
            mv_id = await self.db.insert_price_movement(movement_data)
            logger.info(f"📊 Movement [{tf_key}] [{market_type}] {symbol}: "
                         f"{change_pct:+.2%}, dir={direction}, agg={aggressiveness:.2f}")

            # LLM-powered anomaly classification
            bridge = _get_llm_bridge()
            if bridge and abs_change >= 0.015:  # Only for significant moves
                try:
                    llm_analysis = await bridge.scout_analyze_anomaly(
                        symbol=symbol,
                        price_change_pct=change_pct,
                        volume_ratio=aggressiveness,
                        buy_sell_ratio=buy_volume / max(total_volume, 1e-8),
                        timeframe=tf_key,
                        recent_prices=prices[-20:],
                    )
                    if llm_analysis and llm_analysis.get("_parsed"):
                        severity = llm_analysis.get("severity", "medium")
                        event_type = llm_analysis.get("event_type", "UNKNOWN")
                        logger.info(
                            f"🤖 LLM Scout [{symbol}]: {event_type} severity={severity} "
                            f"conf={llm_analysis.get('confidence', 0):.2f}"
                        )
                except Exception as e:
                    logger.debug(f"LLM anomaly analysis skipped: {e}")

            # ≥2% move → capture historical signature (pre-move pattern)
            if abs_change >= SIGNATURE_THRESHOLD:
                await self._capture_historical_signature(
                    symbol, market_type, tf_key, tf_minutes,
                    direction, change_pct, trades, mv_id)

        except Exception as e:
            logger.error(f"Error checking price movement {symbol} {tf_key} ({market_type}): {e}")

    async def _capture_historical_signature(self, symbol: str, market_type: str,
                                              tf_key: str, tf_minutes: int,
                                              direction: str, change_pct: float,
                                              move_trades: List[Dict], movement_id: int):
        """Capture the pre-move pattern as a Historical Signature for future similarity matching."""
        try:
            # Get trades from BEFORE the move started (same duration window)
            move_start = move_trades[0]['timestamp']
            if isinstance(move_start, str):
                move_start = datetime.fromisoformat(move_start)
            pre_start = move_start - timedelta(minutes=tf_minutes)

            pre_trades = await self.db.get_trades_in_range(
                symbol, pre_start, move_start, market_type=market_type)
            if len(pre_trades) < 10:
                return

            pre_prices = np.array([float(t['price']) for t in pre_trades], dtype=np.float64)
            pre_vector = self._build_movement_vector(pre_prices.tolist())

            # Calculate pre-move indicators
            pre_indicators = {}
            try:
                ind = compute_all_indicators(pre_prices)
                pre_indicators = {
                    'rsi': float(ind['rsi']) if ind.get('rsi') is not None else None,
                    'macd_histogram': float(ind['macd']['histogram']) if ind.get('macd') else None,
                    'bollinger_pct_b': float(ind['bollinger']['pct_b']) if ind.get('bollinger') else None,
                    'atr_ratio': float(ind['atr_ratio']) if ind.get('atr_ratio') is not None else None,
                    'trend': ind.get('trend_summary', {}).get('trend', 'neutral'),
                    'trend_strength': float(ind.get('trend_summary', {}).get('strength', 0)),
                }
            except Exception:
                pass

            # Volume profile of pre-move period
            pre_buy_vol = sum(float(t['quantity']) for t in pre_trades if t['side'] == 'buy')
            pre_sell_vol = sum(float(t['quantity']) for t in pre_trades if t['side'] == 'sell')
            volume_profile = {
                'total': float(pre_buy_vol + pre_sell_vol),
                'buy_ratio': float(pre_buy_vol / max(pre_buy_vol + pre_sell_vol, 1e-8)),
                'trade_count': len(pre_trades),
            }

            # 1-day lookback context for >= 2% moves
            day_context = None
            try:
                day_start = move_start - timedelta(days=1)
                day_trades = await self.db.get_trades_in_range(
                    symbol, day_start, move_start, market_type=market_type)
                if len(day_trades) >= 10:
                    day_prices = [float(t['price']) for t in day_trades]
                    day_start_price = day_prices[0]
                    day_end_price = day_prices[-1]
                    day_change_pct = (day_end_price - day_start_price) / max(day_start_price, 1e-8)
                    day_buy = sum(float(t['quantity']) for t in day_trades if t['side'] == 'buy')
                    day_sell = sum(float(t['quantity']) for t in day_trades if t['side'] == 'sell')
                    day_context = {
                        'start_price': float(day_start_price),
                        'end_price': float(day_end_price),
                        'change_pct': float(day_change_pct),
                        'high': float(max(day_prices)),
                        'low': float(min(day_prices)),
                        'buy_ratio': float(day_buy / max(day_buy + day_sell, 1e-8)),
                        'trade_count': len(day_trades),
                    }
            except Exception:
                day_context = None

            if day_context:
                volume_profile['day_context'] = day_context

            sig_data = {
                'symbol': symbol,
                'market_type': market_type,
                'timeframe': tf_key,
                'direction': direction,
                'change_pct': float(max(min(change_pct, 9999.9999), -9999.9999)),
                'pre_move_vector': pre_vector,
                'pre_move_indicators': pre_indicators,
                'volume_profile': volume_profile,
                'movement_id': movement_id,
            }
            sig_id = await self.db.insert_historical_signature(sig_data)
            snapshot = self.vector_store.build_feature_snapshot(
                symbol=symbol,
                prices=pre_prices.tolist(),
                volumes=[float(t.get('quantity', 0) or 0) for t in pre_trades],
                timeframe=tf_key,
                market_type=market_type,
                exchange=move_trades[0].get('exchange', 'mixed'),
                metadata={
                    'direction': direction,
                    'magnitude': float(change_pct),
                    'movement_id': movement_id,
                    'signature_id': sig_id,
                    'day_context': day_context or {},
                    'buy_ratio': float(volume_profile.get('buy_ratio', 0.5)),
                },
                observed_at=move_start,
            )
            vector_id = self.vector_store.upsert_pattern_snapshot(
                snapshot,
                reference_id=f"sig:{sig_id}",
                direction=direction,
                magnitude=float(change_pct),
            )
            logger.info(f"🔖 Historical signature captured [{tf_key}] {symbol} "
                         f"{direction} {change_pct:+.2%} (sig_id={sig_id}, vector_id={vector_id})")

        except Exception as e:
            logger.error(f"Error capturing signature {symbol} {tf_key}: {e}")

    async def _check_price_movement(self, symbol: str, market_type: str):
        """Legacy single-window check — now delegates to multi-TF."""
        await self._check_price_movement_tf(symbol, market_type, '15m', 15)

    def _build_movement_vector(self, prices: List[float]) -> List[float]:
        """Build a normalized movement vector for similarity comparison."""
        if not prices:
            return []
        base = prices[0]
        return [(price - base) / max(base, 1e-8) for price in prices]

    async def health_check(self) -> Dict[str, Any]:
        """Return health status of the scout agent."""
        try:
            healthy = len(self.connections) > 0 and any(
                conn and not conn.closed for conn in self.connections.values()
            )
            active_connections = sum(1 for conn in self.connections.values() if conn and not conn.closed)
            
            return {
                "healthy": healthy,
                "active_connections": active_connections,
                "total_connections": len(self.connections),
                "last_activity": self.last_activity.isoformat() if self.last_activity else None,
                "monitored_symbols": len(self._active_watchlist),
                "watchlist": self._active_watchlist,
                "price_cache_size": len(self.price_cache),
                "trade_counter": self.trade_counter,
                "trade_queue_depth": self.trade_ingest_queue.qsize(),
                "trade_queue_drops": self._trade_queue_drops,
                "bybit_spot_candidates": Config.get_bybit_ws_candidates('spot'),
                "bybit_futures_candidates": Config.get_bybit_ws_candidates('futures'),
                "bybit_rest_candidates": Config.get_bybit_rest_candidates(),
            }
        except Exception as e:
            return {"healthy": False, "error": str(e)}

    async def add_symbol_live(self, symbol: str):
        """Sembolü anında takip listesine ekle ve verilerini çek"""
        symbol = symbol.upper()
        if symbol not in self._active_watchlist:
            self._active_watchlist.append(symbol)
            self.price_cache[symbol] = 0.0

        # DB'ye kaydet
        try:
            await self.db.add_user_watchlist(symbol, 'all', 'spot')
            await self.db.add_user_watchlist(symbol, 'all', 'futures')
        except Exception as e:
            logger.debug(f"Watchlist DB save: {e}")

        # Anında REST API'den veri çek (Binance + Bybit fallback)
        logger.info(f"🆕 Fetching data for new symbol: {symbol}")
        for market_type in ['spot', 'futures']:
            await self._fetch_binance_rest(market_type, symbol)
            await self._fetch_bybit_rest(market_type, symbol)

        logger.info(f"🆕 Live tracking started: {symbol}")

        # WebSocket bağlantılarını kapat → yeni sembolle yeniden bağlanır
        await self._reconnect_websockets()

    async def _reconnect_websockets(self):
        """WebSocket bağlantılarını kapat, otomatik yeniden bağlanır"""
        for conn_name, conn in list(self.connections.items()):
            try:
                if conn and not conn.closed:
                    await conn.close()
                    logger.info(f"🔄 Closed {conn_name} for reconnection")
            except Exception:
                pass
        self.connections.clear()

    async def _watchlist_refresher(self):
        """Watchlist'i periyodik olarak DB'den güncelle - eklenen ve kaldırılan coinleri yönet"""
        while self.running:
            try:
                await asyncio.sleep(120)  # 2 dakikada bir kontrol (event-driven watchlist trigger eklendi)
                old_watchlist = set(self._active_watchlist)
                await self._refresh_watchlist()
                new_watchlist = set(self._active_watchlist)

                # Eklenen semboller
                added_symbols = new_watchlist - old_watchlist
                # Kaldırılan semboller  
                removed_symbols = old_watchlist - new_watchlist

                if added_symbols:
                    logger.info(f"➕ Watchlist'e eklendi: {added_symbols}")
                    # Yeni semboller için anında REST fetch
                    for symbol in added_symbols:
                        self.price_cache[symbol] = 0.0
                        for market_type in ['spot', 'futures']:
                            await self._fetch_binance_rest(market_type, symbol)
                            await self._fetch_bybit_rest(market_type, symbol)

                if removed_symbols:
                    logger.info(f"➖ Watchlist'ten çıkarıldı: {removed_symbols}")
                    # Kaldırılan sembolleri cache'den temizle
                    for symbol in removed_symbols:
                        self.price_cache.pop(symbol, None)
                        self.last_rest_fetch.pop(symbol, None)

                # Değişiklik varsa WebSocket yeniden bağlan
                if added_symbols or removed_symbols:
                    await self._reconnect_websockets()

            except Exception as e:
                logger.debug(f"Watchlist refresh error: {e}")

    def _exchange_is_stale(self, exchange: str, market_type: str) -> bool:
        key = f"{exchange}:{market_type}"
        last_seen = self._exchange_last_trade_at.get(key)
        if not last_seen:
            return True
        return (datetime.utcnow() - last_seen).total_seconds() >= self._bybit_stale_after_seconds

    async def _bybit_stale_recovery_loop(self):
        while self.running:
            try:
                await asyncio.sleep(self._bybit_fast_poll_seconds)
                active_symbols = self.get_watchlist()
                if not active_symbols:
                    continue

                tasks = []
                for market_type in ['spot', 'futures']:
                    if not self._exchange_is_stale('bybit', market_type):
                        continue
                    for symbol in active_symbols:
                        tasks.append(self._fetch_bybit_rest(market_type, symbol))

                if tasks:
                    results = await asyncio.gather(*tasks, return_exceptions=True)
                    for item in results:
                        if isinstance(item, Exception):
                            logger.debug(f"Bybit stale recovery fetch error: {item}")
            except Exception as e:
                logger.debug(f"Bybit stale recovery loop error: {e}")
