"use client";

import { useEffect, useState } from "react";
import type { ConnectionState, QwenPulse } from "@/lib/missionControl";

const PHASE_LABELS: Record<string, { label: string; color: string }> = {
  "0": { label: "Faz 0: Gözlem", color: "text-purple-300 border-purple-500/40 bg-purple-500/5" },
  "1": { label: "Faz 1: Gatekeeper", color: "text-emerald-300 border-emerald-500/40 bg-emerald-500/5" },
  "2": { label: "Faz 2: Etki Takibi", color: "text-amber-300 border-amber-500/40 bg-amber-500/5" },
  "3": { label: "Faz 3: Kilitlendi", color: "text-red-300 border-red-500/40 bg-red-500/5" },
};

interface Props {
  generatedAt: number | undefined;
  connection: ConnectionState;
  qwen: QwenPulse | undefined;
  overallScore: number | undefined;
}

export function MissionControlClock({ generatedAt, connection, qwen, overallScore }: Props) {
  const [now, setNow] = useState(Date.now());
  useEffect(() => {
    const id = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(id);
  }, []);

  const lag = generatedAt ? Math.max(0, Math.floor(now / 1000 - generatedAt)) : null;
  const phase = qwen?.phase ?? "0";
  const phaseCfg = PHASE_LABELS[phase] ?? PHASE_LABELS["0"];

  const connDot =
    connection === "live" ? { color: "bg-emerald-400", label: "Canlı" } :
    connection === "polling" ? { color: "bg-amber-400", label: "Polling" } :
    { color: "bg-red-400", label: "Offline" };

  const scoreTone =
    overallScore === undefined ? "text-gray-400" :
    overallScore >= 85 ? "text-emerald-300" :
    overallScore >= 60 ? "text-amber-300" :
    "text-red-300";

  return (
    <div className="flex flex-wrap items-center gap-2 text-xs">
      <div className="flex items-center gap-1.5 rounded-full border border-surface-border bg-surface-card px-2 py-1">
        <span className={`relative flex h-2 w-2`}>
          <span className={`absolute inline-flex h-full w-full rounded-full ${connDot.color} opacity-75 animate-ping`} />
          <span className={`relative inline-flex h-2 w-2 rounded-full ${connDot.color}`} />
        </span>
        <span className="text-gray-300">{connDot.label}</span>
        {lag !== null && <span className="text-gray-500 tabular-nums">· {lag}sn</span>}
      </div>

      <div className={`rounded-full border px-2 py-1 text-[11px] font-medium ${phaseCfg.color}`}>
        {phaseCfg.label}
      </div>

      <div className="rounded-full border border-surface-border bg-surface-card px-2 py-1">
        <span className="text-gray-500">Sistem Skoru:</span>{" "}
        <span className={`font-bold tabular-nums ${scoreTone}`}>{overallScore ?? "—"}</span>
      </div>

      {qwen?.lockdown && (
        <div className="rounded-full border border-red-500/60 bg-red-500/10 px-2 py-1 font-semibold text-red-300 animate-pulse">
          🚨 LOCKDOWN
        </div>
      )}
    </div>
  );
}
