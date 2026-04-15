"use client";

import { useLearningLog, useLearningStats } from "@/lib/api";
import { Brain, CheckCircle, XCircle, TrendingUp } from "lucide-react";
import { formatInQuenbotTimeZone } from "@/lib/time";

export default function LearningLog() {
  const { data: log } = useLearningLog();
  const { data: stats } = useLearningStats();
  const toNumber = (value: unknown, fallback = 0) => {
    const n = Number(value);
    return Number.isFinite(n) ? n : fallback;
  };

  return (
    <div className="h-full flex flex-col bg-surface-card/30 overflow-hidden">
      <div className="flex items-center justify-between px-3 py-2 border-b border-surface-border">
        <div className="flex items-center gap-1.5">
          <Brain size={12} className="text-accent" />
          <span className="text-xs font-semibold text-gray-300 tracking-wide">ÖĞRENME LOGU</span>
        </div>
        {stats && (
          <span className="text-[10px] text-gray-500">
            Doğruluk: <span className={toNumber(stats.accuracy) >= 50 ? "text-bull" : "text-bear"}>{toNumber(stats.accuracy).toFixed(1)}%</span>
          </span>
        )}
      </div>

      {/* Stats summary */}
      {stats && (
        <div className="grid grid-cols-4 gap-px bg-surface-border/50 border-b border-surface-border">
          <div className="bg-surface-card/50 px-2 py-1.5 text-center">
            <div className="text-[10px] text-gray-500">Toplam</div>
            <div className="text-xs font-semibold text-gray-200">{stats.total}</div>
          </div>
          <div className="bg-surface-card/50 px-2 py-1.5 text-center">
            <div className="text-[10px] text-gray-500">Doğru</div>
            <div className="text-xs font-semibold text-bull">{stats.correct}</div>
          </div>
          <div className="bg-surface-card/50 px-2 py-1.5 text-center">
            <div className="text-[10px] text-gray-500">Doğruluk</div>
            <div className={`text-xs font-semibold ${toNumber(stats.accuracy) >= 50 ? "text-bull" : "text-bear"}`}>{toNumber(stats.accuracy).toFixed(1)}%</div>
          </div>
          <div className="bg-surface-card/50 px-2 py-1.5 text-center">
            <div className="text-[10px] text-gray-500">Ort PnL</div>
            <div className={`text-xs font-semibold ${toNumber(stats.avg_pnl) >= 0 ? "text-bull" : "text-bear"}`}>{toNumber(stats.avg_pnl) >= 0 ? "+" : ""}{toNumber(stats.avg_pnl).toFixed(2)}%</div>
          </div>
        </div>
      )}

      {/* By type breakdown */}
      {stats && stats.by_type && stats.by_type.length > 0 && (
        <div className="px-3 py-1.5 border-b border-surface-border/50 space-y-1">
          <div className="text-[10px] text-gray-500 font-medium">Sinyal Türüne Göre</div>
          {stats.by_type.slice(0, 5).map((t) => (
            <div key={t.signal_type} className="flex items-center justify-between text-[10px]">
              <span className="text-gray-400 truncate">{t.signal_type}</span>
              <div className="flex items-center gap-2">
                <span className={toNumber(t.correct) / Math.max(toNumber(t.total), 1) >= 0.5 ? "text-bull" : "text-bear"}>
                  {((toNumber(t.correct) / Math.max(toNumber(t.total), 1)) * 100).toFixed(0)}%
                </span>
                <span className="text-gray-600">({t.total})</span>
              </div>
            </div>
          ))}
        </div>
      )}

      {/* Log entries */}
      <div className="flex-1 overflow-y-auto custom-scrollbar">
        {!log || log.length === 0 ? (
          <div className="flex items-center justify-center h-full text-gray-600 text-xs">Öğrenme kaydı yok</div>
        ) : (
          <div className="divide-y divide-surface-border/30">
            {log.map((entry) => (
              <div key={entry.id} className="flex items-start gap-2 px-3 py-2 hover:bg-white/[0.02] transition-colors">
                {entry.was_correct ? (
                  <CheckCircle size={12} className="text-bull mt-0.5 flex-shrink-0" />
                ) : (
                  <XCircle size={12} className="text-bear mt-0.5 flex-shrink-0" />
                )}
                <div className="min-w-0 flex-1">
                  <div className="flex items-center justify-between">
                    <span className="text-[10px] font-medium text-gray-300">{entry.signal_type}</span>
                    <span className={`text-[10px] font-mono ${toNumber(entry.pnl_pct) >= 0 ? "text-bull" : "text-bear"}`}>
                      {toNumber(entry.pnl_pct) >= 0 ? "+" : ""}{toNumber(entry.pnl_pct).toFixed(2)}%
                    </span>
                  </div>
                  <div className="text-[9px] text-gray-600">
                    {formatInQuenbotTimeZone(entry.created_at, { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" })}
                  </div>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
