"use client";

import { ModuleStatus, ORGAN_ACCENT, ORGAN_LABELS, type ModuleOrgan } from "@/lib/missionControl";

const ORDER: ModuleOrgan[] = ["agent", "brain", "detector", "fusion", "learning", "safety", "runtime"];

export function OrganSummaryRow({ modules }: { modules: ModuleStatus[] }) {
  const groups: Record<string, { total: number; healthy: number; slow: number; unhealthy: number; avg: number }> = {};
  for (const o of ORDER) groups[o] = { total: 0, healthy: 0, slow: 0, unhealthy: 0, avg: 0 };
  for (const m of modules) {
    const g = groups[m.organ];
    if (!g) continue;
    g.total += 1;
    if (m.status === "healthy") g.healthy += 1;
    else if (m.status === "slow") g.slow += 1;
    else if (m.status === "unhealthy") g.unhealthy += 1;
    g.avg += m.health_score;
  }
  for (const o of ORDER) {
    const g = groups[o];
    g.avg = g.total > 0 ? Math.round(g.avg / g.total) : 0;
  }

  return (
    <div className="grid grid-cols-2 gap-2 sm:grid-cols-4 lg:grid-cols-7">
      {ORDER.map((o) => {
        const g = groups[o];
        const color = ORGAN_ACCENT[o];
        const tone = g.avg >= 85 ? "text-emerald-300" : g.avg >= 60 ? "text-amber-300" : "text-red-300";
        return (
          <div
            key={o}
            className="rounded-lg border border-surface-border bg-surface-card px-2 py-1.5"
            style={{ borderLeftColor: color, borderLeftWidth: 3 }}
          >
            <div className="flex items-center justify-between">
              <span className="text-[10px] font-semibold text-gray-300">{ORGAN_LABELS[o]}</span>
              <span className={`text-xs font-bold tabular-nums ${tone}`}>{g.avg}</span>
            </div>
            <div className="mt-0.5 text-[9px] text-gray-500">
              {g.total} modül · <span className="text-emerald-400">{g.healthy}</span>
              {g.slow > 0 && <> · <span className="text-amber-400">{g.slow}</span></>}
              {g.unhealthy > 0 && <> · <span className="text-red-400">{g.unhealthy}</span></>}
            </div>
          </div>
        );
      })}
    </div>
  );
}
