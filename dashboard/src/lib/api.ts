import useSWR from "swr";

const API = process.env.NEXT_PUBLIC_API_URL || "";

// Connection state tracking
let lastSuccessfulFetch = Date.now();
let connectionHealthy = true;

async function fetcher<T>(url: string): Promise<T> {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 12000); // Increased timeout
  try {
    const res = await fetch(url, { signal: controller.signal });
    if (!res.ok) throw new Error(`API ${res.status}`);
    const data = await res.json();
    lastSuccessfulFetch = Date.now();
    connectionHealthy = true;
    return data;
  } catch (error) {
    // Track connection health
    if (Date.now() - lastSuccessfulFetch > 30000) {
      connectionHealthy = false;
    }
    throw error;
  } finally {
    clearTimeout(timeout);
  }
}

// Check if connection is healthy
export function isConnectionHealthy(): boolean {
  return connectionHealthy && Date.now() - lastSuccessfulFetch < 60000;
}

export const swrConfig = {
  onErrorRetry: (error: Error, key: string, _config: any, revalidate: any, { retryCount }: { retryCount: number }) => {
    // More retries for integration endpoints
    const maxRetries = key.includes('/integration/') ? 4 : 2;
    if (retryCount >= maxRetries) return;
    // Shorter retry interval for connection issues
    const retryDelay = retryCount < 2 ? 5000 : 15000;
    setTimeout(() => revalidate({ retryOnFocus: false }), retryDelay);
  },
  shouldRetryOnError: true,
  dedupingInterval: 8000,
  revalidateOnFocus: true,
  revalidateOnReconnect: true,
  errorRetryInterval: 5000,
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
  llm_stats?: { total_calls?: number; total_errors?: number };
  resources: { cpu: number; ram: number; ram_mb: string; disk: number };
  state: { mode: string; trades: number; pnl: number };
  brain: { patterns: number; accuracy: number };
  pattern_matcher: { ok: boolean; scans: number; matches: number; best_similarity: number };
  mamis?: { ok: boolean; bars: number; alerts: number; classifications: number; signals: number; last_pattern?: string | null };
  warnings: { level: string; comp: string; msg: string }[];
  uptime: number;
}

export interface MamisBar {
  symbol: string;
  bar_index: number;
  total_volume: number;
  cumulative_volume_delta: number;
  ofi_normalized: number;
  vpin: number;
  volatility: number;
  spread_bps: number;
  cancel_to_trade_ratio: number;
  ended_at: string;
}

export interface MamisClassification {
  symbol: string;
  pattern_type: string;
  confidence: number;
  direction_hint: string;
  estimated_volatility: number;
  reason: string;
  event_bar: MamisBar;
}

export interface MamisSignal {
  timestamp: string;
  symbol: string;
  signal_direction: string;
  confidence_score: number;
  detected_pattern_type: string;
  estimated_volatility: number;
  position_size: number;
  metadata: Record<string, any>;
}

export interface MamisStatus {
  health: {
    healthy: boolean;
    sentinel?: Record<string, any>;
    forensic?: Record<string, any>;
    strategist?: Record<string, any>;
  };
  bars: MamisBar[];
  classifications: MamisClassification[];
  signals: MamisSignal[];
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
  source?: string;
  source_model?: string;
  expires_at?: string;
  exchange?: string;
  market_type?: string;
  status: string;
  timestamp: string;
  metadata: Record<string, any>;
}

export interface SignalTargetHorizon {
  label: string;
  eta_minutes: number;
  target_pct: number;
  target_price: number;
  strength: number;
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

export interface ChatResponse {
  success: boolean;
  message: string;
  assistant?: {
    name: string;
    model: string;
    role: string;
  };
  routed_actions?: Array<Record<string, any>>;
  timestamp?: string;
}

export interface ChatActionView {
  type: string;
  [key: string]: any;
}

export interface DirectiveStatus {
  master_directive: string;
  agent_overrides: Record<string, string>;
  updated_at?: string;
  history_count?: number;
}

export interface CodeOperatorEditPreview {
  path: string;
  reason?: string;
  old_preview?: string;
  new_preview?: string;
}

export interface CodeOperatorTask {
  id: string;
  prompt: string;
  requested_by?: string;
  source?: string;
  mode: string;
  status: string;
  summary?: string;
  clarification?: string;
  error?: string;
  created_at: string;
  updated_at?: string;
  plan?: {
    summary?: string;
    needs_clarification?: boolean;
    clarification?: string;
    paths?: string[];
    validation_commands?: string[];
  };
  selected_files?: string[];
  preview?: CodeOperatorEditPreview[];
  validation_commands?: string[];
  validation?: Array<{ command: string; status: string; output?: string; returncode?: number }>;
  apply_result?: { ok: boolean; changed_files?: string[]; backup_dir?: string; error?: string };
}

export interface CodeOperatorStatus {
  enabled: boolean;
  available?: boolean;
  repo_root?: string;
  model?: string;
  active_task_id?: string | null;
  queued?: number;
  recent_tasks?: CodeOperatorTask[];
}

export interface LlmStatus {
  healthy: boolean;
  active_model?: string;
  available_models?: string[];
  call_count?: number;
  llm_stats?: Record<string, any>;
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

export function useDirectiveStatus() {
  return useSWR<DirectiveStatus>(`${API}/api/directives`, fetcher, {
    refreshInterval: 30000,
  });
}

export function useCodeOperatorStatus() {
  return useSWR<CodeOperatorStatus>(`${API}/api/code/status`, fetcher, {
    refreshInterval: 8000,
  });
}

export function useCodeOperatorTasks(limit: number = 20) {
  return useSWR<{ items: CodeOperatorTask[] }>(`${API}/api/code/tasks?limit=${encodeURIComponent(String(limit))}`, fetcher, {
    refreshInterval: 8000,
  });
}

export async function createCodeTask(prompt: string, mode: "preview" | "apply" = "preview") {
  const res = await fetch(`${API}/api/code/tasks`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ prompt, mode, requested_by: "dashboard", source: "dashboard" }),
  });
  if (!res.ok) throw new Error(`Code task ${res.status}`);
  return res.json() as Promise<CodeOperatorTask>;
}

export async function applyCodeTask(taskId: string) {
  const res = await fetch(`${API}/api/code/tasks/${encodeURIComponent(taskId)}/apply`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
  });
  if (!res.ok) throw new Error(`Code task apply ${res.status}`);
  return res.json();
}

export async function setDirective(text: string) {
  const res = await fetch(`${API}/api/directives`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ master_directive: text }),
  });
  if (!res.ok) throw new Error(`Directive ${res.status}`);
  return res.json();
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

export async function dismissSignal(signalId: number) {
  const res = await fetch(`${API}/api/signals/${signalId}/dismiss`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
  });

  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    throw new Error(data?.error || `API ${res.status}`);
  }

  return res.json();
}

export async function clearSignals(signalIds: number[]) {
  const res = await fetch(`${API}/api/signals/clear`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ ids: signalIds }),
  });

  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    throw new Error(data?.error || `API ${res.status}`);
  }

  return res.json();
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
  const attempts = [
    { exchange: opts?.exchange || "both", market_type: opts?.market_type || "both" },
    { exchange: "all", market_type: "both" },
    { exchange: "all", market_type: "spot" },
  ];

  let lastError = "";
  for (const attempt of attempts) {
    const res = await fetch(`${API}/api/watchlist/add`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        symbol,
        exchange: attempt.exchange,
        market_type: attempt.market_type,
      }),
    });

    if (res.ok) return res.json();

    try {
      const data = await res.json();
      lastError = data?.error || `API ${res.status}`;
    } catch {
      lastError = `API ${res.status}`;
    }
  }

  throw new Error(lastError || "Coin eklenemedi");
}

export async function removeWatchlistCoin(symbol: string, opts?: { exchange?: string; market_type?: string }) {
  const res = await fetch(`${API}/api/watchlist/remove`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      symbol,
      exchange: opts?.exchange || "all",
      market_type: opts?.market_type || "both",
    }),
  });

  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    throw new Error(data?.error || `API ${res.status}`);
  }

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

export async function sendChat(message: string): Promise<ChatResponse> {
  const res = await fetch(`${API}/api/chat`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ message }),
  });
  return res.json();
}

export async function clearChatMessages() {
  const res = await fetch(`${API}/api/chat/messages`, {
    method: "DELETE",
  });
  if (!res.ok) {
    throw new Error(`Chat clear ${res.status}`);
  }
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

export interface IntegrationAgent {
  name: string;
  status: string;
  source_status: string;
  last_heartbeat: string;
  age_seconds: number;
  activity_score: number;
  metadata: Record<string, any>;
}

export interface IntegrationModel {
  name: string;
  owner: string;
  activity: number;
  source: string;
}

export interface IntegrationSignalPerformance {
  source: string;
  source_model: string;
  total_signals: number;
  active_signals: number;
  closed_simulations: number;
  wins: number;
  avg_pnl_pct: number;
  best_confidence: number;
  last_signal_at: string;
  win_rate: number;
}

export interface IntegrationExchange {
  exchange: string;
  market_type: string;
  last_trade_at: string;
  trades_5m: number;
  trades_1h: number;
  age_seconds: number;
}

export interface IntegrationOverview {
  generated_at: string;
  agents: IntegrationAgent[];
  models: IntegrationModel[];
  signals: {
    recent: Array<Record<string, any>>;
    performance: IntegrationSignalPerformance[];
  };
  exchanges: IntegrationExchange[];
  resources: {
    cpu_percent: number;
    ram_percent: number;
    ram_used_mb: number;
    process_rss_mb: number;
    disk_percent: number;
    load_avg: number[];
  };
  brain: {
    total: number;
    correct: number;
    accuracy: number;
    avg_pnl: number;
    history: Array<{
      timestamp: string;
      mode: string;
      cumulative_pnl: number;
      daily_pnl: number;
      win_rate: number;
      total_trades: number;
    }>;
  };
  brain_control: {
    mode: string;
    health: string;
    directive_updated_at: string | null;
    directive_preview: string | null;
    decision_core: {
      ok: boolean;
      model: string;
      approval_rate: number;
      total_requests: number;
      gemma_calls: number;
      fallback_calls: number;
      avg_latency_ms: number;
    };
    learning_weights: {
      similarity: number;
      volume_match: number;
      direction_match: number;
      confidence_history: number;
    };
    efom: {
      ok: boolean;
      logged_trades: number;
      optimizations_run: number;
      config_path: string | null;
      latest_report_summary?: string | null;
      latest_report_sample_size?: number;
      failure_patterns?: Array<{ condition: string; impact: string }>;
      optuna_total_trials?: number;
      optuna_best_value?: number;
      optuna_best_trial?: Record<string, any> | null;
    };
  };
}

export interface EfomOverview {
  ok: boolean;
  generated_at: string;
  reports_path: string;
  runtime_config_path: string;
  post_mortem: null | {
    summary?: string;
    sample_size?: number;
    regime_summary?: Array<Record<string, any>>;
    failure_patterns?: Array<{ condition: string; impact: string }>;
    parameter_adjustment_suggestions?: Record<string, any>;
  };
  optuna: {
    trials: Array<{
      number: number;
      value: number;
      coverage?: number;
      sharpe?: number;
      sortino?: number;
      params?: Record<string, number>;
    }>;
    total_trials: number;
    best_trial: null | {
      number: number;
      value: number;
      coverage?: number;
      sharpe?: number;
      sortino?: number;
      params?: Record<string, number>;
    };
  };
  runtime_config: Record<string, any> | null;
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

export function useIntegrationOverview() {
  return useSWR<IntegrationOverview>(`${API}/api/integration/overview`, fetcher, {
    refreshInterval: 2500,
    dedupingInterval: 1000,
    refreshWhenHidden: true,
    refreshWhenOffline: true,
    revalidateOnFocus: true,
    revalidateOnReconnect: true,
  });
}

export function useEfomOverview() {
  return useSWR<EfomOverview>(`${API}/api/efom/overview`, fetcher, {
    refreshInterval: 5000,
    dedupingInterval: 2000,
    refreshWhenHidden: true,
    refreshWhenOffline: true,
    revalidateOnFocus: true,
    revalidateOnReconnect: true,
  });
}

export function useSystemEvents() {
  return useSWR<SystemEventStats>(`${API}/api/system/events`, fetcher, {
    refreshInterval: 2000,
    dedupingInterval: 1000,
  });
}

export function useMamisStatus() {
  return useSWR<MamisStatus>(`${API}/api/mamis/status`, fetcher, {
    refreshInterval: 5000,
    dedupingInterval: 2000,
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
  signal_time?: string;
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

/* ─── Signature Matches (Neuro-Symbolic Engine) ─── */

export interface SignatureMatchRecord {
  id: number;
  symbol: string;
  timeframe: string;
  direction: string;
  similarity: number;
  dtw_score: number;
  fft_score: number;
  cosine_score: number;
  poly_score: number;
  matched_signature_id: number | null;
  match_label: string | null;
  pattern_name: string | null;
  historical_timestamp: string | null;
  historical_price: number;
  historical_end_price: number;
  historical_volume_ratio: number;
  context_string: string | null;
  current_price: number;
  created_at: string;
}

export function useSignatureMatches(symbol?: string) {
  const key = symbol
    ? `${API}/api/signature-matches?symbol=${symbol}`
    : `${API}/api/signature-matches`;
  return useSWR<SignatureMatchRecord[]>(key, fetcher, { refreshInterval: 10000 });
}
