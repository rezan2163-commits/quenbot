import { useEffect, useRef, useState } from "react";
import useSWR from "swr";

export type ModuleOrgan = "agent" | "brain" | "detector" | "fusion" | "learning" | "safety" | "runtime";
export type ModuleStatusKind = "healthy" | "slow" | "unhealthy" | "dormant" | "disabled" | "unknown";
export type EdgeActivityBucket = "hot" | "warm" | "cool" | "silent";
export type ConnectionState = "live" | "polling" | "offline";

export interface VitalSign {
  value: number | null;
  unit?: string;
  trend?: number[];
  status?: "ok" | "warn" | "crit" | "unknown";
  note?: string;
}

export interface VitalSigns {
  [key: string]: VitalSign;
}

export interface ModuleStatus {
  id: string;
  display_name: string;
  organ: ModuleOrgan;
  status: ModuleStatusKind;
  health_score: number;
  last_heartbeat_ts: number | null;
  throughput_per_sec: number;
  error_rate: number | null;
  latency_p95_ms: number | null;
  dependencies: string[];
  flag_gated: boolean;
  dormant: boolean;
  warnings: string[];
  last_event_preview?: string | null;
  expected_period_sec: number;
}

export interface EdgeActivity {
  from: string;
  to: string;
  rate_per_sec: number;
  bucket: EdgeActivityBucket;
}

export interface QwenPulse {
  phase: "0" | "1" | "2" | "3";
  directives_last_hour: number;
  rejection_rate: number;
  last_directive_ts: number | null;
  ifi: number | null;
  lockdown: boolean;
}

export interface MissionSnapshot {
  generated_at: number;
  overall_health_score: number;
  vital_signs: VitalSigns;
  modules: ModuleStatus[];
  edges: EdgeActivity[];
  qwen_pulse: QwenPulse;
}

export interface AutopsyBundle {
  module_id: string;
  display_name: string;
  organ: ModuleOrgan;
  status: ModuleStatusKind;
  health_score: number;
  warnings: string[];
  recent_events: Array<{ ts: number; type: string; preview: string }>;
  dependencies: Array<{ id: string; status: ModuleStatusKind; health_score: number }>;
  diagnosis: string | null;
  generated_at: number;
}

const API = process.env.NEXT_PUBLIC_API_URL || "";

async function jsonFetch<T>(url: string, init?: RequestInit): Promise<T> {
  const res = await fetch(url, init);
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
}

export function fetchAutopsy(moduleId: string): Promise<AutopsyBundle> {
  return jsonFetch<AutopsyBundle>(`${API}/api/mission-control/autopsy/${encodeURIComponent(moduleId)}`);
}

export async function restartModule(moduleId: string, adminToken?: string): Promise<{ ok: boolean; message?: string }> {
  const headers: Record<string, string> = {};
  if (adminToken) headers["X-Admin-Token"] = adminToken;
  const res = await fetch(`${API}/api/mission-control/restart/${encodeURIComponent(moduleId)}`, {
    method: "POST",
    headers,
  });
  const body = await res.json().catch(() => ({}));
  return { ok: res.ok, message: body?.message || body?.error };
}

export function useMissionControlStream(): {
  snapshot: MissionSnapshot | null;
  connection: ConnectionState;
  error: string | null;
  reload: () => void;
} {
  const [snapshot, setSnapshot] = useState<MissionSnapshot | null>(null);
  const [connection, setConnection] = useState<ConnectionState>("offline");
  const [error, setError] = useState<string | null>(null);
  const esRef = useRef<EventSource | null>(null);
  const failCountRef = useRef(0);

  const swr = useSWR<MissionSnapshot>(
    connection === "polling" ? `${API}/api/mission-control/snapshot` : null,
    (url: string) => jsonFetch<MissionSnapshot>(url),
    { refreshInterval: 2000, dedupingInterval: 500 }
  );

  useEffect(() => {
    if (connection === "polling" && swr.data) {
      setSnapshot(swr.data);
    }
  }, [connection, swr.data]);

  useEffect(() => {
    let cancelled = false;

    const connect = () => {
      if (cancelled) return;
      try {
        const es = new EventSource(`${API}/api/mission-control/stream`);
        esRef.current = es;

        es.onopen = () => {
          if (cancelled) return;
          setConnection("live");
          setError(null);
          failCountRef.current = 0;
        };

        es.onmessage = (ev) => {
          if (cancelled) return;
          try {
            const data = JSON.parse(ev.data) as MissionSnapshot;
            setSnapshot(data);
            setConnection("live");
            setError(null);
          } catch (e) {
            /* ignore malformed frame */
          }
        };

        es.onerror = () => {
          if (cancelled) return;
          es.close();
          esRef.current = null;
          failCountRef.current += 1;
          if (failCountRef.current >= 2) {
            setConnection("polling");
          } else {
            setConnection("offline");
          }
          // Try to reconnect SSE after a delay
          setTimeout(() => {
            if (!cancelled) connect();
          }, 5000);
        };
      } catch (e) {
        setConnection("polling");
        setError(e instanceof Error ? e.message : "SSE unavailable");
      }
    };

    connect();

    return () => {
      cancelled = true;
      if (esRef.current) {
        esRef.current.close();
        esRef.current = null;
      }
    };
  }, []);

  const reload = () => {
    swr.mutate();
  };

  return { snapshot, connection, error, reload };
}

export const STATUS_COLORS: Record<ModuleStatusKind, { fg: string; bg: string; border: string; label: string }> = {
  healthy: { fg: "text-emerald-300", bg: "bg-emerald-500/10", border: "border-emerald-500/40", label: "Sağlıklı" },
  slow: { fg: "text-amber-300", bg: "bg-amber-500/10", border: "border-amber-500/40", label: "Yavaş" },
  unhealthy: { fg: "text-red-300", bg: "bg-red-500/10", border: "border-red-500/40", label: "Arızalı" },
  dormant: { fg: "text-slate-400", bg: "bg-slate-500/10", border: "border-slate-500/40", label: "Uyku" },
  disabled: { fg: "text-zinc-500", bg: "bg-zinc-500/10", border: "border-zinc-500/40", label: "Kapalı" },
  unknown: { fg: "text-gray-400", bg: "bg-gray-500/10", border: "border-gray-500/40", label: "Bilinmiyor" },
};

export const ORGAN_LABELS: Record<ModuleOrgan, string> = {
  agent: "Ajanlar",
  brain: "Beyin",
  detector: "Dedektörler",
  fusion: "Füzyon",
  learning: "Öğrenme",
  safety: "Güvenlik",
  runtime: "Çalışma",
};

export const ORGAN_ACCENT: Record<ModuleOrgan, string> = {
  agent: "#38bdf8",
  brain: "#a855f7",
  detector: "#f59e0b",
  fusion: "#22d3ee",
  learning: "#14b8a6",
  safety: "#f43f5e",
  runtime: "#64748b",
};

export function formatAge(ts: number | null): string {
  if (!ts) return "—";
  const sec = Math.max(0, Math.floor(Date.now() / 1000 - ts));
  if (sec < 2) return "şimdi";
  if (sec < 60) return `${sec}sn`;
  if (sec < 3600) return `${Math.floor(sec / 60)}dk`;
  return `${Math.floor(sec / 3600)}sa`;
}
