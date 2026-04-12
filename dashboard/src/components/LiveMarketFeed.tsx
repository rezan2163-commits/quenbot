"use client";

import { useLivePrices, useTopMovers } from "@/lib/api";
import { TrendingUp, TrendingDown, Wifi, WifiOff } from "lucide-react";

export default function LiveMarketFeed() {
  const { data: prices, error: priceErr } = useLivePrices();
  const { data: movers } = useTopMovers();

  const connected = !priceErr && !!prices;

  // Merge live prices with mover data for change_pct
  const moverMap = new Map(movers?.map((m) => [m.symbol, m]) || []);

  // Group by symbol, pick latest
  const symbolMap = new Map<string, { symbol: string; price: number; change_pct: number; exchange: string; ts: string }>();
  prices?.forEach((p) => {
    const existing = symbolMap.get(p.symbol);
    if (!existing || new Date(p.timestamp) > new Date(existing.ts)) {
      const mover = moverMap.get(p.symbol);
      symbolMap.set(p.symbol, {
        symbol: p.symbol,
        price: p.price,
        change_pct: mover?.change_pct ?? 0,
        exchange: p.exchange,
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
              const up = t.change_pct >= 0;
              return (
                <div key={t.symbol} className="flex items-center justify-between px-3 py-1.5 hover:bg-white/[0.02] transition-colors">
                  <div className="flex items-center gap-2 min-w-0">
                    <span className="text-xs font-medium text-gray-200 truncate">{t.symbol}</span>
                    <span className="text-[10px] text-gray-600 uppercase">{t.exchange}</span>
                  </div>
                  <div className="flex items-center gap-2">
                    <span className="text-xs font-mono text-gray-300">${t.price < 1 ? t.price.toFixed(6) : t.price.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}</span>
                    <span className={`flex items-center gap-0.5 text-[10px] font-medium ${up ? "text-bull" : "text-bear"}`}>
                      {up ? <TrendingUp size={10} /> : <TrendingDown size={10} />}
                      {up ? "+" : ""}{t.change_pct.toFixed(2)}%
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
