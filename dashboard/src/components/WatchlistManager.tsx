"use client";

import { useState } from "react";
import { mutate } from "swr";
import { addWatchlistCoin, removeWatchlistCoin, useLivePrices, useTopMovers, useWatchlist } from "@/lib/api";
import { Plus, TrendingUp, TrendingDown, Wifi, WifiOff, Trash2, RefreshCw } from "lucide-react";
import AddCoinDialog from "./AddCoinDialog";

const API = process.env.NEXT_PUBLIC_API_URL || "";

export default function WatchlistManager() {
  const { data: prices, error: priceErr } = useLivePrices();
  const { data: movers } = useTopMovers();
  const { data: watchlist, mutate: mutateWatchlist } = useWatchlist();
  const [dialogOpen, setDialogOpen] = useState(false);
  const [removing, setRemoving] = useState<string | null>(null);
  const [feedback, setFeedback] = useState<{ type: "success" | "error"; msg: string } | null>(null);

  // Keep the (unused-but-kept-for-backwards-compat) helper accessible so external
  // callers importing it continue to work. eslint-disable-next-line @typescript-eslint/no-unused-vars
  const _legacyAdd = addWatchlistCoin;

  const toNumber = (value: unknown, fallback = 0) => {
    const n = Number(value);
    return Number.isFinite(n) ? n : fallback;
  };

  const connected = !priceErr && !!prices;

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

  const handleAdded = async ({ symbol }: { symbol: string }) => {
    await Promise.all([mutateWatchlist(), mutate(API + "/api/live/prices")]);
    setFeedback({ type: "success", msg: symbol + " takibe eklendi" });
    setTimeout(() => setFeedback(null), 3000);
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
      <div className="flex items-center justify-between px-3 py-2 border-b border-surface-border">
        <div className="flex items-center gap-2 min-w-0">
          <span className="text-xs font-semibold text-gray-300 tracking-wide">CANLI PIYASA</span>
          <span className="text-[10px] text-gray-500">{watchedSymbols.size} coin</span>
        </div>
        <div className="flex items-center gap-2">
          <button
            onClick={() => setDialogOpen(true)}
            className="inline-flex items-center gap-1 rounded border border-surface-border px-2 py-1 text-[10px] text-gray-300 hover:bg-white/[0.04]"
            title="Coin ekle"
          >
            <Plus size={10} /> Ekle
          </button>
          <span className={"flex items-center gap-1 text-[10px] " + (connected ? "text-bull" : "text-red-400")}>
            {connected ? <Wifi size={10} /> : <WifiOff size={10} />}
          </span>
        </div>
      </div>

      <AddCoinDialog
        open={dialogOpen}
        onClose={() => setDialogOpen(false)}
        onAdded={handleAdded}
      />

      {feedback && (
        <div className={"px-3 py-1.5 border-b border-surface-border text-[10px] " + (feedback.type === "success" ? "text-bull bg-bull/5" : "text-bear bg-bear/5")}>
          {feedback.msg}
        </div>
      )}

      <div className="flex-1 overflow-y-auto custom-scrollbar">
        {tickers.length === 0 ? (
          <div className="flex flex-col items-center justify-center h-full text-gray-500 text-xs gap-2 p-4">
            <p>Henuz coin eklenmedi</p>
            <button onClick={() => setDialogOpen(true)} className="px-3 py-1.5 rounded bg-accent text-white text-[10px]">
              Ilk coini ekle
            </button>
          </div>
        ) : (
          <div className="divide-y divide-surface-border/50">
            {tickers.map((t) => {
              const up = toNumber(t.change_pct) >= 0;
              const base = t.symbol.replace("USDT", "");
              return (
                <div key={t.symbol} className="grid grid-cols-[minmax(0,1fr)_auto_auto] gap-2 px-3 py-2 hover:bg-white/[0.02] transition-colors group">
                  <div className="min-w-0">
                    <div className="text-xs font-semibold text-gray-100 tracking-wide leading-tight">{base}</div>
                    <div className="text-[10px] text-gray-500 leading-tight">{t.exchange} - {t.market_type}</div>
                  </div>
                  <div className="flex flex-col items-end gap-0.5">
                    <span className="text-xs font-mono text-gray-200">${t.price_text}</span>
                    <span className={"flex items-center gap-0.5 text-[10px] font-medium " + (up ? "text-bull" : "text-bear")}>
                      {up ? <TrendingUp size={10} /> : <TrendingDown size={10} />}
                      {up ? "+" : ""}{toNumber(t.change_pct).toFixed(2)}%
                    </span>
                  </div>
                  <div className="flex items-center opacity-0 group-hover:opacity-100 transition-opacity">
                    <button
                      onClick={() => void handleRemove(t.symbol)}
                      disabled={removing === t.symbol}
                      className="p-1 rounded hover:bg-red-500/20 text-red-400 hover:text-red-300 transition-colors disabled:opacity-50"
                      title="Takipten cikar"
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

      <div className="px-3 py-1.5 border-t border-surface-border bg-surface/30 text-[9px] text-gray-500 text-center">
        {watchedSymbols.size} coin izleniyor - Her coinin uzerine gel, kaldir
      </div>
    </div>
  );
}
