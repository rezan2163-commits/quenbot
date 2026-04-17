"use client";
/** Lightweight SVG charts — sparkline, bar, donut. No deps. */
import * as React from "react";

/* ─── Sparkline ─── */
export function Sparkline({
  data,
  width = 120,
  height = 32,
  stroke = "#818cf8",
  fill = "rgba(129,140,248,0.15)",
}: {
  data: number[];
  width?: number;
  height?: number;
  stroke?: string;
  fill?: string;
}) {
  if (!data || data.length < 2) {
    return (
      <svg width={width} height={height}>
        <line x1={0} y1={height / 2} x2={width} y2={height / 2} stroke="#334155" strokeWidth={1} />
      </svg>
    );
  }
  const min = Math.min(...data);
  const max = Math.max(...data);
  const rng = max - min || 1;
  const step = width / (data.length - 1);
  const pts = data.map((v, i) => [i * step, height - ((v - min) / rng) * height]);
  const line = pts.map((p, i) => `${i === 0 ? "M" : "L"} ${p[0].toFixed(1)} ${p[1].toFixed(1)}`).join(" ");
  const area = `${line} L ${width} ${height} L 0 ${height} Z`;
  return (
    <svg width={width} height={height}>
      <path d={area} fill={fill} stroke="none" />
      <path d={line} fill="none" stroke={stroke} strokeWidth={1.5} strokeLinejoin="round" />
    </svg>
  );
}

/* ─── Horizontal Bar ─── */
export function HBar({
  label,
  value,
  max,
  tone = "accent",
  right,
}: {
  label: string;
  value: number;
  max: number;
  tone?: "accent" | "bull" | "bear" | "warn";
  right?: React.ReactNode;
}) {
  const pct = max > 0 ? Math.max(0, Math.min(100, (value / max) * 100)) : 0;
  const bg =
    tone === "bull" ? "bg-bull" :
    tone === "bear" ? "bg-bear" :
    tone === "warn" ? "bg-warn" : "bg-accent";
  return (
    <div className="flex items-center gap-2">
      <span className="w-20 truncate text-[11px] text-gray-400">{label}</span>
      <div className="relative h-2 flex-1 overflow-hidden rounded-full bg-surface-border">
        <div className={`h-full ${bg} transition-all`} style={{ width: `${pct}%` }} />
      </div>
      <span className="w-14 text-right text-[10px] font-mono text-gray-400">{right}</span>
    </div>
  );
}

/* ─── Calibration diagonal chart ─── */
export function CalibrationChart({
  bins,
  width = 300,
  height = 180,
}: {
  bins: Array<{ p_pred_center: number; count: number; p_realized_up: number | null }>;
  width?: number;
  height?: number;
}) {
  const padL = 28, padR = 10, padT = 10, padB = 20;
  const innerW = width - padL - padR;
  const innerH = height - padT - padB;
  const x = (v: number) => padL + v * innerW;
  const y = (v: number) => padT + (1 - v) * innerH;

  const validBins = bins.filter(b => b.count > 0 && b.p_realized_up !== null);

  return (
    <svg width={width} height={height} className="overflow-visible">
      {/* grid */}
      {[0, 0.25, 0.5, 0.75, 1].map((g) => (
        <g key={g}>
          <line x1={x(g)} y1={padT} x2={x(g)} y2={padT + innerH} stroke="#1e293b" strokeWidth={1} />
          <line x1={padL} y1={y(g)} x2={padL + innerW} y2={y(g)} stroke="#1e293b" strokeWidth={1} />
          <text x={padL - 4} y={y(g) + 3} textAnchor="end" fontSize={9} fill="#64748b">
            {g.toFixed(2)}
          </text>
          <text x={x(g)} y={padT + innerH + 12} textAnchor="middle" fontSize={9} fill="#64748b">
            {g.toFixed(2)}
          </text>
        </g>
      ))}
      {/* ideal diagonal */}
      <line x1={x(0)} y1={y(0)} x2={x(1)} y2={y(1)} stroke="#475569" strokeDasharray="3 3" strokeWidth={1} />
      {/* points */}
      {validBins.map((b, i) => {
        const r = 3 + Math.min(6, Math.sqrt(b.count));
        const ideal = Math.abs(b.p_realized_up! - b.p_pred_center) < 0.08;
        const color = ideal ? "#22c55e" : "#f59e0b";
        return (
          <circle key={i} cx={x(b.p_pred_center)} cy={y(b.p_realized_up!)} r={r}
                  fill={color} fillOpacity={0.7} stroke={color} strokeWidth={1} />
        );
      })}
      {/* labels */}
      <text x={padL + innerW / 2} y={padT + innerH + 16} textAnchor="middle" fontSize={9} fill="#64748b">
        p (model)
      </text>
    </svg>
  );
}

/* ─── Donut (two-slice) ─── */
export function Donut({
  value,
  size = 72,
  label,
  tone = "accent",
}: {
  value: number; // 0-1
  size?: number;
  label?: React.ReactNode;
  tone?: "accent" | "bull" | "bear" | "warn";
}) {
  const r = size / 2 - 6;
  const c = 2 * Math.PI * r;
  const stroke =
    tone === "bull" ? "#22c55e" :
    tone === "bear" ? "#ef4444" :
    tone === "warn" ? "#f59e0b" : "#818cf8";
  const v = Math.max(0, Math.min(1, value));
  return (
    <div className="relative inline-flex items-center justify-center" style={{ width: size, height: size }}>
      <svg width={size} height={size} className="-rotate-90 transform">
        <circle cx={size / 2} cy={size / 2} r={r} stroke="#334155" strokeWidth={6} fill="none" />
        <circle
          cx={size / 2}
          cy={size / 2}
          r={r}
          stroke={stroke}
          strokeWidth={6}
          fill="none"
          strokeLinecap="round"
          strokeDasharray={c}
          strokeDashoffset={c * (1 - v)}
        />
      </svg>
      <div className="absolute inset-0 flex items-center justify-center text-center text-xs font-mono">
        {label ?? `${(v * 100).toFixed(0)}%`}
      </div>
    </div>
  );
}
