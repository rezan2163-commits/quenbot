"use client";

import { useState } from "react";
import { mutate } from "swr";
import Link from "next/link";
import { addWatchlistCoin, useDashboardSummary, useTopMovers, useLivePrices } from "@/lib/api";
import { TrendingUp, TrendingDown, BarChart3, Target, Activity, Compass } from "lucide-react";

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
    <div className="flex flex-col gap-3 px-3 py-3 border-b border-surface-border bg-surface-card/30 lg:flex-row lg:flex-wrap lg:items-center lg:gap-4 lg:px-4 lg:py-2">
      {/* KPIs */}
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-4 lg:flex lg:flex-wrap lg:items-center lg:gap-4">
        <KPI
          icon={BarChart3}
          label="Simülasyon"
          value={summary ? `${summary.closed_simulations}` : "—"}
          sub={summary ? `${summary.open_simulations} paper açık` : ""}
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
          sub="pending havuzu"
        />
      </div>

      {/* Top movers divider */}
      <div className="hidden h-6 w-px bg-surface-border flex-shrink-0 lg:block" />

      <Link
        href="/mission-control"
        className="inline-flex items-center gap-1 rounded-md border border-accent/40 bg-accent/10 px-2 py-1 text-[11px] font-semibold text-accent hover:bg-accent/20"
        title="Mission Control"
      >
        <Compass size={12} />
        Mission Control
      </Link>

      {/* Top movers */}
      <div className="grid w-full grid-cols-2 gap-2 sm:grid-cols-3 lg:min-w-[320px] lg:w-auto xl:grid-cols-3">
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
      <div className="hidden h-6 w-px bg-surface-border flex-shrink-0 lg:block" />
      <div className="flex w-full flex-wrap items-center gap-2 lg:min-w-[320px] lg:w-auto lg:flex-nowrap">
        <span className="text-[10px] text-gray-500 uppercase">Coin Ekle</span>
        <input
          value={symbolInput}
          onChange={(e) => setSymbolInput(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") void handleAddCoin();
          }}
          list="known-symbols"
          placeholder="BTC, ETH veya BTCUSDT"
          className="h-9 min-w-0 flex-1 rounded border border-surface-border bg-surface px-3 text-xs text-gray-200 placeholder:text-gray-600 focus:outline-none lg:h-7 lg:w-[170px] lg:flex-none lg:px-2"
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
        {message && <span className="w-full text-[10px] text-gray-400 lg:w-auto lg:whitespace-nowrap">{message}</span>}
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
    <div className="flex min-w-0 items-center gap-2 whitespace-nowrap rounded-lg border border-surface-border bg-surface/50 px-2 py-2 lg:border-0 lg:bg-transparent lg:px-0 lg:py-0">
      <Icon size={14} className="text-gray-500 flex-shrink-0" />
      <div className="min-w-0">
        <p className="text-[10px] text-gray-500 uppercase">{label}</p>
        <p className={`truncate text-sm font-bold font-mono ${color || "text-gray-200"}`}>
          {value}
          {sub && <span className="text-[10px] text-gray-500 font-normal ml-1">{sub}</span>}
        </p>
      </div>
    </div>
  );
}
