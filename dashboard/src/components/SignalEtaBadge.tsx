"use client";

import { Timer } from "lucide-react";
import type { SignalEtaBundle } from "@/lib/api";
import { cn } from "./ui/primitives";

function formatEta(sec: number): string {
  if (!Number.isFinite(sec) || sec <= 0) return "—";
  if (sec < 60) return `${Math.round(sec)}sn`;
  if (sec < 3600) return `${Math.round(sec / 60)}dk`;
  if (sec < 86400) return `${(sec / 3600).toFixed(1)}sa`;
  return `${(sec / 86400).toFixed(1)}g`;
}

export function formatEtaSeconds(sec: number): string {
  return formatEta(sec);
}

const BASIS_LABEL: Record<string, string> = {
  historical_similar_patterns: "geçmiş benzer patternler",
  atr_rate: "ATR hız modeli",
  wavelet_frequency: "wavelet frekansı",
  hawkes_decay: "hawkes bozunumu",
};

export default function SignalEtaBadge({
  eta,
  fallbackMinutes,
  className,
}: {
  eta?: SignalEtaBundle | null;
  fallbackMinutes?: number;
  className?: string;
}) {
  if (!eta || !Number.isFinite(eta.p50_seconds)) {
    if (fallbackMinutes && fallbackMinutes > 0) {
      return (
        <span
          className={cn(
            "inline-flex items-center gap-1 rounded-md border border-cyan-400/20 bg-cyan-400/5 px-1.5 py-0.5 text-[9px] text-cyan-200",
            className,
          )}
          title="Tahmini süre (ATR hız modeli)"
        >
          <Timer size={9} />~{formatEta(fallbackMinutes * 60)}
        </span>
      );
    }
    return null;
  }

  const basisLabel = BASIS_LABEL[eta.basis] || eta.basis;
  const confPct = eta.confidence ? ` · güven %${Math.round(eta.confidence * 100)}` : "";

  return (
    <span
      className={cn(
        "inline-flex items-center gap-1 rounded-md border border-cyan-400/25 bg-cyan-400/10 px-1.5 py-0.5 text-[9px] font-medium text-cyan-200",
        className,
      )}
      title={`Hedefe tahmini süre (${basisLabel}${confPct})`}
    >
      <Timer size={9} />
      <span className="font-mono">
        {formatEta(eta.p50_seconds)} <span className="opacity-60">– {formatEta(eta.p80_seconds)}</span>
      </span>
    </span>
  );
}
