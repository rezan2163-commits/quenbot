/**
 * Intel Upgrade API (Phase 1-5)
 * ==============================
 * Dashboard hooks for the FastBrain / DecisionRouter / CrossAssetGraph /
 * Confluence / OnlineLearning endpoints exposed by the python agents layer.
 */
import useSWR from "swr";

const API = process.env.NEXT_PUBLIC_API_URL || "";

async function fetcher<T>(url: string): Promise<T> {
  const res = await fetch(url);
  if (!res.ok) throw new Error(`API ${res.status}`);
  return res.json();
}

/* ───── Types ───── */

export interface IntelSummary {
  feature_store?: { enabled: boolean; health?: any; metrics?: any; error?: string };
  ofi?: { enabled: boolean; health?: any; metrics?: any; error?: string };
  multi_horizon?: { enabled: boolean; health?: any; metrics?: any; error?: string };
  confluence?: { enabled: boolean; health?: any; metrics?: any; error?: string };
  cross_asset?: { enabled: boolean; health?: any; metrics?: any; error?: string };
  fast_brain?: { enabled: boolean; health?: any; metrics?: any; error?: string };
  decision_router?: { enabled: boolean; health?: any; metrics?: any; error?: string };
  online_learning?: { enabled: boolean; health?: any; metrics?: any; error?: string };
  metrics_exporter?: { enabled: boolean; health?: any; metrics?: any; error?: string };
  oracle?: {
    enabled: boolean;
    detectors_active?: number;
    channels_registered?: number;
    modules?: Record<string, { enabled: boolean; channel?: string | null; health?: any; metrics?: any; error?: string }>;
    error?: string;
  };
}

export interface ConfluenceSnapshot {
  symbol: string;
  confluence_score?: number;
  log_odds?: number;
  contributors?: Record<string, number>;
  top_contributors?: Array<[string, number]>;
  weights?: Record<string, number>;
  [k: string]: any;
}

export interface CrossAssetGraph {
  nodes: Array<{ symbol: string; degree?: number; leader_score?: number }>;
  edges: Array<{
    source: string;
    target: string;
    lag_sec: number;
    rho: number;
    strength?: number;
  }>;
  tracked_symbols?: number;
  last_rebuild_ts?: number;
  error?: string;
}

export interface CrossAssetNeighbors {
  symbol: string;
  leaders: Array<{ symbol: string; lag_sec: number; rho: number }>;
  followers: Array<{ symbol: string; lag_sec: number; rho: number }>;
  active_spillover: number;
  error?: string;
}

export interface FastBrainResponse {
  symbol: string;
  enabled: boolean;
  prediction?: {
    probability: number;
    direction: string;
    raw_score: number;
    confidence: number;
    features_used: number;
    missing_features: string[];
    latency_ms: number;
    ts: number;
  } | null;
  last?: any;
  reason?: string;
}

export interface DecisionRouterStatus {
  enabled: boolean;
  health?: {
    healthy: boolean;
    shadow: boolean;
    log_rows: number;
    max_log_rows: number;
    routed_total: number;
    agree_total: number;
    disagree_total: number;
    fast_overrides_total: number;
    tracked_symbols: number;
  };
  metrics?: Record<string, number>;
  last_decisions?: Record<string, any>;
}

export interface OnlineLearningStats {
  enabled: boolean;
  health?: any;
  rolling?: {
    samples: number;
    fast_brain?: { directional_hit_rate: number | null; n: number };
    gemma?: { directional_hit_rate: number | null; n: number };
    agreement?: { rate: number | null; hit_rate_when_agreed: number | null; n: number };
    calibration_bins?: Array<{ p_pred_center: number; count: number; p_realized_up: number | null }>;
    ece?: number | null;
  };
}

/* ───── Hooks ───── */

export function useIntelSummary() {
  return useSWR<IntelSummary>(`${API}/api/intel/summary`, fetcher, {
    refreshInterval: 5000,
    dedupingInterval: 2000,
  });
}

export function useConfluence(symbol: string | null) {
  return useSWR<ConfluenceSnapshot>(
    symbol ? `${API}/api/confluence/${encodeURIComponent(symbol)}` : null,
    fetcher,
    { refreshInterval: 5000 }
  );
}

export function useCrossAssetGraph() {
  return useSWR<CrossAssetGraph>(`${API}/api/cross-asset/graph`, fetcher, {
    refreshInterval: 15000,
    shouldRetryOnError: false,
  });
}

export function useCrossAssetNeighbors(symbol: string | null) {
  return useSWR<CrossAssetNeighbors>(
    symbol ? `${API}/api/cross-asset/${encodeURIComponent(symbol)}` : null,
    fetcher,
    { refreshInterval: 10000 }
  );
}

export function useFastBrain(symbol: string | null) {
  return useSWR<FastBrainResponse>(
    symbol ? `${API}/api/fast-brain/${encodeURIComponent(symbol)}` : null,
    fetcher,
    { refreshInterval: 5000 }
  );
}

export function useDecisionRouter() {
  return useSWR<DecisionRouterStatus>(`${API}/api/decision-router/status`, fetcher, {
    refreshInterval: 6000,
  });
}

export function useOnlineLearning(symbol?: string | null) {
  const key = symbol
    ? `${API}/api/online-learning/stats?symbol=${encodeURIComponent(symbol)}`
    : `${API}/api/online-learning/stats`;
  return useSWR<OnlineLearningStats>(key, fetcher, { refreshInterval: 15000 });
}

/* ───── Phase 6: Oracle Stack ───── */

export interface OracleDetector {
  name: string;
  channel?: string | null;
  health?: any;
  metrics?: Record<string, any>;
  error?: string;
}

export interface OracleSummary {
  enabled: boolean;
  detectors: OracleDetector[];
  channels: string[];
  channels_error?: string;
}

export interface OracleChannelsResponse {
  symbol: string;
  channels: Record<string, { value?: number; source?: string; ts?: number; extra?: any } | any>;
}

export function useOracleSummary() {
  return useSWR<OracleSummary>(`${API}/api/oracle/summary`, fetcher, {
    refreshInterval: 5000,
  });
}

export function useOracleChannels(symbol: string | null) {
  return useSWR<OracleChannelsResponse>(
    symbol ? `${API}/api/oracle/channels/${encodeURIComponent(symbol)}` : null,
    fetcher,
    { refreshInterval: 5000 }
  );
}
