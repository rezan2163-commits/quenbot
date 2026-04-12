"use client";

import { useEffect, useRef, useState } from "react";
import { createChart, IChartApi, CandlestickData, Time, CandlestickSeries, createSeriesMarkers } from "lightweight-charts";
import { usePriceHistory, useSignals, useLivePrices, Signal } from "@/lib/api";

const SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT"];
const TIMEFRAMES = ["5m", "15m", "1h", "4h", "8h", "1d"] as const;

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
      time: (new Date(c.minute).getTime() / 1000) as Time,
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
          Math.floor(new Date(s.signal_time || s.timestamp).getTime() / 1000),
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
      <div className="flex items-center justify-between px-4 py-2 border-b border-surface-border bg-surface-card/40">
        <div className="flex items-center gap-3">
          {SYMBOLS.map((sym) => (
            <button
              key={sym}
              onClick={() => setActiveSymbol(sym)}
              className={`px-3 py-1.5 rounded text-xs font-medium transition-colors ${
                activeSymbol === sym
                  ? "bg-accent/20 text-accent"
                  : "text-gray-500 hover:text-gray-300 hover:bg-surface-hover"
              }`}
            >
              {sym.replace("USDT", "")}
            </button>
          ))}
          <div className="flex items-center gap-1 ml-2">
            {TIMEFRAMES.map((tf) => (
              <button
                key={tf}
                onClick={() => setActiveTf(tf)}
                className={`px-2 py-1 rounded text-[10px] font-medium transition-colors ${
                  activeTf === tf
                    ? "bg-accent/20 text-accent"
                    : "text-gray-500 hover:text-gray-300 hover:bg-surface-hover"
                }`}
              >
                {tf}
              </button>
            ))}
          </div>
        </div>

        {currentPrice && (
          <div className="text-right">
            <span className="text-lg font-bold font-mono text-gray-100">
              ${toNumber(currentPrice.price).toLocaleString()}
            </span>
          </div>
        )}
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
