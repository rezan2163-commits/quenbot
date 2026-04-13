import useSWR from "swr";

const API = process.env.NEXT_PUBLIC_API_URL || "";

async function fetcher<T>(url: string): Promise<T> {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 8000);
  try {
    const res = await fetch(url, { signal: controller.signal });
    if (!res.ok) throw new Error(`API ${res.status}`);
    return res.json();
  } finally {
    clearTimeout(timeout);
  }
}

export const swrConfig = {
  onErrorRetry: (_error: Error, _key: string, _config: any, revalidate: any, { retryCount }: { retryCount: number }) => {
    if (retryCount >= 2) return;
    setTimeout(() => revalidate({ retryOnFocus: false }), 30000);
  },
  shouldRetryOnError: true,
  dedupingInterval: 10000,
  revalidateOnFocus: false,
  revalidateOnReconnect: false,
};

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
  signal_time?: string;
  entry_price?: number;
  current_price_at_signal?: number;
  target_price?: number;
  target_pct?: number;
  estimated_duration_to_target_minutes?: number;
  exchange?: string;
  market_type?: string;
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
  market_type: string;
  price: number;
  price_text: string;
  timestamp: string;
}

export interface TopMover {
  symbol: string;
  open_price: number;
  current_price: number;
  change_pct: number;
  timestamp: string;
}

export interface WatchlistItem {
  id: number;
  symbol: string;
  exchange: string;
  market_type: string;
  active: boolean;
  created_at: string;
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
    refreshInterval: 15000,
  });
}

export function useSystemSummary() {
  return useSWR<SystemSummary>(`${API}/api/system/summary`, fetcher, {
    refreshInterval: 15000,
  });
}

export function useDashboardSummary() {
  return useSWR<DashboardSummary>(`${API}/api/dashboard/summary`, fetcher, {
    refreshInterval: 15000,
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

export function usePriceHistory(symbol: string, tf: string = "5m") {
  return useSWR<PriceCandle[]>(
    symbol ? `${API}/api/analytics/price-history/${symbol}?tf=${encodeURIComponent(tf)}` : null,
    fetcher,
    { refreshInterval: 15000 }
  );
}

export function useLivePrices() {
  return useSWR<LivePrice[]>(`${API}/api/live/prices`, fetcher, {
    refreshInterval: 10000,
  });
}

export function useTopMovers() {
  return useSWR<TopMover[]>(`${API}/api/analytics/top-movers`, fetcher, {
    refreshInterval: 10000,
  });
}

export function useWatchlist() {
  return useSWR<WatchlistItem[]>(`${API}/api/watchlist`, fetcher, {
    refreshInterval: 15000,
  });
}

export async function addWatchlistCoin(symbol: string, opts?: { exchange?: string; market_type?: string }) {
  const res = await fetch(`${API}/api/watchlist/add`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      symbol,
      exchange: opts?.exchange || "both",
      market_type: opts?.market_type || "both",
    }),
  });
  if (!res.ok) throw new Error(`API ${res.status}`);
  return res.json();
}

export function useTradeTimeline() {
  return useSWR<TradeTimeline[]>(`${API}/api/analytics/trade-timeline`, fetcher, {
    refreshInterval: 60000,
    shouldRetryOnError: false,
  });
}

export function useChatMessages() {
  return useSWR<ChatMessage[]>(`${API}/api/chat/messages`, fetcher, {
    refreshInterval: 15000,
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

/* ─── Backtest Types & Hooks ─── */

export interface BacktestScore {
  symbol: string;
  signal_type: string;
  total: number;
  wins: number;
  losses: number;
  avg_pnl_pct: number;
  success_rate: number;
}

export interface BacktestRecent {
  id: number;
  symbol: string;
  side: string;
  entry_price: number;
  exit_price: number;
  pnl: number;
  pnl_pct: number;
  entry_time: string;
  exit_time: string;
  signal_type: string;
  confidence: number;
  signal_metadata?: Record<string, any>;
  success: boolean;
}

export interface SelfCorrectionStatus {
  recent_performance: {
    recent_trades: number;
    recent_wins: number;
    recent_win_rate: number;
    avg_pnl_pct: number;
  };
  needs_correction: boolean;
  corrections: Array<{
    id: number;
    signal_type: string;
    failure_type: string;
    adjustment_key: string;
    adjustment_value: string;
    reason: string;
    applied: boolean;
  }>;
  strategy_state: Array<{ state_key: string; state_value: any; updated_at: string }>;
  rca_summary: Array<{ failure_type: string; count: number; avg_confidence: number }>;
}

export interface StrategyEvent {
  state: Array<{ state_key: string; state_value: any; updated_at: string }>;
  audits: Array<{
    id: number;
    timestamp: string;
    total_simulations: number;
    successful: number;
    failed: number;
    success_rate: number;
    avg_win_pct: number;
    avg_loss_pct: number;
  }>;
}

export interface AgentFlowData {
  agents: Record<string, any>;
  pipeline: Record<string, {
    status: string;
    lastBeat: string;
    recent?: any[];
    recent_signals?: any[];
    recent_sims?: any[];
  }>;
}

export interface EventLogItem {
  type: string;
  source: string;
  data_keys: string[];
  data_preview?: Record<string, any>;
  timestamp: number;
  priority?: number;
}

export interface SystemEventStats {
  total_events: number;
  subscriber_count: number;
  topics: Record<string, number>;
  recent_events: EventLogItem[];
}

export interface EquityPoint {
  time: string;
  pnl: number;
  cumulative_pnl: number;
}

export function useBacktestScores() {
  return useSWR<BacktestScore[]>(`${API}/api/backtest/scores`, fetcher, {
    refreshInterval: 15000,
  });
}

export function useBacktestRecent() {
  return useSWR<BacktestRecent[]>(`${API}/api/backtest/recent`, fetcher, {
    refreshInterval: 10000,
  });
}

export function useSelfCorrection() {
  return useSWR<SelfCorrectionStatus>(`${API}/api/selfcorrection/status`, fetcher, {
    refreshInterval: 60000,
    shouldRetryOnError: false,
  });
}

export function useStrategyEvents() {
  return useSWR<StrategyEvent>(`${API}/api/strategy/events`, fetcher, {
    refreshInterval: 60000,
    shouldRetryOnError: false,
  });
}

export function useAgentFlow() {
  return useSWR<AgentFlowData>(`${API}/api/agents/flow`, fetcher, {
    refreshInterval: 30000,
    shouldRetryOnError: false,
  });
}

export function useEquityCurve() {
  return useSWR<EquityPoint[]>(`${API}/api/analytics/equity-curve`, fetcher, {
    refreshInterval: 15000,
  });
}

export function useSystemEvents() {
  return useSWR<SystemEventStats>(`${API}/api/system/events`, fetcher, {
    refreshInterval: 2000,
    dedupingInterval: 1000,
  });
}

/* ─── Pattern Library Types & Hooks ─── */

export interface PatternRecord {
  id: number;
  symbol: string;
  exchange: string;
  market_type: string;
  snapshot_data: any;
  outcome_15m: number | null;
  outcome_1h: number | null;
  outcome_4h: number | null;
  outcome_1d: number | null;
  created_at: string;
}

export function usePatterns(symbol?: string) {
  const key = symbol
    ? `${API}/api/brain/patterns?symbol=${symbol}`
    : `${API}/api/brain/patterns`;
  return useSWR<PatternRecord[]>(key, fetcher, { refreshInterval: 15000 });
}

/* ─── Signal History ─── */

export interface SignalHistoryItem {
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

export function useSignalHistory(symbol?: string, status?: string) {
  const params = new URLSearchParams();
  if (symbol) params.set("symbol", symbol);
  if (status) params.set("status", status);
  const qs = params.toString();
  return useSWR<SignalHistoryItem[]>(
    `${API}/api/signals/history${qs ? `?${qs}` : ""}`,
    fetcher,
    { refreshInterval: 10000 }
  );
}

/* ─── Learning Log ─── */

export interface LearningLogEntry {
  id: number;
  signal_type: string;
  was_correct: boolean;
  pnl_pct: number;
  context: any;
  created_at: string;
}

export interface LearningStats {
  total: number;
  correct: number;
  accuracy: number;
  avg_pnl: number;
  daily_accuracy: Array<{ day: string; total: number; correct: number }>;
  by_type: Array<{ signal_type: string; total: number; correct: number; avg_pnl: number; total_pnl: number }>;
}

export function useLearningLog() {
  return useSWR<LearningLogEntry[]>(`${API}/api/brain/learning-log`, fetcher, {
    refreshInterval: 10000,
  });
}

export function useLearningStats() {
  return useSWR<LearningStats>(`${API}/api/brain/learning-stats`, fetcher, {
    refreshInterval: 15000,
  });
}
