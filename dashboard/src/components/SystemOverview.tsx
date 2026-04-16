"use client";

import {
  useDashboardSummary,
  useTopMovers,
  useSignals,
  useSignatureMatches,
  useLearningStats,
} from "@/lib/api";
import { formatInQuenbotTimeZone } from "@/lib/time";
import {
  Activity,
  TrendingUp,
  TrendingDown,
  Zap,
  Shield,
  Brain,
  Crosshair,
  Fingerprint,
  ArrowUpCircle,
  ArrowDownCircle,
} from "lucide-react";

/* ── Helpers ── */

function toNumber(v: unknown, fallback = 0) {
  const n = Number(v);
  return Number.isFinite(n) ? n : fallback;
}

function pctColor(v: number) {
  if (v > 0) return "text-emerald-400";
  if (v < 0) return "text-rose-400";
  return "text-gray-400";
}

/* ── KPI Metric Card ── */

function MetricCard({
  label,
  value,
  sub,
  icon: Icon,
  accent = "text-accent",
}: {
  label: string;
  value: string | number;
  sub?: string;
  icon: React.ElementType;
  accent?: string;
}) {
  return (
    <div className="flex flex-col gap-1.5 rounded-xl border border-surface-border/60 bg-surface-card/40 px-4 py-3 backdrop-blur-sm">
      <div className="flex items-center gap-2">
        <Icon size={13} className={accent} />
        <span className="text-[10px] font-medium uppercase tracking-widest text-gray-500">{label}</span>
      </div>
      <div className="text-lg font-bold text-white tabular-nums leading-none">{value}</div>
      {sub && <span className="text-[10px] text-gray-500">{sub}</span>}
    </div>
  );
}

/* ── Top Mover Row ── */

function MoverRow({ m }: { m: any }) {
  const changePct = toNumber(m.change_pct ?? m.changePct);
  const up = changePct >= 0;
  return (
    <div className="flex items-center justify-between py-1.5 border-b border-surface-border/30 last:border-0">
      <div className="flex items-center gap-2">
        {up ? <TrendingUp size={11} className="text-emerald-400" /> : <TrendingDown size={11} className="text-rose-400" />}
        <span className="text-[11px] font-medium text-gray-200">{m.symbol}</span>
      </div>
      <div className="flex items-center gap-3">
        <span className="text-[10px] font-mono text-gray-400">${toNumber(m.current_price ?? m.price ?? m.last_price).toLocaleString(undefined, { maximumFractionDigits: 2 })}</span>
        <span className={`text-[10px] font-mono font-semibold ${pctColor(changePct)}`}>
          {up ? "+" : ""}{changePct.toFixed(2)}%
        </span>
      </div>
    </div>
  );
}

/* ── Recent Signal Row ── */

function SignalRow({ s }: { s: any }) {
  const isLong = String(s.direction || "").toLowerCase() === "long" || String(s.direction || "").toLowerCase() === "buy";
  const conf = (toNumber(s.confidence) * 100).toFixed(0);
  const targetPct = (toNumber(s.target_pct) * 100).toFixed(1);

  return (
    <div className="flex items-center justify-between py-1.5 border-b border-surface-border/30 last:border-0">
      <div className="flex items-center gap-2">
        {isLong ? <ArrowUpCircle size={11} className="text-emerald-400" /> : <ArrowDownCircle size={11} className="text-rose-400" />}
        <span className="text-[11px] font-medium text-gray-200">{s.symbol}</span>
        <span className={`text-[9px] font-semibold px-1.5 py-0.5 rounded-full ${isLong ? "text-emerald-200 bg-emerald-400/15" : "text-rose-200 bg-rose-400/15"}`}>
          {isLong ? "LONG" : "SHORT"}
        </span>
      </div>
      <div className="flex items-center gap-3">
        <span className="text-[10px] text-amber-300">%{conf}</span>
        <span className="text-[10px] text-cyan-300">Hedef %{targetPct}</span>
        <span className="text-[9px] text-gray-600">{formatInQuenbotTimeZone(s.signal_time || s.timestamp, { hour: "2-digit", minute: "2-digit" })}</span>
      </div>
    </div>
  );
}

/* ── Signature Match Row ── */

function SigMatchRow({ m }: { m: any }) {
  const pct = (toNumber(m.similarity) * 100).toFixed(1);
  return (
    <div className="flex items-center justify-between py-1.5 border-b border-surface-border/30 last:border-0">
      <div className="flex items-center gap-2">
        <Fingerprint size={11} className="text-violet-400" />
        <span className="text-[11px] font-medium text-gray-200">{m.symbol}</span>
      </div>
      <div className="flex items-center gap-2">
        <span className={`text-[10px] font-mono font-semibold ${toNumber(m.similarity) >= 0.8 ? "text-emerald-300" : "text-amber-300"}`}>
          %{pct}
        </span>
        <span className="text-[9px] text-gray-600">{m.timeframe}</span>
      </div>
    </div>
  );
}

/* ── Section Card ── */

function SectionCard({
  title,
  icon: Icon,
  accent = "text-accent",
  children,
  className = "",
}: {
  title: string;
  icon: React.ElementType;
  accent?: string;
  children: React.ReactNode;
  className?: string;
}) {
  return (
    <div className={`flex flex-col rounded-xl border border-surface-border/60 bg-surface-card/40 backdrop-blur-sm overflow-hidden ${className}`}>
      <div className="flex items-center gap-2 px-4 py-2.5 border-b border-surface-border/40">
        <Icon size={12} className={accent} />
        <span className="text-[10px] font-semibold uppercase tracking-widest text-gray-400">{title}</span>
      </div>
      <div className="flex-1 overflow-y-auto custom-scrollbar px-4 py-2">
        {children}
      </div>
    </div>
  );
}

/* ── Main Component ── */

export default function SystemOverview() {
  const { data: summary } = useDashboardSummary();
  const { data: topMovers } = useTopMovers();
  const { data: signals } = useSignals();
  const { data: sigMatches } = useSignatureMatches();
  const { data: learningStats } = useLearningStats();

  const s = summary || {
    total_trades: 0,
    active_signals: 0,
    open_simulations: 0,
    total_pnl: 0,
    win_rate: 0,
    closed_simulations: 0,
    winning_simulations: 0,
    losing_simulations: 0,
  };

  const movers = Array.isArray(topMovers) ? topMovers.slice(0, 8) : [];
  const recentSignals = (signals || []).slice(0, 6);
  const recentMatches = (sigMatches || []).slice(0, 5);
  const accuracy = toNumber(learningStats?.accuracy);

  // PnL formatla - büyük sayılar için K/M notasyonu
  const formatPnL = (pnl: number) => {
    const abs = Math.abs(pnl);
    const sign = pnl >= 0 ? "+" : "-";
    if (abs >= 1_000_000) return `${sign}$${(abs / 1_000_000).toFixed(1)}M`;
    if (abs >= 1_000) return `${sign}$${(abs / 1_000).toFixed(1)}K`;
    return `${sign}$${abs.toFixed(2)}`;
  };

  return (
    <div className="h-full flex flex-col gap-3 p-3 overflow-y-auto custom-scrollbar bg-[radial-gradient(ellipse_at_top,_rgba(59,130,246,0.06),_transparent_50%)]">
      {/* KPI Row */}
      <div className="grid grid-cols-2 gap-2 xl:grid-cols-4">
        <MetricCard
          label="İşlemler"
          value={s.total_trades.toLocaleString()}
          sub={`${s.open_simulations} açık sim.`}
          icon={Activity}
          accent="text-cyan-400"
        />
        <MetricCard
          label="Toplam PnL"
          value={formatPnL(s.total_pnl)}
          sub={`${s.winning_simulations} kazanç / ${s.losing_simulations} kayıp`}
          icon={s.total_pnl >= 0 ? TrendingUp : TrendingDown}
          accent={s.total_pnl >= 0 ? "text-emerald-400" : "text-rose-400"}
        />
        <MetricCard
          label="Kazanma Oranı"
          value={`%${(s.win_rate * 100).toFixed(1)}`}
          sub={`${s.closed_simulations} kapanan`}
          icon={Crosshair}
          accent="text-amber-400"
        />
        <MetricCard
          label="Öğrenme"
          value={accuracy > 0 ? `%${(accuracy * 100).toFixed(1)}` : "-"}
          sub={learningStats ? `${learningStats.total} pattern` : "Veri yok"}
          icon={Brain}
          accent="text-violet-400"
        />
      </div>

      {/* Data Sections */}
      <div className="grid grid-cols-1 gap-2 flex-1 min-h-0 xl:grid-cols-3">
        <SectionCard title="En Çok Hareket Edenler" icon={Zap} accent="text-amber-400">
          {movers.length === 0 ? (
            <span className="text-[10px] text-gray-600">Veri yok</span>
          ) : (
            movers.map((m: any, i: number) => <MoverRow key={m.symbol || i} m={m} />)
          )}
        </SectionCard>

        <SectionCard title="Son Sinyaller" icon={Shield} accent="text-emerald-400">
          {recentSignals.length === 0 ? (
            <span className="text-[10px] text-gray-600">Aktif sinyal yok</span>
          ) : (
            recentSignals.map((s: any) => <SignalRow key={s.id} s={s} />)
          )}
        </SectionCard>

        <SectionCard title="İmza Eşleşmeleri" icon={Fingerprint} accent="text-violet-400">
          {recentMatches.length === 0 ? (
            <span className="text-[10px] text-gray-600">Eşleşme yok</span>
          ) : (
            recentMatches.map((m: any) => <SigMatchRow key={m.id} m={m} />)
          )}
        </SectionCard>
      </div>
    </div>
  );
}
