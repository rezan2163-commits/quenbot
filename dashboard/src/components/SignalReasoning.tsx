"use client";

import { useState } from "react";
import { ChevronDown, ChevronRight, BrainCircuit } from "lucide-react";
import type { SignalReasoningBundle } from "@/lib/api";
import { cn } from "./ui/primitives";

const CATEGORY_TONES: Record<string, string> = {
  confluence: "bg-sky-400/15 text-sky-200 border-sky-400/30",
  indicator: "bg-amber-400/15 text-amber-200 border-amber-400/30",
  microstructure: "bg-fuchsia-400/15 text-fuchsia-200 border-fuchsia-400/30",
  changepoint: "bg-emerald-400/15 text-emerald-200 border-emerald-400/30",
  factor_graph: "bg-indigo-400/15 text-indigo-200 border-indigo-400/30",
};

const CATEGORY_LABEL: Record<string, string> = {
  confluence: "birleşim",
  indicator: "indikatör",
  microstructure: "mikroyapı",
  changepoint: "değişim",
  factor_graph: "faktör grafiği",
};

function StrengthBar({ value }: { value: number }) {
  const pct = Math.max(0, Math.min(1, Number(value) || 0)) * 100;
  const tone = pct >= 75 ? "bg-emerald-400" : pct >= 50 ? "bg-amber-400" : "bg-rose-400";
  return (
    <div className="h-1 w-16 overflow-hidden rounded-full bg-white/10">
      <div className={cn("h-full transition-all", tone)} style={{ width: `${pct}%` }} />
    </div>
  );
}

export default function SignalReasoning({
  reasoning,
  defaultOpen = false,
}: {
  reasoning?: SignalReasoningBundle | null;
  defaultOpen?: boolean;
}) {
  const [open, setOpen] = useState(defaultOpen);

  const triggers = reasoning?.triggers ?? [];
  const similar = reasoning?.similar_patterns;

  if (!reasoning || triggers.length === 0) {
    return null;
  }

  return (
    <div className="rounded-md border border-white/5 bg-black/25">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="flex w-full items-center justify-between gap-2 px-2 py-1.5 text-[10px] text-gray-300 hover:bg-white/[0.03]"
      >
        <span className="flex items-center gap-1.5">
          <BrainCircuit size={10} className="text-accent" />
          <span className="font-medium">Neden bu sinyal?</span>
          <span className="text-gray-500">({triggers.length} tetik)</span>
        </span>
        {open ? <ChevronDown size={10} /> : <ChevronRight size={10} />}
      </button>
      {open && (
        <div className="flex flex-col gap-1.5 border-t border-white/5 px-2 py-1.5">
          <ul className="flex flex-col gap-1">
            {triggers.map((t, idx) => (
              <li
                key={`${t.label}-${idx}`}
                className="flex items-center gap-2 text-[10px] text-gray-300"
              >
                <span
                  className={cn(
                    "shrink-0 rounded-full border px-1.5 py-px text-[8px] uppercase tracking-wide",
                    CATEGORY_TONES[t.category] ?? "bg-white/5 text-gray-400 border-white/10",
                  )}
                >
                  {CATEGORY_LABEL[t.category] ?? t.category}
                </span>
                <span className="min-w-0 flex-1 truncate">{t.label}</span>
                <span className="font-mono text-[9px] text-gray-400">
                  {(Number(t.strength) || 0).toFixed(2)}
                </span>
                <StrengthBar value={t.strength} />
              </li>
            ))}
          </ul>
          {similar && similar.count > 0 && (
            <div className="mt-1 rounded border border-white/5 bg-white/[0.02] px-2 py-1 text-[9px] text-gray-400">
              📈 Geçmiş:{" "}
              <span className="font-mono text-gray-200">{similar.count}</span> benzer pattern
              {similar.win_rate != null && (
                <>
                  , %<span className="font-mono text-bull">{(similar.win_rate * 100).toFixed(0)}</span>{" "}
                  başarı
                </>
              )}
              {similar.avg_realized_pct != null && (
                <>
                  , ort %
                  <span className="font-mono text-gray-200">
                    {(similar.avg_realized_pct * 100).toFixed(2)}
                  </span>
                </>
              )}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
