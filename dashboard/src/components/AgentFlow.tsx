"use client";

import { useAgentFlow } from "@/lib/api";
import {
  Eye, TrendingUp, Ghost, Shield, Brain, Grid3X3,
  ArrowRight, ArrowDown, CheckCircle, AlertCircle, Clock,
} from "lucide-react";

const PIPELINE_STEPS = [
  { key: "scout", label: "Scout", icon: Eye, desc: "Veri Toplama" },
  { key: "pattern_matcher", label: "Pattern", icon: Grid3X3, desc: "Benzerlik Tarama" },
  { key: "strategist", label: "Strategist", icon: TrendingUp, desc: "Sinyal Üretimi" },
  { key: "ghost_simulator", label: "Ghost Sim", icon: Ghost, desc: "Paper Trade" },
  { key: "auditor", label: "Auditor", icon: Shield, desc: "Hata Analizi" },
  { key: "brain", label: "Brain", icon: Brain, desc: "Öğrenme" },
];

function StatusIcon({ status }: { status: string }) {
  if (status === "running") return <CheckCircle size={10} className="text-bull" />;
  if (status === "stale") return <Clock size={10} className="text-warn" />;
  return <AlertCircle size={10} className="text-bear" />;
}

export default function AgentFlow() {
  const { data } = useAgentFlow();
  const pipeline = data?.pipeline || {};

  return (
    <div className="flex flex-col h-full">
      <div className="px-4 py-2 border-b border-surface-border bg-surface-card/40 flex items-center gap-2">
        <Grid3X3 size={13} className="text-accent" />
        <span className="text-[11px] font-semibold text-gray-400 uppercase tracking-wider">
          Ajan Akış Diyagramı
        </span>
      </div>

      {/* Flow diagram */}
      <div className="flex-1 overflow-y-auto px-3 py-3">
        <div className="flex flex-col gap-0.5">
          {PIPELINE_STEPS.map((step, i) => {
            const agent = (pipeline as any)[step.key];
            const status = agent?.status || "unknown";
            const Icon = step.icon;
            const recentItems = agent?.recent || agent?.recent_signals || agent?.recent_sims || [];

            return (
              <div key={step.key}>
                {/* Step card */}
                <div className={`flex items-center gap-3 px-3 py-2.5 rounded-lg border transition-colors ${
                  status === "running"
                    ? "border-bull/20 bg-bull/5"
                    : status === "stale"
                    ? "border-warn/20 bg-warn/5"
                    : "border-surface-border bg-surface-card/30"
                }`}>
                  <div className={`p-1.5 rounded-md ${
                    status === "running" ? "bg-bull/10 text-bull" :
                    status === "stale" ? "bg-warn/10 text-warn" :
                    "bg-gray-700 text-gray-400"
                  }`}>
                    <Icon size={16} />
                  </div>
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-1.5">
                      <StatusIcon status={status} />
                      <span className="text-xs font-medium text-gray-200">{step.label}</span>
                    </div>
                    <p className="text-[10px] text-gray-500 mt-0.5">{step.desc}</p>
                  </div>
                  {/* Mini activity indicator */}
                  {recentItems.length > 0 && (
                    <span className="text-[10px] font-mono text-accent bg-accent/10 px-1.5 py-0.5 rounded">
                      {recentItems.length}
                    </span>
                  )}
                </div>

                {/* Connector arrow */}
                {i < PIPELINE_STEPS.length - 1 && (
                  <div className="flex items-center justify-center py-0.5">
                    <ArrowDown size={14} className="text-gray-600" />
                  </div>
                )}
              </div>
            );
          })}
        </div>

        {/* Recent pipeline activity */}
        {data && (
          <div className="mt-3 pt-3 border-t border-surface-border">
            <p className="text-[10px] text-gray-500 uppercase mb-2">Son Aktivite</p>
            <div className="space-y-1">
              {(pipeline as any).strategist?.recent_signals?.slice(0, 3).map((s: any) => (
                <div key={s.id} className="flex items-center gap-2 px-2 py-1 rounded bg-surface-card/30 text-[11px]">
                  <ArrowRight size={10} className="text-accent" />
                  <span className="text-gray-400">{s.symbol?.replace("USDT", "")}</span>
                  <span className="text-gray-500">{s.signal_type}</span>
                  <span className={`ml-auto font-mono ${s.status === "processed" ? "text-bull" : "text-gray-500"}`}>
                    {s.status}
                  </span>
                </div>
              ))}
              {(pipeline as any).ghost_simulator?.recent_sims?.slice(0, 3).map((s: any) => (
                <div key={s.id} className="flex items-center gap-2 px-2 py-1 rounded bg-surface-card/30 text-[11px]">
                  <Ghost size={10} className="text-gray-500" />
                  <span className="text-gray-400">{s.symbol?.replace("USDT", "")}</span>
                  <span className={`ml-auto font-mono ${s.pnl > 0 ? "text-bull" : s.pnl < 0 ? "text-bear" : "text-gray-500"}`}>
                    {s.status === "open" ? "açık" : s.pnl != null ? `$${Number(s.pnl).toFixed(2)}` : "—"}
                  </span>
                </div>
              ))}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
