"use client";

import type { VitalSigns } from "@/lib/missionControl";

const LABELS: Array<{ key: string; title: string; unit?: string }> = [
  { key: "scout_flow", title: "Scout Akışı", unit: "/s" },
  { key: "qwen_directives", title: "Qwen Direktifi", unit: "/sa" },
  { key: "safety_net", title: "Safety Net" },
  { key: "active_signals", title: "Aktif Sinyal" },
  { key: "ghost_pnl_24h", title: "Ghost P&L 24s", unit: "%" },
  { key: "ws_uptime", title: "WS Uptime", unit: "%" },
  { key: "warnings", title: "Uyarı" },
  { key: "ifi", title: "IFI" },
  { key: "tests_pass", title: "Testler" },
];

function Spark({ data }: { data: number[] | undefined }) {
  if (!data || data.length < 2) return <div className="h-5" />;
  const min = Math.min(...data);
  const max = Math.max(...data);
  const span = max - min || 1;
  const pts = data
    .map((v, i) => {
      const x = (i / (data.length - 1)) * 100;
      const y = 20 - ((v - min) / span) * 18 - 1;
      return `${x.toFixed(1)},${y.toFixed(1)}`;
    })
    .join(" ");
  return (
    <svg viewBox="0 0 100 20" className="h-5 w-full" preserveAspectRatio="none">
      <polyline points={pts} fill="none" stroke="currentColor" strokeWidth="1.2" className="text-accent" />
    </svg>
  );
}

function statusTone(s?: string): string {
  if (s === "crit") return "border-red-500/40 bg-red-500/5 text-red-300";
  if (s === "warn") return "border-amber-500/40 bg-amber-500/5 text-amber-300";
  if (s === "ok") return "border-emerald-500/40 bg-emerald-500/5 text-emerald-300";
  return "border-surface-border bg-surface-card text-gray-300";
}

export function VitalSignsStrip({ vitals }: { vitals: VitalSigns | undefined }) {
  return (
    <div className="grid grid-cols-3 gap-2 sm:grid-cols-5 lg:grid-cols-9">
      {LABELS.map((l) => {
        const v = vitals?.[l.key];
        const value = v?.value;
        const display = value === null || value === undefined ? "—" : typeof value === "number" ? value.toFixed(value >= 10 ? 0 : 2) : String(value);
        return (
          <div key={l.key} className={`rounded-lg border px-2 py-1.5 ${statusTone(v?.status)}`} title={v?.note || ""}>
            <div className="text-[9px] uppercase tracking-wide opacity-70">{l.title}</div>
            <div className="flex items-baseline gap-1">
              <span className="text-sm font-bold tabular-nums">{display}</span>
              {l.unit && <span className="text-[9px] opacity-60">{l.unit}</span>}
            </div>
            <Spark data={v?.trend} />
          </div>
        );
      })}
    </div>
  );
}
