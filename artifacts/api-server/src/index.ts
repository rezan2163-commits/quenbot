import express from "express";
import cors from "cors";
import compression from "compression";
import fs from "fs/promises";
import path from "path";
import { connectDatabase, createTables, sql } from "./db";

const app = express();
const port = Number(process.env.PORT || 3001);
const EFOM_BASE_DIR = process.env.QUENBOT_EFOM_DIR || path.resolve(process.cwd(), "../../python_agents/efom_data");
const EFOM_REPORTS_DIR = path.join(EFOM_BASE_DIR, "reports");
const TARGET_CARD_MIN_CONFIDENCE = Number(process.env.QUENBOT_TARGET_CARD_MIN_CONF || 0.62);
const TARGET_CARD_MIN_QUALITY = Number(process.env.QUENBOT_TARGET_CARD_MIN_QUALITY || 0.64);
const MAMIS_TARGET_CARD_MIN_CONFIDENCE = Number(process.env.QUENBOT_MAMIS_TARGET_CARD_MIN_CONF || 0.72);
const MAMIS_TARGET_CARD_MIN_VOLATILITY = Number(process.env.QUENBOT_MAMIS_TARGET_CARD_MIN_VOLATILITY || 0.0035);
const META_LABELER_VETO_PROBA = Number(process.env.QUENBOT_META_LABELER_VETO_PROBA || 0.15);
const SIGNAL_CARDS_PER_SYMBOL = Math.max(1, Number(process.env.QUENBOT_SIGNAL_CARDS_PER_SYMBOL || 1));

function normalizeTimestamp(value: unknown): string | null {
  if (value == null) return null;
  if (value instanceof Date) return value.toISOString();
  if (typeof value === "number") return new Date(value < 1_000_000_000_000 ? value * 1000 : value).toISOString();
  if (typeof value !== "string") return String(value);

  const trimmed = value.trim();
  if (!trimmed) return null;
  if (/^\d+$/.test(trimmed)) {
    const numeric = Number(trimmed);
    return new Date(numeric < 1_000_000_000_000 ? numeric * 1000 : numeric).toISOString();
  }

  const normalized = /[zZ]$|[+-]\d{2}:?\d{2}$/.test(trimmed)
    ? trimmed
    : `${trimmed.replace(" ", "T")}Z`;
  const date = new Date(normalized);
  return Number.isNaN(date.getTime()) ? trimmed : date.toISOString();
}

function hasExplicitTimezone(value: unknown) {
  return typeof value === "string" && /[zZ]$|[+-]\d{2}:?\d{2}$/.test(value.trim());
}

function normalizeSignalMetadata(metadata: any) {
  if (!metadata || typeof metadata !== "object" || Array.isArray(metadata)) return metadata;
  return {
    ...metadata,
    signal_time: normalizeTimestamp(metadata.signal_time) ?? metadata.signal_time ?? null,
    expires_at: normalizeTimestamp(metadata.expires_at) ?? metadata.expires_at ?? null,
    dismissed_at: normalizeTimestamp(metadata.dismissed_at) ?? metadata.dismissed_at ?? null,
    expired_at: normalizeTimestamp(metadata.expired_at) ?? metadata.expired_at ?? null,
  };
}

function toFiniteNumber(value: unknown, fallback = 0) {
  const numeric = Number(value);
  return Number.isFinite(numeric) ? numeric : fallback;
}

function normalizeTargetPct(value: unknown) {
  const numeric = Math.abs(toFiniteNumber(value, 0));
  if (numeric <= 0) return 0;
  return numeric > 0.5 ? numeric / 100 : numeric;
}

function resolveSignalTargetPct(signal: any) {
  const metadata = signal.metadata || {};
  const direct = normalizeTargetPct(signal.target_pct ?? metadata.target_pct ?? metadata.predicted_magnitude);
  if (direct > 0) return direct;
  const entry = toFiniteNumber(signal.entry_price ?? metadata.entry_price ?? signal.price, 0);
  const target = toFiniteNumber(signal.target_price ?? metadata.target_price, 0);
  if (entry > 0 && target > 0) return Math.abs((target - entry) / entry);
  return 0;
}

function resolveSignalEtaMinutes(signal: any) {
  const metadata = signal.metadata || {};
  const direct = toFiniteNumber(metadata.estimated_duration_to_target_minutes ?? signal.estimated_duration_to_target_minutes, Number.NaN);
  if (Number.isFinite(direct)) return Math.round(direct);
  const horizons = Array.isArray(metadata.target_horizons) ? metadata.target_horizons : [];
  if (horizons.length) {
    const mins = horizons
      .map((item: any) => Math.round(toFiniteNumber(item?.eta_minutes, Number.NaN)))
      .filter((v: number) => Number.isFinite(v));
    if (mins.length) return Math.min(...mins);
  }
  return 60;
}

const INTEGRATION_STRATEGIC_SOURCES = new Set(["strategist", "pattern_matcher"]);

function isIntegrationStrategicSource(source: unknown): boolean {
  return INTEGRATION_STRATEGIC_SOURCES.has(String(source || "").trim().toLowerCase());
}

function resolveSignalQuality(signal: any) {
  const targetPct = resolveSignalTargetPct(signal);
  const confidence = Math.min(Math.max(toFiniteNumber(signal.confidence, 0), 0), 1);
  const explicit = toFiniteNumber(signal.metadata?.quality_score, Number.NaN);
  if (Number.isFinite(explicit)) return explicit;
  const ideal = 0.025;
  const targetComponent = 1 - Math.min(Math.abs(targetPct - ideal) / 0.03, 1);
  return Math.min(Math.max(confidence * 0.8 + targetComponent * 0.2, 0), 1);
}

function resolveSignalSource(signal: any) {
  return String(signal.source || signal.metadata?.source || signal.metadata?.signal_provider || "unknown").toLowerCase();
}

function isActionableTargetCard(signal: any) {
  const status = String(signal.status || "").toLowerCase();
  if (!['pending', 'active', 'open', 'processed', 'risk_rejected'].includes(status)) return false;

  const confidence = toFiniteNumber(signal.confidence, 0);
  const targetPct = resolveSignalTargetPct(signal);
  const quality = resolveSignalQuality(signal);
  const source = resolveSignalSource(signal);
  const etaMinutes = resolveSignalEtaMinutes(signal);
  const explicitCandidate = String(signal.metadata?.dashboard_candidate || "").toLowerCase() === 'true';

  if (targetPct < 0.02) return false;
  if (etaMinutes < 60 || etaMinutes > 1440) return false;

  if (!['strategist', 'pattern_matcher'].includes(source)) return false;

  // Meta-labeler advisory: tavsiye niteliğinde; sadece çok düşük olasılıkta veto et.
  // Kullanıcı felsefesi: sistem mantıklı bulduğu sinyalleri versin, sonuçlardan öğrensin.
  const meta = signal.metadata?.meta_labeler;
  if (meta && typeof meta === 'object' && meta.accept === false && typeof meta.proba === 'number' && meta.proba < META_LABELER_VETO_PROBA) {
    return false;
  }

  return explicitCandidate || (
    confidence >= TARGET_CARD_MIN_CONFIDENCE
    && quality >= TARGET_CARD_MIN_QUALITY
  );
}

function isVisibleSignalHistory(signal: any) {
  const status = String(signal.status || '').toLowerCase();
  const source = resolveSignalSource(signal);

  if (status === 'dismissed' || status === 'filtered_duplicate' || status === 'filtered_noise') {
    return false;
  }
  if (status.startsWith('risk_')) {
    return false;
  }
  if (!['strategist', 'pattern_matcher'].includes(source)) {
    return false;
  }
  return true;
}

function normalizeSignalRow(row: any) {
  const timestamp = normalizeTimestamp(row.timestamp) ?? row.timestamp;
  const signalTime = timestamp ?? normalizeTimestamp(row.signal_time) ?? row.signal_time;
  const meta = normalizeSignalMetadata(row.metadata) || {};
  // Prefer metadata expires_at (horizon-based), fall back to DB column, then 24h default
  const metaExpires = normalizeTimestamp(meta.expires_at);
  const expiresAt = metaExpires
    ?? (timestamp ? new Date(new Date(timestamp).getTime() + 24 * 3600 * 1000).toISOString() : null)
    ?? normalizeTimestamp(row.expires_at)
    ?? row.expires_at;

  return {
    ...row,
    timestamp,
    signal_time: signalTime,
    expires_at: expiresAt,
    metadata: {
      ...meta,
      signal_time: signalTime,
      expires_at: expiresAt,
    },
  };
}

async function readJsonFileOrNull(filePath: string) {
  try {
    const raw = await fs.readFile(filePath, "utf-8");
    return JSON.parse(raw);
  } catch {
    return null;
  }
}

type SummaryCache = {
  total_trades: number;
  total_movements: number;
  active_signals: number;
  open_simulations: number;
  total_pnl: number;
  win_rate: number;
  closed_simulations: number;
  winning_simulations: number;
  losing_simulations: number;
};

const summaryCache: { data: SummaryCache; updatedAt: number; refreshing: boolean } = {
  data: {
    total_trades: 0,
    total_movements: 0,
    active_signals: 0,
    open_simulations: 0,
    total_pnl: 0,
    win_rate: 0,
    closed_simulations: 0,
    winning_simulations: 0,
    losing_simulations: 0,
  },
  updatedAt: 0,
  refreshing: false,
};

const pricesCache: { data: any[]; updatedAt: number; refreshing: boolean } = {
  data: [],
  updatedAt: 0,
  refreshing: false,
};

const moversCache: { data: any[]; updatedAt: number; refreshing: boolean } = {
  data: [],
  updatedAt: 0,
  refreshing: false,
};
const exchangeFreshnessCache: { data: any[]; updatedAt: number; refreshing: boolean } = {
  data: [],
  updatedAt: 0,
  refreshing: false,
};

async function buildLocalChatFallback() {
  try {
    await refreshSummaryCache();
    const summary = summaryCache.data;
    const latestSignals = await sql`
      SELECT symbol
      FROM signals
      WHERE status IN ('pending', 'active', 'open')
        AND COALESCE(target_pct, 0) >= 0.02
        AND LOWER(COALESCE(source, '')) IN ('strategist', 'pattern_matcher')
      ORDER BY timestamp DESC, confidence DESC
      LIMIT 3
    `;

    const symbols = latestSignals
      .map((row) => String(row.symbol || '').trim())
      .filter(Boolean)
      .join(', ');

    return {
      success: true,
      status: 'chat_timeout_fallback_local',
      message: [
        'Sistem aktif ama chat modeli su an yogun.',
        `Bekleyen sinyal: ${summary.active_signals}`,
        `Acik simulasyon: ${summary.open_simulations}`,
        `Win rate: %${Number(summary.win_rate || 0).toFixed(1)}`,
        symbols ? `Gorunen hedef kartlari: ${symbols}` : 'Hedef kartlari canli akista izleniyor.',
        'Istersen soruyu daha kisa gonder veya kod gorevini dogrudan Code Operator panelinden ver.'
      ].join(' | '),
      assistant: {
        name: 'SuperGemma Command',
        model: 'fallback-status',
        role: 'direct_operator',
      },
      routed_actions: [],
      timestamp: new Date().toISOString(),
    };
  } catch (error) {
    console.warn('Local chat fallback build failed:', error);
    return {
      success: true,
      status: 'chat_timeout_fallback_local',
      message: 'Sistem aktif ama chat modeli su an yogun. Birazdan tekrar deneyin veya gorevi Code Operator panelinden iletin.',
      assistant: {
        name: 'SuperGemma Command',
        model: 'fallback-status',
        role: 'direct_operator',
      },
      routed_actions: [],
      timestamp: new Date().toISOString(),
    };
  }
}

async function refreshExchangeFreshnessCache() {
  if (exchangeFreshnessCache.refreshing) return;
  exchangeFreshnessCache.refreshing = true;
  try {
    const rows = await sql`
      SELECT
        exchange,
        market_type,
        MAX(timestamp) AS last_trade_at,
        COUNT(*) FILTER (WHERE timestamp >= NOW() - INTERVAL '5 minutes')::int AS trades_5m,
        COUNT(*) FILTER (WHERE timestamp >= NOW() - INTERVAL '1 hour')::int AS trades_1h,
        EXTRACT(EPOCH FROM (NOW() - MAX(timestamp)))::double precision AS age_seconds
      FROM trades
      WHERE timestamp >= NOW() - INTERVAL '24 hours'
      GROUP BY exchange, market_type
      ORDER BY exchange, market_type
    `;
    exchangeFreshnessCache.data = rows;
    exchangeFreshnessCache.updatedAt = Date.now();
  } catch (error) {
    console.error("Exchange freshness cache refresh failed:", error);
  } finally {
    exchangeFreshnessCache.refreshing = false;
  }
}

function buildExchangeFallback(agentRows: Array<{ name: string; age_seconds: number; last_heartbeat: string | null; metadata?: any }>) {
  const scout = agentRows.find((row) => row.name === "scout");
  const lastTradeAt = normalizeTimestamp(scout?.metadata?.last_activity) ?? normalizeTimestamp(scout?.last_heartbeat) ?? new Date().toISOString();
  const ageSeconds = Math.max(0, Number(scout?.age_seconds || 0));
  const feeds = [
    { exchange: "binance", market_type: "futures" },
    { exchange: "binance", market_type: "spot" },
    { exchange: "bybit", market_type: "futures" },
    { exchange: "bybit", market_type: "spot" },
  ];

  return feeds.map((feed) => ({
    ...feed,
    last_trade_at: lastTradeAt,
    trades_5m: 0,
    trades_1h: 0,
    age_seconds: ageSeconds,
  }));
}

async function refreshSummaryCache() {
  if (summaryCache.refreshing) return;
  summaryCache.refreshing = true;
  try {
    const [tableStats, activeSignals, openSimulations, totalPnl, wonSimulations, lostSimulations] = await Promise.all([
      sql`
        SELECT relname, COALESCE(n_live_tup, 0)::bigint AS est_rows
        FROM pg_stat_user_tables
        WHERE relname IN ('trades', 'price_movements')
      `,
      sql`SELECT COUNT(*)::int AS count FROM signals WHERE status = 'pending'`,
      sql`SELECT COUNT(*)::int AS count FROM simulations WHERE status = 'open'`,
      sql`SELECT COALESCE(SUM(pnl), 0)::double precision AS value FROM simulations WHERE status = 'closed'`,
      sql`SELECT COUNT(*)::int AS count FROM simulations WHERE status = 'closed' AND pnl > 0`,
      sql`SELECT COUNT(*)::int AS count FROM simulations WHERE status = 'closed' AND pnl <= 0`,
    ]);

    const est = Object.fromEntries(tableStats.map((r: any) => [r.relname, Number(r.est_rows) || 0]));
    const wins = Number(wonSimulations[0]?.count || 0);
    const losses = Number(lostSimulations[0]?.count || 0);
    const closed = wins + losses;
    const winRate = closed > 0 ? Number(((wins / closed) * 100).toFixed(2)) : 0;

    summaryCache.data = {
      total_trades: est.trades || 0,
      total_movements: est.price_movements || 0,
      active_signals: Number(activeSignals[0]?.count || 0),
      open_simulations: Number(openSimulations[0]?.count || 0),
      total_pnl: Number(totalPnl[0]?.value || 0),
      win_rate: winRate,
      closed_simulations: closed,
      winning_simulations: wins,
      losing_simulations: losses,
    };
    summaryCache.updatedAt = Date.now();
  } catch (error) {
    console.error("Summary cache refresh failed:", error);
  } finally {
    summaryCache.refreshing = false;
  }
}

async function refreshPricesCache() {
  if (pricesCache.refreshing) return;
  pricesCache.refreshing = true;
  try {
    // Get latest price for each symbol+exchange from trades
    const rows = await sql`
      SELECT DISTINCT ON (symbol, exchange, market_type)
        symbol,
        exchange,
        market_type,
        price::double precision AS price,
        price::text AS price_text,
        timestamp
      FROM trades
      WHERE timestamp > NOW() - INTERVAL '5 minutes'
      ORDER BY symbol, exchange, market_type, timestamp DESC
      LIMIT 100
    `;
    
    const formatted = rows.map((row: any) => ({
      symbol: row.symbol,
      exchange: row.exchange || "binance",
      market_type: row.market_type || "spot",
      price: Number(row.price) || 0,
      price_text: String(row.price_text ?? row.price ?? "0"),
      timestamp: row.timestamp,
    }));
    
    pricesCache.data = formatted.length > 0 ? formatted : [];
    pricesCache.updatedAt = Date.now();
  } catch (error) {
    console.error("Prices cache refresh failed:", error);
  } finally {
    pricesCache.refreshing = false;
  }
}

async function refreshMoversCache() {
  if (moversCache.refreshing) return;
  moversCache.refreshing = true;
  try {
    const rows = await sql`
      WITH latest_per_symbol AS (
        SELECT DISTINCT ON (symbol)
          symbol,
          start_price::double precision AS open_price,
          end_price::double precision AS current_price,
          (
            CASE WHEN COALESCE(direction, 'long') = 'short'
              THEN -ABS(change_pct::double precision)
              ELSE ABS(change_pct::double precision)
            END
          ) AS signed_change,
          end_time AS timestamp
        FROM price_movements
        WHERE end_time >= NOW() - INTERVAL '3 hours'
          AND start_price > 0
          AND end_price > 0
          AND ABS(change_pct::double precision) <= 0.25
        ORDER BY symbol, end_time DESC
      )
      SELECT symbol,
             open_price,
             current_price,
             (signed_change * 100)::double precision AS change_pct,
             timestamp
      FROM latest_per_symbol
      ORDER BY ABS(signed_change) DESC
      LIMIT 20
    `;
    moversCache.data = rows;
    moversCache.updatedAt = Date.now();
  } catch (error) {
    console.error("Movers cache refresh failed:", error);
  } finally {
    moversCache.refreshing = false;
  }
}

// Performance: gzip compression for all responses
app.use(compression());
app.use(cors());
app.use(express.json());

const AGENT_STALE_SECONDS = Number(process.env.AGENT_STALE_SECONDS || 600);

// Python agents API cache - prevents repeated failed requests
const agentApiCache = new Map<string, { data: any; updatedAt: number; errorCount: number }>();
const AGENT_API_CACHE_TTL = 5000; // 5 seconds cache
const AGENT_API_ERROR_BACKOFF = 30000; // 30s backoff on errors

async function fetchJsonOrNull(url: string, timeoutMs = 8000, retries = 2): Promise<any> {
  // Check cache first
  const cached = agentApiCache.get(url);
  const now = Date.now();
  if (cached) {
    // If we had errors, use longer backoff
    const backoffTime = cached.errorCount > 0 ? AGENT_API_ERROR_BACKOFF : AGENT_API_CACHE_TTL;
    if (now - cached.updatedAt < backoffTime) {
      return cached.data;
    }
  }

  for (let attempt = 0; attempt <= retries; attempt++) {
    const controller = new AbortController();
    const timeoutId = setTimeout(() => controller.abort(), timeoutMs);
    try {
      const response = await fetch(url, { signal: controller.signal });
      clearTimeout(timeoutId);
      if (!response.ok) {
        // Cache null result with error count
        const errorCount = (cached?.errorCount || 0) + 1;
        agentApiCache.set(url, { data: null, updatedAt: now, errorCount });
        return null;
      }
      const data = await response.json();
      // Cache successful result
      agentApiCache.set(url, { data, updatedAt: now, errorCount: 0 });
      return data;
    } catch (err) {
      clearTimeout(timeoutId);
      if (attempt < retries) {
        // Wait before retry (exponential backoff)
        await new Promise(r => setTimeout(r, 500 * (attempt + 1)));
        continue;
      }
      // Cache null with error count on final failure
      const errorCount = (cached?.errorCount || 0) + 1;
      agentApiCache.set(url, { data: cached?.data || null, updatedAt: now, errorCount });
      return cached?.data || null; // Return stale data if available
    }
  }
  return null;
}

async function buildAgentStatusResponse() {
  const heartbeats = await sql`
    SELECT agent_name, status, last_heartbeat, metadata,
           EXTRACT(EPOCH FROM (NOW() - last_heartbeat)) AS age_seconds
    FROM agent_heartbeat ORDER BY agent_name
  `;

  const agents: Record<string, any> = {};
  for (const hb of heartbeats) {
    const ageSeconds = Math.max(0, Math.round(Number(hb.age_seconds || 0)));
    const isFresh = ageSeconds < AGENT_STALE_SECONDS;
    agents[hb.agent_name] = {
      status: isFresh ? hb.status : "stale",
      source_status: hb.status,
      last_heartbeat: hb.last_heartbeat,
      age_seconds: ageSeconds,
      metadata: hb.metadata,
    };
  }

  if (Object.keys(agents).length === 0) {
    for (const name of ['scout', 'strategist', 'ghost_simulator', 'auditor', 'brain', 'decision_core', 'efom', 'chat_engine']) {
      agents[name] = { status: "unknown", source_status: null, last_heartbeat: null, age_seconds: null, metadata: null };
    }
  }

  const [configSignals] = await sql`SELECT COUNT(*)::int AS count FROM signals`;
  const [configMovements] = await sql`SELECT COUNT(*)::int AS count FROM price_movements`;
  return {
    agents,
    summary: {
      signals: configSignals.count,
      movements: configMovements.count,
      stale_threshold_seconds: AGENT_STALE_SECONDS,
    },
  };
}

app.get("/api/health", async (req, res) => {
  try {
    res.json({
      status: "ok",
      timestamp: new Date().toISOString(),
      cache: {
        summary_age_ms: Date.now() - summaryCache.updatedAt,
        prices_age_ms: Date.now() - pricesCache.updatedAt,
        movers_age_ms: Date.now() - moversCache.updatedAt,
      },
    });
  } catch (error) {
    res.status(500).json({ status: "error", error: String(error) });
  }
});

app.get("/api/dashboard/summary", async (req, res) => {
  try {
    if (Date.now() - summaryCache.updatedAt > 3000) {
      void refreshSummaryCache();
    }
    res.json(summaryCache.data);
  } catch (error) {
    res.status(500).json({ error: String(error) });
  }
});

app.get("/api/live/prices", async (req, res) => {
  try {
    if (Date.now() - pricesCache.updatedAt > 2000) {
      void refreshPricesCache();
    }
    res.json(pricesCache.data);
  } catch (error) {
    res.status(500).json({ error: String(error) });
  }
});

app.get("/api/bot/summary", async (req, res) => {
  try {
    const [totalSimulations] = await sql`SELECT COUNT(*)::int AS count FROM simulations`;
    const [openSimulations] = await sql`SELECT COUNT(*)::int AS count FROM simulations WHERE status = 'open'`;
    const [closedSimulations] = await sql`SELECT COUNT(*)::int AS count FROM simulations WHERE status = 'closed'`;
    const [wonSimulations] = await sql`SELECT COUNT(*)::int AS count FROM simulations WHERE status = 'closed' AND pnl > 0`;
    const [lossSimulations] = await sql`SELECT COUNT(*)::int AS count FROM simulations WHERE status = 'closed' AND pnl <= 0`;
    const [avgPnl] = await sql`SELECT COALESCE(AVG(pnl), 0)::double precision AS value FROM simulations WHERE status = 'closed'`;
    const [avgPnlPct] = await sql`SELECT COALESCE(AVG(pnl_pct), 0)::double precision AS value FROM simulations WHERE status = 'closed'`;

    res.json({
      total_simulations: totalSimulations.count,
      open_simulations: openSimulations.count,
      closed_simulations: closedSimulations.count,
      wins: wonSimulations.count,
      losses: lossSimulations.count,
      win_rate: closedSimulations.count > 0 ? Number((wonSimulations.count / closedSimulations.count) * 100).toFixed(2) : 0,
      average_pnl: avgPnl.value,
      average_pnl_pct: avgPnlPct.value
    });
  } catch (error) {
    res.status(500).json({ error: String(error) });
  }
});

app.get("/api/agents/status", async (req, res) => {
  try {
    const payload = await buildAgentStatusResponse();
    res.json(payload);
  } catch (error) {
    res.status(500).json({ error: String(error) });
  }
});

// Backward-compatible alias used by older dashboards/tools
app.get("/api/system/agents", async (req, res) => {
  try {
    const payload = await buildAgentStatusResponse();
    res.json(payload);
  } catch (error) {
    res.status(500).json({ error: String(error) });
  }
});

app.get("/api/scout/trades", async (req, res) => {
  try {
    const symbol = String(req.query.symbol || "").toUpperCase();
    const limit = Number(req.query.limit || 50);
    const query = symbol
      ? sql`SELECT * FROM trades WHERE symbol = ${symbol} ORDER BY timestamp DESC LIMIT ${limit}`
      : sql`SELECT * FROM trades ORDER BY timestamp DESC LIMIT ${limit}`;

    const trades = await query;
    res.json(trades);
  } catch (error) {
    res.status(500).json({ error: String(error) });
  }
});

app.get("/api/scout/movements", async (req, res) => {
  try {
    const symbol = String(req.query.symbol || "").toUpperCase();
    const limit = Number(req.query.limit || 50);
    const query = symbol
      ? sql`SELECT * FROM price_movements WHERE symbol = ${symbol} ORDER BY start_time DESC LIMIT ${limit}`
      : sql`SELECT * FROM price_movements ORDER BY start_time DESC LIMIT ${limit}`;

    const movements = await query;
    res.json(movements);
  } catch (error) {
    res.status(500).json({ error: String(error) });
  }
});

app.get("/api/signals", async (req, res) => {
  try {
    const includeRejected = ["1", "true", "yes", "on"].includes(String(req.query.includeRejected || "").toLowerCase());
    const statusFilter = includeRejected
      ? sql`('pending', 'active', 'open', 'processed', 'risk_rejected')`
      : sql`('pending', 'active', 'open', 'processed')`;
    const signals = await sql`
      SELECT
        id,
        symbol,
        signal_type,
        COALESCE(metadata->>'direction', CASE WHEN signal_type ILIKE '%short%' THEN 'short' ELSE 'long' END) AS direction,
        confidence::double precision AS confidence,
        price::double precision AS price,
        COALESCE(metadata->>'signal_time', timestamp::text) AS signal_time,
        COALESCE((metadata->>'entry_price')::double precision, price::double precision) AS entry_price,
        COALESCE((metadata->>'current_price_at_signal')::double precision, price::double precision) AS current_price_at_signal,
        COALESCE(NULLIF((metadata->>'target_price')::double precision, 0),
          CASE WHEN COALESCE(metadata->>'position_bias', 'long') = 'short'
            THEN price::double precision * (1 - GREATEST(
              CASE WHEN COALESCE((metadata->>'target_pct')::double precision, 0.02) > 0.5
                THEN COALESCE((metadata->>'target_pct')::double precision, 0.02) / 100.0
                ELSE COALESCE((metadata->>'target_pct')::double precision, 0.02)
              END, 0.02))
            ELSE price::double precision * (1 + GREATEST(
              CASE WHEN COALESCE((metadata->>'target_pct')::double precision, 0.02) > 0.5
                THEN COALESCE((metadata->>'target_pct')::double precision, 0.02) / 100.0
                ELSE COALESCE((metadata->>'target_pct')::double precision, 0.02)
              END, 0.02))
          END
        ) AS target_price,
        GREATEST(
          CASE WHEN COALESCE((metadata->>'target_pct')::double precision, 0.02) > 0.5
            THEN COALESCE((metadata->>'target_pct')::double precision, 0.02) / 100.0
            ELSE COALESCE((metadata->>'target_pct')::double precision, 0.02)
          END, 0.02
        ) AS target_pct,
        COALESCE((metadata->>'estimated_duration_to_target_minutes')::int, 60) AS estimated_duration_to_target_minutes,
        COALESCE(metadata->>'source', metadata->>'signal_provider', 'unknown') AS source,
        COALESCE(metadata->>'source_model', 'unknown') AS source_model,
        COALESCE(metadata->>'expires_at', (timestamp + INTERVAL '24 hours')::text) AS expires_at,
        status,
        timestamp,
        metadata,
        COALESCE(metadata->>'exchange', 'binance') AS exchange,
        market_type
      FROM signals
      WHERE status IN ${statusFilter}
        AND timestamp >= NOW() - INTERVAL '24 hours'
        AND (
          (metadata->>'expires_at') IS NULL
          OR (metadata->>'expires_at')::timestamptz > NOW()
        )
        AND COALESCE(metadata->>'source', metadata->>'signal_provider', 'unknown') IN ('strategist', 'pattern_matcher')
        AND GREATEST(
          CASE WHEN COALESCE((metadata->>'target_pct')::double precision, 0.02) > 0.5
            THEN COALESCE((metadata->>'target_pct')::double precision, 0.02) / 100.0
            ELSE COALESCE((metadata->>'target_pct')::double precision, 0.02)
          END, 0.02
        ) >= 0.02
      ORDER BY timestamp DESC, confidence DESC
      LIMIT 200
    `;
    res.json((() => {
      const all = signals.map(normalizeSignalRow).filter(isActionableTargetCard);
      // Sembol başına TEK kart: aynı coinin iki borsa (binance+bybit) ve iki parite
      // (spot+futures) verisinden üretilen sinyalleri tek yön kartında birleştir.
      // Varsayılan `QUENBOT_SIGNAL_CARDS_PER_SYMBOL=1`; gerekirse env ile artırılabilir.
      const bySymbol = new Map<string, any[]>();
      for (const s of all) {
        const key = String(s.symbol || '').toUpperCase();
        if (!key) continue;
        const list = bySymbol.get(key) || [];
        list.push(s);
        list.sort((a, b) => {
          const aTs = new Date(a.signal_time || a.timestamp).getTime();
          const bTs = new Date(b.signal_time || b.timestamp).getTime();
          if (bTs !== aTs) return bTs - aTs;
          return Number(b.confidence || 0) - Number(a.confidence || 0);
        });
        bySymbol.set(key, list.slice(0, SIGNAL_CARDS_PER_SYMBOL));
      }
      return Array.from(bySymbol.values()).flat().sort(
        (a, b) => new Date(b.signal_time || b.timestamp).getTime() - new Date(a.signal_time || a.timestamp).getTime()
      );
    })());
  } catch (error) {
    res.status(500).json({ error: String(error) });
  }
});

app.post("/api/signals/:id/dismiss", async (req, res) => {
  try {
    const signalId = Number(req.params.id || 0);
    if (!Number.isInteger(signalId) || signalId <= 0) {
      res.status(400).json({ error: "invalid_signal_id" });
      return;
    }

    const updated = await sql`
      UPDATE signals
      SET status = 'dismissed',
          metadata = COALESCE(metadata, '{}'::jsonb) || jsonb_build_object(
            'dismissed_at', NOW()::text,
            'dismissed_from', 'dashboard'
          )
      WHERE id = ${signalId}
      RETURNING id
    `;

    if (!updated.length) {
      res.status(404).json({ error: "signal_not_found" });
      return;
    }

    res.json({ ok: true, id: signalId });
  } catch (error) {
    res.status(500).json({ error: String(error) });
  }
});

app.post("/api/signals/clear", async (req, res) => {
  try {
    const rawIds = Array.isArray(req.body?.ids) ? req.body.ids : [];
    const ids = rawIds.map((value: unknown) => Number(value)).filter((value: number) => Number.isInteger(value) && value > 0);
    if (!ids.length) {
      res.status(400).json({ error: "no_signal_ids" });
      return;
    }

    let updatedCount = 0;
    await Promise.all(ids.map(async (signalId: number) => {
      const updated = await sql`
        UPDATE signals
        SET status = 'dismissed',
            metadata = COALESCE(metadata, '{}'::jsonb) || jsonb_build_object(
              'dismissed_at', NOW()::text,
              'dismissed_from', 'dashboard_bulk'
            )
        WHERE id = ${signalId}
        RETURNING id
      `;
      updatedCount += updated.length;
    }));

    res.json({ ok: true, updated: updatedCount });
  } catch (error) {
    res.status(500).json({ error: String(error) });
  }
});

app.get("/api/efom/overview", async (_req, res) => {
  try {
    const [postMortem, optunaTrials, runtimeConfig] = await Promise.all([
      readJsonFileOrNull(path.join(EFOM_REPORTS_DIR, "post_mortem_report.json")),
      readJsonFileOrNull(path.join(EFOM_REPORTS_DIR, "optuna_trials.json")),
      readJsonFileOrNull(path.join(EFOM_BASE_DIR, "runtime_config.json")),
    ]);

    const trials = Array.isArray(optunaTrials) ? optunaTrials : [];
    const bestTrial = trials.reduce<any | null>((best, trial) => {
      if (!trial || typeof trial !== "object") return best;
      if (typeof trial.value !== "number") return best;
      if (!best || trial.value > best.value) return trial;
      return best;
    }, null);

    res.json({
      ok: true,
      generated_at: new Date().toISOString(),
      reports_path: EFOM_REPORTS_DIR,
      runtime_config_path: path.join(EFOM_BASE_DIR, "runtime_config.json"),
      post_mortem: postMortem,
      optuna: {
        trials,
        total_trials: trials.length,
        best_trial: bestTrial,
      },
      runtime_config: runtimeConfig,
    });
  } catch (error) {
    res.status(500).json({ error: String(error) });
  }
});

app.get("/api/simulations", async (req, res) => {
  try {
    const simulations = await sql`
      SELECT
        id, signal_id, market_type, symbol,
        entry_price::double precision AS entry_price,
        exit_price::double precision AS exit_price,
        quantity::double precision AS quantity,
        side, status,
        pnl::double precision AS pnl,
        pnl_pct::double precision AS pnl_pct,
        entry_time, exit_time, metadata
      FROM simulations
      ORDER BY entry_time DESC
      LIMIT 100
    `;
    res.json(simulations);
  } catch (error) {
    res.status(500).json({ error: String(error) });
  }
});

// ─── Target Cards (proxy to Python SimulationEngine on port 3002) ───
app.get("/api/target-cards", async (req, res) => {
  try {
    const agentApi = process.env.QUENBOT_DIRECTIVE_API || "http://127.0.0.1:3002";
    const response = await fetch(`${agentApi}/api/target-cards`, {
      signal: AbortSignal.timeout(5000),
    });
    const data = await response.json();
    res.json(data);
  } catch (error) {
    // Fallback: return empty data if Python agent is unreachable
    res.json({ live_cards: [], recent_archive: [], stats: {}, error: "Agent unavailable" });
  }
});

app.get("/api/patterns/blacklist", async (req, res) => {
  try {
    const patterns = await sql`SELECT * FROM blacklist_patterns ORDER BY created_at DESC LIMIT 100`;
    res.json(patterns);
  } catch (error) {
    res.status(500).json({ error: String(error) });
  }
});

app.get("/api/patterns/audit-reports", async (req, res) => {
  try {
    const reports = await sql`SELECT * FROM audit_reports ORDER BY created_at DESC LIMIT 100`;
    res.json(reports);
  } catch (error) {
    res.status(500).json({ error: String(error) });
  }
});

// Volume by exchange
app.get("/api/analytics/volume-by-exchange", async (req, res) => {
  try {
    const rows = await sql`
      SELECT exchange, market_type,
             COUNT(*)::int AS trade_count,
             SUM((price * quantity)::double precision)::double precision AS total_volume
      FROM trades
      WHERE timestamp >= NOW() - INTERVAL '1 hour'
      GROUP BY exchange, market_type
      ORDER BY total_volume DESC
    `;
    res.json(rows);
  } catch (error) {
    res.status(500).json({ error: String(error) });
  }
});

// Top movers (price change in last hour)
app.get("/api/analytics/top-movers", async (req, res) => {
  try {
    if (Date.now() - moversCache.updatedAt > 3000) {
      void refreshMoversCache();
    }
    res.json(moversCache.data);
  } catch (error) {
    res.status(500).json({ error: String(error) });
  }
});

// Trade activity timeline (last 60 minutes, per minute)
app.get("/api/analytics/trade-timeline", async (req, res) => {
  try {
    const rows = await sql`
      SELECT date_trunc('minute', timestamp) AS minute,
             COUNT(*)::int AS count,
             SUM((price * quantity)::double precision)::double precision AS volume
      FROM trades
      WHERE timestamp >= NOW() - INTERVAL '60 minutes'
      GROUP BY minute
      ORDER BY minute ASC
    `;
    res.json(rows);
  } catch (error) {
    res.status(500).json({ error: String(error) });
  }
});

// Buy/Sell pressure per symbol
app.get("/api/analytics/order-flow", async (req, res) => {
  try {
    const rows = await sql`
      SELECT symbol,
             SUM(CASE WHEN side = 'buy' THEN (price * quantity)::double precision ELSE 0 END)::double precision AS buy_volume,
             SUM(CASE WHEN side = 'sell' THEN (price * quantity)::double precision ELSE 0 END)::double precision AS sell_volume,
             COUNT(CASE WHEN side = 'buy' THEN 1 END)::int AS buy_count,
             COUNT(CASE WHEN side = 'sell' THEN 1 END)::int AS sell_count
      FROM trades
      WHERE timestamp >= NOW() - INTERVAL '30 minutes'
      GROUP BY symbol
      ORDER BY symbol
    `;
    res.json(rows.map(normalizeSignalRow));
  } catch (error) {
    res.status(500).json({ error: String(error) });
  }
});

// Price history for a symbol (1 min candles, last 60 min)
app.get("/api/analytics/price-history/:symbol", async (req, res) => {
  try {
    const symbol = req.params.symbol.toUpperCase();
    const tfRaw = String(req.query.tf || "5m").toLowerCase();

    const tfMap: Record<string, { seconds: number; lookback: string }> = {
      "1m": { seconds: 60, lookback: "6 hours" },
      "5m": { seconds: 300, lookback: "24 hours" },
      "15m": { seconds: 900, lookback: "3 days" },
      "1h": { seconds: 3600, lookback: "7 days" },
      "4h": { seconds: 14400, lookback: "30 days" },
      "8h": { seconds: 28800, lookback: "60 days" },
      "1d": { seconds: 86400, lookback: "180 days" },
    };

    const tfCfg = tfMap[tfRaw] || tfMap["5m"];
    const bucketSeconds = tfCfg.seconds;
    const lookbackLiteral = tfCfg.lookback;
    const binanceInterval = tfRaw in tfMap ? tfRaw : "5m";

    const rows = await sql`
      SELECT to_timestamp(FLOOR(EXTRACT(EPOCH FROM timestamp) / ${bucketSeconds}) * ${bucketSeconds}) AS minute,
             (ARRAY_AGG(price::double precision ORDER BY timestamp ASC))[1] AS open,
             MAX(price::double precision) AS high,
             MIN(price::double precision) AS low,
             (ARRAY_AGG(price::double precision ORDER BY timestamp DESC))[1] AS close,
             SUM(quantity::double precision)::double precision AS volume
      FROM trades
      WHERE symbol = ${symbol}
        AND timestamp >= NOW() - (${lookbackLiteral}::interval)
        AND price > 0
        AND quantity > 0
      GROUP BY minute
      ORDER BY minute ASC
    `;

    let merged = rows.map((r: any) => ({
      minute: new Date(r.minute).toISOString(),
      open: Number(r.open) || 0,
      high: Number(r.high) || 0,
      low: Number(r.low) || 0,
      close: Number(r.close) || 0,
      volume: Number(r.volume) || 0,
      source: "local",
    }));

    // If local candle history is too short, supplement from Binance spot klines.
    if (merged.length < 120) {
      try {
        const controller = new AbortController();
        const timeout = setTimeout(() => controller.abort(), 7000);
        const url = `https://api.binance.com/api/v3/klines?symbol=${encodeURIComponent(symbol)}&interval=${encodeURIComponent(binanceInterval)}&limit=500`;
        const resp = await fetch(url, { signal: controller.signal });
        clearTimeout(timeout);

        if (resp.ok) {
          const klines = await resp.json() as any[];
          const remote = (klines || []).map((k: any[]) => ({
            minute: new Date(Number(k[0])).toISOString(),
            open: Number(k[1]) || 0,
            high: Number(k[2]) || 0,
            low: Number(k[3]) || 0,
            close: Number(k[4]) || 0,
            volume: Number(k[5]) || 0,
            source: "binance",
          }));

          const byMinute = new Map<string, any>();
          for (const c of remote) byMinute.set(c.minute, c);
          for (const c of merged) byMinute.set(c.minute, c); // Local data overrides remote for freshness.

          merged = Array.from(byMinute.values()).sort((a, b) =>
            new Date(a.minute).getTime() - new Date(b.minute).getTime()
          );
        }
      } catch {
        // Silent fallback: keep serving local candles if remote kline call fails.
      }
    }

    res.json(merged);
  } catch (error) {
    res.status(500).json({ error: String(error) });
  }
});

// System stats
app.get("/api/analytics/system-stats", async (req, res) => {
  try {
    const [dbSize] = await sql`SELECT pg_database_size(current_database())::bigint AS size`;
    const [tradeRate] = await sql`
      SELECT COUNT(*)::int AS count FROM trades WHERE timestamp >= NOW() - INTERVAL '1 minute'
    `;
    const [totalTrades] = await sql`SELECT COUNT(*)::bigint AS count FROM trades`;
    const [oldestTrade] = await sql`SELECT MIN(timestamp) AS ts FROM trades`;
    const [newestTrade] = await sql`SELECT MAX(timestamp) AS ts FROM trades`;
    res.json({
      db_size_bytes: Number(dbSize.size),
      db_size_mb: Math.round(Number(dbSize.size) / 1024 / 1024),
      trades_per_minute: tradeRate.count,
      total_trades: Number(totalTrades.count),
      oldest_trade: oldestTrade.ts,
      newest_trade: newestTrade.ts,
      uptime_minutes: oldestTrade.ts ? Math.round((Date.now() - new Date(oldestTrade.ts).getTime()) / 60000) : 0
    });
  } catch (error) {
    res.status(500).json({ error: String(error) });
  }
});

// ─── Brain & AI Endpoints ───

app.get("/api/brain/status", async (req, res) => {
  try {
    const [patternCount] = await sql`SELECT COUNT(*)::int AS count FROM pattern_records`;
    const [learningTotal] = await sql`SELECT COUNT(*)::int AS count FROM brain_learning_log`;
    const [learningCorrect] = await sql`SELECT COUNT(*)::int AS count FROM brain_learning_log WHERE was_correct = TRUE`;
    const [avgPnl] = await sql`SELECT COALESCE(AVG(pnl_pct), 0)::double precision AS value FROM brain_learning_log`;

    const recentPatterns = await sql`
      SELECT symbol, outcome_15m::double precision, outcome_1h::double precision,
             outcome_4h::double precision, outcome_1d::double precision, created_at
      FROM pattern_records ORDER BY created_at DESC LIMIT 10
    `;

    const signalTypeStats = await sql`
      SELECT signal_type,
             COUNT(*)::int AS total,
             COUNT(CASE WHEN was_correct THEN 1 END)::int AS correct,
             COALESCE(AVG(pnl_pct), 0)::double precision AS avg_pnl
      FROM brain_learning_log
      GROUP BY signal_type
      ORDER BY total DESC
    `;

    res.json({
      pattern_count: patternCount.count,
      learning: {
        total: learningTotal.count,
        correct: learningCorrect.count,
        accuracy: learningTotal.count > 0 ? Number((learningCorrect.count / learningTotal.count * 100).toFixed(1)) : 0,
        avg_pnl: avgPnl.value,
      },
      recent_patterns: recentPatterns,
      signal_type_stats: signalTypeStats,
    });
  } catch (error) {
    res.status(500).json({ error: String(error) });
  }
});

// ─── Chat Endpoints ───

app.get("/api/chat/messages", async (req, res) => {
  try {
    const limit = Number(req.query.limit || 50);
    const messages = await sql`
      SELECT id, role, message, agent_name, created_at
      FROM chat_messages ORDER BY created_at DESC LIMIT ${limit}
    `;
    res.json(messages.reverse());
  } catch (error) {
    res.status(500).json({ error: String(error) });
  }
});

app.delete("/api/chat/messages", async (_req, res) => {
  try {
    const deleted = await sql`DELETE FROM chat_messages`;
    res.json({ success: true, deleted: deleted.count ?? 0 });
  } catch (error) {
    res.status(500).json({ error: String(error) });
  }
});

app.post("/api/chat/send", async (req, res) => {
  try {
    const { message } = req.body;
    if (!message || typeof message !== "string" || message.trim().length === 0) {
      return res.status(400).json({ error: "Message is required" });
    }
    const trimmed = message.trim().slice(0, 1000); // Max 1000 char
    const [row] = await sql`
      INSERT INTO chat_messages (role, message, agent_name)
      VALUES ('user', ${trimmed}, 'User')
      RETURNING id, role, message, agent_name, created_at
    `;
    res.json(row);
  } catch (error) {
    res.status(500).json({ error: String(error) });
  }
});

// ─── User Watchlist Endpoints ───

app.get("/api/watchlist", async (req, res) => {
  try {
    const rows = await sql`
      SELECT id, symbol, exchange, market_type, active, created_at
      FROM user_watchlist WHERE active = TRUE ORDER BY symbol
    `;
    res.json(rows);
  } catch (error) {
    res.status(500).json({ error: String(error) });
  }
});

app.post("/api/watchlist/add", async (req, res) => {
  try {
    const { symbol, exchange, market_type } = req.body;
    if (!symbol || typeof symbol !== "string") {
      return res.status(400).json({ error: "Symbol is required" });
    }
    const aliasMap: Record<string, string> = {
      BITCOIN: "BTC",
      ETHEREUM: "ETH",
      RIPPLE: "XRP",
      SOLANA: "SOL",
      CARDANO: "ADA",
      LITECOIN: "LTC",
      DOGECOIN: "DOGE",
    };
    let sym = symbol.toUpperCase().replace(/[^A-Z0-9]/g, '').slice(0, 20);
    if (aliasMap[sym]) {
      sym = aliasMap[sym];
    }
    if (sym && !sym.endsWith("USDT")) sym = `${sym}USDT`;

    const exchRaw = (exchange || "all").toLowerCase().slice(0, 50);
    const mtRaw = (market_type || "spot").toLowerCase();

    if (!["spot", "futures", "both"].includes(mtRaw)) {
      return res.status(400).json({ error: "market_type must be 'spot', 'futures' or 'both'" });
    }
    if (!["all", "binance", "bybit", "both"].includes(exchRaw)) {
      return res.status(400).json({ error: "exchange must be 'all', 'binance', 'bybit' or 'both'" });
    }

    const exchanges = exchRaw === "both" ? ["binance", "bybit"] : [exchRaw];
    const markets = mtRaw === "both" ? ["spot", "futures"] : [mtRaw];
    const addedRows: any[] = [];

    for (const exch of exchanges) {
      for (const mt of markets) {
        const [row] = await sql`
          INSERT INTO user_watchlist (symbol, exchange, market_type)
          VALUES (${sym}, ${exch}, ${mt})
          ON CONFLICT (symbol, exchange, market_type)
          DO UPDATE SET active = TRUE
          RETURNING id, symbol, exchange, market_type, active
        `;
        if (row) addedRows.push(row);
      }
    }

    const first = addedRows[0] || { symbol: sym, exchange: exchRaw, market_type: mtRaw, active: true };
    res.json({ success: true, ...first, entries: addedRows });
  } catch (error) {
    res.status(500).json({ error: String(error) });
  }
});

app.post("/api/watchlist/remove", async (req, res) => {
  try {
    const { symbol, exchange, market_type } = req.body;
    if (!symbol) return res.status(400).json({ error: "Symbol is required" });
    let sym = String(symbol).toUpperCase().replace(/[^A-Z0-9]/g, '').slice(0, 20);
    if (sym && !sym.endsWith("USDT")) sym = `${sym}USDT`;
    const exchRaw = (exchange || "all").toLowerCase();
    const mtRaw = (market_type || "spot").toLowerCase();

    const exchanges = exchRaw === "both" ? ["binance", "bybit"] : [exchRaw];
    const markets = mtRaw === "both" ? ["spot", "futures"] : [mtRaw];

    for (const exch of exchanges) {
      for (const mt of markets) {
        await sql`
          UPDATE user_watchlist SET active = FALSE
          WHERE symbol = ${sym} AND exchange = ${exch} AND market_type = ${mt}
        `;
      }
    }
    res.json({ success: true, symbol: sym, exchange: exchRaw, market_type: mtRaw });
  } catch (error) {
    res.status(500).json({ error: String(error) });
  }
});

// ─── Enhanced Simulations with details ───

app.get("/api/simulations/detailed", async (req, res) => {
  try {
    const rows = await sql`
      SELECT s.*, sig.signal_type, sig.confidence::double precision AS signal_confidence
      FROM simulations s
      LEFT JOIN signals sig ON s.signal_id = sig.id
      ORDER BY s.entry_time DESC LIMIT 50
    `;
    res.json(rows);
  } catch (error) {
    res.status(500).json({ error: String(error) });
  }
});

// ─── Learning Log ───

app.get("/api/brain/learning-log", async (req, res) => {
  try {
    const rows = await sql`
      SELECT id, signal_type, was_correct, pnl_pct::double precision AS pnl_pct,
             context, created_at
      FROM brain_learning_log ORDER BY created_at DESC LIMIT 50
    `;
    res.json(rows);
  } catch (error) {
    res.status(500).json({ error: String(error) });
  }
});

// ─── Enhanced Intelligence: Loss Autopsy ───

app.get("/api/brain/loss-autopsies", async (req, res) => {
  try {
    const limit = Math.min(100, Number(req.query.limit || 20));
    const rows = await sql`
      SELECT id, signal_id, symbol, signal_type, direction,
             entry_price::double precision AS entry_price,
             exit_price::double precision AS exit_price,
             loss_pct::double precision AS loss_pct,
             barrier_hit, duration_s::double precision AS duration_s,
             root_causes, microstructure, regime, fingerprint, temporal,
             lesson_rule, score::double precision AS score, created_at
      FROM loss_autopsies ORDER BY created_at DESC LIMIT ${limit}
    `;
    res.json(rows);
  } catch (error) {
    res.status(500).json({ error: String(error) });
  }
});

app.get("/api/brain/autopsy-rules", async (req, res) => {
  try {
    const rows = await sql`
      SELECT lesson_rule, COUNT(*)::int AS frequency,
             AVG(score)::double precision AS avg_score
      FROM loss_autopsies
      WHERE created_at > NOW() - INTERVAL '7 days'
      GROUP BY lesson_rule
      ORDER BY frequency DESC LIMIT 50
    `;
    res.json(rows);
  } catch (error) {
    res.status(500).json({ error: String(error) });
  }
});

app.get("/api/brain/bandit", async (_req, res) => {
  try {
    const rows = await sql`
      SELECT arm, alpha::double precision AS alpha, beta::double precision AS beta,
             n, last_ts::double precision AS last_ts
      FROM bandit_state ORDER BY (alpha / NULLIF(alpha+beta,0)) DESC
    `;
    const arms = rows.map((r: any) => ({
      arm: r.arm, alpha: r.alpha, beta: r.beta, n: r.n,
      expected_value: r.alpha / Math.max(r.alpha + r.beta, 1e-9),
      last_ts: r.last_ts,
    }));
    res.json({ arms });
  } catch (error) {
    res.status(500).json({ error: String(error) });
  }
});

app.get("/api/brain/barrier-stats", async (_req, res) => {
  try {
    const rows = await sql`
      SELECT signal_type, barrier_hit, COUNT(*)::int AS n,
             AVG(mfe_pct)::double precision AS avg_mfe,
             AVG(mae_pct)::double precision AS avg_mae,
             AVG(barrier_time_s)::double precision AS avg_time_s,
             AVG(risk_adjusted_return)::double precision AS avg_risk_adj
      FROM brain_learning_log
      WHERE barrier_hit IS NOT NULL
        AND created_at > NOW() - INTERVAL '14 days'
      GROUP BY signal_type, barrier_hit
      ORDER BY signal_type, barrier_hit
    `;
    res.json(rows);
  } catch (error) {
    res.status(500).json({ error: String(error) });
  }
});

// Son çözümlenen sinyal sonuçları — dashboard "Sonuç Paneli" için
app.get("/api/signals/outcomes", async (_req, res) => {
  try {
    const rows = await sql`
      SELECT s.id, s.symbol, s.signal_type,
             LOWER(COALESCE(
               NULLIF(s.metadata->>'direction', ''),
               NULLIF(s.metadata->>'position_bias', ''),
               NULLIF(s.metadata->'mamis_context'->>'direction', ''),
               CASE
                 WHEN POSITION('short' IN LOWER(COALESCE(s.signal_type, ''))) > 0 THEN 'short'
                 WHEN POSITION('long'  IN LOWER(COALESCE(s.signal_type, ''))) > 0 THEN 'long'
                 WHEN POSITION('sell'  IN LOWER(COALESCE(s.signal_type, ''))) > 0 THEN 'short'
                 WHEN POSITION('buy'   IN LOWER(COALESCE(s.signal_type, ''))) > 0 THEN 'long'
                 ELSE 'long'
               END
             )) AS direction,
             s.confidence::double precision AS confidence,
             s.price::double precision AS entry_price,
             COALESCE((s.metadata->>'target_price')::double precision, 0) AS target_price,
             COALESCE((s.metadata->>'target_pct')::double precision, 0) AS target_pct,
             COALESCE(
               (s.metadata->>'exit_price')::double precision,
               (s.metadata->>'close_price')::double precision,
               (s.metadata->>'actual_price')::double precision,
               sim.exit_price::double precision
             ) AS exit_price,
             s.status,
             s.timestamp AS signal_time,
             s.metadata,
             CASE
               WHEN s.status = 'target_hit' THEN 'win'
               WHEN s.status IN ('target_missed','expired','failed') THEN 'loss'
               WHEN s.status = 'closed' AND COALESCE(sim.pnl_pct, 0) > 0 THEN 'win'
               WHEN s.status = 'closed' AND COALESCE(sim.pnl_pct, 0) <= 0 THEN 'loss'
               WHEN s.metadata->'target_horizons' IS NOT NULL
                    AND EXISTS (SELECT 1 FROM jsonb_array_elements(s.metadata->'target_horizons') h
                                WHERE h->>'status' = 'hit') THEN 'win'
               WHEN s.metadata->'target_horizons' IS NOT NULL
                    AND NOT EXISTS (SELECT 1 FROM jsonb_array_elements(s.metadata->'target_horizons') h
                                    WHERE COALESCE(h->>'status','active') = 'active') THEN 'loss'
               ELSE 'neutral'
             END AS resolved_kind,
             COALESCE(
               sim.pnl_pct::double precision / 100.0,
               (SELECT (h->>'actual_change_pct')::double precision
                FROM jsonb_array_elements(s.metadata->'target_horizons') h
                WHERE h->>'status' = 'hit' LIMIT 1),
               CASE WHEN s.status = 'target_hit'
                 THEN COALESCE((s.metadata->>'target_pct')::double precision, 0.02)
                 ELSE 0 END
             ) AS actual_change_pct,
             COALESCE(
               sim.exit_time,
               (s.metadata->>'closed_at')::timestamptz,
               (s.metadata->>'exit_time')::timestamptz,
               (s.metadata->>'resolved_at')::timestamptz,
               (SELECT MAX((h->>'closed_at')::timestamptz)
                FROM jsonb_array_elements(s.metadata->'target_horizons') h
                WHERE h->>'closed_at' IS NOT NULL),
               (SELECT MAX((h->>'evaluated_at')::timestamptz)
                FROM jsonb_array_elements(s.metadata->'target_horizons') h
                WHERE h->>'evaluated_at' IS NOT NULL),
               s.timestamp
             ) AS resolved_at
      FROM signals s
      LEFT JOIN LATERAL (
        SELECT pnl_pct, exit_time, exit_price FROM simulations
        WHERE signal_id = s.id AND status = 'closed'
        ORDER BY exit_time DESC NULLS LAST LIMIT 1
      ) sim ON TRUE
      WHERE s.timestamp > NOW() - INTERVAL '7 days'
        AND COALESCE(s.metadata->>'source', s.metadata->>'signal_provider', 'unknown') IN ('strategist','pattern_matcher')
        AND (
          s.status IN ('target_hit','target_missed','closed','expired','failed')
          OR (s.metadata->'target_horizons' IS NOT NULL
              AND NOT EXISTS (
                SELECT 1 FROM jsonb_array_elements(s.metadata->'target_horizons') h
                WHERE COALESCE(h->>'status', 'active') = 'active'
              ))
        )
      ORDER BY COALESCE(sim.exit_time, s.timestamp) DESC
      LIMIT 200
    `;
    res.json(rows);
  } catch (error) {
    res.status(500).json({ error: String(error) });
  }
});

// Kazanan/kaybeden imzalar — pattern hatırlama görünürlüğü
app.get("/api/signals/top-patterns", async (_req, res) => {
  try {
    const rows = await sql`
      SELECT signal_type,
             COUNT(*)::int AS samples,
             SUM(CASE WHEN was_correct THEN 1 ELSE 0 END)::int AS wins,
             AVG(CASE WHEN was_correct THEN 1.0 ELSE 0.0 END)::double precision AS win_rate,
             AVG(pnl_pct)::double precision AS avg_pnl,
             AVG(confidence)::double precision AS avg_confidence
      FROM brain_learning_log
      WHERE created_at > NOW() - INTERVAL '21 days'
        AND was_correct IS NOT NULL
      GROUP BY signal_type
      HAVING COUNT(*) >= 3
      ORDER BY win_rate DESC, samples DESC
      LIMIT 30
    `;
    res.json(rows);
  } catch (error) {
    res.status(500).json({ error: String(error) });
  }
});

// ─── Admin Auth ───
const ADMIN_PIN = process.env.ADMIN_PIN || "BABA";

app.post("/api/admin/login", (req, res) => {
  const { pin } = req.body;
  if (pin === ADMIN_PIN) {
    res.json({ success: true, token: Buffer.from(`admin:${Date.now()}`).toString("base64") });
  } else {
    res.status(401).json({ success: false, error: "Yanlış PIN" });
  }
});

// ─── Live Data Stream - Canlı Veri Akışı Doğrulama ───

app.get("/api/live/data-stream", async (req, res) => {
  try {
    const limit = Number(req.query.limit || 30);
    const latestTrades = await sql`
      SELECT id, exchange, market_type, symbol, price::double precision AS price,
             quantity::double precision AS quantity, side, timestamp, trade_id
      FROM trades ORDER BY timestamp DESC LIMIT ${limit}
    `;
    // Exchange freshness with age_seconds
    const freshness = await sql`
      SELECT exchange, market_type,
             MAX(timestamp) AS latest_time,
             COUNT(*)::int AS trades_5min,
             EXTRACT(EPOCH FROM (NOW() - MAX(timestamp)))::double precision AS age_seconds
      FROM trades
      WHERE timestamp >= NOW() - INTERVAL '5 minutes'
      GROUP BY exchange, market_type
      ORDER BY latest_time DESC
    `;
    // 5 minute breakdown per minute
    const breakdown = await sql`
      SELECT date_trunc('minute', timestamp) AS period,
             COUNT(*)::int AS trade_count,
             SUM((price * quantity)::double precision)::double precision AS total_volume
      FROM trades
      WHERE timestamp >= NOW() - INTERVAL '5 minutes'
      GROUP BY period
      ORDER BY period DESC
    `;
    res.json({
      latest_trades: latestTrades,
      exchange_freshness: freshness,
      five_min_breakdown: breakdown
    });
  } catch (error) {
    res.status(500).json({ error: String(error) });
  }
});

// ─── Admin Config Yönetimi ───

app.get("/api/admin/config", async (req, res) => {
  try {
    const configs = await sql`SELECT * FROM agent_config ORDER BY agent_name, config_key`;
    res.json(configs);
  } catch (error) {
    res.status(500).json({ error: String(error) });
  }
});

app.post("/api/admin/config", async (req, res) => {
  try {
    const { agent_name, config_key, config_value } = req.body;
    if (!agent_name || !config_key) {
      return res.status(400).json({ error: "agent_name and config_key required" });
    }
    const safeAgent = String(agent_name).slice(0, 50);
    const safeKey = String(config_key).slice(0, 100);
    await sql`
      INSERT INTO agent_config (agent_name, config_key, config_value, updated_at)
      VALUES (${safeAgent}, ${safeKey}, ${JSON.stringify(config_value)}, NOW())
      ON CONFLICT (agent_name, config_key)
      DO UPDATE SET config_value = ${JSON.stringify(config_value)}, updated_at = NOW()
    `;
    res.json({ success: true });
  } catch (error) {
    res.status(500).json({ error: String(error) });
  }
});

// ─── Audit Log ───

app.get("/api/admin/audit-records", async (req, res) => {
  try {
    const rows = await sql`
      SELECT id, timestamp, total_simulations, successful_simulations, failed_simulations,
             success_rate::double precision AS success_rate,
             avg_win_pct::double precision AS avg_win_pct,
             avg_loss_pct::double precision AS avg_loss_pct,
             metadata, created_at
      FROM audit_records ORDER BY timestamp DESC LIMIT 50
    `;
    res.json(rows);
  } catch (error) {
    res.status(500).json({ error: String(error) });
  }
});

app.get("/api/admin/failure-analysis", async (req, res) => {
  try {
    const rows = await sql`
      SELECT id, timestamp, signal_type, failure_count,
             avg_loss_pct::double precision AS avg_loss_pct,
             recommendation, metadata, created_at
      FROM failure_analysis ORDER BY timestamp DESC LIMIT 50
    `;
    res.json(rows);
  } catch (error) {
    res.status(500).json({ error: String(error) });
  }
});

// ─── DB Table Stats ───

app.get("/api/admin/table-stats", async (req, res) => {
  try {
    const tables = ['trades', 'price_movements', 'signals', 'simulations', 'pattern_records',
                    'brain_learning_log', 'chat_messages', 'audit_records', 'failure_analysis',
                    'user_watchlist', 'watchlist', 'blacklist_patterns', 'agent_config', 'audit_reports',
                    'agent_heartbeat'];
    const stats = [];
    for (const table of tables) {
      try {
        const [row] = await sql.unsafe(`SELECT COUNT(*)::int AS count FROM ${table}`);
        stats.push({ table_name: table, row_count: row.count });
      } catch {
        stats.push({ table_name: table, row_count: -1 });
      }
    }
    res.json(stats);
  } catch (error) {
    res.status(500).json({ error: String(error) });
  }
});

// ─── Trade History by Symbol (paginated) ───

app.get("/api/trades/history/:symbol", async (req, res) => {
  try {
    const symbol = req.params.symbol.toUpperCase();
    const page = Math.max(1, Number(req.query.page || 1));
    const limit = Math.min(100, Number(req.query.limit || 50));
    const offset = (page - 1) * limit;
    const [total] = await sql`SELECT COUNT(*)::int AS count FROM trades WHERE symbol = ${symbol}`;
    const rows = await sql`
      SELECT id, exchange, market_type, symbol, price::double precision AS price,
             quantity::double precision AS quantity, side, timestamp
      FROM trades WHERE symbol = ${symbol}
      ORDER BY timestamp DESC LIMIT ${limit} OFFSET ${offset}
    `;
    res.json({ data: rows, total: total.count, page, pages: Math.ceil(total.count / limit) });
  } catch (error) {
    res.status(500).json({ error: String(error) });
  }
});

// ─── Signal History (all, with filter) ───

app.get("/api/signals/history", async (req, res) => {
  try {
    const rawStatus = (req.query.status as string | undefined)?.toLowerCase();
    // UI uses generic labels; map to DB status sets.
    const MEANINGFUL_STATUSES = ['processed','target_hit','target_missed','expired','closed'];
    const statusMap: Record<string, string[]> = {
      active: ['processed'],
      open: ['processed'],
      closed: ['target_hit','target_missed','closed'],
      expired: ['expired'],
      target_hit: ['target_hit'],
      target_missed: ['target_missed'],
    };
    const statusList: string[] = rawStatus
      ? (statusMap[rawStatus] ?? [rawStatus])
      : MEANINGFUL_STATUSES;
    const symbol = req.query.symbol ? String(req.query.symbol).toUpperCase() : undefined;
    const limit = Math.min(200, Number(req.query.limit || 100));
    let rows;
    if (symbol) {
      rows = await sql`SELECT *, confidence::double precision AS confidence, price::double precision AS price,
        COALESCE(metadata->>'signal_time', timestamp::text) AS signal_time,
        COALESCE((metadata->>'entry_price')::double precision, price::double precision) AS entry_price,
        COALESCE((metadata->>'current_price_at_signal')::double precision, price::double precision) AS current_price_at_signal,
        COALESCE(NULLIF((metadata->>'target_price')::double precision, 0), price::double precision) AS target_price,
        GREATEST(
          CASE WHEN COALESCE((metadata->>'target_pct')::double precision, 0.02) > 0.5
            THEN COALESCE((metadata->>'target_pct')::double precision, 0.02) / 100.0
            ELSE COALESCE((metadata->>'target_pct')::double precision, 0.02)
          END, 0.02
        ) AS target_pct,
        COALESCE((metadata->>'estimated_duration_to_target_minutes')::int, 60) AS estimated_duration_to_target_minutes
        FROM signals
        WHERE status = ANY(${statusList})
          AND symbol = ${symbol}
        ORDER BY timestamp DESC LIMIT ${limit}`;
    } else {
      rows = await sql`SELECT *, confidence::double precision AS confidence, price::double precision AS price,
        COALESCE(metadata->>'signal_time', timestamp::text) AS signal_time,
        COALESCE((metadata->>'entry_price')::double precision, price::double precision) AS entry_price,
        COALESCE((metadata->>'current_price_at_signal')::double precision, price::double precision) AS current_price_at_signal,
        COALESCE(NULLIF((metadata->>'target_price')::double precision, 0), price::double precision) AS target_price,
        GREATEST(
          CASE WHEN COALESCE((metadata->>'target_pct')::double precision, 0.02) > 0.5
            THEN COALESCE((metadata->>'target_pct')::double precision, 0.02) / 100.0
            ELSE COALESCE((metadata->>'target_pct')::double precision, 0.02)
          END, 0.02
        ) AS target_pct,
        COALESCE((metadata->>'estimated_duration_to_target_minutes')::int, 60) AS estimated_duration_to_target_minutes
        FROM signals
        WHERE status = ANY(${statusList})
        ORDER BY timestamp DESC LIMIT ${limit}`;
    }
    res.json(rows.map(normalizeSignalRow));
  } catch (error) {
    res.status(500).json({ error: String(error) });
  }
});

// ─── Simulation Detail ───

app.get("/api/simulations/:id", async (req, res) => {
  try {
    const id = Number(req.params.id);
    const [sim] = await sql`
      SELECT s.*, sig.signal_type, sig.confidence::double precision AS signal_confidence,
             sig.metadata AS signal_metadata
      FROM simulations s LEFT JOIN signals sig ON s.signal_id = sig.id
      WHERE s.id = ${id}
    `;
    if (!sim) return res.status(404).json({ error: "Not found" });
    res.json(sim);
  } catch (error) {
    res.status(500).json({ error: String(error) });
  }
});

// ─── PnL Timeline ───

app.get("/api/analytics/pnl-timeline", async (req, res) => {
  try {
    const rows = await sql`
      SELECT id, symbol, side,
             entry_price::double precision AS entry_price,
             exit_price::double precision AS exit_price,
             pnl::double precision AS pnl,
             pnl_pct::double precision AS pnl_pct,
             entry_time, exit_time, status
      FROM simulations
      WHERE status = 'closed'
      ORDER BY exit_time ASC
    `;
    // Cumulative PnL
    let cumulative = 0;
    const timeline = rows.map((r: any) => {
      cumulative += r.pnl || 0;
      return { ...r, cumulative_pnl: cumulative };
    });
    res.json(timeline);
  } catch (error) {
    res.status(500).json({ error: String(error) });
  }
});

// ─── Brain Memory Access ───

app.get("/api/brain/patterns", async (req, res) => {
  try {
    const symbol = req.query.symbol ? String(req.query.symbol).toUpperCase() : undefined;
    const limit = Math.min(100, Number(req.query.limit || 30));
    let rows;
    if (symbol) {
      rows = await sql`
        SELECT id, symbol, exchange, market_type, snapshot_data,
               outcome_15m::double precision, outcome_1h::double precision,
               outcome_4h::double precision, outcome_1d::double precision, created_at
        FROM pattern_records WHERE symbol = ${symbol} ORDER BY created_at DESC LIMIT ${limit}
      `;
    } else {
      rows = await sql`
        SELECT id, symbol, exchange, market_type, snapshot_data,
               outcome_15m::double precision, outcome_1h::double precision,
               outcome_4h::double precision, outcome_1d::double precision, created_at
        FROM pattern_records ORDER BY created_at DESC LIMIT ${limit}
      `;
    }
    res.json(rows);
  } catch (error) {
    res.status(500).json({ error: String(error) });
  }
});

app.get("/api/brain/learning-stats", async (req, res) => {
  try {
    const [total] = await sql`SELECT COUNT(*)::int AS count FROM brain_learning_log`;
    const [correct] = await sql`SELECT COUNT(*)::int AS count FROM brain_learning_log WHERE was_correct = TRUE`;
    const [avgPnl] = await sql`SELECT COALESCE(AVG(pnl_pct), 0)::double precision AS val FROM brain_learning_log`;
    // Per day accuracy
    const dailyAccuracy = await sql`
      SELECT date_trunc('day', created_at) AS day,
             COUNT(*)::int AS total,
             COUNT(CASE WHEN was_correct THEN 1 END)::int AS correct
      FROM brain_learning_log
      GROUP BY day ORDER BY day DESC LIMIT 14
    `;
    // Per signal type
    const byType = await sql`
      SELECT signal_type,
             COUNT(*)::int AS total,
             COUNT(CASE WHEN was_correct THEN 1 END)::int AS correct,
             COALESCE(AVG(pnl_pct), 0)::double precision AS avg_pnl,
             SUM(pnl_pct)::double precision AS total_pnl
      FROM brain_learning_log GROUP BY signal_type ORDER BY total DESC
    `;
    res.json({
      total: total.count, correct: correct.count,
      accuracy: total.count > 0 ? (correct.count / total.count * 100) : 0,
      avg_pnl: avgPnl.val,
      daily_accuracy: dailyAccuracy,
      by_type: byType,
    });
  } catch (error) {
    res.status(500).json({ error: String(error) });
  }
});

// ─── RCA Results ───
app.get("/api/rca/results", async (req, res) => {
  try {
    const rows = await sql`
      SELECT r.*, s.symbol, s.side, s.entry_price::double precision AS entry_price,
             s.exit_price::double precision AS exit_price,
             s.pnl::double precision AS sim_pnl,
             s.pnl_pct::double precision AS sim_pnl_pct
      FROM rca_results r
      LEFT JOIN simulations s ON r.simulation_id = s.id
      ORDER BY r.created_at DESC LIMIT 50
    `;
    res.json(rows);
  } catch (error) {
    res.status(500).json({ error: String(error) });
  }
});

app.get("/api/rca/stats", async (req, res) => {
  try {
    const distribution = await sql`
      SELECT failure_type, COUNT(*)::int AS count,
             COALESCE(AVG(confidence), 0)::double precision AS avg_confidence
      FROM rca_results GROUP BY failure_type ORDER BY count DESC
    `;
    const [total] = await sql`SELECT COUNT(*)::int AS count FROM rca_results`;
    res.json({ total: total.count, distribution });
  } catch (error) {
    res.status(500).json({ error: String(error) });
  }
});

// ─── Correction Notes ───
app.get("/api/corrections", async (req, res) => {
  try {
    const rows = await sql`
      SELECT * FROM correction_notes ORDER BY created_at DESC LIMIT 100
    `;
    res.json(rows);
  } catch (error) {
    res.status(500).json({ error: String(error) });
  }
});

app.get("/api/corrections/pending", async (req, res) => {
  try {
    const rows = await sql`
      SELECT * FROM correction_notes WHERE applied = FALSE ORDER BY created_at DESC
    `;
    res.json(rows);
  } catch (error) {
    res.status(500).json({ error: String(error) });
  }
});

// ─── Historical Signatures ───
app.get("/api/signatures", async (req, res) => {
  try {
    const symbol = req.query.symbol ? String(req.query.symbol).toUpperCase() : undefined;
    const limit = Math.min(200, Number(req.query.limit || 50));
    let rows;
    if (symbol) {
      rows = await sql`
        SELECT id, symbol, market_type, timeframe, direction,
               change_pct::double precision AS change_pct,
               pre_move_indicators, volume_profile, created_at
        FROM historical_signatures
        WHERE symbol = ${symbol}
        ORDER BY created_at DESC LIMIT ${limit}
      `;
    } else {
      rows = await sql`
        SELECT id, symbol, market_type, timeframe, direction,
               change_pct::double precision AS change_pct,
               pre_move_indicators, volume_profile, created_at
        FROM historical_signatures
        ORDER BY created_at DESC LIMIT ${limit}
      `;
    }
    res.json(rows);
  } catch (error) {
    res.status(500).json({ error: String(error) });
  }
});

// ─── Signature Matches (Neuro-Symbolic Engine) ───
app.get("/api/signature-matches", async (req, res) => {
  try {
    const symbol = req.query.symbol ? String(req.query.symbol).toUpperCase() : undefined;
    const minSimilarity = Math.max(0, Math.min(1, Number(req.query.min_similarity || 0.6)));
    const limit = Math.min(100, Number(req.query.limit || 20));
    const hours = Math.min(168, Number(req.query.hours || 24));
    const cutoff = new Date(Date.now() - hours * 3600 * 1000);

    let rows;
    if (symbol) {
      rows = await sql`
        SELECT s.id, s.symbol, s.timeframe, s.direction,
               s.similarity::double precision AS similarity,
               s.dtw_score::double precision AS dtw_score,
               s.fft_score::double precision AS fft_score,
               s.cosine_score::double precision AS cosine_score,
               s.poly_score::double precision AS poly_score,
               s.matched_signature_id, s.match_label, s.pattern_name,
               s.historical_timestamp, s.historical_price::double precision AS historical_price,
               s.historical_end_price::double precision AS historical_end_price,
               s.historical_volume_ratio::double precision AS historical_volume_ratio,
               s.context_string, s.current_price::double precision AS current_price,
               s.created_at
        FROM signature_matches s
        WHERE s.symbol = ${symbol}
          AND s.similarity >= ${minSimilarity}
          AND s.created_at >= ${cutoff}
        ORDER BY s.similarity DESC, s.created_at DESC
        LIMIT ${limit}
      `;
    } else {
      rows = await sql`
        SELECT s.id, s.symbol, s.timeframe, s.direction,
               s.similarity::double precision AS similarity,
               s.dtw_score::double precision AS dtw_score,
               s.fft_score::double precision AS fft_score,
               s.cosine_score::double precision AS cosine_score,
               s.poly_score::double precision AS poly_score,
               s.matched_signature_id, s.match_label, s.pattern_name,
               s.historical_timestamp, s.historical_price::double precision AS historical_price,
               s.historical_end_price::double precision AS historical_end_price,
               s.historical_volume_ratio::double precision AS historical_volume_ratio,
               s.context_string, s.current_price::double precision AS current_price,
               s.created_at
        FROM signature_matches s
        WHERE s.similarity >= ${minSimilarity}
          AND s.created_at >= ${cutoff}
        ORDER BY s.similarity DESC, s.created_at DESC
        LIMIT ${limit}
      `;
    }

    // Fallback: primary feed `pattern_match_results` — UI kullanabilsin diye mapliyoruz.
    if (!rows || rows.length === 0) {
      const fallback = symbol
        ? await sql`
            SELECT p.id, p.symbol, p.timeframe,
                   COALESCE(p.predicted_direction, p.matched_direction) AS direction,
                   p.similarity::double precision AS similarity,
                   NULL::double precision AS dtw_score,
                   NULL::double precision AS fft_score,
                   NULL::double precision AS cosine_score,
                   NULL::double precision AS poly_score,
                   p.matched_signature_id,
                   p.brain_decision AS match_label,
                   NULL::text AS pattern_name,
                   NULL::timestamp AS historical_timestamp,
                   NULL::double precision AS historical_price,
                   NULL::double precision AS historical_end_price,
                   NULL::double precision AS historical_volume_ratio,
                   COALESCE(
                     NULLIF(p.brain_reasoning, ''),
                     CONCAT('→ ', p.predicted_direction, ' ', ROUND((p.predicted_magnitude * 100)::numeric, 2), '% · conf ', ROUND((p.confidence * 100)::numeric, 0), '%')
                   ) AS context_string,
                   COALESCE(
                     (SELECT price::double precision FROM trades t WHERE t.symbol = p.symbol ORDER BY t.timestamp DESC LIMIT 1),
                     0
                   ) AS current_price,
                   p.created_at
            FROM pattern_match_results p
            WHERE p.symbol = ${symbol}
              AND p.similarity >= ${minSimilarity}
              AND p.created_at >= ${cutoff}
            ORDER BY p.similarity DESC, p.created_at DESC
            LIMIT ${limit}
          `
        : await sql`
            SELECT p.id, p.symbol, p.timeframe,
                   COALESCE(p.predicted_direction, p.matched_direction) AS direction,
                   p.similarity::double precision AS similarity,
                   NULL::double precision AS dtw_score,
                   NULL::double precision AS fft_score,
                   NULL::double precision AS cosine_score,
                   NULL::double precision AS poly_score,
                   p.matched_signature_id,
                   p.brain_decision AS match_label,
                   NULL::text AS pattern_name,
                   NULL::timestamp AS historical_timestamp,
                   NULL::double precision AS historical_price,
                   NULL::double precision AS historical_end_price,
                   NULL::double precision AS historical_volume_ratio,
                   COALESCE(
                     NULLIF(p.brain_reasoning, ''),
                     CONCAT('→ ', p.predicted_direction, ' ', ROUND((p.predicted_magnitude * 100)::numeric, 2), '% · conf ', ROUND((p.confidence * 100)::numeric, 0), '%')
                   ) AS context_string,
                   COALESCE(
                     (SELECT price::double precision FROM trades t WHERE t.symbol = p.symbol ORDER BY t.timestamp DESC LIMIT 1),
                     0
                   ) AS current_price,
                   p.created_at
            FROM pattern_match_results p
            WHERE p.similarity >= ${minSimilarity}
              AND p.created_at >= ${cutoff}
            ORDER BY p.similarity DESC, p.created_at DESC
            LIMIT ${limit}
          `;
      rows = fallback;
    }

    res.json(rows);
  } catch (error) {
    res.status(500).json({ error: String(error) });
  }
});

// ─── State History ───
app.get("/api/state/history", async (req, res) => {
  try {
    const hours = Math.min(168, Number(req.query.hours || 24));
    const cutoff = new Date(Date.now() - hours * 3600 * 1000);
    const rows = await sql`
      SELECT timestamp, mode, cumulative_pnl::double precision AS cumulative_pnl,
             daily_pnl::double precision AS daily_pnl,
             daily_trade_count, current_drawdown::double precision AS current_drawdown,
             win_rate::double precision AS win_rate,
             active_positions, total_trades, metadata
      FROM state_history
      WHERE timestamp >= ${cutoff}
      ORDER BY timestamp ASC
      LIMIT 500
    `;
    res.json(rows);
  } catch (error) {
    res.status(500).json({ error: String(error) });
  }
});

// ─── Signals Summary by Type ───
app.get("/api/signals/summary", async (req, res) => {
  try {
    const byType = await sql`
      SELECT signal_type, COUNT(*)::int AS total,
             COUNT(CASE WHEN status = 'pending' THEN 1 END)::int AS pending,
             COUNT(CASE WHEN status = 'processed' THEN 1 END)::int AS processed,
             COUNT(CASE WHEN status LIKE 'risk_%' THEN 1 END)::int AS risk_rejected,
             COALESCE(AVG(confidence), 0)::double precision AS avg_confidence
      FROM signals GROUP BY signal_type ORDER BY total DESC
    `;
    const [total] = await sql`SELECT COUNT(*)::int AS count FROM signals`;
    res.json({ total: total.count, by_type: byType });
  } catch (error) {
    res.status(500).json({ error: String(error) });
  }
});

/* ═══ LLM Directive Proxy (forwards to Python directive API on port 3002) ═══ */
const DIRECTIVE_API = process.env.QUENBOT_DIRECTIVE_API || "http://127.0.0.1:3002";

app.get("/api/directives", async (req, res) => {
  try {
    const r = await fetch(`${DIRECTIVE_API}/api/directives`);
    res.json(await r.json());
  } catch { res.json({ master_directive: "", agent_overrides: {}, error: "Directive API unavailable" }); }
});

app.post("/api/directives", async (req, res) => {
  try {
    const r = await fetch(`${DIRECTIVE_API}/api/directives`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(req.body) });
    res.json(await r.json());
  } catch (e) { res.status(502).json({ error: "Directive API unavailable" }); }
});

app.delete("/api/directives", async (req, res) => {
  try {
    const r = await fetch(`${DIRECTIVE_API}/api/directives`, { method: "DELETE" });
    res.json(await r.json());
  } catch { res.json({ status: "Directive API unavailable" }); }
});

app.get("/api/code/status", async (_req, res) => {
  try {
    const r = await fetch(`${DIRECTIVE_API}/api/code/status`);
    res.status(r.status).json(await r.json());
  } catch {
    res.json({ enabled: false, error: "Code operator unavailable" });
  }
});

app.get("/api/code/tasks", async (req, res) => {
  try {
    const limit = Number(req.query.limit || 20);
    const r = await fetch(`${DIRECTIVE_API}/api/code/tasks?limit=${encodeURIComponent(String(limit))}`);
    res.status(r.status).json(await r.json());
  } catch {
    res.json({ items: [], error: "Code operator unavailable" });
  }
});

app.post("/api/code/tasks", async (req, res) => {
  try {
    const r = await fetch(`${DIRECTIVE_API}/api/code/tasks`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(req.body || {}),
    });
    res.status(r.status).json(await r.json());
  } catch {
    res.status(502).json({ error: "Code operator unavailable" });
  }
});

app.post("/api/code/tasks/:taskId/apply", async (req, res) => {
  try {
    const taskId = String(req.params.taskId || "").trim();
    const r = await fetch(`${DIRECTIVE_API}/api/code/tasks/${encodeURIComponent(taskId)}/apply`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(req.body || {}),
    });
    res.status(r.status).json(await r.json());
  } catch {
    res.status(502).json({ error: "Code operator unavailable" });
  }
});

app.get("/api/llm/status", async (req, res) => {
  try {
    const r = await fetch(`${DIRECTIVE_API}/api/llm/status`);
    res.json(await r.json());
  } catch { res.json({ healthy: false, error: "LLM API unavailable" }); }
});

app.get("/api/llm/queue", async (req, res) => {
  try {
    const r = await fetch(`${DIRECTIVE_API}/api/llm/queue`);
    res.json(await r.json());
  } catch { res.json({ queue_size: 0, error: "Queue API unavailable" }); }
});

/* ═══ System Resource & Status Proxy ═══ */
app.get("/api/system/resources", async (req, res) => {
  try {
    const compact = req.query.compact === "1" ? "?compact=1" : "";
    const r = await fetch(`${DIRECTIVE_API}/api/system/resources${compact}`);
    res.json(await r.json());
  } catch {
    // Fallback: read from heartbeat DB
    try {
      const [hb] = await sql`SELECT metadata FROM agent_heartbeat WHERE agent_name = 'system_resources'`;
      if (hb?.metadata) {
        const m = typeof hb.metadata === 'string' ? JSON.parse(hb.metadata) : hb.metadata;
        res.json(m);
      } else {
        res.json({ error: "Resource monitor unavailable", cpu_percent: 0, ram_percent: 0, disk_percent: 0 });
      }
    } catch { res.json({ error: "Resource monitor unavailable" }); }
  }
});

app.get("/api/system/summary", async (req, res) => {
  try {
    const r = await fetch(`${DIRECTIVE_API}/api/system/summary`);
    res.json(await r.json());
  } catch { res.json({ mode: "unknown", error: "System API unavailable" }); }
});

app.get("/api/system/events", async (req, res) => {
  try {
    const limit = Number(req.query.limit || 200);
    const r = await fetch(`${DIRECTIVE_API}/api/system/events?limit=${limit}`);
    res.json(await r.json());
  } catch { res.json({ total_events: 0, recent_events: [], error: "Event API unavailable" }); }
});

app.get("/api/mamis/status", async (_req, res) => {
  try {
    const r = await fetch(`${DIRECTIVE_API}/api/mamis/status`);
    res.json(await r.json());
  } catch {
    res.json({ health: { healthy: false }, bars: [], classifications: [], signals: [], error: "MAMIS API unavailable" });
  }
});

/* ═══ Intel Upgrade (Phase 1-5) Proxy ═══ */
app.get("/api/intel/summary", async (_req, res) => {
  try {
    const r = await fetch(`${DIRECTIVE_API}/api/intel/summary`);
    res.json(await r.json());
  } catch { res.json({ error: "intel summary unavailable" }); }
});
app.get("/api/confluence/:symbol", async (req, res) => {
  try {
    const r = await fetch(`${DIRECTIVE_API}/api/confluence/${encodeURIComponent(req.params.symbol)}`);
    res.status(r.status).json(await r.json());
  } catch { res.status(502).json({ error: "confluence unavailable" }); }
});
app.get("/api/cross-asset/graph", async (_req, res) => {
  try {
    const r = await fetch(`${DIRECTIVE_API}/api/cross-asset/graph`);
    res.status(r.status).json(await r.json());
  } catch { res.status(502).json({ error: "cross_asset unavailable" }); }
});
app.get("/api/cross-asset/:symbol", async (req, res) => {
  try {
    const r = await fetch(`${DIRECTIVE_API}/api/cross-asset/${encodeURIComponent(req.params.symbol)}`);
    res.status(r.status).json(await r.json());
  } catch { res.status(502).json({ error: "cross_asset unavailable" }); }
});
app.get("/api/fast-brain/:symbol", async (req, res) => {
  try {
    const r = await fetch(`${DIRECTIVE_API}/api/fast-brain/${encodeURIComponent(req.params.symbol)}`);
    res.status(r.status).json(await r.json());
  } catch { res.status(502).json({ error: "fast_brain unavailable" }); }
});
app.get("/api/decision-router/status", async (_req, res) => {
  try {
    const r = await fetch(`${DIRECTIVE_API}/api/decision-router/status`);
    res.json(await r.json());
  } catch { res.json({ enabled: false, error: "decision_router unavailable" }); }
});
app.get("/api/online-learning/stats", async (req, res) => {
  try {
    const qs = req.query.symbol ? `?symbol=${encodeURIComponent(String(req.query.symbol))}` : "";
    const r = await fetch(`${DIRECTIVE_API}/api/online-learning/stats${qs}`);
    res.json(await r.json());
  } catch { res.json({ enabled: false, error: "online_learning unavailable" }); }
});

/* ═══ Phase 6: Oracle Stack + Brain + Runtime Proxy ═══ */
app.get("/api/oracle/summary", async (_req, res) => {
  try {
    const r = await fetch(`${DIRECTIVE_API}/api/oracle/summary`);
    res.status(r.status).json(await r.json());
  } catch { res.status(502).json({ enabled: false, error: "oracle summary unavailable" }); }
});
app.get("/api/oracle/channels/:symbol", async (req, res) => {
  try {
    const r = await fetch(`${DIRECTIVE_API}/api/oracle/channels/${encodeURIComponent(req.params.symbol)}`);
    res.status(r.status).json(await r.json());
  } catch { res.status(502).json({ error: "oracle channels unavailable" }); }
});
app.get("/api/oracle/detector/:name", async (req, res) => {
  try {
    const r = await fetch(`${DIRECTIVE_API}/api/oracle/detector/${encodeURIComponent(req.params.name)}`);
    res.status(r.status).json(await r.json());
  } catch { res.status(502).json({ error: "oracle detector unavailable" }); }
});
app.get("/api/oracle/factor-graph", async (_req, res) => {
  try {
    const r = await fetch(`${DIRECTIVE_API}/api/oracle/factor-graph`);
    res.status(r.status).json(await r.json());
  } catch { res.status(502).json({ enabled: false, error: "factor-graph unavailable" }); }
});
app.get("/api/oracle/factor-graph/:symbol", async (req, res) => {
  try {
    const r = await fetch(`${DIRECTIVE_API}/api/oracle/factor-graph/${encodeURIComponent(req.params.symbol)}`);
    res.status(r.status).json(await r.json());
  } catch { res.status(502).json({ enabled: false, error: "factor-graph unavailable" }); }
});
app.get("/api/oracle/brain/directives", async (_req, res) => {
  try {
    const r = await fetch(`${DIRECTIVE_API}/api/oracle/brain/directives`);
    res.status(r.status).json(await r.json());
  } catch { res.status(502).json({ enabled: false, error: "brain directives unavailable" }); }
});
app.get("/api/oracle/brain/traces", async (req, res) => {
  try {
    const qs = req.query.limit ? `?limit=${encodeURIComponent(String(req.query.limit))}` : "";
    const r = await fetch(`${DIRECTIVE_API}/api/oracle/brain/traces${qs}`);
    res.status(r.status).json(await r.json());
  } catch { res.status(502).json({ enabled: false, error: "brain traces unavailable" }); }
});
app.get("/api/oracle/brain/health", async (_req, res) => {
  try {
    const r = await fetch(`${DIRECTIVE_API}/api/oracle/brain/health`);
    res.status(r.status).json(await r.json());
  } catch { res.status(502).json({ enabled: false, error: "brain health unavailable" }); }
});

/* ─── Aşama 1: Gatekeeper + AutoRollback + Warmup proxies ─── */
app.get("/api/oracle/gatekeeper/stats", async (_req, res) => {
  try {
    const r = await fetch(`${DIRECTIVE_API}/api/oracle/gatekeeper/stats`);
    res.status(r.status).json(await r.json());
  } catch { res.status(502).json({ enabled: false, error: "gatekeeper unavailable" }); }
});
app.get("/api/oracle/autorollback/status", async (_req, res) => {
  try {
    const r = await fetch(`${DIRECTIVE_API}/api/oracle/autorollback/status`);
    res.status(r.status).json(await r.json());
  } catch { res.status(502).json({ enabled: false, error: "autorollback unavailable" }); }
});
app.post("/api/oracle/autorollback/force", async (req, res) => {
  try {
    const r = await fetch(`${DIRECTIVE_API}/api/oracle/autorollback/force`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(req.body ?? {}),
    });
    res.status(r.status).json(await r.json());
  } catch { res.status(502).json({ ok: false, error: "autorollback unavailable" }); }
});
app.get("/api/oracle/warmup/report", async (_req, res) => {
  try {
    const r = await fetch(`${DIRECTIVE_API}/api/oracle/warmup/report`);
    res.status(r.status).json(await r.json());
  } catch { res.status(502).json({ enabled: false, error: "warmup report unavailable" }); }
});

// Aşama 2 — Directive Impact proxies
app.get("/api/oracle/impact/recent", async (req, res) => {
  try {
    const q = req.url.includes("?") ? req.url.slice(req.url.indexOf("?")) : "";
    const r = await fetch(`${DIRECTIVE_API}/api/oracle/impact/recent${q}`);
    res.status(r.status).json(await r.json());
  } catch { res.status(502).json({ error: "impact unavailable" }); }
});
app.get("/api/oracle/impact/by-type", async (_req, res) => {
  try {
    const r = await fetch(`${DIRECTIVE_API}/api/oracle/impact/by-type`);
    res.status(r.status).json(await r.json());
  } catch { res.status(502).json({ error: "impact unavailable" }); }
});
app.get("/api/oracle/impact/synthetic-vs-live", async (_req, res) => {
  try {
    const r = await fetch(`${DIRECTIVE_API}/api/oracle/impact/synthetic-vs-live`);
    res.status(r.status).json(await r.json());
  } catch { res.status(502).json({ error: "impact unavailable" }); }
});

// Aşama 3 — Free Roam proxies
app.get("/api/oracle/asama3/status", async (_req, res) => {
  try {
    const r = await fetch(`${DIRECTIVE_API}/api/oracle/asama3/status`);
    res.status(r.status).json(await r.json());
  } catch { res.status(502).json({ error: "asama3 status unavailable" }); }
});
app.post("/api/oracle/emergency-lockdown", async (req, res) => {
  try {
    const token = req.headers["x-emergency-token"] || "";
    const r = await fetch(`${DIRECTIVE_API}/api/oracle/emergency-lockdown`, {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-Emergency-Token": String(token) },
      body: JSON.stringify(req.body ?? {}),
    });
    res.status(r.status).json(await r.json());
  } catch { res.status(502).json({ ok: false, error: "lockdown unavailable" }); }
});
app.get("/api/runtime/status", async (_req, res) => {
  try {
    const r = await fetch(`${DIRECTIVE_API}/api/runtime/status`);
    res.status(r.status).json(await r.json());
  } catch { res.status(502).json({ enabled: false, error: "runtime unavailable" }); }
});

/* ═══ DATA AUDIT / VALIDATION ═══ */
app.get("/api/audit/validate", async (req, res) => {
  try {
    const issues: { field: string; issue: string; severity: string; count: number }[] = [];

    // Check confidence values outside valid range
    const [badConf] = await sql`SELECT COUNT(*)::int AS count FROM signals WHERE confidence IS NOT NULL AND (confidence < 0 OR confidence > 1)`;
    if (badConf.count > 0) issues.push({ field: "signals.confidence", issue: `${badConf.count} sinyal güven değeri 0-1 aralığı dışında`, severity: "warning", count: badConf.count });

    // Check simulations with invalid PnL
    const [badPnl] = await sql`SELECT COUNT(*)::int AS count FROM simulations WHERE status = 'closed' AND pnl IS NULL`;
    if (badPnl.count > 0) issues.push({ field: "simulations.pnl", issue: `${badPnl.count} kapalı simülasyonda PnL değeri yok`, severity: "error", count: badPnl.count });

    // Check simulations missing exit data
    const [noExit] = await sql`SELECT COUNT(*)::int AS count FROM simulations WHERE status = 'closed' AND (exit_price IS NULL OR exit_time IS NULL)`;
    if (noExit.count > 0) issues.push({ field: "simulations.exit", issue: `${noExit.count} kapalı simülasyonda çıkış bilgisi eksik`, severity: "error", count: noExit.count });

    // Check stale open simulations (>24h)
    const [staleSim] = await sql`SELECT COUNT(*)::int AS count FROM simulations WHERE status = 'open' AND entry_time < NOW() - INTERVAL '24 hours'`;
    if (staleSim.count > 0) issues.push({ field: "simulations.stale", issue: `${staleSim.count} simülasyon 24 saatten uzun süredir açık`, severity: "warning", count: staleSim.count });

    // Check signals with zero/null prices
    const [noPrice] = await sql`SELECT COUNT(*)::int AS count FROM signals WHERE price IS NULL OR price = 0`;
    if (noPrice.count > 0) issues.push({ field: "signals.price", issue: `${noPrice.count} sinyalde fiyat bilgisi yok`, severity: "warning", count: noPrice.count });

    // Data freshness
    const [latestTrade] = await sql`SELECT MAX(timestamp) AS ts FROM trades`;
    const tradeAge = latestTrade?.ts ? Math.floor((Date.now() - new Date(latestTrade.ts).getTime()) / 60000) : -1;
    if (tradeAge > 5) issues.push({ field: "trades.freshness", issue: `Son trade ${tradeAge} dakika önce - veri akışı durmuş olabilir`, severity: tradeAge > 30 ? "error" : "warning", count: 1 });

    const [latestSignal] = await sql`SELECT MAX(created_at) AS ts FROM signals`;
    const sigAge = latestSignal?.ts ? Math.floor((Date.now() - new Date(latestSignal.ts).getTime()) / 60000) : -1;
    if (sigAge > 30) issues.push({ field: "signals.freshness", issue: `Son sinyal ${sigAge} dakika önce`, severity: "warning", count: 1 });

    res.json({
      timestamp: new Date().toISOString(),
      total_issues: issues.length,
      issues,
      status: issues.some(i => i.severity === "error") ? "error" : issues.length > 0 ? "warning" : "ok"
    });
  } catch (error) {
    res.status(500).json({ error: String(error) });
  }
});

/* ═══ CHAT / STRATEGY ═══ */
app.post("/api/chat", express.json(), async (req, res) => {
  const { message } = req.body;
  if (!message) {
    return res.status(400).json({ error: "Message required" });
  }

  try {
    // Forward to Python agents on port 3002 with a bounded timeout for snappy UX.
    const controller = new AbortController();
    // Gemma 3 12B Q4 CPU inference typically 15-35s; allow 50s total to cover
    // cold KV cache + context collection. Warm turns usually finish <20s.
    const chatTimeout = Number(process.env.QUENBOT_API_CHAT_TIMEOUT_MS || 50000);
    const timeoutId = setTimeout(() => controller.abort(), chatTimeout);

    try {
      const agentResponse = await fetch("http://127.0.0.1:3002/api/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ message }),
        signal: controller.signal,
      });

      clearTimeout(timeoutId);

      if (agentResponse.ok) {
        const data = await agentResponse.json();
        return res.json({
          success: true,
          message: data.message || "SuperGemma response generated",
          assistant: data.assistant,
          routed_actions: data.routed_actions || [],
          status: data.status,
          timestamp: data.timestamp || new Date().toISOString(),
        });
      }
    } catch (fetchError) {
      clearTimeout(timeoutId);
      console.warn("Agents connection attempt:", fetchError instanceof Error ? fetchError.message : "timeout");
    }

    return res.json(await buildLocalChatFallback());
  } catch (error) {
    res.status(500).json({
      error: String(error),
      message: "Chat processing temporarily unavailable",
    });
  }
});

// ─── Backtest Scoring ───
app.get("/api/backtest/scores", async (_req, res) => {
  try {
    const rows = await sql`
      SELECT
        s.symbol,
        s.signal_type,
        COUNT(*)::int AS total,
        SUM(CASE WHEN sim.pnl > 0 THEN 1 ELSE 0 END)::int AS wins,
        SUM(CASE WHEN sim.pnl <= 0 THEN 1 ELSE 0 END)::int AS losses,
        ROUND(AVG(sim.pnl_pct)::numeric, 3)::float AS avg_pnl_pct,
        ROUND((SUM(CASE WHEN sim.pnl > 0 THEN 1 ELSE 0 END)::float / NULLIF(COUNT(*), 0) * 100)::numeric, 1)::float AS success_rate
      FROM signals s
      JOIN simulations sim ON sim.signal_id = s.id
      WHERE sim.status = 'closed'
      GROUP BY s.symbol, s.signal_type
      ORDER BY total DESC
      LIMIT 50
    `;
    res.json(rows);
  } catch (error) {
    res.status(500).json({ error: String(error) });
  }
});

// Recent closed backtest trades (last 50). Consumed by BacktestPanel.
app.get("/api/backtest/recent", async (_req, res) => {
  try {
    const rows = await sql`
      SELECT
        sim.id,
        s.symbol,
        s.signal_type,
        COALESCE(s.metadata->>'direction', CASE WHEN s.signal_type ILIKE '%short%' THEN 'short' ELSE 'long' END) AS direction,
        s.confidence,
        sim.entry_price,
        sim.exit_price,
        sim.pnl,
        sim.pnl_pct,
        sim.entry_time,
        sim.exit_time,
        sim.status
      FROM simulations sim
      LEFT JOIN signals s ON s.id = sim.signal_id
      WHERE sim.status = 'closed' AND sim.exit_time IS NOT NULL
      ORDER BY sim.exit_time DESC
      LIMIT 50
    `;
    res.json(rows);
  } catch (error) {
    res.status(500).json({ error: String(error) });
  }
});

app.get("/api/selfcorrection/status", async (_req, res) => {
  try {
    const [recentPerf, corrections, strategyEvents, rca] = await Promise.all([
      sql`
        SELECT
          COUNT(*)::int AS recent_trades,
          SUM(CASE WHEN pnl > 0 THEN 1 ELSE 0 END)::int AS recent_wins,
          ROUND((SUM(CASE WHEN pnl > 0 THEN 1 ELSE 0 END)::numeric / NULLIF(COUNT(*), 0) * 100), 1)::float AS recent_win_rate,
          ROUND(AVG(pnl_pct)::numeric, 3)::float AS avg_pnl_pct
        FROM simulations
        WHERE status = 'closed' AND exit_time > NOW() - INTERVAL '24 hours'
      `,
      sql`
        SELECT id, signal_type, failure_type, adjustment_key, adjustment_value, reason, applied
        FROM correction_notes
        ORDER BY created_at DESC
        LIMIT 20
      `,
      sql`
        SELECT state_key, state_value, updated_at
        FROM bot_state
        WHERE state_key IN ('strategy_mode', 'last_strategy_update', 'adaptive_params')
        ORDER BY updated_at DESC
      `,
      sql`
        SELECT
          failure_type,
          COUNT(*)::int AS count,
          ROUND(AVG(confidence)::numeric, 2)::float AS avg_confidence
        FROM rca_results
        WHERE id > (SELECT GREATEST(COALESCE(MAX(id), 0) - 50, 0) FROM rca_results)
        GROUP BY failure_type
        ORDER BY count DESC
      `,
    ]);

    const perf = recentPerf[0] || {};
    const needsCorrection = (perf.recent_win_rate ?? 100) < 50 && (perf.recent_trades ?? 0) >= 5;
    res.json({
      recent_performance: perf,
      needs_correction: needsCorrection,
      corrections,
      strategy_state: strategyEvents,
      rca_summary: rca,
    });
  } catch (error) {
    res.status(500).json({ error: String(error) });
  }
});

// ─── Strategy Update Events ───
app.get("/api/strategy/events", async (_req, res) => {
  try {
    const rows = await sql`
      SELECT state_key, state_value, updated_at
      FROM bot_state
      WHERE state_key IN ('strategy_mode', 'last_strategy_update', 'adaptive_params', 'brain_calibration', 'risk_mode')
      ORDER BY updated_at DESC
    `;
    const auditRows = await sql`
      SELECT id, timestamp, total_simulations, successful_simulations as successful, failed_simulations as failed, success_rate, avg_win_pct, avg_loss_pct
      FROM audit_records ORDER BY timestamp DESC LIMIT 10
    `;
    res.json({ state: rows, audits: auditRows });
  } catch (error) {
    res.status(500).json({ error: String(error) });
  }
});

// ─── Agent Flow (Decision Pipeline) ───
app.get("/api/agents/flow", async (_req, res) => {
  try {
    const [heartbeats, recentSignals, recentSims, recentPatterns] = await Promise.all([
      sql`SELECT agent_name, status, last_heartbeat, metadata FROM agent_heartbeat`,
      sql`SELECT id, symbol, signal_type, confidence, status, timestamp FROM signals ORDER BY id DESC LIMIT 5`,
      sql`SELECT id, symbol, side, status, pnl, entry_time FROM simulations ORDER BY id DESC LIMIT 5`,
      sql`SELECT id, symbol, similarity, predicted_direction, confidence, brain_decision FROM pattern_match_results ORDER BY id DESC LIMIT 5`,
    ]);
    const agents = Object.fromEntries(heartbeats.map((h: any) => [h.agent_name, h]));
    res.json({
      agents,
      pipeline: {
        scout: { status: agents.scout?.status || "unknown", lastBeat: agents.scout?.last_heartbeat },
        pattern_matcher: { status: agents.pattern_matcher?.status || "unknown", lastBeat: agents.pattern_matcher?.last_heartbeat, recent: recentPatterns },
        strategist: { status: agents.strategist?.status || "unknown", lastBeat: agents.strategist?.last_heartbeat, recent_signals: recentSignals },
        ghost_simulator: { status: agents.ghost_simulator?.status || "unknown", lastBeat: agents.ghost_simulator?.last_heartbeat, recent_sims: recentSims },
        auditor: { status: agents.auditor?.status || "unknown", lastBeat: agents.auditor?.last_heartbeat },
        brain: { status: agents.brain?.status || "unknown", lastBeat: agents.brain?.last_heartbeat },
      },
    });
  } catch (error) {
    res.status(500).json({ error: String(error) });
  }
});

// Cache the integration overview response to smooth out slow underlying queries.
let _integrationOverviewCache: { data: any; updatedAt: number } | null = null;
let _integrationOverviewInFlight: Promise<any> | null = null;
const INTEGRATION_OVERVIEW_TTL_MS = 15000;

function _withDbTimeout<T>(promise: Promise<T>, ms: number, label: string, fallback: T): Promise<T> {
  return Promise.race<T>([
    promise.catch((e) => {
      console.warn(`[integration] ${label} query failed:`, String(e));
      return fallback;
    }),
    new Promise<T>((resolve) => setTimeout(() => {
      console.warn(`[integration] ${label} query exceeded ${ms}ms, using fallback`);
      resolve(fallback);
    }, ms)),
  ]);
}

async function _computeIntegrationOverview(): Promise<any> {
  const [heartbeats, recentSignals, sourcePerformance, learningStats, stateHistory, resourceHb, systemSummary, directives, efomOverview] = await Promise.all([
      sql`
        SELECT
          agent_name,
          status,
          last_heartbeat,
          metadata,
          EXTRACT(EPOCH FROM (NOW() - last_heartbeat))::double precision AS age_seconds
        FROM agent_heartbeat
        ORDER BY agent_name
      `,
      _withDbTimeout(sql`
        SELECT
          id,
          symbol,
          signal_type,
          confidence::double precision AS confidence,
          status,
          timestamp,
          COALESCE(metadata->>'source', metadata->>'signal_provider', 'unknown') AS source,
          COALESCE(metadata->>'source_model', 'unknown') AS source_model,
          COALESCE(metadata->>'exchange', 'binance') AS exchange,
          COALESCE(market_type, 'spot') AS market_type
        FROM signals
        WHERE timestamp >= NOW() - INTERVAL '24 hours'
        ORDER BY timestamp DESC
        LIMIT 24
      ` as unknown as Promise<any[]>, 3500, "recent_signals", [] as any[]),
      _withDbTimeout(sql`
        SELECT
          COALESCE(s.metadata->>'source', s.metadata->>'signal_provider', 'unknown') AS source,
          COALESCE(s.metadata->>'source_model', 'unknown') AS source_model,
          COUNT(*)::int AS total_signals,
          COUNT(CASE WHEN s.status IN ('pending', 'active', 'open') THEN 1 END)::int AS active_signals,
          COUNT(CASE WHEN sim.status = 'closed' THEN 1 END)::int AS closed_simulations,
          COUNT(CASE WHEN sim.status = 'closed' AND COALESCE(sim.pnl, 0) > 0 THEN 1 END)::int AS wins,
          COALESCE(AVG(sim.pnl_pct), 0)::double precision AS avg_pnl_pct,
          COALESCE(MAX(s.confidence), 0)::double precision AS best_confidence,
          MAX(s.timestamp) AS last_signal_at
        FROM signals s
        LEFT JOIN simulations sim ON sim.signal_id = s.id
        WHERE s.timestamp >= NOW() - INTERVAL '7 days'
        GROUP BY 1, 2
        ORDER BY total_signals DESC, avg_pnl_pct DESC
        LIMIT 12
      ` as unknown as Promise<any[]>, 6000, "source_performance", [] as any[]),
      sql`
        SELECT
          COUNT(*)::int AS total,
          COUNT(CASE WHEN was_correct THEN 1 END)::int AS correct,
          COALESCE(AVG(pnl_pct), 0)::double precision AS avg_pnl
        FROM brain_learning_log
      `,
      sql`
        SELECT
          timestamp,
          mode,
          cumulative_pnl::double precision AS cumulative_pnl,
          daily_pnl::double precision AS daily_pnl,
          win_rate::double precision AS win_rate,
          total_trades
        FROM state_history
        WHERE timestamp >= NOW() - INTERVAL '24 hours'
        ORDER BY timestamp ASC
        LIMIT 96
      `,
      sql`SELECT metadata FROM agent_heartbeat WHERE agent_name = 'system_resources' LIMIT 1`,
      fetchJsonOrNull(`${DIRECTIVE_API}/api/system/summary`, 2000, 0),
      fetchJsonOrNull(`${DIRECTIVE_API}/api/directives`, 2000, 0),
      readJsonFileOrNull(path.join(EFOM_REPORTS_DIR, "post_mortem_report.json")).then(async (postMortem) => {
        const trials = await readJsonFileOrNull(path.join(EFOM_REPORTS_DIR, "optuna_trials.json"));
        return {
          post_mortem: postMortem,
          optuna_trials: Array.isArray(trials) ? trials : [],
        };
      }),
    ]);

    // Separate query for fallback data (decision_core, brain, efom, system heartbeats)
    const fallbackRows = await sql`
      SELECT agent_name, metadata 
      FROM agent_heartbeat 
      WHERE agent_name IN ('decision_core', 'brain', 'efom', 'system')
    `;
    const fallbackData: Record<string, any> = {};
    for (const row of fallbackRows) {
      const meta = typeof row.metadata === "string" ? JSON.parse(row.metadata) : (row.metadata || {});
      fallbackData[row.agent_name] = meta;
    }

    // Brain evolution: daily accuracy & avg PnL over last 14 days
    let brainEvolutionSeries: Array<{ day: any; total: number; correct: number; accuracy: number; avg_pnl: number }> = [];
    try {
      const brainEvolution = await Promise.race([
        sql`
          SELECT
            DATE_TRUNC('day', created_at) AS day,
            COUNT(*)::int AS total,
            COUNT(CASE WHEN was_correct THEN 1 END)::int AS correct,
            COALESCE(AVG(pnl_pct), 0)::double precision AS avg_pnl
          FROM brain_learning_log
          WHERE created_at >= NOW() - INTERVAL '14 days'
          GROUP BY 1
          ORDER BY 1 ASC
        `,
        new Promise<any[]>((_, reject) => setTimeout(() => reject(new Error("brain_evolution timeout")), 3000)),
      ]);
      brainEvolutionSeries = (brainEvolution as any[]).map((row) => {
        const total = Number(row.total || 0);
        const correct = Number(row.correct || 0);
        return {
          day: row.day,
          total,
          correct,
          accuracy: total > 0 ? (correct / total) * 100 : 0,
          avg_pnl: Number(row.avg_pnl || 0),
        };
      });
    } catch (e) {
      console.warn("[integration] brain_evolution failed:", String(e));
    }

    // Per-agent intelligence score: combines activity freshness + contribution
    const agentIntelligence = (heartbeats as any[]).map((hb: any) => {
      const metadata = typeof hb.metadata === "string" ? JSON.parse(hb.metadata) : (hb.metadata || {});
      const ageSeconds = Math.max(0, Math.round(Number(hb.age_seconds || 0)));
      const freshness = Math.max(0, 1 - ageSeconds / (AGENT_STALE_SECONDS * 2));
      const activity = Number(
        metadata.trade_counter ?? metadata.scans ?? metadata.analysis_count ??
        metadata.signals_generated ?? metadata.audit_count ?? metadata.match_count ??
        metadata.logged_trades ?? metadata.gemma_calls ?? metadata.total_requests ?? 0
      );
      const activityIndex = Math.min(1, Math.log10(activity + 1) / 4);
      const healthy = hb.status === "running" ? 1 : hb.status === "degraded" ? 0.5 : 0;
      const iq = Math.round((freshness * 0.3 + activityIndex * 0.5 + healthy * 0.2) * 100);
      return {
        name: hb.agent_name,
        iq,
        freshness: Math.round(freshness * 100),
        activity_index: Math.round(activityIndex * 100),
        healthy: Boolean(healthy),
      };
    }).sort((a, b) => b.iq - a.iq);

    const agentRows = heartbeats.map((hb: any) => {
      const metadata = typeof hb.metadata === "string" ? JSON.parse(hb.metadata) : (hb.metadata || {});
      const ageSeconds = Math.max(0, Math.round(Number(hb.age_seconds || 0)));
      const status = ageSeconds <= AGENT_STALE_SECONDS ? hb.status : "stale";
      const activityScore = Number(
        metadata.trade_counter ??
        metadata.scans ??
        metadata.processed_signals ??
        metadata.total_processed ??
        metadata.events_processed ??
        metadata.logged_trades ??
        metadata.optimizations_run ??
        metadata.total_requests ??
        metadata.gemma_calls ??
        metadata.patterns ??
        0
      );
      return {
        name: hb.agent_name,
        status,
        source_status: hb.status,
        last_heartbeat: hb.last_heartbeat,
        age_seconds: ageSeconds,
        activity_score: activityScore,
        metadata,
      };
    });

    const modelsMap = new Map<string, { name: string; owner: string; activity: number; source: string }>();
    const strategicRecentSignals = (recentSignals as any[]).filter((row) => isIntegrationStrategicSource(row.source));
    const strategicPerformance = (sourcePerformance as any[]).filter((row) => isIntegrationStrategicSource(row.source));
    if (!exchangeFreshnessCache.refreshing && Date.now() - exchangeFreshnessCache.updatedAt > 15000) {
      void refreshExchangeFreshnessCache();
    }
    for (const row of strategicPerformance) {
      const modelName = String(row.source_model || "unknown");
      if (!modelName || modelName === "unknown") continue;
      const existing = modelsMap.get(modelName) || { name: modelName, owner: String(row.source || "unknown"), activity: 0, source: String(row.source || "unknown") };
      existing.activity += Number(row.total_signals || 0);
      modelsMap.set(modelName, existing);
    }

    for (const row of agentRows) {
      const modelName = String(
        row.metadata?.source_model ||
        row.metadata?.model ||
        row.metadata?.active_model ||
        row.metadata?.llm_model ||
        ""
      ).trim();
      if (!modelName) continue;
      const existing = modelsMap.get(modelName) || { name: modelName, owner: row.name, activity: 0, source: row.name };
      existing.activity += Math.max(1, Number(row.activity_score || 0));
      modelsMap.set(modelName, existing);
    }

    const resourceMetaRaw = resourceHb[0]?.metadata;
    const resourceMeta = typeof resourceMetaRaw === "string" ? JSON.parse(resourceMetaRaw) : (resourceMetaRaw || {});
    const learning = learningStats[0] || { total: 0, correct: 0, avg_pnl: 0 };
    const accuracy = Number(learning.total || 0) > 0 ? (Number(learning.correct || 0) / Number(learning.total || 1)) * 100 : 0;
    const systemSummaryData = systemSummary && typeof systemSummary === "object" && !Array.isArray(systemSummary) ? (systemSummary as any) : {};
    
    // Use fallback from heartbeat if Python API unavailable
    const dcFallback = fallbackData['decision_core'] || {};
    const brainFallback = fallbackData['brain'] || {};
    const efomFallback = fallbackData['efom'] || {};
    const systemFallback = fallbackData['system'] || {};
    
    const learningWeights = systemSummaryData?.brain?.learning_weights || brainFallback?.learning_weights || {};
    const decisionCore = systemSummaryData?.decision_core || dcFallback || {};
    const efom = systemSummaryData?.efom || efomFallback || {};
    const systemMode = systemSummaryData?.mode || systemFallback?.mode || "unknown";
    const systemHealth = systemSummaryData?.health || systemFallback?.llm_available ? "healthy" : "degraded";
    const llmModel = systemSummaryData?.llm?.model || dcFallback?.active_model || "unknown";
    
    const masterDirective = String(directives?.master_directive || "").trim();
    const postMortem = efomOverview?.post_mortem || null;
    const optunaTrials: Array<{ value?: number; [key: string]: any }> = Array.isArray(efomOverview?.optuna_trials) ? efomOverview.optuna_trials : [];
    const bestTrial = optunaTrials.reduce<{ value?: number; [key: string]: any } | null>((best, trial) => {
      if (!trial || typeof trial !== "object" || typeof trial.value !== "number") return best;
      if (!best || typeof best.value !== "number" || trial.value > best.value) return trial;
      return best;
    }, null);

    return {
      generated_at: new Date().toISOString(),
      agents: agentRows,
      models: Array.from(modelsMap.values()).sort((a, b) => b.activity - a.activity).slice(0, 10),
      signals: {
        recent: strategicRecentSignals,
        performance: strategicPerformance.map((row: any) => ({
          ...row,
          win_rate: Number(row.closed_simulations || 0) > 0 ? (Number(row.wins || 0) / Number(row.closed_simulations || 1)) * 100 : 0,
        })),
      },
      exchanges: exchangeFreshnessCache.data.length > 0 ? exchangeFreshnessCache.data : buildExchangeFallback(agentRows),
      resources: {
        cpu_percent: Number(resourceMeta.cpu_percent || 0),
        ram_percent: Number(resourceMeta.ram_percent || 0),
        ram_used_mb: Number(resourceMeta.ram_used_mb || 0),
        process_rss_mb: Number(resourceMeta.process_rss_mb || 0),
        disk_percent: Number(resourceMeta.disk_percent || 0),
        load_avg: [
          Number(resourceMeta.load_avg_1m || 0),
          Number(resourceMeta.load_avg_5m || 0),
          Number(resourceMeta.load_avg_15m || 0),
        ],
      },
      brain: {
        total: Number(learning.total || 0),
        correct: Number(learning.correct || 0),
        accuracy,
        avg_pnl: Number(learning.avg_pnl || 0),
        history: stateHistory,
        evolution: brainEvolutionSeries,
      },
      agent_intelligence: agentIntelligence,
      brain_control: {
        mode: String(systemMode || "unknown"),
        health: String(systemHealth || "unknown"),
        directive_updated_at: directives?.updated_at || null,
        directive_preview: masterDirective ? masterDirective.slice(0, 180) : null,
        decision_core: {
          ok: Boolean(decisionCore?.ok ?? (dcFallback?.active_model ? true : false)),
          model: String(decisionCore?.model || llmModel || "unknown"),
          approval_rate: Number(decisionCore?.approval_rate || dcFallback?.approval_rate || 0),
          total_requests: Number(decisionCore?.total_requests || dcFallback?.total_requests || 0),
          gemma_calls: Number(decisionCore?.gemma_calls || dcFallback?.gemma_calls || 0),
          fallback_calls: Number(decisionCore?.fallback_calls || dcFallback?.fallback_calls || 0),
          avg_latency_ms: Number(decisionCore?.avg_latency_ms || dcFallback?.avg_latency_ms || 0),
        },
        learning_weights: {
          similarity: Number(learningWeights?.similarity || brainFallback?.pattern_match?.learning_weights?.similarity || 0),
          volume_match: Number(learningWeights?.volume_match || brainFallback?.pattern_match?.learning_weights?.volume_match || 0),
          direction_match: Number(learningWeights?.direction_match || brainFallback?.pattern_match?.learning_weights?.direction_match || 0),
          confidence_history: Number(learningWeights?.confidence_history || brainFallback?.pattern_match?.learning_weights?.confidence_history || 0),
        },
        efom: {
          ok: Boolean(efom?.ok ?? efomFallback?.healthy),
          logged_trades: Number(efom?.logged_trades || efomFallback?.logged_trades || 0),
          optimizations_run: Number(efom?.optimizations_run || efomFallback?.optimizations_run || 0),
          config_path: efom?.config_path || efomFallback?.config_path || null,
          latest_report_summary: postMortem?.summary || null,
          latest_report_sample_size: Number(postMortem?.sample_size || 0),
          failure_patterns: Array.isArray(postMortem?.failure_patterns) ? postMortem.failure_patterns : [],
          optuna_total_trials: optunaTrials.length,
          optuna_best_value: Number(bestTrial?.value || 0),
          optuna_best_trial: bestTrial,
        },
      },
    };
}

app.get("/api/integration/overview", async (_req, res) => {
  const now = Date.now();
  if (_integrationOverviewCache && now - _integrationOverviewCache.updatedAt < INTEGRATION_OVERVIEW_TTL_MS) {
    return res.json(_integrationOverviewCache.data);
  }
  // Stale-while-revalidate: if we have any previous data, return it immediately
  // and refresh in the background so the dashboard never sees a slow/error response.
  if (_integrationOverviewCache) {
    if (!_integrationOverviewInFlight) {
      _integrationOverviewInFlight = _computeIntegrationOverview()
        .then((data) => {
          _integrationOverviewCache = { data, updatedAt: Date.now() };
          return data;
        })
        .catch((e) => {
          console.warn("[integration] background refresh failed:", String(e));
          return _integrationOverviewCache?.data;
        })
        .finally(() => {
          _integrationOverviewInFlight = null;
        });
    }
    return res.json(_integrationOverviewCache.data);
  }
  if (!_integrationOverviewInFlight) {
    _integrationOverviewInFlight = _computeIntegrationOverview()
      .then((data) => {
        _integrationOverviewCache = { data, updatedAt: Date.now() };
        return data;
      })
      .finally(() => {
        _integrationOverviewInFlight = null;
      });
  }
  try {
    const data = await _integrationOverviewInFlight;
    res.json(data);
  } catch (error) {
    const cached = _integrationOverviewCache as { data: any; updatedAt: number } | null;
    if (cached) {
      return res.json(cached.data);
    }
    res.status(500).json({ error: String(error) });
  }
});

// ─── PnL Equity Curve ───
app.get("/api/analytics/equity-curve", async (_req, res) => {
  try {
    const rows = await sql`
      SELECT
        exit_time AS time,
        pnl::float,
        SUM(pnl::float) OVER (ORDER BY exit_time) AS cumulative_pnl
      FROM simulations
      WHERE status = 'closed' AND exit_time IS NOT NULL
      ORDER BY exit_time
      LIMIT 200
    `;
    res.json(rows);
  } catch (error) {
    res.status(500).json({ error: String(error) });
  }
});

/* ═══ Mission Control Proxy (forwards to Python aiohttp on 3002) ═══ */
app.get("/api/mission-control/snapshot", async (_req, res) => {
  try {
    const r = await fetch(`${DIRECTIVE_API}/api/mission-control/snapshot`);
    res.status(r.status).json(await r.json());
  } catch {
    res.status(502).json({ error: "mission-control snapshot unavailable" });
  }
});
app.get("/api/mission-control/autopsy/:module_id", async (req, res) => {
  try {
    const ctrl = new AbortController();
    const to = setTimeout(() => ctrl.abort(), 15_000);
    const r = await fetch(
      `${DIRECTIVE_API}/api/mission-control/autopsy/${encodeURIComponent(req.params.module_id)}`,
      { signal: ctrl.signal }
    );
    clearTimeout(to);
    res.status(r.status).json(await r.json());
  } catch {
    res.status(502).json({ error: "autopsy unavailable" });
  }
});
app.post("/api/mission-control/restart/:module_id", async (req, res) => {
  try {
    const headers: Record<string, string> = { "Content-Type": "application/json" };
    const adminToken = req.header("x-admin-token");
    if (adminToken) headers["X-Admin-Token"] = adminToken;
    const r = await fetch(
      `${DIRECTIVE_API}/api/mission-control/restart/${encodeURIComponent(req.params.module_id)}`,
      { method: "POST", headers }
    );
    res.status(r.status).json(await r.json());
  } catch {
    res.status(502).json({ ok: false, error: "restart proxy unavailable" });
  }
});
// SSE pass-through — stream upstream body directly to the client.
app.get("/api/mission-control/stream", async (_req, res) => {
  try {
    const upstream = await fetch(`${DIRECTIVE_API}/api/mission-control/stream`);
    if (!upstream.ok || !upstream.body) {
      res.status(upstream.status || 502).end();
      return;
    }
    res.setHeader("Content-Type", "text/event-stream");
    res.setHeader("Cache-Control", "no-cache, no-store, must-revalidate");
    res.setHeader("Connection", "keep-alive");
    res.setHeader("X-Accel-Buffering", "no");
    const reader = (upstream.body as any).getReader();
    const decoder = new TextDecoder();
    const pump = async (): Promise<void> => {
      const { done, value } = await reader.read();
      if (done) {
        res.end();
        return;
      }
      res.write(decoder.decode(value, { stream: true }));
      return pump();
    };
    pump().catch(() => {
      try { res.end(); } catch { /* noop */ }
    });
  } catch {
    res.status(502).end();
  }
});

app.listen(port, async () => {
  await connectDatabase();
  await createTables();
  await refreshSummaryCache();
  await refreshPricesCache();
  await refreshMoversCache();
  setInterval(() => {
    void refreshSummaryCache();
  }, 5000);
  setInterval(() => {
    void refreshPricesCache();
  }, 3000);
  setInterval(() => {
    void refreshMoversCache();
  }, 5000);
  console.log(`API Server running on port ${port}`);
}).keepAliveTimeout = 65_000;  // Keep-alive > ALB default (60s) for connection reuse
