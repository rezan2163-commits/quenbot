"use client";

import { useMemo } from "react";
import { ModuleStatus, EdgeActivity, ORGAN_ACCENT, ORGAN_LABELS, STATUS_COLORS, type ModuleOrgan } from "@/lib/missionControl";

interface Props {
  modules: ModuleStatus[];
  edges: EdgeActivity[];
  qwenPhase: string;
  onSelect: (moduleId: string) => void;
  selectedId: string | null;
}

const ORGAN_ORDER: ModuleOrgan[] = ["brain", "agent", "detector", "fusion", "learning", "safety", "runtime"];

// Polar layout: brain at center; organs around it; modules on organ arc.
export function ConstellationCanvas({ modules, edges, qwenPhase, onSelect, selectedId }: Props) {
  const positions = useMemo(() => computePositions(modules), [modules]);

  const width = 1000;
  const height = 640;

  const byOrgan = useMemo(() => {
    const m: Record<string, ModuleStatus[]> = {};
    for (const mod of modules) {
      (m[mod.organ] ||= []).push(mod);
    }
    return m;
  }, [modules]);

  const qwenMod = modules.find((m) => m.id === "gemma_decision_core");
  const phaseColor = qwenPhase === "3" ? "#ef4444" : qwenPhase === "2" ? "#f59e0b" : qwenPhase === "1" ? "#22c55e" : "#a855f7";

  return (
    <div className="relative w-full h-full bg-gradient-to-br from-slate-950 via-slate-900 to-slate-950 overflow-hidden rounded-lg border border-surface-border">
      <svg viewBox={`0 0 ${width} ${height}`} className="w-full h-full" preserveAspectRatio="xMidYMid meet">
        <defs>
          <radialGradient id="bg-glow" cx="50%" cy="50%" r="70%">
            <stop offset="0%" stopColor="rgba(168,85,247,0.08)" />
            <stop offset="100%" stopColor="rgba(0,0,0,0)" />
          </radialGradient>
          <filter id="pulse-blur" x="-50%" y="-50%" width="200%" height="200%">
            <feGaussianBlur stdDeviation="2" />
          </filter>
        </defs>
        <rect x={0} y={0} width={width} height={height} fill="url(#bg-glow)" />

        {/* Organ arcs (subtle) */}
        {ORGAN_ORDER.map((organ, i) => {
          if (organ === "brain") return null;
          const radius = 220 + (i % 3) * 40;
          return (
            <circle
              key={organ}
              cx={width / 2}
              cy={height / 2}
              r={radius}
              fill="none"
              stroke={ORGAN_ACCENT[organ]}
              strokeOpacity={0.06}
              strokeDasharray="2 6"
            />
          );
        })}

        {/* Edges */}
        {edges.map((e, i) => {
          const from = positions[e.from];
          const to = positions[e.to];
          if (!from || !to) return null;
          const intensity =
            e.bucket === "hot" ? 0.85 : e.bucket === "warm" ? 0.5 : e.bucket === "cool" ? 0.25 : 0.08;
          const strokeWidth = e.bucket === "hot" ? 1.8 : e.bucket === "warm" ? 1.2 : 0.6;
          const color = e.bucket === "silent" ? "#334155" : e.bucket === "cool" ? "#475569" : "#64748b";
          return (
            <line
              key={`${e.from}-${e.to}-${i}`}
              x1={from.x}
              y1={from.y}
              x2={to.x}
              y2={to.y}
              stroke={color}
              strokeOpacity={intensity}
              strokeWidth={strokeWidth}
            />
          );
        })}

        {/* Modules */}
        {modules.map((m) => {
          const p = positions[m.id];
          if (!p) return null;
          const isQwen = m.id === "gemma_decision_core";
          const baseR = isQwen ? 34 : 12;
          const color = statusHex(m.status);
          const selected = selectedId === m.id;
          return (
            <g
              key={m.id}
              transform={`translate(${p.x}, ${p.y})`}
              className="cursor-pointer"
              onClick={() => onSelect(m.id)}
            >
              {/* Pulse ring for healthy modules */}
              {m.status === "healthy" && m.throughput_per_sec > 0 && (
                <circle r={baseR + 6} fill="none" stroke={color} strokeOpacity={0.35} strokeWidth={1}>
                  <animate attributeName="r" values={`${baseR};${baseR + 14};${baseR}`} dur="2.4s" repeatCount="indefinite" />
                  <animate attributeName="stroke-opacity" values="0.5;0;0.5" dur="2.4s" repeatCount="indefinite" />
                </circle>
              )}
              {/* Qwen phase glow */}
              {isQwen && (
                <circle r={baseR + 14} fill="none" stroke={phaseColor} strokeOpacity={0.35} strokeWidth={2}>
                  <animate attributeName="stroke-opacity" values="0.6;0.1;0.6" dur="3s" repeatCount="indefinite" />
                </circle>
              )}
              <circle
                r={baseR}
                fill={color}
                fillOpacity={m.status === "disabled" ? 0.15 : 0.9}
                stroke={selected ? "#fff" : color}
                strokeWidth={selected ? 2.5 : 1}
              />
              {isQwen && (
                <text textAnchor="middle" dy="4" fontSize="14" fill="#fff" fontWeight="700">Q</text>
              )}
              <text
                textAnchor="middle"
                y={baseR + 10}
                fontSize={isQwen ? 11 : 9}
                fill="#cbd5e1"
                fontWeight={isQwen ? 600 : 400}
              >
                {m.display_name.length > 18 ? m.display_name.slice(0, 16) + "…" : m.display_name}
              </text>
            </g>
          );
        })}
      </svg>

      {/* Legend */}
      <div className="absolute bottom-2 left-2 right-2 flex flex-wrap gap-1.5 text-[9px]">
        {ORGAN_ORDER.map((o) => (
          <span
            key={o}
            className="flex items-center gap-1 rounded-full border border-surface-border bg-black/40 px-2 py-0.5 text-gray-300"
          >
            <span className="h-2 w-2 rounded-full" style={{ backgroundColor: ORGAN_ACCENT[o] }} />
            {ORGAN_LABELS[o]} ({(byOrgan[o] || []).length})
          </span>
        ))}
      </div>
    </div>
  );
}

function statusHex(s: string): string {
  if (s === "healthy") return "#10b981";
  if (s === "slow") return "#f59e0b";
  if (s === "unhealthy") return "#ef4444";
  if (s === "dormant") return "#64748b";
  if (s === "disabled") return "#475569";
  return "#94a3b8";
}

function computePositions(modules: ModuleStatus[]): Record<string, { x: number; y: number }> {
  const cx = 500;
  const cy = 320;
  const out: Record<string, { x: number; y: number }> = {};

  // Center: brain cluster (gemma_decision_core at center)
  const brainMods = modules.filter((m) => m.organ === "brain");
  brainMods.forEach((m, i) => {
    if (m.id === "gemma_decision_core") {
      out[m.id] = { x: cx, y: cy };
    } else {
      const angle = (i / Math.max(1, brainMods.length - 1)) * Math.PI * 2;
      out[m.id] = { x: cx + Math.cos(angle) * 110, y: cy + Math.sin(angle) * 110 };
    }
  });

  // Other organs around
  const organs: Record<string, { start: number; end: number; radius: number }> = {
    agent: { start: -Math.PI / 2 - 0.5, end: -Math.PI / 2 + 0.5, radius: 240 },
    detector: { start: 0, end: Math.PI / 2, radius: 260 },
    fusion: { start: Math.PI / 2 - 0.2, end: Math.PI / 2 + 0.3, radius: 240 },
    learning: { start: Math.PI / 2 + 0.3, end: Math.PI + 0.2, radius: 250 },
    safety: { start: Math.PI + 0.2, end: Math.PI + 1.0, radius: 240 },
    runtime: { start: -Math.PI + 0.2, end: -Math.PI / 2 - 0.7, radius: 240 },
  };

  for (const [organ, cfg] of Object.entries(organs)) {
    const mods = modules.filter((m) => m.organ === organ);
    mods.forEach((m, i) => {
      const t = mods.length === 1 ? 0.5 : i / (mods.length - 1);
      const angle = cfg.start + (cfg.end - cfg.start) * t;
      // spread on arc + secondary radius variation
      const r = cfg.radius + (i % 2 === 0 ? 0 : 30);
      out[m.id] = { x: cx + Math.cos(angle) * r, y: cy + Math.sin(angle) * r };
    });
  }

  return out;
}
