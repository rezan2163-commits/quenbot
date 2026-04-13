"use client";

import { useState } from "react";
import { mutate } from "swr";
import { addWatchlistCoin, useDashboardSummary, useTopMovers } from "@/lib/api";
import { TrendingUp, TrendingDown, BarChart3, Target, Activity } from "lucide-react";

const API = process.env.NEXT_PUBLIC_API_URL || "";

export default function TopBar() {
  const { data: summary } = useDashboardSummary();
  const { data: movers } = useTopMovers();
  const [symbolInput, setSymbolInput] = useState("");
  const [adding, setAdding] = useState(false);
  const [message, setMessage] = useState("");
  const toNumber = (value: unknown, fallback = 0) => {
    const n = Number(value);
    return Number.isFinite(n) ? n : fallback;
  };

  const handleAddCoin = async () => {
    const raw = symbolInput.trim().toUpperCase();
    if (!raw || adding) return;
    setAdding(true);
    setMessage("");
    try {
      await addWatchlistCoin(raw, { exchange: "both", market_type: "both" });
      await Promise.all([mutate(`${API}/api/watchlist`), mutate(`${API}/api/live/prices`)]);
      const normalized = raw.endsWith("USDT") ? raw : `${raw}USDT`;
      setMessage(`${normalized} eklendi`);
      setSymbolInput("");
    } catch {
      setMessage("Eklenemedi");
    } finally {
      setAdding(false);
    }
  };

  return (
    <div className="flex items-center gap-4 px-4 py-2 border-b border-surface-border bg-surface-card/30 overflow-x-auto">
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
      <div className="flex items-center gap-3 overflow-x-auto">
        {(movers || []).slice(0, 5).map((m) => (
          <div key={m.symbol} className="flex items-center gap-1.5 whitespace-nowrap">
            <span className="text-xs text-gray-400 font-medium">
              {m.symbol.replace("USDT", "")}
            </span>
            <span
              className={`text-xs font-mono font-medium ${
                toNumber(m.change_pct) >= 0 ? "text-bull" : "text-bear"
              }`}
            >
              {toNumber(m.change_pct) >= 0 ? "+" : ""}
              {toNumber(m.change_pct).toFixed(2)}%
            </span>
          </div>
        ))}
      </div>

      {/* Always-visible coin add */}
      <div className="w-px h-6 bg-surface-border flex-shrink-0" />
      <div className="flex items-center gap-2 min-w-[260px]">
        <span className="text-[10px] text-gray-500 uppercase">Coin Ekle</span>
        <input
          value={symbolInput}
          onChange={(e) => setSymbolInput(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") void handleAddCoin();
          }}
          placeholder="BTC veya BTCUSDT"
          className="h-7 w-[130px] rounded border border-surface-border bg-surface px-2 text-xs text-gray-200 placeholder:text-gray-600 focus:outline-none"
        />
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
