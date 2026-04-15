"use client";

import { useEffect, useRef, useState } from "react";
import { createChart, IChartApi, CandlestickData, Time, CandlestickSeries, createSeriesMarkers } from "lightweight-charts";
import { mutate } from "swr";
import { addWatchlistCoin, usePriceHistory, useSignals, useLivePrices, useWatchlist, Signal } from "@/lib/api";
import { toTimestampMs } from "@/lib/time";

const SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT"];
const TIMEFRAMES = ["5m", "15m", "1h", "4h", "8h", "1d"] as const;
const API = process.env.NEXT_PUBLIC_API_URL || "";

const TF_SECONDS: Record<string, number> = {
  "5m": 300,
  "15m": 900,
  "1h": 3600,
  "4h": 14400,
  "8h": 28800,
  "1d": 86400,
};

export default function ChartCanvas() {
  const [activeSymbol, setActiveSymbol] = useState(SYMBOLS[0]);
  const [activeTf, setActiveTf] = useState<(typeof TIMEFRAMES)[number]>("5m");
  const [symbolInput, setSymbolInput] = useState("");
  const [adding, setAdding] = useState(false);
  const [message, setMessage] = useState("");
  const { data: watchlist } = useWatchlist();
  const { data: candles } = usePriceHistory(activeSymbol, activeTf);
  const { data: signals } = useSignals();
  const { data: prices } = useLivePrices();

  const containerRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const seriesRef = useRef<any>(null);

  const toNumber = (value: unknown, fallback = 0) => {
    const n = Number(value);
    return Number.isFinite(n) ? n : fallback;
  };

  const toTargetPct = (signal: Signal) => {
    const raw = toNumber(signal.target_pct ?? signal.metadata?.target_pct ?? 0);
    if (raw <= 0) return 0;
    return raw > 0.5 ? raw / 100 : raw;
  };

  const alignToTf = (unixSec: number, tf: string) => {
    const step = TF_SECONDS[tf] || 300;
    return Math.floor(unixSec / step) * step;
  };

  const chartSymbols = Array.from(
    new Set([
      ...(watchlist || []).map((w) => String(w.symbol || "").toUpperCase()).filter(Boolean),
      ...SYMBOLS,
    ])
  );

  useEffect(() => {
    if (!chartSymbols.includes(activeSymbol)) {
      setActiveSymbol(chartSymbols[0] || SYMBOLS[0]);
    }
  }, [chartSymbols, activeSymbol]);

  const handleAddCoin = async () => {
    const raw = symbolInput.trim().toUpperCase();
    if (!raw || adding) return;
    setAdding(true);
    setMessage("");
    try {
      await addWatchlistCoin(raw, { exchange: "both", market_type: "both" });
      const normalized = raw.endsWith("USDT") ? raw : `${raw}USDT`;
      await Promise.all([
        mutate(`${API}/api/watchlist`),
        mutate(`${API}/api/live/prices`),
      ]);
      setActiveSymbol(normalized);
      setMessage(`${normalized} eklendi`);
      setSymbolInput("");
    } catch {
      setMessage("Eklenemedi");
    } finally {
      setAdding(false);
    }
  };

  // Initialize chart
  useEffect(() => {
    if (!containerRef.current) return;
    try {
      const chart = createChart(containerRef.current, {
      layout: {
        background: { color: "#0f172a" },
        textColor: "#94a3b8",
        fontSize: 11,
      },
      grid: {
        vertLines: { color: "#1e293b" },
        horzLines: { color: "#1e293b" },
      },
      crosshair: {
        vertLine: { color: "#475569", width: 1, style: 2 },
        horzLine: { color: "#475569", width: 1, style: 2 },
      },
      rightPriceScale: { borderColor: "#334155" },
      timeScale: {
        borderColor: "#334155",
        timeVisible: true,
        secondsVisible: false,
      },
      width: containerRef.current.clientWidth,
      height: containerRef.current.clientHeight,
    });

    const series = chart.addSeries(CandlestickSeries, {
      upColor: "#22c55e",
      downColor: "#ef4444",
      borderDownColor: "#ef4444",
      borderUpColor: "#22c55e",
      wickDownColor: "#ef4444",
      wickUpColor: "#22c55e",
    });

    chartRef.current = chart;
    seriesRef.current = series;

    const handleResize = () => {
      if (containerRef.current) {
        chart.applyOptions({
          width: containerRef.current.clientWidth,
          height: containerRef.current.clientHeight,
        });
      }
    };
    window.addEventListener("resize", handleResize);

    return () => {
      window.removeEventListener("resize", handleResize);
      chart.remove();
      chartRef.current = null;
      seriesRef.current = null;
    };
    } catch (err) {
      console.error("Chart init error:", err);
    }
  }, []);

  // Update data
  useEffect(() => {
    if (!seriesRef.current || !candles?.length) return;

    const mapped: CandlestickData<Time>[] = candles.map((c) => ({
      time: (toTimestampMs(c.minute) / 1000) as Time,
      open: toNumber(c.open),
      high: toNumber(c.high),
      low: toNumber(c.low),
      close: toNumber(c.close),
    }));

    seriesRef.current.setData(mapped);

    // Add signal markers
    const symbolSignals = (signals || []).filter((s) => {
      if (s.symbol !== activeSymbol) return false;
      if (!(s.status === "pending" || s.status === "processed" || s.status === "active")) return false;
      return toTargetPct(s) >= 0.02;
    });

    if (symbolSignals.length > 0 && chartRef.current) {
      const markers = symbolSignals.slice(0, 20).map((s) => ({
        time: alignToTf(
          Math.floor(toTimestampMs(s.signal_time || s.timestamp) / 1000),
          activeTf
        ) as Time,
        position: s.direction === "long" ? ("belowBar" as const) : ("aboveBar" as const),
        color: s.direction === "long" ? "#22c55e" : "#ef4444",
        shape: s.direction === "long" ? ("arrowUp" as const) : ("arrowDown" as const),
        text: s.direction === "long" ? "AL %2+" : "SAT %2+",
      }));

      createSeriesMarkers(seriesRef.current, markers);
    }

    chartRef.current?.timeScale().fitContent();
  }, [candles, signals, activeSymbol, activeTf]);

  // Current price from live data
  const currentPrice = prices?.find((p) => p.symbol === activeSymbol);

  // Recent signals for this symbol
  const recentSignals = (signals || [])
    .filter((s) => s.symbol === activeSymbol)
    .slice(0, 5);

  return (
    <div className="flex flex-col h-full">
      {/* Symbol tabs + price */}
      <div className="px-4 py-2 border-b border-surface-border bg-surface-card/40 space-y-2">
        <div className="flex items-center justify-between gap-3 overflow-x-auto">
          <div className="flex items-center gap-2 min-w-0">
            {chartSymbols.map((sym) => (
              <button
                key={sym}
                onClick={() => setActiveSymbol(sym)}
                className={`px-3 py-1.5 rounded text-xs font-medium transition-colors whitespace-nowrap ${
                  activeSymbol === sym
                    ? "bg-accent/20 text-accent"
                    : "text-gray-500 hover:text-gray-300 hover:bg-surface-hover"
                }`}
              >
                {sym.replace("USDT", "")}
              </button>
            ))}
          </div>

          {currentPrice && (
            <div className="text-right flex-shrink-0">
              <span className="text-lg font-bold font-mono text-gray-100">
                ${toNumber(currentPrice.price).toLocaleString()}
              </span>
            </div>
          )}
        </div>

        <div className="flex items-center justify-between gap-3 overflow-x-auto">
          <div className="flex items-center gap-1 min-w-0">
            {TIMEFRAMES.map((tf) => (
              <button
                key={tf}
                onClick={() => setActiveTf(tf)}
                className={`px-2 py-1 rounded text-[10px] font-medium transition-colors whitespace-nowrap ${
                  activeTf === tf
                    ? "bg-accent/20 text-accent"
                    : "text-gray-500 hover:text-gray-300 hover:bg-surface-hover"
                }`}
              >
                {tf}
              </button>
            ))}
          </div>

          <div className="flex items-center gap-1.5 flex-shrink-0">
            <span className="text-[10px] text-gray-400 uppercase font-semibold">Coin Ekle</span>
            <input
              value={symbolInput}
              onChange={(e) => setSymbolInput(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter") void handleAddCoin();
              }}
              placeholder="BTC veya BTCUSDT"
              className="h-7 w-[140px] rounded border border-surface-border bg-surface px-2 text-xs text-gray-200 placeholder:text-gray-600 focus:outline-none"
            />
            <button
              onClick={() => void handleAddCoin()}
              disabled={adding || !symbolInput.trim()}
              className="h-7 rounded bg-accent px-3 text-[11px] font-semibold text-white disabled:opacity-50"
            >
              {adding ? "Ekleniyor" : "Ekle"}
            </button>
            {message && <span className="text-[10px] text-gray-400 whitespace-nowrap">{message}</span>}
          </div>
        </div>
      </div>

      {/* Chart */}
      <div ref={containerRef} className="flex-1 min-h-0" />

      {/* Signal pills */}
      {recentSignals.length > 0 && (
        <div className="px-4 py-2 border-t border-surface-border bg-surface-card/30 flex gap-2 overflow-x-auto">
          {recentSignals.map((s) => (
            <SignalPill key={s.id} signal={s} />
          ))}
        </div>
      )}
    </div>
  );
}

function SignalPill({ signal }: { signal: Signal }) {
  const isLong = signal.direction === "long";
  const conf = Number(signal.confidence) || 0;
  return (
    <div
      className={`flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-medium whitespace-nowrap ${
        isLong ? "bg-bull/10 text-bull" : "bg-bear/10 text-bear"
      }`}
    >
      <span>{isLong ? "▲ AL" : "▼ SAT"}</span>
      <span className="text-gray-400">
        {(signal.symbol || "").replace("USDT", "")}
      </span>
      <span className="opacity-70">
        %{(conf * 100).toFixed(0)}
      </span>
    </div>
  );
}
