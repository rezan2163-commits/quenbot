"use client";

import { useSignals } from "@/lib/api";
import { ArrowUpCircle, ArrowDownCircle, Clock, Target } from "lucide-react";

export default function ActiveSignals() {
  const { data: signals } = useSignals();

  // Show only active/pending signals
  const active = signals?.filter((s) => s.status === "active" || s.status === "pending" || s.status === "open") || [];

  return (
    <div className="h-full flex flex-col bg-surface-card/30 overflow-hidden">
      <div className="flex items-center justify-between px-3 py-2 border-b border-surface-border">
        <span className="text-xs font-semibold text-gray-300 tracking-wide">AKTİF SİNYALLER</span>
        <span className="text-[10px] text-gray-500">{active.length} sinyal</span>
      </div>

      <div className="flex-1 overflow-y-auto custom-scrollbar">
        {active.length === 0 ? (
          <div className="flex items-center justify-center h-full text-gray-600 text-xs">Aktif sinyal yok</div>
        ) : (
          <div className="space-y-1 p-2">
            {active.map((s) => {
              const isLong = (s.direction || "").toLowerCase() === "long" || (s.direction || "").toLowerCase() === "buy";
              const meta = s.metadata || {};
              const entry = meta.entry_price || s.price;
              const target = meta.target_price;
              const reason = meta.reason || s.signal_type;
              const conf = (s.confidence * 100).toFixed(0);
              const age = new Date(s.timestamp);

              return (
                <div
                  key={s.id}
                  className={`rounded-lg border p-2.5 transition-colors ${
                    isLong
                      ? "border-bull/20 bg-bull/[0.04] hover:bg-bull/[0.08]"
                      : "border-bear/20 bg-bear/[0.04] hover:bg-bear/[0.08]"
                  }`}
                >
                  {/* Top row */}
                  <div className="flex items-center justify-between mb-1">
                    <div className="flex items-center gap-1.5">
                      {isLong ? (
                        <ArrowUpCircle size={14} className="text-bull" />
                      ) : (
                        <ArrowDownCircle size={14} className="text-bear" />
                      )}
                      <span className="text-xs font-semibold text-gray-200">{s.symbol}</span>
                      <span className={`text-[10px] font-bold ${isLong ? "text-bull" : "text-bear"}`}>
                        {(s.direction || "?").toUpperCase()}
                      </span>
                    </div>
                    <span className="text-[10px] text-accent font-medium">{conf}%</span>
                  </div>

                  {/* Details */}
                  <div className="grid grid-cols-2 gap-1 text-[10px] text-gray-400">
                    <div className="flex items-center gap-1">
                      <span className="text-gray-600">Giriş:</span>
                      <span className="text-gray-300 font-mono">${Number(entry).toLocaleString()}</span>
                    </div>
                    {target && (
                      <div className="flex items-center gap-1">
                        <Target size={8} className="text-gray-600" />
                        <span className="text-gray-300 font-mono">${Number(target).toLocaleString()}</span>
                      </div>
                    )}
                    <div className="col-span-2 flex items-center gap-1">
                      <Clock size={8} className="text-gray-600" />
                      <span>{age.toLocaleTimeString("tr-TR", { hour: "2-digit", minute: "2-digit" })}</span>
                      <span className="ml-1 text-gray-600">|</span>
                      <span className="text-gray-500 truncate">{reason}</span>
                    </div>
                  </div>
                </div>
              );
            })}
          </div>
        )}
      </div>
    </div>
  );
}
