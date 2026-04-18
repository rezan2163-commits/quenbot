/**
 * Tests for the exchange symbol registry. Uses Node's built-in test runner
 * (``node --test``) so no additional dev dependencies are required. Runs via:
 *
 *   npx tsx --test artifacts/api-server/__tests__/exchange_registry.test.ts
 */
import { describe, it, before, after } from "node:test";
import assert from "node:assert/strict";

import * as registry from "../src/exchange_registry";

type FetchLike = typeof fetch;
const realFetch = globalThis.fetch as FetchLike;

function mockResponse(body: unknown, ok = true): Response {
  return {
    ok,
    status: ok ? 200 : 500,
    json: async () => body,
  } as unknown as Response;
}

const BINANCE_SPOT_BODY = {
  symbols: [
    { symbol: "BTCUSDT", baseAsset: "BTC", quoteAsset: "USDT", status: "TRADING" },
    { symbol: "ETHUSDT", baseAsset: "ETH", quoteAsset: "USDT", status: "TRADING" },
    { symbol: "BTCUSDC", baseAsset: "BTC", quoteAsset: "USDC", status: "TRADING" },
    { symbol: "OLDBTC", baseAsset: "OLD", quoteAsset: "BTC", status: "BREAK" },
  ],
};

const BYBIT_SPOT_BODY = {
  result: {
    list: [
      { symbol: "BTCUSDT", baseCoin: "BTC", quoteCoin: "USDT", status: "Trading" },
      { symbol: "SOLUSDT", baseCoin: "SOL", quoteCoin: "USDT", status: "Trading" },
    ],
  },
};

describe("exchange_registry", () => {
  before(() => {
    registry.__resetCacheForTests();
    globalThis.fetch = (async (input: any) => {
      const url = typeof input === "string" ? input : input?.url ?? "";
      if (url.includes("binance.com") && url.includes("exchangeInfo") && url.includes("fapi")) {
        return mockResponse({ symbols: [] });
      }
      if (url.includes("binance.com") && url.includes("exchangeInfo")) {
        return mockResponse(BINANCE_SPOT_BODY);
      }
      if (url.includes("bybit.com") && url.includes("category=spot")) {
        return mockResponse(BYBIT_SPOT_BODY);
      }
      return mockResponse({ result: { list: [] } });
    }) as FetchLike;
  });

  after(() => {
    globalThis.fetch = realFetch;
    registry.__resetCacheForTests();
  });

  it("ranks exact base match first", async () => {
    registry.__resetCacheForTests();
    const { results } = await registry.searchSymbols({
      query: "BTC",
      exchange: "binance",
      quote: "USDT",
      market_type: "spot",
    });
    assert.equal(results[0].symbol, "BTCUSDT");
    assert.equal(results[0].base, "BTC");
  });

  it("filters by quote", async () => {
    registry.__resetCacheForTests();
    const { results } = await registry.searchSymbols({
      query: "BTC",
      exchange: "binance",
      quote: "USDC",
      market_type: "spot",
    });
    assert.ok(results.length >= 1);
    assert.ok(results.every((r) => r.quote === "USDC"));
  });

  it("pools both venues when exchange=both", async () => {
    registry.__resetCacheForTests();
    const { results } = await registry.searchSymbols({
      query: "BTC",
      exchange: "both",
      quote: "USDT",
      market_type: "spot",
    });
    const venues = new Set(results.map((r) => r.exchange));
    assert.ok(venues.has("binance"));
    assert.ok(venues.has("bybit"));
  });

  it("returns empty result for empty query without throwing", async () => {
    const { results, warnings } = await registry.searchSymbols({ query: "   " });
    assert.deepEqual(results, []);
    assert.deepEqual(warnings, []);
  });

  it("gracefully handles API failure with warning", async () => {
    registry.__resetCacheForTests();
    const prev = globalThis.fetch;
    globalThis.fetch = (async () => mockResponse({}, false)) as FetchLike;
    try {
      const { results, warnings } = await registry.searchSymbols({
        query: "BTC",
        exchange: "binance",
        quote: "USDT",
        market_type: "spot",
      });
      assert.deepEqual(results, []);
      assert.ok(warnings.some((w) => w.includes("binance")));
    } finally {
      globalThis.fetch = prev;
    }
  });

  it("excludes non-TRADING binance symbols", async () => {
    registry.__resetCacheForTests();
    const { results } = await registry.searchSymbols({
      query: "OLD",
      exchange: "binance",
      quote: "BTC",
      market_type: "spot",
    });
    assert.equal(results.length, 0);
  });
});
