"use client";

import { useLivePrices, useTopMovers } from "@/lib/api";
import { TrendingUp, TrendingDown, Wifi, WifiOff } from "lucide-react";

export default function LiveMarketFeed() {
  const { data: prices, error: priceErr } = useLivePrices();
  const { data: movers } = useTopMovers();
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

  return (
    <div className="h-full flex flex-col bg-surface-card/30 overflow-hidden">
      {/* Header */}
      <div className="flex items-center justify-between px-3 py-2 border-b border-surface-border">
        <span className="text-xs font-semibold text-gray-300 tracking-wide">CANLI PİYASA</span>
        <span className={`flex items-center gap-1 text-[10px] ${connected ? "text-bull" : "text-red-400"}`}>
          {connected ? <Wifi size={10} /> : <WifiOff size={10} />}
          {connected ? "Bağlı" : "Bağlantı Yok"}
        </span>
      </div>

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
