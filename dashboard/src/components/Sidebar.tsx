"use client";

import { useAgents, useSystemSummary, AgentInfo } from "@/lib/api";
import {
  Activity,
  Cpu,
  HardDrive,
  MemoryStick,
  Brain,
  Eye,
  TrendingUp,
  Ghost,
  Shield,
  Grid3X3,
  MessageSquare,
  Zap,
} from "lucide-react";

const AGENT_META: Record<string, { label: string; icon: React.ElementType; desc: string }> = {
  scout:           { label: "Scout",          icon: Eye,          desc: "Veri toplama & indikatörler" },
  strategist:      { label: "Strategist",     icon: TrendingUp,   desc: "Sinyal üretimi" },
  ghost_simulator: { label: "Ghost Sim",      icon: Ghost,        desc: "Paper-trade simülasyonu" },
  auditor:         { label: "Auditor",        icon: Shield,       desc: "Hata analizi & düzeltme" },
  pattern_matcher: { label: "PatternMatcher", icon: Grid3X3,      desc: "Benzerlik taraması" },
  brain:           { label: "Brain",          icon: Brain,        desc: "Öğrenme & hafıza" },
  chat_engine:     { label: "Chat Engine",    icon: MessageSquare, desc: "Doğal dil sohbet" },
  llm_brain:       { label: "LLM",           icon: Zap,          desc: "SuperGemma model durumu" },
  system:          { label: "System",         icon: Activity,     desc: "Orkestratör" },
};

function StatusDot({ status }: { status: string }) {
  const color =
    status === "running" ? "bg-bull" :
    status === "stale" ? "bg-warn" :
    status === "error" ? "bg-bear" :
    "bg-gray-500";
  return (
    <span className={`inline-block w-2 h-2 rounded-full ${color} ${status === "running" ? "pulse-dot" : ""}`} />
  );
}

function AgentCard({ name, info }: { name: string; info: AgentInfo }) {
  const meta = AGENT_META[name] || { label: name, icon: Activity, desc: "" };
  const Icon = meta.icon;
  const age = info.age_seconds != null ? `${info.age_seconds}s önce` : "—";

  return (
    <div className="flex items-start gap-3 p-3 rounded-lg bg-surface-card/60 hover:bg-surface-hover transition-colors">
      <div className="mt-0.5 p-1.5 rounded-md bg-accent/10 text-accent">
        <Icon size={16} />
      </div>
      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-2">
          <StatusDot status={info.status} />
          <span className="text-sm font-medium truncate">{meta.label}</span>
        </div>
        <p className="text-[11px] text-gray-500 mt-0.5 truncate">{meta.desc}</p>
        <p className="text-[11px] text-gray-600 mt-0.5">{age}</p>
      </div>
    </div>
  );
}

export default function Sidebar() {
  const { data: agentsData } = useAgents();
  const { data: sys } = useSystemSummary();

  const agents = agentsData?.agents || {};
  const agentEntries = Object.entries(agents).filter(
    ([k]) => !["system_resources", "event_bus", "system"].includes(k)
  );
  const runningCount = agentEntries.filter(([, v]) => v.status === "running").length;

  return (
    <aside className="flex h-full w-full flex-shrink-0 flex-col overflow-y-auto border-r border-surface-border bg-surface lg:h-screen lg:w-64">
      {/* Header */}
      <div className="p-4 border-b border-surface-border">
        <div className="flex items-center gap-2">
          <div className="w-8 h-8 rounded-lg bg-accent/20 flex items-center justify-center">
            <Zap size={18} className="text-accent" />
          </div>
          <div>
            <h1 className="text-sm font-bold tracking-wide">QuenBot</h1>
            <p className="text-[10px] text-gray-500 uppercase tracking-widest">
              {sys?.mode || "—"}
            </p>
          </div>
        </div>
      </div>

      {/* Resource bars */}
      {sys && (
        <div className="px-4 py-3 border-b border-surface-border space-y-2">
          <ResourceBar icon={Cpu} label="CPU" value={sys.resources?.cpu ?? 0} />
          <ResourceBar icon={MemoryStick} label="RAM" value={sys.resources?.ram ?? 0} extra={sys.resources?.ram_mb} />
          <ResourceBar icon={HardDrive} label="Disk" value={sys.resources?.disk ?? 0} />
          <div className="flex items-center justify-between text-[11px] text-gray-500 pt-1">
            <span>LLM: {sys.llm?.ok ? "✓" : "✗"} {sys.llm?.model ?? "—"}</span>
          </div>
          <div className="flex items-center justify-between text-[11px] text-gray-500">
            <span>Uptime: {formatUptime(sys.uptime ?? 0)}</span>
          </div>
        </div>
      )}

      {/* Agent list */}
      <div className="flex-1 overflow-y-auto px-3 py-3 space-y-1.5">
        <div className="flex items-center justify-between px-1 mb-2">
          <span className="text-[11px] font-semibold text-gray-400 uppercase tracking-wider">
            Ajanlar
          </span>
          <span className="text-[11px] text-accent font-mono">
            {runningCount}/{agentEntries.length}
          </span>
        </div>
        {agentEntries.map(([name, info]) => (
          <AgentCard key={name} name={name} info={info} />
        ))}
      </div>

      {/* Brain stats */}
      {sys?.brain && (
        <div className="px-4 py-3 border-t border-surface-border">
          <div className="text-[11px] text-gray-500 space-y-1">
            <div className="flex justify-between">
              <span>Pattern</span>
              <span className="text-gray-300">{sys.brain?.patterns ?? 0}</span>
            </div>
            <div className="flex justify-between">
              <span>Doğruluk</span>
              <span className="text-accent">%{sys.brain?.accuracy ?? 0}</span>
            </div>
          </div>
        </div>
      )}
    </aside>
  );
}

function ResourceBar({
  icon: Icon,
  label,
  value,
  extra,
}: {
  icon: React.ElementType;
  label: string;
  value: number;
  extra?: string;
}) {
  const pct = Math.min(100, Math.max(0, value));
  const color = pct > 85 ? "bg-bear" : pct > 65 ? "bg-warn" : "bg-bull";
  return (
    <div className="flex items-center gap-2">
      <Icon size={12} className="text-gray-500 flex-shrink-0" />
      <span className="text-[11px] text-gray-400 w-8">{label}</span>
      <div className="flex-1 h-1.5 rounded-full bg-surface-hover overflow-hidden">
        <div className={`h-full rounded-full ${color} transition-all`} style={{ width: `${pct}%` }} />
      </div>
      <span className="text-[11px] text-gray-500 w-10 text-right font-mono">
        {extra || `%${Math.round(pct)}`}
      </span>
    </div>
  );
}

function formatUptime(seconds: number): string {
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  return h > 0 ? `${h}s ${m}dk` : `${m}dk`;
}
