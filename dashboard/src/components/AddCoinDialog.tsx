"use client";

import { useEffect, useMemo, useState } from "react";
import { Search, X, Loader2, Check, AlertTriangle } from "lucide-react";
import {
  addWatchlistCoin,
  searchSymbols,
  type SymbolSearchResult,
  type WatchlistExchange,
  type WatchlistQuote,
} from "@/lib/api";
import { cn } from "./ui/primitives";

type Market = "spot" | "futures";

interface Props {
  open: boolean;
  onClose: () => void;
  onAdded: (result: { symbol: string }) => void;
}

const QUOTES: WatchlistQuote[] = ["USDT", "USDC", "BTC", "ETH"];
const EXCHANGES: Array<{ value: WatchlistExchange; label: string; hint: string }> = [
  { value: "binance", label: "Binance", hint: "en likit" },
  { value: "bybit", label: "Bybit", hint: "türev odaklı" },
  { value: "both", label: "Her ikisi", hint: "paralel izle" },
];

export default function AddCoinDialog({ open, onClose, onAdded }: Props) {
  const [baseInput, setBaseInput] = useState("");
  const [exchange, setExchange] = useState<WatchlistExchange>("binance");
  const [markets, setMarkets] = useState<Market[]>(["spot"]);
  const [quote, setQuote] = useState<WatchlistQuote>("USDT");
  const [results, setResults] = useState<SymbolSearchResult[]>([]);
  const [searching, setSearching] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Reset state whenever the dialog is re-opened.
  useEffect(() => {
    if (!open) return;
    setBaseInput("");
    setResults([]);
    setError(null);
    setExchange("binance");
    setMarkets(["spot"]);
    setQuote("USDT");
  }, [open]);

  // Debounced symbol search.
  useEffect(() => {
    if (!open) return;
    const q = baseInput.trim();
    if (q.length < 1) {
      setResults([]);
      return;
    }
    let cancelled = false;
    setSearching(true);
    const timer = setTimeout(async () => {
      const rows = await searchSymbols(q, {
        exchange: exchange === "both" ? "both" : exchange,
        quote,
        market_type: markets.includes("spot") ? "spot" : "futures",
        limit: 12,
      });
      if (cancelled) return;
      setResults(rows);
      setSearching(false);
    }, 200);
    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, [baseInput, exchange, quote, markets, open]);

  const previewPairs = useMemo(() => {
    const base = baseInput.trim().toUpperCase().replace(/[^A-Z0-9]/g, "");
    if (!base) return [] as Array<{ venue: string; symbol: string; tradeable: boolean }>;
    const pair = base.endsWith(quote) ? base : base + quote;
    const exchanges = exchange === "both" ? (["binance", "bybit"] as const) : [exchange];
    const resultSet = new Set(results.map((r) => `${r.exchange}_${r.market_type}_${r.symbol}`));
    const out: Array<{ venue: string; symbol: string; tradeable: boolean }> = [];
    for (const ex of exchanges) {
      for (const mt of markets) {
        out.push({
          venue: `${ex} ${mt === "spot" ? "Spot" : "Vadeli"}`,
          symbol: pair,
          tradeable: resultSet.has(`${ex}_${mt}_${pair}`),
        });
      }
    }
    return out;
  }, [baseInput, exchange, markets, quote, results]);

  const canSubmit = baseInput.trim().length > 0 && markets.length > 0 && !submitting;

  function toggleMarket(m: Market) {
    setMarkets((prev) => {
      if (prev.includes(m)) {
        const next = prev.filter((x) => x !== m);
        return next.length > 0 ? next : prev; // at least one required
      }
      return [...prev, m];
    });
  }

  async function handleSubmit() {
    if (!canSubmit) return;
    setError(null);
    setSubmitting(true);
    try {
      const res = await addWatchlistCoin(baseInput.trim(), {
        exchange,
        market_types: markets,
        quote,
      });
      onAdded({ symbol: res?.symbol || baseInput.toUpperCase() });
      onClose();
    } catch (err: any) {
      setError(err?.message || "Coin eklenemedi");
    } finally {
      setSubmitting(false);
    }
  }

  if (!open) return null;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm p-4">
      <div
        className="w-full max-w-lg rounded-lg border border-surface-border bg-surface-card text-gray-100 shadow-2xl"
        role="dialog"
        aria-label="Yeni Coin Ekle"
      >
        <div className="flex items-center justify-between border-b border-surface-border px-4 py-3">
          <h2 className="text-sm font-semibold">Yeni Coin Ekle</h2>
          <button
            onClick={onClose}
            className="rounded p-1 text-gray-400 hover:bg-white/10"
            aria-label="Kapat"
          >
            <X size={14} />
          </button>
        </div>

        <div className="flex flex-col gap-4 p-4">
          {/* 1. Symbol search */}
          <div className="flex flex-col gap-1">
            <label className="text-[10px] font-medium uppercase tracking-wide text-gray-500">
              1. Coin sembolü
            </label>
            <div className="relative">
              <Search
                size={12}
                className="absolute left-2 top-1/2 -translate-y-1/2 text-gray-500"
              />
              <input
                value={baseInput}
                onChange={(e) => setBaseInput(e.target.value)}
                placeholder="Örn. BTC, ETH, SOL"
                className="w-full rounded border border-surface-border bg-surface pl-7 pr-2 py-1.5 text-xs text-gray-100 placeholder:text-gray-600 focus:border-accent/50 focus:outline-none"
                autoFocus
              />
              {searching && (
                <Loader2
                  size={12}
                  className="absolute right-2 top-1/2 -translate-y-1/2 animate-spin text-gray-500"
                />
              )}
            </div>
            {results.length > 0 && (
              <div className="mt-1 max-h-40 overflow-y-auto rounded border border-surface-border bg-surface/50 text-xs custom-scrollbar">
                {results.slice(0, 8).map((r) => (
                  <button
                    key={`${r.exchange}_${r.market_type}_${r.symbol}`}
                    type="button"
                    onClick={() => setBaseInput(r.base)}
                    className="flex w-full items-center justify-between px-2 py-1 text-left hover:bg-white/[0.04]"
                  >
                    <span className="font-mono text-gray-200">{r.symbol}</span>
                    <span className="text-[10px] text-gray-500">
                      {r.exchange} · {r.market_type}
                    </span>
                  </button>
                ))}
              </div>
            )}
          </div>

          {/* 2. Exchange */}
          <div className="flex flex-col gap-1">
            <label className="text-[10px] font-medium uppercase tracking-wide text-gray-500">
              2. Borsa
            </label>
            <div className="flex flex-wrap gap-2">
              {EXCHANGES.map((opt) => (
                <button
                  key={opt.value}
                  type="button"
                  onClick={() => setExchange(opt.value)}
                  className={cn(
                    "rounded border px-2.5 py-1 text-xs transition-colors",
                    exchange === opt.value
                      ? "border-accent bg-accent/15 text-accent"
                      : "border-surface-border text-gray-300 hover:bg-white/[0.04]",
                  )}
                >
                  {opt.label}
                  <span className="ml-1 text-[9px] text-gray-500">({opt.hint})</span>
                </button>
              ))}
            </div>
          </div>

          {/* 3. Market type */}
          <div className="flex flex-col gap-1">
            <label className="text-[10px] font-medium uppercase tracking-wide text-gray-500">
              3. Piyasa tipi (en az bir tane)
            </label>
            <div className="flex flex-wrap gap-2">
              {(["spot", "futures"] as const).map((m) => {
                const active = markets.includes(m);
                return (
                  <button
                    key={m}
                    type="button"
                    onClick={() => toggleMarket(m)}
                    className={cn(
                      "flex items-center gap-1 rounded border px-2.5 py-1 text-xs transition-colors",
                      active
                        ? "border-bull bg-bull/15 text-bull"
                        : "border-surface-border text-gray-300 hover:bg-white/[0.04]",
                    )}
                  >
                    {active && <Check size={10} />}
                    {m === "spot" ? "Spot" : "Vadeli (Futures)"}
                  </button>
                );
              })}
            </div>
          </div>

          {/* 4. Quote */}
          <div className="flex flex-col gap-1">
            <label className="text-[10px] font-medium uppercase tracking-wide text-gray-500">
              4. Karşı para (quote)
            </label>
            <div className="flex flex-wrap gap-2">
              {QUOTES.map((q) => (
                <button
                  key={q}
                  type="button"
                  onClick={() => setQuote(q)}
                  className={cn(
                    "rounded border px-2.5 py-1 text-xs transition-colors",
                    quote === q
                      ? "border-accent bg-accent/15 text-accent"
                      : "border-surface-border text-gray-300 hover:bg-white/[0.04]",
                  )}
                >
                  {q}
                  {q === "USDT" && <span className="ml-1 text-[9px] text-gray-500">(önerilen)</span>}
                </button>
              ))}
            </div>
          </div>

          {/* 5. Preview */}
          {previewPairs.length > 0 && (
            <div className="flex flex-col gap-1">
              <label className="text-[10px] font-medium uppercase tracking-wide text-gray-500">
                5. Önizleme
              </label>
              <ul className="flex flex-col gap-0.5 rounded border border-surface-border bg-surface/40 px-2 py-1.5 text-xs">
                {previewPairs.map((p, idx) => (
                  <li
                    key={`${p.venue}-${idx}`}
                    className="flex items-center justify-between gap-2 text-[11px]"
                  >
                    <span className="font-mono text-gray-200">{p.symbol}</span>
                    <span className="text-[10px] text-gray-500">@ {p.venue}</span>
                    <span
                      className={cn(
                        "text-[10px]",
                        p.tradeable ? "text-bull" : "text-gray-500",
                      )}
                    >
                      {p.tradeable ? "✓ canlı" : "— doğrulanamadı"}
                    </span>
                  </li>
                ))}
              </ul>
            </div>
          )}

          {error && (
            <div className="flex items-center gap-1 rounded border border-rose-400/30 bg-rose-400/10 px-2 py-1 text-[11px] text-rose-200">
              <AlertTriangle size={11} />
              {error}
            </div>
          )}
        </div>

        <div className="flex items-center justify-end gap-2 border-t border-surface-border bg-surface/30 px-4 py-3">
          <button
            type="button"
            onClick={onClose}
            className="rounded border border-surface-border px-3 py-1 text-xs text-gray-300 hover:bg-white/[0.04]"
          >
            İptal
          </button>
          <button
            type="button"
            onClick={handleSubmit}
            disabled={!canSubmit}
            className="flex items-center gap-1 rounded bg-bull px-3 py-1 text-xs font-medium text-white disabled:opacity-50"
          >
            {submitting ? <Loader2 size={12} className="animate-spin" /> : <Check size={12} />}
            {submitting ? "Ekleniyor…" : "Ekle"}
          </button>
        </div>
      </div>
    </div>
  );
}
