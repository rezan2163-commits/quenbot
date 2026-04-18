/**
 * Exchange symbol registry with 5-minute in-memory TTL cache.
 *
 * Surfaces Binance and Bybit instrument lists (both spot and futures) to the
 * dashboard so the add-coin dialog can autocomplete against live, tradeable
 * pairs and pre-validate selections before submission.
 *
 * All network errors degrade gracefully — a failing venue yields an empty
 * list and a soft warning instead of propagating an exception.
 */

export type Venue = "binance" | "bybit";
export type MarketType = "spot" | "futures";

export interface SymbolEntry {
  symbol: string; // full pair e.g. BTCUSDT
  base: string; // BTC
  quote: string; // USDT
  exchange: Venue;
  market_type: MarketType;
  tradeable: boolean;
  volume_24h_usd?: number;
}

interface VenueBucket {
  fetchedAt: number;
  entries: SymbolEntry[];
  warning?: string;
}

const CACHE_TTL_MS = 5 * 60 * 1000; // 5 minutes
const cache: Map<string, VenueBucket> = new Map();

function bucketKey(exchange: Venue, market_type: MarketType) {
  return `${exchange}:${market_type}`;
}

async function safeJson(url: string, timeoutMs = 4500): Promise<any | null> {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const res = await fetch(url, { signal: controller.signal });
    if (!res.ok) return null;
    return await res.json();
  } catch {
    return null;
  } finally {
    clearTimeout(timer);
  }
}

async function loadBinanceSpot(): Promise<VenueBucket> {
  const data = await safeJson("https://api.binance.com/api/v3/exchangeInfo");
  if (!data || !Array.isArray(data.symbols)) {
    return { fetchedAt: Date.now(), entries: [], warning: "binance_spot_unavailable" };
  }
  const entries: SymbolEntry[] = data.symbols
    .filter((s: any) => s?.status === "TRADING")
    .map((s: any) => ({
      symbol: String(s.symbol),
      base: String(s.baseAsset),
      quote: String(s.quoteAsset),
      exchange: "binance" as Venue,
      market_type: "spot" as MarketType,
      tradeable: true,
    }));
  return { fetchedAt: Date.now(), entries };
}

async function loadBinanceFutures(): Promise<VenueBucket> {
  const data = await safeJson("https://fapi.binance.com/fapi/v1/exchangeInfo");
  if (!data || !Array.isArray(data.symbols)) {
    return { fetchedAt: Date.now(), entries: [], warning: "binance_futures_unavailable" };
  }
  const entries: SymbolEntry[] = data.symbols
    .filter((s: any) => s?.status === "TRADING" && s?.contractType === "PERPETUAL")
    .map((s: any) => ({
      symbol: String(s.symbol),
      base: String(s.baseAsset),
      quote: String(s.quoteAsset),
      exchange: "binance" as Venue,
      market_type: "futures" as MarketType,
      tradeable: true,
    }));
  return { fetchedAt: Date.now(), entries };
}

async function loadBybit(market_type: MarketType): Promise<VenueBucket> {
  const category = market_type === "futures" ? "linear" : "spot";
  const data = await safeJson(
    `https://api.bybit.com/v5/market/instruments-info?category=${category}&limit=1000`,
  );
  const list = data?.result?.list;
  if (!Array.isArray(list)) {
    return { fetchedAt: Date.now(), entries: [], warning: `bybit_${market_type}_unavailable` };
  }
  const entries: SymbolEntry[] = list
    .filter((s: any) => String(s?.status || "").toLowerCase() === "trading")
    .map((s: any) => ({
      symbol: String(s.symbol),
      base: String(s.baseCoin),
      quote: String(s.quoteCoin),
      exchange: "bybit" as Venue,
      market_type,
      tradeable: true,
    }));
  return { fetchedAt: Date.now(), entries };
}

async function fetchBucket(exchange: Venue, market_type: MarketType): Promise<VenueBucket> {
  if (exchange === "binance" && market_type === "spot") return loadBinanceSpot();
  if (exchange === "binance" && market_type === "futures") return loadBinanceFutures();
  if (exchange === "bybit") return loadBybit(market_type);
  return { fetchedAt: Date.now(), entries: [], warning: "unsupported_venue" };
}

async function getBucket(exchange: Venue, market_type: MarketType): Promise<VenueBucket> {
  const key = bucketKey(exchange, market_type);
  const now = Date.now();
  const cached = cache.get(key);
  if (cached && now - cached.fetchedAt < CACHE_TTL_MS) return cached;
  const fresh = await fetchBucket(exchange, market_type);
  // Only cache successful fetches so transient failures don't stick.
  if (fresh.entries.length > 0 || !cached) {
    cache.set(key, fresh);
  }
  return cache.get(key) ?? fresh;
}

export interface SearchOpts {
  query: string;
  exchange?: Venue | "both" | "all";
  quote?: string;
  market_type?: MarketType;
  limit?: number;
}

export interface SearchResponse {
  results: SymbolEntry[];
  warnings: string[];
}

/**
 * Prefix-rank search over the chosen venue/market. Matches by base symbol
 * (preferred) then by full pair. Ordering:
 *   1. exact base match, then pair match
 *   2. prefix match on base
 *   3. substring match
 * Ties break by preferring USDT quote, then alphabetical.
 */
export async function searchSymbols(opts: SearchOpts): Promise<SearchResponse> {
  const query = String(opts.query || "").trim().toUpperCase();
  if (!query) return { results: [], warnings: [] };

  const limit = Math.min(Math.max(opts.limit ?? 20, 1), 100);
  const quote = (opts.quote || "USDT").toUpperCase();
  const market = (opts.market_type || "spot") as MarketType;

  const venues: Venue[] =
    opts.exchange === "bybit"
      ? ["bybit"]
      : opts.exchange === "both" || opts.exchange === "all"
      ? ["binance", "bybit"]
      : ["binance"];

  const warnings: string[] = [];
  const pooled: SymbolEntry[] = [];

  for (const venue of venues) {
    const bucket = await getBucket(venue, market);
    if (bucket.warning) warnings.push(bucket.warning);
    pooled.push(...bucket.entries);
  }

  const filtered = pooled.filter((e) => e.quote.toUpperCase() === quote);

  function score(entry: SymbolEntry): number {
    const base = entry.base.toUpperCase();
    const sym = entry.symbol.toUpperCase();
    if (base === query) return 0;
    if (sym === query) return 1;
    if (base.startsWith(query)) return 2;
    if (sym.startsWith(query)) return 3;
    if (base.includes(query) || sym.includes(query)) return 4;
    return 99;
  }

  const ranked = filtered
    .map((e) => ({ entry: e, score: score(e) }))
    .filter((x) => x.score < 99)
    .sort((a, b) => {
      if (a.score !== b.score) return a.score - b.score;
      if (a.entry.quote !== b.entry.quote) return a.entry.quote === "USDT" ? -1 : 1;
      return a.entry.base.localeCompare(b.entry.base);
    })
    .slice(0, limit)
    .map((x) => x.entry);

  return { results: ranked, warnings };
}

/** Look up the canonical entry for a venue+market+pair. Used by /api/watchlist/add. */
export async function resolveSymbol(
  symbol: string,
  exchange: Venue,
  market_type: MarketType,
): Promise<SymbolEntry | null> {
  const bucket = await getBucket(exchange, market_type);
  const needle = symbol.toUpperCase();
  return bucket.entries.find((e) => e.symbol.toUpperCase() === needle) ?? null;
}

/**
 * Test-only helper: clear the module cache. Not exported via index.ts public
 * API — access via ``import * as registry from ...``.
 */
export function __resetCacheForTests(): void {
  cache.clear();
}
