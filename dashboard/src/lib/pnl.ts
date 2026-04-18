/**
 * Direction-aware P&L utilities.
 *
 * Single source of truth for classifying winners vs losers on the Signals
 * dashboard. Mirrors `python_agents/utils/pnl.py`; keep the two in sync.
 */

export type SignalDirectionLike = string | null | undefined;

export interface SignalLike {
  direction?: SignalDirectionLike;
  entry_price?: number | string | null;
  exit_price?: number | string | null;
  current_price?: number | string | null;
  price?: number | string | null;
  status?: string | null;
  metadata?: Record<string, any> | null;
}

export type OutcomeBucket = "profit" | "loss" | "pending";

function coerceFloat(value: unknown): number | null {
  if (value === null || value === undefined || value === "") return null;
  const n = Number(value);
  return Number.isFinite(n) ? n : null;
}

export function normalizeDirection(raw: SignalDirectionLike | unknown): "long" | "short" {
  if (raw === null || raw === undefined) return "long";
  const text = String(raw).trim().toLowerCase();
  if (text === "short" || text === "sell" || text === "down" || text === "bear") return "short";
  return "long";
}

/**
 * Returns the signed realized P&L percentage (positive = profit), or `null`
 * when inputs are insufficient.
 */
export function computeSignalPnlPct(signal: SignalLike): number | null {
  const meta = signal.metadata ?? {};
  const entry = coerceFloat(signal.entry_price ?? meta.entry_price ?? signal.price);
  if (entry === null || entry <= 0) return null;

  const ref = coerceFloat(
    signal.exit_price ?? meta.exit_price ?? signal.current_price ?? meta.current_price_at_signal,
  );
  if (ref === null) return null;

  const direction = normalizeDirection(
    signal.direction ?? meta.direction ?? meta.position_bias,
  );

  const raw = ((ref - entry) / entry) * 100;
  return direction === "short" ? -raw : raw;
}

export function isProfitable(signal: SignalLike): boolean {
  const p = computeSignalPnlPct(signal);
  return p !== null && p > 0;
}

export function classifySignalOutcome(signal: SignalLike): OutcomeBucket {
  const p = computeSignalPnlPct(signal);
  if (p === null) return "pending";
  if (p > 0) return "profit";
  if (p < 0) return "loss";
  return "pending";
}

/**
 * Resolve the best realized reference price from a signal blob that may also
 * carry horizon outcomes. Used by the outcome cards which need to show the
 * hit / close price alongside entry.
 */
export function resolveRealizedPrice(signal: any): number | null {
  const meta = signal?.metadata ?? {};
  const horizons: any[] = Array.isArray(meta.target_horizons) ? meta.target_horizons : [];
  const hit = horizons.find((h) => h?.status === "hit");
  const primary = hit || horizons.find((h) => ["missed", "expired"].includes(String(h?.status)));
  const candidates = [
    signal?.exit_price,
    meta.exit_price,
    primary?.actual_price,
    signal?.current_price,
    meta.current_price_at_signal,
  ];
  for (const c of candidates) {
    const num = coerceFloat(c);
    if (num !== null && num > 0) return num;
  }
  return null;
}
