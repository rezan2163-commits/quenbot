"use client";

import { useState } from "react";
import Link from "next/link";
import { ArrowLeft } from "lucide-react";
import { useMissionControlStream } from "@/lib/missionControl";
import { VitalSignsStrip } from "./VitalSignsStrip";
import { ConstellationCanvas } from "./ConstellationCanvas";
import { OrganSummaryRow } from "./OrganSummaryRow";
import { AutopsyDrawer } from "./AutopsyDrawer";
import { MissionControlClock } from "./MissionControlClock";
import { WakeAnimation, WakeAnimationToggle } from "./WakeAnimation";

export function MissionControl() {
  const { snapshot, connection, error } = useMissionControlStream();
  const [selectedId, setSelectedId] = useState<string | null>(null);

  return (
    <div className="flex min-h-svh flex-col bg-surface">
      <WakeAnimation />

      <header className="flex flex-wrap items-center justify-between gap-2 border-b border-surface-border px-4 py-3">
        <div className="flex items-center gap-3">
          <Link
            href="/"
            className="inline-flex items-center gap-1 rounded-md border border-surface-border bg-surface-card px-2 py-1 text-xs text-gray-300 hover:text-gray-100"
          >
            <ArrowLeft size={12} />
            Ana Panel
          </Link>
          <h1 className="text-lg font-semibold text-gray-100">Mission Control</h1>
          <span className="text-[10px] text-gray-500">Canlı Sistem Takımyıldızı</span>
        </div>
        <MissionControlClock
          generatedAt={snapshot?.generated_at}
          connection={connection}
          qwen={snapshot?.qwen_pulse}
          overallScore={snapshot?.overall_health_score}
        />
      </header>

      {error && connection === "offline" && (
        <div className="border-b border-red-500/30 bg-red-500/10 px-4 py-1.5 text-[11px] text-red-300">
          Bağlantı hatası: {error}
        </div>
      )}

      <main className="flex-1 space-y-3 p-3">
        <VitalSignsStrip vitals={snapshot?.vital_signs} />
        <OrganSummaryRow modules={snapshot?.modules ?? []} />

        <div className="h-[640px]">
          <ConstellationCanvas
            modules={snapshot?.modules ?? []}
            edges={snapshot?.edges ?? []}
            qwenPhase={snapshot?.qwen_pulse?.phase ?? "0"}
            onSelect={setSelectedId}
            selectedId={selectedId}
          />
        </div>

        {!snapshot && (
          <div className="rounded-lg border border-surface-border bg-surface-card p-6 text-center text-xs text-gray-400">
            Takımyıldız yükleniyor…
          </div>
        )}
      </main>

      <footer className="flex items-center justify-between border-t border-surface-border px-4 py-2 text-[10px] text-gray-500">
        <span>
          QuenBot · {snapshot?.modules?.length ?? 0} modül · {snapshot?.edges?.length ?? 0} bağlantı
        </span>
        <WakeAnimationToggle />
      </footer>

      <AutopsyDrawer moduleId={selectedId} onClose={() => setSelectedId(null)} />
    </div>
  );
}
