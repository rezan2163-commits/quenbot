"use client";

import { useState } from "react";
import { mutate } from "swr";
import { addWatchlistCoin, useDashboardSummary, useTopMovers, useLivePrices } from "@/lib/api";
import { TrendingUp, TrendingDown, BarChart3, Target, Activity } from "lucide-react";

const API = process.env.NEXT_PUBLIC_API_URL || "";

export default function TopBar() {
  const { data: summary } = useDashboardSummary();
  const { data: movers } = useTopMovers();
  const { data: prices } = useLivePrices();
  const [symbolInput, setSymbolInput] = useState("");
  const [adding, setAdding] = useState(false);
  const [message, setMessage] = useState("");
  const toNumber = (value: unknown, fallback = 0) => {
    const n = Number(value);
    return Number.isFinite(n) ? n : fallback;
  };

  const aliasMap: Record<string, string> = {
    BITCOIN: "BTCUSDT",
    ETHEREUM: "ETHUSDT",
    SOLANA: "SOLUSDT",
    RIPPLE: "XRPUSDT",
    DOGECOIN: "DOGEUSDT",
    CARDANO: "ADAUSDT",
    LITECOIN: "LTCUSDT",
  };

  const knownSymbols = Array.from(new Set((prices || []).map((p) => String(p.symbol || "").toUpperCase()))).filter(Boolean);

  const normalizeInputSymbol = (rawInput: string) => {
    const clean = rawInput.trim().toUpperCase().replace(/[^A-Z0-9]/g, "");
    if (!clean) return "";
    if (aliasMap[clean]) return aliasMap[clean];
    if (clean.endsWith("USDT")) return clean;
    return `${clean}USDT`;
  };

  const handleAddCoin = async () => {
    const raw = symbolInput.trim();
    if (!raw || adding) return;
    const normalized = normalizeInputSymbol(raw);
    if (!normalized) return;

    setAdding(true);
    setMessage("");
    try {
      await addWatchlistCoin(normalized, { exchange: "both", market_type: "both" });
      await Promise.all([mutate(`${API}/api/watchlist`), mutate(`${API}/api/live/prices`)]);
      setMessage(`${normalized} eklendi`);
      setSymbolInput("");
    } catch (err: any) {
      setMessage(err?.message ? `Eklenemedi: ${err.message}` : "Eklenemedi");
    } finally {
      setAdding(false);
    }
  };

  const marketCards = (movers || []).slice(0, 6).map((m) => {
    const latestPrice = (prices || []).find((p) => p.symbol === m.symbol)?.price;
    return {
      symbol: m.symbol,
      change: toNumber(m.change_pct),
      price: latestPrice != null ? toNumber(latestPrice) : toNumber(m.current_price),
    };
  });

  return (
    <div className="flex flex-wrap items-center gap-4 px-4 py-2 border-b border-surface-border bg-surface-card/30">
      {/* KPIs */}
      <KPI
        icon={BarChart3}
        label="Simülasyon"
        value={summary ? `${summary.closed_simulations}` : "—"}
        sub={summary ? `${summary.open_simulations} açık` : ""}
      />
      <KPI
        icon={Target}
        label="Win Rate"
        value={summary ? `%${toNumber(summary.win_rate).toFixed(1)}` : "—"}
        color={summary && toNumber(summary.win_rate) >= 50 ? "text-bull" : "text-bear"}
      />
      <KPI
        icon={Activity}
        label="PnL"
        value={summary ? `$${toNumber(summary.total_pnl).toFixed(2)}` : "—"}
        color={summary && toNumber(summary.total_pnl) >= 0 ? "text-bull" : "text-bear"}
      />
      <KPI
        icon={TrendingUp}
        label="Sinyal"
        value={summary ? `${summary.active_signals}` : "—"}
        sub="aktif"
      />

      {/* Top movers divider */}
      <div className="w-px h-6 bg-surface-border flex-shrink-0" />

      {/* Top movers */}
      <div className="grid grid-cols-2 xl:grid-cols-3 gap-2 min-w-[320px]">
        {marketCards.map((m) => (
          <div
            key={m.symbol}
            className="rounded-md border border-surface-border bg-surface/70 px-2 py-1.5"
            title={`${m.symbol} fiyat ve değişim`}
          >
            <div className="flex items-center justify-between gap-2">
              <span className="text-[11px] text-gray-300 font-semibold tracking-wide">{m.symbol}</span>
              <span className={`text-[11px] font-mono font-semibold ${m.change >= 0 ? "text-bull" : "text-bear"}`}>
                {m.change >= 0 ? "+" : ""}{m.change.toFixed(2)}%
              </span>
            </div>
            <div className="text-[10px] text-gray-400 font-mono mt-0.5">${m.price.toLocaleString()}</div>
          </div>
        ))}
      </div>

      {/* Always-visible coin add */}
      <div className="w-px h-6 bg-surface-border flex-shrink-0" />
      <div className="flex items-center gap-2 min-w-[320px]">
        <span className="text-[10px] text-gray-500 uppercase">Coin Ekle</span>
        <input
          value={symbolInput}
          onChange={(e) => setSymbolInput(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") void handleAddCoin();
          }}
          list="known-symbols"
          placeholder="BTC, ETH veya BTCUSDT"
          className="h-7 w-[170px] rounded border border-surface-border bg-surface px-2 text-xs text-gray-200 placeholder:text-gray-600 focus:outline-none"
        />
        <datalist id="known-symbols">
          {knownSymbols.slice(0, 80).map((s) => (
            <option key={s} value={s.replace("USDT", "")} />
          ))}
          {knownSymbols.slice(0, 80).map((s) => (
            <option key={`${s}-full`} value={s} />
          ))}
        </datalist>
        <button
          onClick={() => void handleAddCoin()}
          disabled={adding || !symbolInput.trim()}
          className="h-7 rounded bg-accent px-2.5 text-[11px] font-medium text-white disabled:opacity-50"
          title="Spot + Futures, Binance + Bybit"
        >
          {adding ? "Ekleniyor" : "Ekle"}
        </button>
        {message && <span className="text-[10px] text-gray-400 whitespace-nowrap">{message}</span>}
      </div>
    </div>
  );
}

function KPI({
  icon: Icon,
  label,
  value,
  sub,
  color,
}: {
  icon: React.ElementType;
  label: string;
  value: string;
  sub?: string;
  color?: string;
}) {
  return (
    <div className="flex items-center gap-2 whitespace-nowrap">
      <Icon size={14} className="text-gray-500 flex-shrink-0" />
      <div>
        <p className="text-[10px] text-gray-500 uppercase">{label}</p>
        <p className={`text-sm font-bold font-mono ${color || "text-gray-200"}`}>
          {value}
          {sub && <span className="text-[10px] text-gray-500 font-normal ml-1">{sub}</span>}
        </p>
      </div>
    </div>
  );
}
