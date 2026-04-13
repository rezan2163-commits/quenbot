"use client";

import { useState } from "react";
import { mutate } from "swr";
import { addWatchlistCoin, useLivePrices, useTopMovers, useWatchlist } from "@/lib/api";
import { Plus, TrendingUp, TrendingDown, Wifi, WifiOff } from "lucide-react";

const API = process.env.NEXT_PUBLIC_API_URL || "";

export default function LiveMarketFeed() {
  const { data: prices, error: priceErr } = useLivePrices();
  const { data: movers } = useTopMovers();
  const { data: watchlist } = useWatchlist();
  const [showAdd, setShowAdd] = useState(false);
  const [symbolInput, setSymbolInput] = useState("");
  const [adding, setAdding] = useState(false);
  const [feedback, setFeedback] = useState<string>("");
  const toNumber = (value: unknown, fallback = 0) => {
    const n = Number(value);
    return Number.isFinite(n) ? n : fallback;
  };

  const connected = !priceErr && !!prices;

  // Merge live prices with mover data for change_pct
  const moverMap = new Map(movers?.map((m) => [m.symbol, m]) || []);

  // Group by symbol, pick latest
  const symbolMap = new Map<string, { symbol: string; price: number; price_text: string; change_pct: number; exchange: string; market_type: string; ts: string }>();
  prices?.forEach((p) => {
    const key = `${p.symbol}:${p.exchange}:${p.market_type}`;
    const existing = symbolMap.get(key);
    if (!existing || new Date(p.timestamp) > new Date(existing.ts)) {
      const mover = moverMap.get(p.symbol);
      symbolMap.set(key, {
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

  const handleAdd = async () => {
    const raw = symbolInput.trim().toUpperCase();
    if (!raw || adding) return;
    setAdding(true);
    setFeedback("");
    try {
      await addWatchlistCoin(raw, { exchange: "both", market_type: "both" });
      await Promise.all([
        mutate(`${API}/api/watchlist`),
        mutate(`${API}/api/live/prices`),
      ]);
      const normalized = raw.endsWith("USDT") ? raw : `${raw}USDT`;
      setFeedback(`${normalized} spot+futures (binance+bybit) takibe eklendi.`);
      setSymbolInput("");
      setShowAdd(false);
    } catch {
      setFeedback("Coin eklenemedi. Sembolü kontrol et.");
    } finally {
      setAdding(false);
    }
  };

  return (
    <div className="h-full flex flex-col bg-surface-card/30 overflow-hidden">
      {/* Header */}
      <div className="flex items-center justify-between px-3 py-2 border-b border-surface-border">
        <div className="flex items-center gap-2 min-w-0">
          <span className="text-xs font-semibold text-gray-300 tracking-wide">CANLI PİYASA</span>
          <span className="text-[10px] text-gray-500">{watchlist?.length ?? 0} aktif coin</span>
        </div>
        <div className="flex items-center gap-2">
          <button
            onClick={() => setShowAdd((v) => !v)}
            className="inline-flex items-center gap-1 rounded border border-surface-border px-2 py-1 text-[10px] text-gray-300 hover:bg-white/[0.04]"
            title="Coin ekle (spot+futures, binance+bybit)"
          >
            <Plus size={10} /> Coin Ekle
          </button>
          <span className={`flex items-center gap-1 text-[10px] ${connected ? "text-bull" : "text-red-400"}`}>
            {connected ? <Wifi size={10} /> : <WifiOff size={10} />}
            {connected ? "Bağlı" : "Bağlantı Yok"}
          </span>
        </div>
      </div>

      {showAdd && (
        <div className="px-3 py-2 border-b border-surface-border bg-surface/50">
          <div className="flex items-center gap-2">
            <input
              value={symbolInput}
              onChange={(e) => setSymbolInput(e.target.value)}
              placeholder="Örn: BTC veya BTCUSDT"
              className="flex-1 rounded border border-surface-border bg-surface px-2 py-1 text-xs text-gray-200 placeholder:text-gray-600 focus:outline-none"
              onKeyDown={(e) => {
                if (e.key === "Enter") void handleAdd();
              }}
            />
            <button
              onClick={() => void handleAdd()}
              disabled={adding || !symbolInput.trim()}
              className="rounded bg-accent px-2 py-1 text-[10px] font-medium text-white disabled:opacity-50"
            >
              {adding ? "Ekleniyor" : "Ekle"}
            </button>
          </div>
          <p className="mt-1 text-[10px] text-gray-500">Eklenen coin otomatik olarak Spot + Futures ve Binance + Bybit akışında izlenir.</p>
        </div>
      )}

      {feedback && (
        <div className="px-3 py-1.5 border-b border-surface-border text-[10px] text-gray-400">{feedback}</div>
      )}

      {/* Tickers */}
      <div className="flex-1 overflow-y-auto custom-scrollbar">
        {tickers.length === 0 ? (
          <div className="flex items-center justify-center h-full text-gray-600 text-xs">Veri bekleniyor…</div>
        ) : (
          <div className="divide-y divide-surface-border/50">
            {tickers.map((t) => {
              const up = toNumber(t.change_pct) >= 0;
              return (
                <div key={t.symbol} className="flex items-center justify-between px-3 py-1.5 hover:bg-white/[0.02] transition-colors">
                  <div className="flex items-center gap-2 min-w-0">
                    <span className="text-xs font-medium text-gray-200 truncate">{t.symbol}</span>
                    <span className="text-[10px] text-gray-600 uppercase">{t.exchange}</span>
                  </div>
                  <div className="flex items-center gap-2">
                    <span className="text-xs font-mono text-gray-300">${t.price_text}</span>
                    <span className="text-[9px] px-1 py-0.5 rounded border border-surface-border text-gray-500 uppercase">{t.exchange}</span>
                    <span className="text-[9px] px-1 py-0.5 rounded border border-surface-border text-gray-500 uppercase">{t.market_type}</span>
                    <span className={`flex items-center gap-0.5 text-[10px] font-medium ${up ? "text-bull" : "text-bear"}`}>
                      {up ? <TrendingUp size={10} /> : <TrendingDown size={10} />}
                      {up ? "+" : ""}{toNumber(t.change_pct).toFixed(2)}%
                    </span>
                  </div>
                </div>
              );
            })}
          </div>
        )}
      </div>
    </div>
  );
}
