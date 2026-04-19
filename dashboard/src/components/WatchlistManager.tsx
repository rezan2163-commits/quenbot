"use client";

import { useState } from "react";
import { mutate } from "swr";
import { addWatchlistCoin, removeWatchlistCoin, useLivePrices, useTopMovers, useWatchlist } from "@/lib/api";
import { Plus, TrendingUp, TrendingDown, Wifi, WifiOff, Trash2, Check, RefreshCw } from "lucide-react";

const API = process.env.NEXT_PUBLIC_API_URL || "";

export default function WatchlistManager() {
  const { data: prices, error: priceErr, isLoading: pricesLoading } = useLivePrices();
  const { data: movers } = useTopMovers();
  const { data: watchlist, mutate: mutateWatchlist } = useWatchlist();
  const [showAdd, setShowAdd] = useState(false);
  const [symbolInput, setSymbolInput] = useState("");
  const [exchange, setExchange] = useState<"binance" | "bybit" | "both">("binance");
  const [marketType, setMarketType] = useState<"spot" | "futures" | "both">("spot");
  const [adding, setAdding] = useState(false);
  const [removing, setRemoving] = useState<string | null>(null);
  const [feedback, setFeedback] = useState<{ type: "success" | "error"; msg: string } | null>(null);

  const toNumber = (value: unknown, fallback = 0) => {
    const n = Number(value);
    return Number.isFinite(n) ? n : fallback;
  };

  const connected = !priceErr && !!prices;

  const aliasMap: Record<string, string> = {
    BITCOIN: "BTCUSDT",
    ETHEREUM: "ETHUSDT",
    RIPPLE: "XRPUSDT",
    SOLANA: "SOLUSDT",
    CARDANO: "ADAUSDT",
    LITECOIN: "LTCUSDT",
    DOGECOIN: "DOGEUSDT",
    AVALANCHE: "AVAXUSDT",
    APTOS: "APTUSDT",
    POLKADOT: "DOTUSDT",
    CHAINLINK: "LINKUSDT",
    BNB: "BNBUSDT",
    BINANCE: "BNBUSDT",
  };

  const normalizeInputSymbol = (rawInput: string) => {
    const clean = rawInput.trim().toUpperCase().replace(/[^A-Z0-9]/g, "");
    if (!clean) return "";
    if (aliasMap[clean]) return aliasMap[clean];
    if (clean.endsWith("USDT")) return clean;
    return clean + "USDT";
  };

  const watchedSymbols = new Set((watchlist || []).map((w) => w.symbol.toUpperCase()));

  const moverMap = new Map(movers?.map((m) => [m.symbol, m]) || []);

  const symbolMap = new Map<string, { symbol: string; price: number; price_text: string; change_pct: number; exchange: string; market_type: string; ts: string }>();
  prices?.forEach((p) => {
    const existing = symbolMap.get(p.symbol);
    if (!existing || new Date(p.timestamp) > new Date(existing.ts)) {
      const mover = moverMap.get(p.symbol);
      symbolMap.set(p.symbol, {
        symbol: p.symbol,
        price: toNumber(p.price),
        price_text: String(p.price_text || p.price || "0"),
        change_pct: toNumber(mover?.change_pct ?? 0),
        exchange: p.exchange,
        market_type: p.market_type || "spot",
        ts: p.timestamp,
      });
    }
  });

  const tickers = Array.from(symbolMap.values()).sort((a, b) => a.symbol.localeCompare(b.symbol));
  const knownSymbols = Array.from(new Set((prices || []).map((p) => String(p.symbol || "").toUpperCase()))).filter(Boolean);

  const handleAdd = async () => {
    const raw = symbolInput.trim();
    if (!raw || adding) return;
    const normalized = normalizeInputSymbol(raw);
    if (!normalized) return;
    setAdding(true);
    setFeedback(null);
    try {
      await addWatchlistCoin(normalized, { exchange, market_type: marketType });
      await Promise.all([mutateWatchlist(), mutate(API + "/api/live/prices")]);
      setFeedback({ type: "success", msg: `${normalized} [${exchange.toUpperCase()}-${marketType.toUpperCase()}] takibe eklendi` });
      setSymbolInput("");
      setTimeout(() => setFeedback(null), 3000);
    } catch (err: any) {
      setFeedback({ type: "error", msg: err?.message || "Coin eklenemedi" });
    } finally {
      setAdding(false);
    }
  };

  const handleRemove = async (symbol: string) => {
    if (removing) return;
    setRemoving(symbol);
    setFeedback(null);
    try {
      await removeWatchlistCoin(symbol, { exchange: "all", market_type: "both" });
      await Promise.all([mutateWatchlist(), mutate(API + "/api/live/prices")]);
      setFeedback({ type: "success", msg: symbol + " takipten cikarildi" });
      setTimeout(() => setFeedback(null), 3000);
    } catch (err: any) {
      setFeedback({ type: "error", msg: err?.message || "Kaldirma basarisiz" });
    } finally {
      setRemoving(null);
    }
  };

  return (
    <div className="h-full flex flex-col bg-surface-card/30 overflow-hidden">
      {/* Header */}
      <div className="flex items-center justify-between px-3 py-2 border-b border-surface-border">
        <div className="flex items-center gap-2 min-w-0">
          <span className="text-xs font-semibold text-gray-300 tracking-wide">CANLI PIYASA</span>
          <span className="text-[10px] text-gray-500">{watchedSymbols.size} coin</span>
        </div>
        <div className="flex items-center gap-2">
          <button
            onClick={() => setShowAdd((v) => !v)}
            className={"inline-flex items-center gap-1 rounded border px-2 py-1 text-[10px] transition-colors " + (showAdd ? "bg-bull/20 border-bull text-bull" : "border-surface-border text-gray-300 hover:bg-white/[0.04]")}
            title="Coin ekle"
          >
            <Plus size={10} /> Ekle
          </button>
          <span className={"flex items-center gap-1 text-[10px] " + (connected ? "text-bull" : "text-red-400")}>
            {connected ? <Wifi size={10} /> : <WifiOff size={10} />}
          </span>
        </div>
      </div>

      {/* Add Panel */}
      {showAdd && (
        <div className="px-3 py-3 border-b border-surface-border bg-surface/50 space-y-3">
          {/* Coin Input */}
          <div className="space-y-1">
            <label className="block text-[9px] font-semibold text-gray-400 uppercase tracking-wide">Coin Adı</label>
            <div className="flex items-center gap-2">
              <input
                value={symbolInput}
                onChange={(e) => setSymbolInput(e.target.value)}
                list="watchlist-known-symbols"
                placeholder="Orn: BTC, ETH, bitcoin..."
                className="flex-1 rounded border border-surface-border bg-surface px-2 py-1.5 text-xs text-gray-200 placeholder:text-gray-600 focus:outline-none focus:border-accent/50"
                onKeyDown={(e) => { if (e.key === "Enter" && !adding) void handleAdd(); }}
              />
              <datalist id="watchlist-known-symbols">
                {knownSymbols.slice(0, 100).map((s) => <option key={s} value={s.replace("USDT", "")} />)}
              </datalist>
              <button
                onClick={() => void handleAdd()}
                disabled={adding || !symbolInput.trim()}
                className="rounded bg-bull px-3 py-1.5 text-[10px] font-medium text-white disabled:opacity-50 disabled:cursor-not-allowed flex items-center gap-1 whitespace-nowrap"
              >
                {adding ? <RefreshCw size={10} className="animate-spin" /> : <Check size={10} />}
                {adding ? "..." : "Ekle"}
              </button>
            </div>
          </div>

          {/* Exchange Selection */}
          <div className="space-y-1.5">
            <label className="block text-[9px] font-semibold text-gray-400 uppercase tracking-wide">📊 Borsa Seç</label>
            <div className="grid grid-cols-3 gap-1.5">
              {(["binance", "bybit", "both"] as const).map((ex) => (
                <button
                  key={`exchange-${ex}`}
                  onClick={() => setExchange(ex)}
                  className={`py-2 px-2 rounded text-[10px] font-semibold uppercase tracking-wider transition-all ${
                    exchange === ex
                      ? "bg-accent text-white border-2 border-accent shadow-lg shadow-accent/30"
                      : "border-2 border-surface-border text-gray-400 hover:border-accent hover:text-gray-200"
                  }`}
                >
                  {ex === "both" ? "BOTH" : ex}
                </button>
              ))}
            </div>
          </div>

          {/* Market Type Selection */}
          <div className="space-y-1.5">
            <label className="block text-[9px] font-semibold text-gray-400 uppercase tracking-wide">📈 Opsiyon Seç</label>
            <div className="grid grid-cols-3 gap-1.5">
              {(["spot", "futures", "both"] as const).map((mt) => (
                <button
                  key={`market-${mt}`}
                  onClick={() => setMarketType(mt)}
                  className={`py-2 px-2 rounded text-[10px] font-semibold uppercase tracking-wider transition-all ${
                    marketType === mt
                      ? "bg-accent text-white border-2 border-accent shadow-lg shadow-accent/30"
                      : "border-2 border-surface-border text-gray-400 hover:border-accent hover:text-gray-200"
                  }`}
                >
                  {mt === "both" ? "BOTH" : mt}
                </button>
              ))}
            </div>
          </div>

          {/* Summary */}
          <div className="rounded bg-surface p-2 border border-surface-border/50">
            <div className="text-[9px] text-gray-500 space-y-0.5">
              <div>📊 Borsa: <span className="text-accent font-semibold uppercase">{exchange}</span></div>
              <div>📈 Opsiyon: <span className="text-accent font-semibold uppercase">{marketType}</span></div>
            </div>
          </div>
        </div>
      )}

      {/* Feedback */}
      {feedback && (
        <div className={`px-3 py-2 border-b border-surface-border text-[10px] font-medium ${
          feedback.type === "success" 
            ? "text-bull bg-bull/10 border-bull/30" 
            : "text-bear bg-bear/10 border-bear/30"
        }`}>
          {feedback.msg}
        </div>
      )}

      {/* Coin List */}
      <div className="flex-1 overflow-y-auto custom-scrollbar">
        {!prices || pricesLoading || tickers.length === 0 ? (
          <div className="flex flex-col items-center justify-center h-full text-gray-500 text-xs gap-3 p-4">
            {pricesLoading && <div className="animate-pulse text-[10px]">Fiyatlar yükleniyor...</div>}
            {!pricesLoading && !prices && <p className="text-[10px]">Fiyat verisi alınamıyor</p>}
            {!pricesLoading && prices && tickers.length === 0 && (
              <>
                <p className="text-[10px]">Henuz takip edilen coin yok</p>
                <button 
                  onClick={() => setShowAdd(true)} 
                  className="px-3 py-1.5 rounded bg-accent text-white text-[10px] font-medium hover:bg-accent/90"
                >
                  İlk coini ekle
                </button>
              </>
            )}
          </div>
        ) : (
          <div className="divide-y divide-surface-border/50">
            {tickers.map((t) => {
              const up = toNumber(t.change_pct) >= 0;
              const base = t.symbol.replace("USDT", "");
              return (
                <div key={t.symbol} className="grid grid-cols-[1fr_auto_auto] gap-2 px-3 py-2 hover:bg-white/[0.02] transition-colors group">
                  <div className="min-w-0">
                    <div className="text-[11px] font-semibold text-gray-100 leading-tight">{base}</div>
                    <div className="text-[9px] text-gray-500 leading-tight">{t.exchange?.toUpperCase() || "?"} • {t.market_type?.toUpperCase?.() || "SPOT"}</div>
                  </div>
                  <div className="flex flex-col items-end gap-0.5 flex-shrink-0">
                    <span className="text-xs font-mono text-gray-200">${toNumber(t.price).toFixed(4)}</span>
                    <span className={`flex items-center gap-0.5 text-[9px] font-semibold ${up ? "text-bull" : "text-bear"}`}>
                      {up ? <TrendingUp size={10} /> : <TrendingDown size={10} />}
                      {up ? "+" : ""}{toNumber(t.change_pct).toFixed(2)}%
                    </span>
                  </div>
                  <div className="flex items-center opacity-0 group-hover:opacity-100 transition-opacity flex-shrink-0">
                    <button
                      onClick={() => void handleRemove(t.symbol)}
                      disabled={removing === t.symbol}
                      className="p-1 rounded hover:bg-red-500/20 text-red-400 hover:text-red-300 transition-colors disabled:opacity-50"
                      title="Takipten kaldır"
                    >
                      {removing === t.symbol ? <RefreshCw size={12} className="animate-spin" /> : <Trash2 size={12} />}
                    </button>
                  </div>
                </div>
              );
            })}
          </div>
        )}
      </div>

      {/* Footer */}
      <div className="px-3 py-1.5 border-t border-surface-border bg-surface/30 text-[9px] text-gray-500 text-center">
        {watchedSymbols.size} coin izleniyor • Hover yapıp kaldırın
      </div>
    </div>
  );
}
