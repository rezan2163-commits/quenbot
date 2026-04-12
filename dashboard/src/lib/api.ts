import useSWR from "swr";

const API = process.env.NEXT_PUBLIC_API_URL || "";

async function fetcher<T>(url: string): Promise<T> {
  const res = await fetch(url);
  if (!res.ok) throw new Error(`API ${res.status}`);
  return res.json();
}

/* ─── Types ─── */

export interface AgentInfo {
  status: string;
  source_status: string | null;
  last_heartbeat: string | null;
  age_seconds: number | null;
  metadata: Record<string, any> | null;
}

export interface AgentsResponse {
  agents: Record<string, AgentInfo>;
  summary: { signals: number; movements: number };
}

export interface SystemSummary {
  mode: string;
  health: string;
  llm: { ok: boolean; model: string };
  resources: { cpu: number; ram: number; ram_mb: string; disk: number };
  state: { mode: string; trades: number; pnl: number };
  brain: { patterns: number; accuracy: number };
  pattern_matcher: { ok: boolean; scans: number; matches: number; best_similarity: number };
  warnings: { level: string; comp: string; msg: string }[];
  uptime: number;
}

export interface Signal {
  id: number;
  symbol: string;
  signal_type: string;
  direction: string;
  confidence: number;
  price: number;
  status: string;
  timestamp: string;
  metadata: Record<string, any>;
}

export interface Simulation {
  id: number;
  symbol: string;
  entry_price: number;
  side: string;
  status: string;
  pnl: number | null;
  pnl_pct: number | null;
  entry_time: string;
  exit_time: string | null;
  exit_price: number | null;
}

export interface PriceCandle {
  minute: string;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
}

export interface LivePrice {
  symbol: string;
  exchange: string;
  price: number;
  timestamp: string;
}

export interface TopMover {
  symbol: string;
  open_price: number;
  current_price: number;
  change_pct: number;
  timestamp: string;
}

export interface DashboardSummary {
  total_trades: number;
  active_signals: number;
  open_simulations: number;
  total_pnl: number;
  win_rate: number;
  closed_simulations: number;
  winning_simulations: number;
  losing_simulations: number;
}

export interface ChatMessage {
  id: number;
  role: string;
  message: string;
  agent_name: string;
  created_at: string;
}

export interface TradeTimeline {
  minute: string;
  count: number;
  volume: number;
}

/* ─── Hooks ─── */

export function useAgents() {
  return useSWR<AgentsResponse>(`${API}/api/agents/status`, fetcher, {
    refreshInterval: 5000,
  });
}

export function useSystemSummary() {
  return useSWR<SystemSummary>(`${API}/api/system/summary`, fetcher, {
    refreshInterval: 5000,
  });
}

export function useDashboardSummary() {
  return useSWR<DashboardSummary>(`${API}/api/dashboard/summary`, fetcher, {
    refreshInterval: 5000,
  });
}

export function useSignals() {
  return useSWR<Signal[]>(`${API}/api/signals`, fetcher, {
    refreshInterval: 8000,
  });
}

export function useSimulations() {
  return useSWR<Simulation[]>(`${API}/api/simulations`, fetcher, {
    refreshInterval: 8000,
  });
}

export function usePriceHistory(symbol: string) {
  return useSWR<PriceCandle[]>(
    symbol ? `${API}/api/analytics/price-history/${symbol}` : null,
    fetcher,
    { refreshInterval: 15000 }
  );
}

export function useLivePrices() {
  return useSWR<LivePrice[]>(`${API}/api/live/prices`, fetcher, {
    refreshInterval: 3000,
  });
}

export function useTopMovers() {
  return useSWR<TopMover[]>(`${API}/api/analytics/top-movers`, fetcher, {
    refreshInterval: 10000,
  });
}

export function useTradeTimeline() {
  return useSWR<TradeTimeline[]>(`${API}/api/analytics/trade-timeline`, fetcher, {
    refreshInterval: 10000,
  });
}

export function useChatMessages() {
  return useSWR<ChatMessage[]>(`${API}/api/chat/messages`, fetcher, {
    refreshInterval: 3000,
  });
}

export async function sendChat(message: string) {
  const res = await fetch(`${API}/api/chat`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ message }),
  });
  return res.json();
}

export async function setDirective(master_directive: string) {
  const res = await fetch(`${API}/api/directives`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ master_directive }),
  });
  return res.json();
}

export async function getDirectives() {
  const res = await fetch(`${API}/api/directives`);
  return res.json();
}
