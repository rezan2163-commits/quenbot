import express from "express";
import cors from "cors";
import { connectDatabase, createTables, sql } from "./db";

const app = express();
const port = Number(process.env.PORT || 3001);

app.use(cors());
app.use(express.json());

app.get("/api/health", async (req, res) => {
  try {
    await sql`SELECT 1`;
    res.json({ status: "ok", database: "connected", timestamp: new Date().toISOString() });
  } catch (error) {
    res.status(500).json({ status: "error", error: String(error) });
  }
});

app.get("/api/dashboard/summary", async (req, res) => {
  try {
    const [totalTrades] = await sql`SELECT COUNT(*)::int AS count FROM trades`;
    const [totalMovements] = await sql`SELECT COUNT(*)::int AS count FROM price_movements`;
    const [activeSignals] = await sql`SELECT COUNT(*)::int AS count FROM signals WHERE status = 'pending'`;
    const [openSimulations] = await sql`SELECT COUNT(*)::int AS count FROM simulations WHERE status = 'open'`;
    const [totalPnl] = await sql`SELECT COALESCE(SUM(pnl), 0)::double precision AS value FROM simulations WHERE status = 'closed'`;
    const [wonSimulations] = await sql`SELECT COUNT(*)::int AS count FROM simulations WHERE status = 'closed' AND pnl > 0`;
    const [lostSimulations] = await sql`SELECT COUNT(*)::int AS count FROM simulations WHERE status = 'closed' AND pnl <= 0`;

    const winRate = wonSimulations.count + lostSimulations.count > 0
      ? Number((wonSimulations.count / (wonSimulations.count + lostSimulations.count)) * 100).toFixed(2)
      : "0.00";

    res.json({
      total_trades: totalTrades.count,
      total_movements: totalMovements.count,
      active_signals: activeSignals.count,
      open_simulations: openSimulations.count,
      total_pnl: totalPnl.value,
      win_rate: Number(winRate),
      closed_simulations: wonSimulations.count + lostSimulations.count,
      winning_simulations: wonSimulations.count,
      losing_simulations: lostSimulations.count
    });
  } catch (error) {
    res.status(500).json({ error: String(error) });
  }
});

app.get("/api/live/prices", async (req, res) => {
  try {
    const prices = await sql`
      SELECT DISTINCT ON (symbol) symbol, exchange, price::double precision AS price, timestamp
      FROM trades
      ORDER BY symbol, timestamp DESC
    `;
    res.json(prices);
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
    const [configSignals] = await sql`SELECT COUNT(*)::int AS count FROM signals`;
    const [configMovements] = await sql`SELECT COUNT(*)::int AS count FROM price_movements`;
    res.json({
      agents: {
        scout: { status: "running", last_activity: null },
        strategist: { status: "running", last_activity: null },
        ghost_simulator: { status: "running", last_activity: null },
        auditor: { status: "running", last_activity: null }
      },
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
    const rows = await sql`
      WITH latest AS (
        SELECT DISTINCT ON (symbol) symbol, price::double precision AS price, timestamp
        FROM trades ORDER BY symbol, timestamp DESC
      ),
      oldest AS (
        SELECT DISTINCT ON (symbol) symbol, price::double precision AS price
        FROM trades
        WHERE timestamp >= NOW() - INTERVAL '1 hour'
        ORDER BY symbol, timestamp ASC
      )
      SELECT l.symbol,
             o.price AS open_price,
             l.price AS current_price,
             CASE WHEN o.price > 0 THEN ((l.price - o.price) / o.price * 100)::double precision ELSE 0 END AS change_pct,
             l.timestamp
      FROM latest l
      JOIN oldest o ON l.symbol = o.symbol
      ORDER BY ABS((l.price - o.price) / NULLIF(o.price, 0)) DESC
    `;
    res.json(rows);
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
    // Son N trade'i anlık çek
    const latestTrades = await sql`
      SELECT id, exchange, market_type, symbol, price::double precision AS price,
             quantity::double precision AS quantity, side, timestamp, trade_id
      FROM trades ORDER BY timestamp DESC LIMIT ${limit}
    `;
    // Her exchange/market_type için son trade zamanı
    const freshness = await sql`
      SELECT exchange, market_type,
             MAX(timestamp) AS last_trade_time,
             COUNT(*)::int AS trade_count_1m
      FROM trades
      WHERE timestamp >= NOW() - INTERVAL '1 minute'
      GROUP BY exchange, market_type
      ORDER BY last_trade_time DESC
    `;
    // Son 5 dakikada exchange/symbol bazli trade sayilari
    const breakdown = await sql`
      SELECT exchange, symbol, market_type,
             COUNT(*)::int AS count,
             MAX(price::double precision) AS max_price,
             MIN(price::double precision) AS min_price,
             MAX(timestamp) AS last_time
      FROM trades
      WHERE timestamp >= NOW() - INTERVAL '5 minutes'
      GROUP BY exchange, symbol, market_type
      ORDER BY count DESC
    `;
    res.json({ latest_trades: latestTrades, freshness, breakdown });
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
                    'user_watchlist', 'watchlist', 'blacklist_patterns', 'agent_config', 'audit_reports'];
    const stats = [];
    for (const table of tables) {
      try {
        const [row] = await sql.unsafe(`SELECT COUNT(*)::int AS count FROM ${table}`);
        stats.push({ table, count: row.count });
      } catch {
        stats.push({ table, count: -1 });
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

app.listen(port, async () => {
  await connectDatabase();
  await createTables();
  console.log(`API Server running on port ${port}`);
});
