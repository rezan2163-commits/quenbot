"use client";

import { useDashboardSummary, useTopMovers } from "@/lib/api";
import { TrendingUp, TrendingDown, BarChart3, Target, Activity } from "lucide-react";

export default function TopBar() {
  const { data: summary } = useDashboardSummary();
  const { data: movers } = useTopMovers();
  const toNumber = (value: unknown, fallback = 0) => {
    const n = Number(value);
    return Number.isFinite(n) ? n : fallback;
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
