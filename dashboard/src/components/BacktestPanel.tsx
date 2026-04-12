"use client";

import { useBacktestScores, useBacktestRecent, useEquityCurve } from "@/lib/api";
import { useEffect, useRef } from "react";
import { createChart, LineSeries, Time } from "lightweight-charts";
import { BarChart3, CheckCircle, XCircle, TrendingUp } from "lucide-react";

export default function BacktestPanel() {
  const { data: scores } = useBacktestScores();
  const { data: recent } = useBacktestRecent();
  const { data: equity } = useEquityCurve();
  const equityRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<any>(null);

  // Equity curve mini chart
  useEffect(() => {
    if (!equityRef.current || !equity?.length || equity.length < 2) return;
    if (chartRef.current) chartRef.current.remove();

    try {
      const chart = createChart(equityRef.current, {
        layout: { background: { color: "transparent" }, textColor: "#64748b", fontSize: 10 },
        grid: { vertLines: { visible: false }, horzLines: { color: "#1e293b" } },
        rightPriceScale: { borderVisible: false },
        timeScale: { borderVisible: false, visible: false },
        width: equityRef.current.clientWidth,
        height: 80,
        crosshair: { vertLine: { visible: false }, horzLine: { visible: false } },
      });

      const series = chart.addSeries(LineSeries, {
        color: "#818cf8",
        lineWidth: 2,
        priceLineVisible: false,
        lastValueVisible: false,
      });

      series.setData(
        equity.map((e, i) => ({
          time: (i + 1) as Time,
          value: Number(e.cumulative_pnl) || 0,
        }))
      );
      chart.timeScale().fitContent();
      chartRef.current = chart;

      return () => { chart.remove(); chartRef.current = null; };
    } catch {
      console.warn("Equity chart init failed");
    }
  }, [equity]);

  const overallWinRate = scores?.length
    ? (scores.reduce((a, s) => a + s.wins, 0) / Math.max(scores.reduce((a, s) => a + s.total, 0), 1)) * 100
    : 0;
  const totalTrades = scores?.reduce((a, s) => a + s.total, 0) || 0;
  const avgPnl = scores?.length
    ? scores.reduce((a, s) => a + Number(s.avg_pnl_pct) * s.total, 0) / Math.max(totalTrades, 1)
    : 0;

  return (
    <div className="flex flex-col h-full border-l border-surface-border bg-surface overflow-hidden">
      {/* Header */}
      <div className="px-4 py-2.5 border-b border-surface-border bg-surface-card/40 flex items-center gap-2">
        <BarChart3 size={14} className="text-accent" />
        <span className="text-xs font-semibold text-gray-300 uppercase tracking-wider">Backtest Sonuçları</span>
      </div>

      {/* Summary KPIs */}
      <div className="grid grid-cols-3 gap-2 px-3 py-2 border-b border-surface-border">
        <div className="text-center">
          <p className="text-[10px] text-gray-500 uppercase">Toplam</p>
          <p className="text-sm font-bold font-mono text-gray-200">{totalTrades}</p>
        </div>
        <div className="text-center">
          <p className="text-[10px] text-gray-500 uppercase">Başarı</p>
          <p className={`text-sm font-bold font-mono ${overallWinRate >= 50 ? "text-bull" : "text-bear"}`}>
            %{overallWinRate.toFixed(1)}
          </p>
        </div>
        <div className="text-center">
          <p className="text-[10px] text-gray-500 uppercase">Avg PnL</p>
          <p className={`text-sm font-bold font-mono ${avgPnl >= 0 ? "text-bull" : "text-bear"}`}>
            {scores?.length ? `%${avgPnl.toFixed(2)}` : "—"}
          </p>
        </div>
      </div>

      {/* Equity curve */}
      {equity && equity.length > 0 && (
        <div className="px-3 py-1.5 border-b border-surface-border">
          <p className="text-[10px] text-gray-500 uppercase mb-1">Equity Eğrisi</p>
          <div ref={equityRef} className="w-full" />
        </div>
      )}

      {/* Score table */}
      <div className="flex-1 overflow-y-auto px-2 py-1">
        <p className="text-[10px] text-gray-500 uppercase px-1 mb-1">Sinyal Tiplerine Göre</p>
        <div className="space-y-1">
          {(scores || []).map((s, i) => (
            <div key={i} className="flex items-center gap-2 px-2 py-1.5 rounded bg-surface-card/40 hover:bg-surface-hover transition-colors">
              <div className="flex-1 min-w-0">
                <div className="flex items-center gap-1.5">
                  <span className="text-xs font-medium text-gray-300 truncate">
                    {(s.symbol || "").replace("USDT", "")}
                  </span>
                  <span className="text-[10px] text-gray-500 truncate">{s.signal_type}</span>
                </div>
              </div>
              <span className="text-[11px] font-mono text-gray-400">{s.total}</span>
              <span className={`text-[11px] font-mono font-medium w-12 text-right ${Number(s.success_rate) >= 50 ? "text-bull" : "text-bear"}`}>
                %{Number(s.success_rate).toFixed(1)}
              </span>
            </div>
          ))}
        </div>

        {/* Recent trades */}
        <p className="text-[10px] text-gray-500 uppercase px-1 mt-3 mb-1">Son İşlemler</p>
        <div className="space-y-0.5">
          {(recent || []).slice(0, 10).map((r) => (
            <div key={r.id} className="flex items-center gap-2 px-2 py-1 rounded hover:bg-surface-card/30 transition-colors">
              {r.success ? (
                <CheckCircle size={12} className="text-bull flex-shrink-0" />
              ) : (
                <XCircle size={12} className="text-bear flex-shrink-0" />
              )}
              <div className="flex-1 min-w-0">
                <span className="text-[11px] text-gray-400 truncate block">
                  {(r.symbol || "").replace("USDT", "")} {r.side?.toUpperCase()}
                </span>
                <span className="text-[10px] text-gray-600 truncate block">
                  Hedef: %{Number(r.signal_metadata?.target_pct ?? 0) * 100 >= 2 ? (Number(r.signal_metadata?.target_pct ?? 0) * 100).toFixed(2) : "2.00"}
                  {" "}| ETA: {Number(r.signal_metadata?.estimated_duration_to_target_minutes ?? 60)} dk
                </span>
              </div>
              <span className={`text-[11px] font-mono ${Number(r.pnl_pct ?? 0) >= 0 ? "text-bull" : "text-bear"}`}>
                {Number(r.pnl_pct ?? 0) >= 0 ? "+" : ""}{Number(r.pnl_pct ?? 0).toFixed(2)}%
              </span>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
