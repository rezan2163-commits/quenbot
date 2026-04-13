"use client";

import { useState } from "react";
import { mutate } from "swr";
import { addWatchlistCoin, removeWatchlistCoin, useLivePrices, useTopMovers, useWatchlist, WatchlistItem } from "@/lib/api";
import { Plus, TrendingUp, TrendingDown, Wifi, WifiOff, X, Settings, Eye, EyeOff, Trash2, Check, RefreshCw } from "lucide-react";

const API = process.env.NEXT_PUBLIC_API_URL || "";

// Default watchlist - sistemin varsayılan izlediği coinler
const DEFAULT_WATCHLIST = [
  "BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT",
  "DOGEUSDT", "ADAUSDT", "AVAXUSDT", "APTUSDT", "LINKUSDT",
  "DOTUSDT", "SUIUSDT", "OPUSDT", "ARBUSDT"
];

export default function WatchlistManager() {
  const { data: prices, error: priceErr } = useLivePrices();
  const { data: movers } = useTopMovers();
  const { data: watchlist, mutate: mutateWatchlist } = useWatchlist();
  const [showAdd, setShowAdd] = useState(false);
  const [manageMode, setManageMode] = useState(false);
  const [symbolInput, setSymbolInput] = useState("");
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
  };

  const normalizeInputSymbol = (rawInput: string) => {
    const clean = rawInput.trim().toUpperCase().replace(/[^A-Z0-9]/g, "");
    if (!clean) return "";
    if (aliasMap[clean]) return aliasMap[clean];
    if (clean.endsWith("USDT")) return clean;
    return `${clean}USDT`;
  };

  // User watchlist symbols (aktif olanlar)
  const userSymbols = new Set((watchlist || []).map((w) => w.symbol.toUpperCase()));

  // Aktif izlenen coinler = default + user
  const activeSymbols = new Set([...DEFAULT_WATCHLIST, ...Array.from(userSymbols)]);

  // Merge live prices with mover data for change_pct
  const moverMap = new Map(movers?.map((m) => [m.symbol, m]) || []);

  // Group by symbol, pick latest
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

  const tickers = Array.from(symbolMap.values())
    .filter((t) => activeSymbols.has(t.symbol))
    .sort((a, b) => a.symbol.localeCompare(b.symbol));

  const knownSymbols = Array.from(new Set((prices || []).map((p) => String(p.symbol || "").toUpperCase()))).filter(Boolean);

  const handleAdd = async () => {
    const raw = symbolInput.trim();
    if (!raw || adding) return;
    const normalized = normalizeInputSymbol(raw);
    if (!normalized) return;
    setAdding(true);
    setFeedback(null);
    try {
      await addWatchlistCoin(normalized, { exchange: "both", market_type: "both" });
      await Promise.all([
        mutateWatchlist(),
        mutate(`${API}/api/live/prices`),
      ]);
      setFeedback({ type: "success", msg: `${normalized} takibe eklendi ✓` });
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
    
    // Varsayılan coinler kaldırılamaz
    if (DEFAULT_WATCHLIST.includes(symbol)) {
      setFeedback({ type: "error", msg: `${symbol} varsayılan listede, kaldırılamaz` });
      setTimeout(() => setFeedback(null), 3000);
      return;
    }

    setRemoving(symbol);
    setFeedback(null);
    try {
      await removeWatchlistCoin(symbol, { exchange: "all", market_type: "both" });
      await Promise.all([
        mutateWatchlist(),
        mutate(`${API}/api/live/prices`),
      ]);
      setFeedback({ type: "success", msg: `${symbol} takipten çıkarıldı` });
      setTimeout(() => setFeedback(null), 3000);
    } catch (err: any) {
      setFeedback({ type: "error", msg: err?.message || "Kaldırma başarısız" });
    } finally {
      setRemoving(null);
    }
  };

  const isUserAdded = (symbol: string) => userSymbols.has(symbol) && !DEFAULT_WATCHLIST.includes(symbol);
  const isDefault = (symbol: string) => DEFAULT_WATCHLIST.includes(symbol);

  return (
    <div className="h-full flex flex-col bg-surface-card/30 overflow-hidden">
      {/* Header */}
      <div className="flex items-center justify-between px-3 py-2 border-b border-surface-border">
        <div className="flex items-center gap-2 min-w-0">
          <span className="text-xs font-semibold text-gray-300 tracking-wide">CANLI PİYASA</span>
          <span className="text-[10px] text-gray-500">{activeSymbols.size} coin izleniyor</span>
        </div>
        <div className="flex items-center gap-2">
          <button
            onClick={() => { setManageMode((v) => !v); setShowAdd(false); }}
            className={`inline-flex items-center gap-1 rounded border px-2 py-1 text-[10px] transition-colors ${
              manageMode 
                ? "bg-accent/20 border-accent text-accent" 
                : "border-surface-border text-gray-300 hover:bg-white/[0.04]"
            }`}
            title="Watchlist yönetimi"
          >
            <Settings size={10} /> Yönet
          </button>
          <button
            onClick={() => { setShowAdd((v) => !v); setManageMode(false); }}
            className={`inline-flex items-center gap-1 rounded border px-2 py-1 text-[10px] transition-colors ${
              showAdd
                ? "bg-bull/20 border-bull text-bull"
                : "border-surface-border text-gray-300 hover:bg-white/[0.04]"
            }`}
            title="Coin ekle"
          >
            <Plus size={10} /> Ekle
          </button>
          <span className={`flex items-center gap-1 text-[10px] ${connected ? "text-bull" : "text-red-400"}`}>
            {connected ? <Wifi size={10} /> : <WifiOff size={10} />}
          </span>
        </div>
      </div>

      {/* Manage Mode Info */}
      {manageMode && (
        <div className="px-3 py-2 border-b border-surface-border bg-amber-500/5">
          <div className="flex items-center justify-between">
            <div className="text-[10px] text-amber-400/80">
              <span className="font-medium">Yönetim Modu:</span> Kırmızı X ile eklediğiniz coinleri kaldırabilirsiniz.
            </div>
            <button
              onClick={() => setManageMode(false)}
              className="text-[10px] text-gray-400 hover:text-gray-200"
            >
              Kapat
            </button>
          </div>
          <div className="mt-1 flex gap-3 text-[9px] text-gray-500">
            <span className="flex items-center gap-1">
              <span className="w-2 h-2 rounded-full bg-blue-500/50"></span> Varsayılan ({DEFAULT_WATCHLIST.length})
            </span>
            <span className="flex items-center gap-1">
              <span className="w-2 h-2 rounded-full bg-green-500/50"></span> Eklediğiniz ({userSymbols.size - DEFAULT_WATCHLIST.filter(s => userSymbols.has(s)).length})
            </span>
          </div>
        </div>
      )}

      {/* Add Coin Input */}
      {showAdd && (
        <div className="px-3 py-2 border-b border-surface-border bg-surface/50">
          <div className="flex items-center gap-2">
            <input
              value={symbolInput}
              onChange={(e) => setSymbolInput(e.target.value)}
              list="watchlist-known-symbols"
              placeholder="Örn: BTC, bitcoin veya BTCUSDT"
              className="flex-1 rounded border border-surface-border bg-surface px-2 py-1 text-xs text-gray-200 placeholder:text-gray-600 focus:outline-none focus:border-accent/50"
              onKeyDown={(e) => {
                if (e.key === "Enter") void handleAdd();
              }}
            />
            <datalist id="watchlist-known-symbols">
              {knownSymbols.slice(0, 80).map((s) => (
                <option key={s} value={s.replace("USDT", "")} />
              ))}
              {knownSymbols.slice(0, 80).map((s) => (
                <option key={`${s}-full`} value={s} />
              ))}
            </datalist>
            <button
              onClick={() => void handleAdd()}
              disabled={adding || !symbolInput.trim()}
              className="rounded bg-bull px-3 py-1 text-[10px] font-medium text-white disabled:opacity-50 flex items-center gap-1"
            >
              {adding ? <RefreshCw size={10} className="animate-spin" /> : <Check size={10} />}
              {adding ? "..." : "Ekle"}
            </button>
          </div>
          <p className="mt-1 text-[10px] text-gray-500">
            Spot + Futures & Binance + Bybit akışına eklenir. Yönet modunda kaldırabilirsiniz.
          </p>
        </div>
      )}

      {/* Feedback */}
      {feedback && (
        <div className={`px-3 py-1.5 border-b border-surface-border text-[10px] ${
          feedback.type === "success" ? "text-bull bg-bull/5" : "text-bear bg-bear/5"
        }`}>
          {feedback.msg}
        </div>
      )}

      {/* Tickers */}
      <div className="flex-1 overflow-y-auto custom-scrollbar">
        {tickers.length === 0 ? (
          <div className="flex items-center justify-center h-full text-gray-600 text-xs">Veri bekleniyor…</div>
        ) : (
          <div className="divide-y divide-surface-border/50">
            {tickers.map((t) => {
              const up = toNumber(t.change_pct) >= 0;
              const base = t.symbol.replace("USDT", "");
              const isDefaultCoin = isDefault(t.symbol);
              const isUserCoin = isUserAdded(t.symbol);

              return (
                <div 
                  key={t.symbol}
                  className={`grid gap-2 px-3 py-2 hover:bg-white/[0.02] transition-colors ${
                    manageMode ? "grid-cols-[auto_minmax(0,1fr)_auto_auto]" : "grid-cols-[minmax(0,1fr)_auto]"
                  }`}
                >
                  {/* Manage mode: Type indicator */}
                  {manageMode && (
                    <div className="flex items-center">
                      {isDefaultCoin ? (
                        <span className="w-2 h-2 rounded-full bg-blue-500/50" title="Varsayılan"></span>
                      ) : (
                        <span className="w-2 h-2 rounded-full bg-green-500/50" title="Eklediğiniz"></span>
                      )}
                    </div>
                  )}

                  {/* Symbol */}
                  <div className="min-w-0">
                    <div className="text-xs font-semibold text-gray-100 tracking-wide leading-tight">{base}</div>
                    <div className="text-[10px] text-gray-500 leading-tight">{t.symbol}</div>
                  </div>

                  {/* Price & Change */}
                  <div className="flex flex-col items-end gap-0.5">
                    <span className="text-xs font-mono text-gray-200">${t.price_text}</span>
                    <span className={`flex items-center gap-0.5 text-[10px] font-medium ${up ? "text-bull" : "text-bear"}`}>
                      {up ? <TrendingUp size={10} /> : <TrendingDown size={10} />}
                      {up ? "+" : ""}{toNumber(t.change_pct).toFixed(2)}%
                    </span>
                  </div>

                  {/* Manage mode: Remove button */}
                  {manageMode && (
                    <div className="flex items-center">
                      {isUserCoin ? (
                        <button
                          onClick={() => void handleRemove(t.symbol)}
                          disabled={removing === t.symbol}
                          className="p-1 rounded hover:bg-red-500/20 text-red-400 hover:text-red-300 transition-colors disabled:opacity-50"
                          title="Takipten çıkar"
                        >
                          {removing === t.symbol ? (
                            <RefreshCw size={12} className="animate-spin" />
                          ) : (
                            <Trash2 size={12} />
                          )}
                        </button>
                      ) : (
                        <span className="w-6 text-center text-[9px] text-gray-600">—</span>
                      )}
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        )}
      </div>

      {/* Footer Stats */}
      <div className="px-3 py-1.5 border-t border-surface-border bg-surface/30 flex items-center justify-between text-[9px] text-gray-500">
        <span>Varsayılan: {DEFAULT_WATCHLIST.length}</span>
        <span>Eklenen: {Array.from(userSymbols).filter(s => !DEFAULT_WATCHLIST.includes(s)).length}</span>
        <span>Toplam: {activeSymbols.size}</span>
      </div>
    </div>
  );
}
