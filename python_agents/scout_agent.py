import asyncio
import json
import logging
from datetime import datetime, timedelta
from typing import Dict, List, Any, Optional

import numpy as np
import websockets
import aiohttp

from config import Config
from database import Database
from indicators import compute_all_indicators
from similarity_engine import cosine_sim

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
SIGNATURE_THRESHOLD = 0.02  # 2%

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

    async def initialize(self):
        """Initialize the scout agent."""
        logger.info("Initializing Scout Agent...")
        self.http_session = aiohttp.ClientSession()
        await self._refresh_watchlist()
        for symbol in self._active_watchlist:
            self.price_cache[symbol] = 0.0
            self.last_rest_fetch[symbol] = datetime.utcnow()

    async def _refresh_watchlist(self):
        """Kullanıcı watchlist'ini DB'den yükle, yoksa config'den al"""
        try:
            user_wl = await self.db.get_user_watchlist()
            if user_wl:
                self._active_watchlist = list(set(w['symbol'] for w in user_wl))
                logger.info(f"📋 User watchlist loaded: {len(self._active_watchlist)} symbols")
            else:
                self._active_watchlist = Config.WATCHLIST.copy()
                logger.info(f"📋 Using default watchlist: {len(self._active_watchlist)} symbols")
        except Exception:
            self._active_watchlist = Config.WATCHLIST.copy()

    def get_watchlist(self) -> List[str]:
        return self._active_watchlist if self._active_watchlist else Config.WATCHLIST

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
            self._price_movement_detector(),
            self._watchlist_refresher(),
        ]

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
                streams = "/".join([f"{symbol.lower()}@trade" for symbol in self.get_watchlist()])
                uri = f"{ws_base}/{streams}"

                async with websockets.connect(uri, ping_interval=20, ping_timeout=10) as websocket:
                    self.connections[f'binance_{market_type}'] = websocket
                    self._ws_retry_count = 0  # Reset on successful connect
                    logger.info(f"✓ Connected to Binance {market_type.upper()} WebSocket")

                    async for message in websocket:
                        if not self.running:
                            break
                        await self._process_binance_message(message, market_type)
                        self.last_activity = datetime.utcnow()

            except (websockets.exceptions.ConnectionClosed,
                    websockets.exceptions.InvalidStatusCode,
                    ConnectionRefusedError, OSError) as e:
                self._ws_retry_count = getattr(self, '_ws_retry_count', 0) + 1
                backoff = min(Config.get_agent_config('scout')['reconnect_delay'] * (2 ** min(self._ws_retry_count - 1, 5)), 120)
                logger.warning(f"✗ Binance {market_type} WS disconnected (#{self._ws_retry_count}): {e} — reconnecting in {backoff}s")
                await asyncio.sleep(backoff)
            except Exception as e:
                logger.error(f"✗ Binance {market_type} WebSocket error: {e}")
                await asyncio.sleep(Config.get_agent_config('scout')['reconnect_delay'])

    async def _monitor_bybit_market(self, market_type: str):
        """Monitor the Bybit trade stream using V5 API format with endpoint fallback/tunnel support."""
        # V5 API uses different URL patterns
        if market_type == 'spot':
            urls = [
                Config.BYBIT_SPOT_WS_TUNNEL_URL,
                Config.BYBIT_SPOT_WS_URL,
                Config.BYBIT_SPOT_WS_FALLBACK_URL,
            ]
        else:
            urls = [
                Config.BYBIT_FUTURES_WS_TUNNEL_URL,
                Config.BYBIT_FUTURES_WS_URL,
                Config.BYBIT_FUTURES_WS_FALLBACK_URL,
            ]
        ws_candidates = [u for u in urls if u]
            
        while self.running:
            try:
                websocket = None
                selected_url = None
                for ws_url in ws_candidates:
                    try:
                        websocket = await websockets.connect(ws_url, ping_interval=20, ping_timeout=10)
                        selected_url = ws_url
                        break
                    except Exception as conn_exc:
                        logger.warning(f"✗ Bybit {market_type} WS connect failed [{ws_url}]: {conn_exc}")
                        continue

                if websocket is None:
                    raise RuntimeError(f"No reachable Bybit {market_type} WS endpoint")

                self.connections[f'bybit_{market_type}'] = websocket
                self._ws_retry_count = 0  # Reset on successful connect
                logger.info(f"✓ Connected to Bybit {market_type.upper()} WebSocket: {selected_url}")

                # Bybit V5 API: subscribe to publicTrade channel for each symbol
                subscribe_msg = {
                    "op": "subscribe",
                    "args": [f"publicTrade.{symbol}" for symbol in self.get_watchlist()]
                }
                await websocket.send(json.dumps(subscribe_msg))
                logger.info(f"Sent subscription to Bybit {market_type}: {len(self.get_watchlist())} symbols")

                try:
                    async for message in websocket:
                        if not self.running:
                            break
                        await self._process_bybit_message(message, market_type)
                        self.last_activity = datetime.utcnow()
                finally:
                    try:
                        await websocket.close()
                    except Exception:
                        pass

            except (websockets.exceptions.ConnectionClosed,
                    websockets.exceptions.InvalidStatusCode,
                    ConnectionRefusedError, OSError) as e:
                self._ws_retry_count = getattr(self, '_ws_retry_count', 0) + 1
                backoff = min(Config.get_agent_config('scout')['reconnect_delay'] * (2 ** min(self._ws_retry_count - 1, 5)), 120)
                logger.warning(f"✗ Bybit {market_type} WS disconnected (#{self._ws_retry_count}): {e} — reconnecting in {backoff}s")
                await asyncio.sleep(backoff)
            except Exception as e:
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
                
                # Fetch from Binance only (WebSocket handles Bybit; REST fallback for emergency)
                active_symbols = self.get_watchlist()
                tasks = []
                for symbol in active_symbols:
                    tasks.append(self._fetch_binance_rest('spot', symbol))
                    tasks.append(self._fetch_binance_rest('futures', symbol))
                    tasks.append(self._fetch_bybit_rest('spot', symbol))
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
                        
                        await self.db.insert_trade(trade_data)
                        self.price_cache[symbol] = price
                    
                    logger.debug(f"Fetched {len(trades)} trades from Binance {market_type}: {symbol}")
                else:
                    logger.warning(f"Binance REST API error for {symbol} ({market_type}): status {resp.status}")
                    
        except Exception as e:
            logger.debug(f"Binance REST fetch error for {symbol} ({market_type}): {e}")

    async def _fetch_bybit_rest(self, market_type: str, symbol: str):
        """Fetch recent trades from Bybit REST API (V5 format) with endpoint fallback/tunnel support."""
        try:
            rest_bases = [
                Config.BYBIT_REST_TUNNEL_API,
                Config.BYBIT_REST_API,
                Config.BYBIT_REST_FALLBACK_API,
            ]
            rest_bases = [b for b in rest_bases if b]
            category = "spot" if market_type == "spot" else "linear"
            params = {
                "category": category,
                "symbol": symbol,
                "limit": Config.get_agent_config('scout').get('rest_fetch_limit', 100)
            }

            last_error = None
            for base in rest_bases:
                endpoint = f"{base}/v5/market/recent-trade"
                try:
                    async with self.http_session.get(endpoint, params=params, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                        if resp.status != 200:
                            last_error = f"status {resp.status}"
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
                                'trade_id': f"bybit_{market_type}_{symbol}_{trade.get('execId', int(timestamp.timestamp() * 1000))}"
                            }

                            await self.db.insert_trade(trade_data)
                            self.price_cache[symbol] = price

                        logger.debug(f"Fetched {len(trades)} trades from Bybit {market_type}: {symbol} via {base}")
                        return
                except Exception as e:
                    last_error = str(e)
                    continue

            if last_error:
                logger.debug(f"Bybit REST fetch error for {symbol} ({market_type}): {last_error}")
                    
        except Exception as e:
            logger.debug(f"Bybit REST fetch error for {symbol} ({market_type}): {e}")

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

                await self.db.insert_trade(trade_data)
                self.price_cache[symbol] = price
                self.trade_counter += 1
            except (ValueError, KeyError) as e:
                logger.warning(f"Failed to parse Binance trade data: {e}")

        except json.JSONDecodeError as e:
            logger.debug(f"JSON decode error in Binance message: {e}")
        except Exception as e:
            logger.error(f"Error processing Binance message: {e}")

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

                    await self.db.insert_trade(trade_data)
                    self.price_cache[symbol] = price
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
                await asyncio.sleep(60)
                for market_type in Config.MARKET_TYPES:
                    for symbol in self.get_watchlist():
                        for tf_key, tf_minutes in MOVEMENT_TIMEFRAMES.items():
                            await self._check_price_movement_tf(symbol, market_type, tf_key, tf_minutes)
            except Exception as e:
                logger.error(f"Price movement detector error: {e}")
                await asyncio.sleep(30)

    async def _check_price_movement_tf(self, symbol: str, market_type: str,
                                        tf_key: str, tf_minutes: int):
        """Check for significant price movement in a specific timeframe window."""
        try:
            cutoff = datetime.utcnow() - timedelta(minutes=tf_minutes)
            trades = await self.db.get_trades_in_range(
                symbol, cutoff, datetime.utcnow(), market_type=market_type)
            if len(trades) < 10:
                return

            prices = [float(t['price']) for t in trades if float(t.get('price') or 0) > 0]
            if len(prices) < 10:
                return
            start_price = prices[0]
            end_price = prices[-1]
            high_price = max(prices)
            low_price = min(prices)

            if start_price <= 0 or end_price <= 0:
                return

            change_pct = (end_price - start_price) / max(start_price, 1e-8)
            abs_change = abs(change_pct)

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
                'change_pct': abs_change,
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

            sig_data = {
                'symbol': symbol,
                'market_type': market_type,
                'timeframe': tf_key,
                'direction': direction,
                'change_pct': float(change_pct),
                'pre_move_vector': pre_vector,
                'pre_move_indicators': pre_indicators,
                'volume_profile': volume_profile,
                'movement_id': movement_id,
            }
            sig_id = await self.db.insert_historical_signature(sig_data)
            logger.info(f"🔖 Historical signature captured [{tf_key}] {symbol} "
                         f"{direction} {change_pct:+.2%} (sig_id={sig_id})")

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

        # Anında REST API'den veri çek (Binance only; Bybit uses WebSocket)
        logger.info(f"🆕 Fetching data for new symbol: {symbol}")
        for market_type in ['spot', 'futures']:
            await self._fetch_binance_rest(market_type, symbol)

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
        """Watchlist'i periyodik olarak DB'den güncelle"""
        while self.running:
            try:
                await asyncio.sleep(15)  # 15 saniyede bir kontrol
                old_watchlist = set(self._active_watchlist)
                await self._refresh_watchlist()
                new_watchlist = set(self._active_watchlist)

                # Yeni semboller eklendiyse
                added_symbols = new_watchlist - old_watchlist
                if added_symbols:
                    logger.info(f"🔄 New symbols detected: {added_symbols}")
                    # Yeni semboller için anında REST fetch
                    for symbol in added_symbols:
                        self.price_cache[symbol] = 0.0
                        for market_type in ['spot', 'futures']:
                            await self._fetch_binance_rest(market_type, symbol)

                    # WebSocket yeniden bağlan
                    await self._reconnect_websockets()
            except Exception as e:
                logger.debug(f"Watchlist refresh error: {e}")
