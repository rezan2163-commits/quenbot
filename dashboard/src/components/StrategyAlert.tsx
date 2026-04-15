"use client";

import { useSelfCorrection, useStrategyEvents } from "@/lib/api";
import { useEffect, useState } from "react";
import { AlertTriangle, CheckCircle, RefreshCw, Shield, X } from "lucide-react";

interface Toast {
  id: number;
  message: string;
  type: "correction" | "update" | "info";
  timestamp: string;
}

export default function StrategyAlert() {
  const { data: correction } = useSelfCorrection();
  const { data: events } = useStrategyEvents();
  const [toasts, setToasts] = useState<Toast[]>([]);
  const [lastCorrectionState, setLastCorrectionState] = useState<boolean | null>(null);
  const [dismissed, setDismissed] = useState<Set<number>>(new Set());

  // Watch for correction state changes
  useEffect(() => {
    if (!correction) return;
    const needs = correction.needs_correction;
    if (lastCorrectionState !== null && needs && !lastCorrectionState) {
      // Transition to needing correction
      const id = Date.now();
      const toast: Toast = {
        id,
        message: `⚠️ Strateji Güncellendi — Win Rate %${correction.recent_performance.recent_win_rate ?? 0} (< %50). Otomatik revizyon uygulandı.`,
        type: "correction",
        timestamp: new Date().toISOString(),
      };
      setToasts((prev) => [...prev, toast].slice(-5));
    }
    setLastCorrectionState(needs);
  }, [correction, lastCorrectionState]);

  // Watch for strategy update events
  useEffect(() => {
    if (!events?.state) return;
    const stratUpdateEntry = events.state.find((s) => s.state_key === "last_strategy_update");
    if (stratUpdateEntry) {
      const val = typeof stratUpdateEntry.state_value === "string"
        ? JSON.parse(stratUpdateEntry.state_value)
        : stratUpdateEntry.state_value;
      if (val?.type === "strategy_revised") {
        const id = Date.now() + 1;
        setToasts((prev) => {
          if (prev.some((t) => t.type === "update" && Date.now() - new Date(t.timestamp).getTime() < 30000)) return prev;
          const toast: Toast = {
            id,
            message: `🔄 Strateji Revize Edildi — Rejim: ${val.regime}, Win Rate: %${val.win_rate}${val.llm_recommendation ? `. AI Öneri: ${val.llm_recommendation.slice(0, 100)}...` : ""}`,
            type: "update",
            timestamp: new Date().toISOString(),
          };
          return [...prev, toast].slice(-5);
        });
      }
    }
  }, [events]);

  const dismissToast = (id: number) => {
    setDismissed((prev) => new Set([...prev, id]));
  };

  const visibleToasts = toasts.filter((t) => !dismissed.has(t.id));
  const perf = correction?.recent_performance;
  const needsCorrection = correction?.needs_correction;

  return (
    <>
      {/* Bottom status bar */}
      <div className="flex flex-wrap items-center justify-between gap-2 px-3 py-2 border-t border-surface-border bg-surface-card/30 sm:px-4 sm:py-1.5">
        {/* Left: correction status */}
        <div className="flex min-w-0 flex-wrap items-center gap-2 sm:gap-3">
          {needsCorrection ? (
            <div className="flex items-center gap-1.5 text-warn">
              <RefreshCw size={12} className="animate-spin" />
              <span className="text-[11px] font-medium">Strateji Revizyonu Aktif</span>
            </div>
          ) : (
            <div className="flex items-center gap-1.5 text-bull">
              <Shield size={12} />
              <span className="text-[11px] font-medium">Strateji Stabil</span>
            </div>
          )}
          {perf && (
            <span className="text-[10px] text-gray-500 font-mono break-words">
              24h: {perf.recent_trades ?? 0} işlem | WR %{perf.recent_win_rate ?? 0} | Avg %{perf.avg_pnl_pct ?? 0}
            </span>
          )}
        </div>

        {/* Right: RCA summary */}
        <div className="flex min-w-0 flex-wrap items-center gap-2">
          {(correction?.rca_summary || []).slice(0, 3).map((r, i) => (
            <span key={i} className="text-[10px] text-gray-500 bg-surface-hover px-1.5 py-0.5 rounded">
              {r.failure_type}: {r.count}
            </span>
          ))}
          {(events?.audits || []).length > 0 && (
            <span className="text-[10px] text-gray-500">
              Son audit: %{events!.audits[0].success_rate}
            </span>
          )}
        </div>
      </div>

      {/* Floating toast notifications */}
      {visibleToasts.length > 0 && (
        <div className="fixed left-3 right-3 top-3 z-50 space-y-2 sm:left-auto sm:right-4 sm:top-4 sm:max-w-sm">
          {visibleToasts.map((t) => (
            <div
              key={t.id}
              className={`flex items-start gap-2 px-3 py-2.5 rounded-lg shadow-lg border backdrop-blur-sm animate-in slide-in-from-top-2 ${
                t.type === "correction"
                  ? "bg-warn/10 border-warn/30 text-warn"
                  : t.type === "update"
                  ? "bg-accent/10 border-accent/30 text-accent"
                  : "bg-surface-card border-surface-border text-gray-300"
              }`}
            >
              {t.type === "correction" ? (
                <AlertTriangle size={14} className="flex-shrink-0 mt-0.5" />
              ) : (
                <CheckCircle size={14} className="flex-shrink-0 mt-0.5" />
              )}
              <p className="text-xs flex-1">{t.message}</p>
              <button onClick={() => dismissToast(t.id)} className="text-gray-500 hover:text-gray-300">
                <X size={12} />
              </button>
            </div>
          ))}
        </div>
      )}
    </>
  );
}
