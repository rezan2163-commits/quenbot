import express from "express";
import cors from "cors";
import compression from "compression";
import { connectDatabase, createTables, sql } from "./db";

const app = express();
const port = Number(process.env.PORT || 3001);

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
    // Lightweight source: latest movement end prices per symbol.
    const rows = await sql`
      SELECT DISTINCT ON (symbol)
             symbol,
             'derived'::text AS exchange,
             end_price::double precision AS price,
             end_time AS timestamp
      FROM price_movements
      ORDER BY symbol, end_time DESC
    `;
    pricesCache.data = rows;
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
      SELECT symbol,
             start_price::double precision AS open_price,
             end_price::double precision AS current_price,
             (change_pct * 100)::double precision AS change_pct,
             end_time AS timestamp
      FROM price_movements
      WHERE end_time >= NOW() - INTERVAL '3 hours'
      ORDER BY ABS(change_pct) DESC
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
    const heartbeats = await sql`
      SELECT agent_name, status, last_heartbeat, metadata,
             EXTRACT(EPOCH FROM (NOW() - last_heartbeat)) AS age_seconds
      FROM agent_heartbeat ORDER BY agent_name
    `;
    const agents: Record<string, any> = {};
    for (const hb of heartbeats) {
      const isHealthy = hb.age_seconds < 120;
      agents[hb.agent_name] = {
        status: isHealthy ? hb.status : "stale",
        last_heartbeat: hb.last_heartbeat,
        age_seconds: Math.round(Number(hb.age_seconds)),
        metadata: hb.metadata,
      };
    }
    // Fallback if no heartbeats yet
    if (Object.keys(agents).length === 0) {
      for (const name of ['scout', 'strategist', 'ghost_simulator', 'auditor', 'brain', 'chat_engine']) {
        agents[name] = { status: "unknown", last_heartbeat: null, age_seconds: null, metadata: null };
      }
    }
    const [configSignals] = await sql`SELECT COUNT(*)::int AS count FROM signals`;
    const [configMovements] = await sql`SELECT COUNT(*)::int AS count FROM price_movements`;
    res.json({
      agents,
      summary: {
        signals: configSignals.count,
        movements: configMovements.count
      }
    });
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
    const signals = await sql`SELECT * FROM signals ORDER BY timestamp DESC LIMIT 100`;
    res.json(signals);
  } catch (error) {
    res.status(500).json({ error: String(error) });
  }
});

app.get("/api/simulations", async (req, res) => {
  try {
    const simulations = await sql`SELECT * FROM simulations ORDER BY entry_time DESC LIMIT 100`;
    res.json(simulations);
  } catch (error) {
    res.status(500).json({ error: String(error) });
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
    res.json(rows);
  } catch (error) {
    res.status(500).json({ error: String(error) });
  }
});

// Price history for a symbol (1 min candles, last 60 min)
app.get("/api/analytics/price-history/:symbol", async (req, res) => {
  try {
    const symbol = req.params.symbol.toUpperCase();
    const rows = await sql`
      SELECT date_trunc('minute', timestamp) AS minute,
             (ARRAY_AGG(price::double precision ORDER BY timestamp ASC))[1] AS open,
             MAX(price::double precision) AS high,
             MIN(price::double precision) AS low,
             (ARRAY_AGG(price::double precision ORDER BY timestamp DESC))[1] AS close,
             SUM(quantity::double precision)::double precision AS volume
      FROM trades
      WHERE symbol = ${symbol} AND timestamp >= NOW() - INTERVAL '60 minutes'
      GROUP BY minute
      ORDER BY minute ASC
    `;
    res.json(rows);
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
    const sym = symbol.toUpperCase().replace(/[^A-Z0-9]/g, '').slice(0, 20);
    const exch = (exchange || "all").toLowerCase().slice(0, 50);
    const mt = (market_type || "spot").toLowerCase();
    if (!["spot", "futures"].includes(mt)) {
      return res.status(400).json({ error: "market_type must be 'spot' or 'futures'" });
    }
    const [row] = await sql`
      INSERT INTO user_watchlist (symbol, exchange, market_type)
      VALUES (${sym}, ${exch}, ${mt})
      ON CONFLICT (symbol, exchange, market_type)
      DO UPDATE SET active = TRUE
      RETURNING id, symbol, exchange, market_type, active
    `;
    res.json(row);
  } catch (error) {
    res.status(500).json({ error: String(error) });
  }
});

app.post("/api/watchlist/remove", async (req, res) => {
  try {
    const { symbol, exchange, market_type } = req.body;
    if (!symbol) return res.status(400).json({ error: "Symbol is required" });
    const sym = symbol.toUpperCase();
    const exch = (exchange || "all").toLowerCase();
    const mt = (market_type || "spot").toLowerCase();
    await sql`
      UPDATE user_watchlist SET active = FALSE
      WHERE symbol = ${sym} AND exchange = ${exch} AND market_type = ${mt}
    `;
    res.json({ success: true });
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
    const status = req.query.status as string | undefined;
    const symbol = req.query.symbol ? String(req.query.symbol).toUpperCase() : undefined;
    const limit = Math.min(200, Number(req.query.limit || 100));
    let rows;
    if (status && symbol) {
      rows = await sql`SELECT *, confidence::double precision AS confidence, price::double precision AS price FROM signals WHERE status = ${status} AND symbol = ${symbol} ORDER BY timestamp DESC LIMIT ${limit}`;
    } else if (status) {
      rows = await sql`SELECT *, confidence::double precision AS confidence, price::double precision AS price FROM signals WHERE status = ${status} ORDER BY timestamp DESC LIMIT ${limit}`;
    } else if (symbol) {
      rows = await sql`SELECT *, confidence::double precision AS confidence, price::double precision AS price FROM signals WHERE symbol = ${symbol} ORDER BY timestamp DESC LIMIT ${limit}`;
    } else {
      rows = await sql`SELECT *, confidence::double precision AS confidence, price::double precision AS price FROM signals ORDER BY timestamp DESC LIMIT ${limit}`;
    }
    res.json(rows);
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
const DIRECTIVE_API = "http://localhost:3002";

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
    const r = await fetch(`${DIRECTIVE_API}/api/system/events`);
    res.json(await r.json());
  } catch { res.json({ total_events: 0, recent_events: [], error: "Event API unavailable" }); }
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
    // Get current system context
    const [summary] = await sql`
      SELECT total_trades, total_pnl, win_rate, active_signals, open_simulations
      FROM dashboard_summary LIMIT 1
    `;

    res.json({
      success: true,
      message: `✓ Komut alındı: "${message.substring(0, 60)}..."`,
      context: summary || {},
      status: "processing",
      timestamp: new Date().toISOString(),
      note: "Chat sistemi Python agents'a yönlendirilecek"
    });
  } catch (error) {
    res.status(500).json({ error: String(error) });
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
