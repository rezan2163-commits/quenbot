import express from "express";
import cors from "cors";
import compression from "compression";
import fs from "fs/promises";
import path from "path";
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

const AGENT_LABELS: Record<string, string> = {
  scout: "Scout",
  strategist: "Strategist",
  ghost_simulator: "Ghost Simulator",
  auditor: "Auditor",
  brain: "Brain",
  pattern_matcher: "Pattern Matcher",
  chat_engine: "Chat Engine",
  llm_brain: "LLM Omurgası",
};

function parseMeta(meta: any): Record<string, any> {
  if (!meta) return {};
  if (typeof meta === "string") {
    try { return JSON.parse(meta); } catch { return {}; }
  }
  return typeof meta === "object" ? meta : {};
}

function classifyAgent(signalType: string): string {
  const t = String(signalType || "").toLowerCase();
  if (t.startsWith("signature_")) return "scout";
  if (t.startsWith("intel_") || t.startsWith("evolutionary_") || t.startsWith("momentum_") || t.startsWith("price_action_")) return "strategist";
  if (t.startsWith("brain_")) return "brain";
  return "strategist";
}

function signalReasonTr(meta: Record<string, any>, signalType: string): string {
  const reasons: string[] = [];
  const sim = Number(meta.cosine_similarity || meta.avg_similarity || meta.similarity_score || 0);
  const trend = meta.trend;
  const rsi = Number(meta.rsi ?? NaN);
  const tp = Number(meta.target_pct || 0) * 100;
  const tf = meta.primary_timeframe || meta.timeframe || "15m";
  if (!Number.isNaN(sim) && sim > 0) reasons.push(`Benzerlik puanı ${sim.toFixed(2)} bulundu.`);
  if (!Number.isNaN(rsi)) reasons.push(`RSI ${rsi.toFixed(1)} seviyesinde.`);
  if (trend) reasons.push(`Piyasa eğilimi ${String(trend)} olarak tespit edildi.`);
  if (tp > 0) reasons.push(`Hedef hareket yaklaşık %${tp.toFixed(2)} olarak hesaplandı.`);
  reasons.push(`Sinyal tipi ${signalType} üzerinden ${tf} odaklı üretildi.`);
  return reasons.join(" ");
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
    // Get latest price for each symbol from trades
    const rows = await sql`
      SELECT symbol, MAX(price)::double precision AS price, MAX(timestamp) AS timestamp
      FROM trades
      WHERE timestamp > NOW() - INTERVAL '1 day'
      GROUP BY symbol
      ORDER BY symbol
      LIMIT 50
    `;
    
    const formatted = rows.map((row: any) => ({
      symbol: row.symbol,
      exchange: "binance",
      price: Number(row.price) || 0,
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
      SELECT symbol,
             start_price::double precision AS open_price,
             end_price::double precision AS current_price,
             (change_pct * 100)::double precision AS change_pct,
             end_time AS timestamp
      FROM price_movements
      WHERE end_time >= NOW() - INTERVAL '3 hours'
        AND end_price > 0
        AND start_price > 0
        AND ABS(change_pct) < 0.5
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
      win_rate: closedSimulations.count > 0 ? Number(((wonSimulations.count / closedSimulations.count) * 100).toFixed(2)) : 0,
      average_pnl: avgPnl.value,
      average_pnl_pct: avgPnlPct.value
    });
  } catch (error) {
    res.status(500).json({ error: String(error) });
  }
});

app.get("/api/agents/status", async (req, res) => {
  try {
    const staleAfterSeconds = Math.max(120, Number(process.env.QUENBOT_AGENT_STALE_SEC || 300));
    const heartbeats = await sql`
      SELECT agent_name, status, last_heartbeat, metadata,
             EXTRACT(EPOCH FROM (NOW() - last_heartbeat)) AS age_seconds
      FROM agent_heartbeat ORDER BY agent_name
    `;
    const agents: Record<string, any> = {};
    for (const hb of heartbeats) {
      const isHealthy = Number(hb.age_seconds) < staleAfterSeconds;
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
        movements: configMovements.count,
        stale_after_seconds: staleAfterSeconds,
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
    const limit = Math.min(500, Number(req.query.limit || 150));
    const direction = String(req.query.direction || "all").toLowerCase();
    const marketType = String(req.query.market_type || "all").toLowerCase();
    const agent = String(req.query.agent || "all").toLowerCase();

    const rows = await sql`SELECT * FROM signals ORDER BY timestamp DESC LIMIT ${limit}`;
    const filtered = rows.filter((row: any) => {
      const meta = parseMeta(row.metadata);
      const rowDirection = (meta.position_bias || (String(row.signal_type || "").includes("long") ? "long" : String(row.signal_type || "").includes("short") ? "short" : "")).toLowerCase();
      const rowMarket = String(meta.market_type || row.market_type || "spot").toLowerCase();
      const rowAgent = classifyAgent(String(row.signal_type || ""));
      if (direction !== "all" && rowDirection !== direction) return false;
      if (marketType !== "all" && rowMarket !== marketType) return false;
      if (agent !== "all" && rowAgent !== agent) return false;
      return true;
    }).map((row: any) => {
      const meta = parseMeta(row.metadata);
      const leverage = Number(meta.leverage_x || meta.kaldirac_x || meta.leverage || 1);
      return {
        ...row,
        metadata: meta,
        agent: classifyAgent(String(row.signal_type || "")),
        leverage_x: leverage > 0 ? leverage : 1,
        aciklama_tr: signalReasonTr(meta, String(row.signal_type || "")),
      };
    });
    res.json(filtered);
  } catch (error) {
    res.status(500).json({ error: String(error) });
  }
});

app.get("/api/agents/signal-stats", async (req, res) => {
  try {
    const [signals, hbs] = await Promise.all([
      sql`SELECT signal_type FROM signals ORDER BY timestamp DESC LIMIT 3000`,
      sql`SELECT agent_name, metadata FROM agent_heartbeat ORDER BY agent_name`,
    ]);
    const out: Record<string, any> = {};
    for (const key of Object.keys(AGENT_LABELS)) out[key] = { agent: key, label: AGENT_LABELS[key], signal_count: 0, generated_count: 0 };
    for (const s of signals as any[]) {
      const a = classifyAgent(String(s.signal_type || ""));
      if (!out[a]) out[a] = { agent: a, label: a, signal_count: 0, generated_count: 0 };
      out[a].signal_count += 1;
    }
    for (const hb of hbs as any[]) {
      const name = String(hb.agent_name || "");
      const m = parseMeta(hb.metadata);
      const generated = Number(m.signals_generated || 0);
      if (!out[name]) out[name] = { agent: name, label: AGENT_LABELS[name] || name, signal_count: 0, generated_count: 0 };
      out[name].generated_count = generated;
    }
    res.json(Object.values(out));
  } catch (error) {
    res.status(500).json({ error: String(error) });
  }
});

app.get("/api/agents/:agent/signals", async (req, res) => {
  try {
    const agent = String(req.params.agent || "strategist").toLowerCase();
    const limit = Math.min(300, Number(req.query.limit || 120));
    const rows = await sql`SELECT * FROM signals ORDER BY timestamp DESC LIMIT ${limit}`;
    const sigRows = (rows as any[]).filter((r) => classifyAgent(String(r.signal_type || "")) === agent);

    const signatureRefs = await sql`
      SELECT symbol, timeframe, direction,
             change_pct::double precision AS change_pct,
             created_at
      FROM historical_signatures
      ORDER BY created_at DESC
      LIMIT 500
    `;
    const refByKey = new Map<string, any>();
    for (const r of signatureRefs as any[]) {
      const k = `${r.symbol}:${r.timeframe}:${r.direction}`;
      if (!refByKey.has(k)) refByKey.set(k, r);
    }

    const items = sigRows.map((r) => {
      const m = parseMeta(r.metadata);
      const dir = (m.position_bias || (String(r.signal_type || "").includes("long") ? "long" : "short"));
      const tf = String(m.primary_timeframe || m.timeframe || "15m");
      const key = `${r.symbol}:${tf}:${dir}`;
      const ref = refByKey.get(key);
      const leverage = Number(m.leverage_x || m.kaldirac_x || m.leverage || 1);
      return {
        ...r,
        metadata: m,
        agent,
        leverage_x: leverage > 0 ? leverage : 1,
        aciklama_tr: signalReasonTr(m, String(r.signal_type || "")),
        benzerlik_etiketi: ref ? `${tf} benzerlik etiketi` : null,
        benzer_ornek: ref ? {
          sembol: ref.symbol,
          yon: ref.direction,
          zaman_dilimi: ref.timeframe,
          gorulme_zamani: ref.created_at,
          sonraki_hareket_yuzde: Number(ref.change_pct || 0) * 100,
        } : null,
      };
    });
    res.json(items);
  } catch (error) {
    res.status(500).json({ error: String(error) });
  }
});

app.get("/api/simulations", async (req, res) => {
  try {
    const limit = Math.min(300, Number(req.query.limit || 150));
    const side = String(req.query.side || "all").toLowerCase();
    const status = String(req.query.status || "all").toLowerCase();
    const rows = await sql`
      SELECT s.*, sg.signal_type, sg.confidence AS signal_confidence, sg.metadata AS signal_metadata
      FROM simulations s
      LEFT JOIN signals sg ON sg.id = s.signal_id
      ORDER BY s.entry_time DESC
      LIMIT ${limit}
    `;
    const filtered = (rows as any[]).filter((r) => {
      if (side !== "all" && String(r.side || "").toLowerCase() !== side) return false;
      if (status !== "all" && String(r.status || "").toLowerCase() !== status) return false;
      return true;
    }).map((r) => {
      const simMeta = parseMeta(r.metadata);
      const sigMeta = parseMeta(r.signal_metadata);
      const leverage = Number(simMeta.leverage_x || sigMeta.leverage_x || simMeta.leverage || sigMeta.leverage || 1);
      return {
        ...r,
        signal_metadata: sigMeta,
        metadata: simMeta,
        confidence: Number(r.signal_confidence || simMeta.signal_confidence || sigMeta.signal_confidence || 0),
        leverage_x: leverage > 0 ? leverage : 1,
      };
    });
    res.json(filtered);
  } catch (error) {
    res.status(500).json({ error: String(error) });
  }
});

app.get("/api/simulations/analysis", async (req, res) => {
  try {
    const side = String(req.query.side || "all").toLowerCase();
    const limit = Math.min(250, Number(req.query.limit || 120));
    const rows = await sql`
      SELECT s.*, sg.signal_type, sg.metadata AS signal_metadata,
             r.failure_type, r.explanation
      FROM simulations s
      LEFT JOIN signals sg ON sg.id = s.signal_id
      LEFT JOIN LATERAL (
        SELECT failure_type, explanation
        FROM rca_results
        WHERE simulation_id = s.id
        ORDER BY id DESC
        LIMIT 1
      ) r ON TRUE
      ORDER BY s.entry_time DESC
      LIMIT ${limit}
    `;
    const items = (rows as any[])
      .filter((r) => side === "all" || String(r.side || "").toLowerCase() === side)
      .map((r) => {
        const simMeta = parseMeta(r.metadata);
        const sigMeta = parseMeta(r.signal_metadata);
        const pnlPct = Number(r.pnl_pct || 0);
        const outcome = pnlPct >= 0
          ? `Pozisyon kârla kapandı. Çıkış öncesi alış/satış dengesi hedef yönü destekledi.`
          : `Pozisyon zararla kapandı. Alış/satış akışı hedef yönün tersine döndü ve risk sınırı devreye girdi.`;
        return {
          ...r,
          metadata: simMeta,
          signal_metadata: sigMeta,
          leverage_x: Number(simMeta.leverage_x || sigMeta.leverage_x || simMeta.leverage || sigMeta.leverage || 1),
          sonuc_aciklamasi_tr: outcome,
          ders_notu_tr: r.failure_type
            ? `${r.failure_type} nedeniyle düzeltme notu oluşturuldu. Benzer durumda parametre güncellemesi uygulanır.`
            : `Bu sonuç öğrenme kayıtlarına eklendi; benzer paternlerde başarı olasılığı güncellenecek.`,
          rca_aciklamasi_tr: r.explanation || null,
        };
      });
    res.json(items);
  } catch (error) {
    res.status(500).json({ error: String(error) });
  }
});

app.get("/api/system/data-folders", async (_req, res) => {
  try {
    const root = "/root/quenbot";
    const top = (await fs.readdir(root, { withFileTypes: true }))
      .filter((d) => d.isDirectory())
      .map((d) => d.name)
      .sort();
    const folders = [
      { path: "python_agents", gorev: "Ajanların karar, sinyal, simülasyon ve öğrenme kodları" },
      { path: "artifacts/api-server", gorev: "Dashboard ve servisler için API katmanı" },
      { path: "artifacts/market-intel", gorev: "Kullanıcı dashboard arayüzü" },
      { path: "db", gorev: "Veritabanı şema ve yapılandırma dosyaları" },
      { path: "lib", gorev: "Paylaşılan kütüphaneler" },
      { path: "scripts", gorev: "Başlatma, test ve bakım komut dosyaları" },
    ];
    const tableMap = [
      { tablo: "trades", biriken_veri: "Borsa bazlı gerçekleşen alım-satım kayıtları" },
      { tablo: "price_movements", biriken_veri: "Zaman dilimi bazlı fiyat hareket özetleri" },
      { tablo: "signals", biriken_veri: "Üretilen long/short sinyalleri ve hedef metadatası" },
      { tablo: "simulations", biriken_veri: "Açık/kapalı kağıt pozisyonlar, kâr/zarar" },
      { tablo: "historical_signatures", biriken_veri: "Geçmiş benzer patern imzaları" },
      { tablo: "rca_results", biriken_veri: "Kayıp/kazanç sonrası kök neden analizi" },
      { tablo: "correction_notes", biriken_veri: "RCA sonrası parametre düzeltme notları" },
      { tablo: "agent_heartbeat", biriken_veri: "Ajan canlılık ve üretim sayaçları" },
    ];
    res.json({
      klasor_sayisi: top.length,
      klasorler: folders,
      ust_klasorler: top,
      veri_haritasi: tableMap,
    });
  } catch (error) {
    res.status(500).json({ error: String(error) });
  }
});

app.get("/api/system/model-koordinasyon", async (_req, res) => {
  try {
    const [hb, sigCount, simCount] = await Promise.all([
      sql`SELECT agent_name, status, last_heartbeat FROM agent_heartbeat ORDER BY agent_name`,
      sql`SELECT COUNT(*)::int AS count FROM signals WHERE timestamp >= NOW() - INTERVAL '24 hours'`,
      sql`SELECT COUNT(*)::int AS count FROM simulations WHERE created_at >= NOW() - INTERVAL '24 hours'`,
    ]);
    const running = (hb as any[]).filter((x) => String(x.status) === "running").length;
    const total = (hb as any[]).length;
    res.json({
      model_durumu: total > 0 && running === total ? "Tam eşgüdüm" : "Kısmi eşgüdüm",
      calisan_ajan: running,
      toplam_ajan: total,
      son_24s_sinyal: Number((sigCount as any[])[0]?.count || 0),
      son_24s_simulasyon: Number((simCount as any[])[0]?.count || 0),
      ajanlar: hb,
      aciklama: "Ajanların aynı veri hattı üzerinden birlikte çalışması heartbeat ve üretim çıktıları ile izlenir.",
    });
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

// ─── PnL Sıfırlama ───

app.post("/api/admin/reset-pnl", async (req, res) => {
  try {
    // Önce bağımlı tabloları sil
    await sql`DELETE FROM rca_results`;
    await sql`DELETE FROM audit_reports`;
    await sql`DELETE FROM correction_notes`;
    const simResult = await sql`DELETE FROM simulations RETURNING id`;
    const auditResult = await sql`DELETE FROM audit_records RETURNING id`;
    // Bozuk price_movements kayıtlarını da temizle
    await sql`DELETE FROM price_movements WHERE end_price = 0 OR start_price = 0`;
    summaryCache.data = {
      total_trades: 0,
      total_movements: 0,
      active_signals: 0,
      open_simulations: 0,
      total_pnl: 0,
      win_rate: 0,
      closed_simulations: 0,
      winning_simulations: 0,
      losing_simulations: 0,
    };
    summaryCache.updatedAt = 0;
    res.json({
      ok: true,
      message: "PnL sıfırlandı",
      deleted_simulations: simResult.length,
      deleted_audit_records: auditResult.length,
    });
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

/* ═══ GEMMA ACTIVITY FEED (Terminal-style) ═══ */
app.get("/api/gemma/activity-feed", async (req, res) => {
  try {
    // 1) Get event bus recent events from directive API
    let events: any[] = [];
    try {
      const r = await fetch(`${DIRECTIVE_API}/api/system/events`);
      const data = await r.json();
      events = data.recent_events || [];
    } catch {}

    // 2) Get recent signals with agent info
    const recentSignals = await sql`
      SELECT signal_type, symbol, confidence, status, metadata, created_at
      FROM signals ORDER BY created_at DESC LIMIT 10
    `;

    // 3) Get recent simulation changes
    const recentSims = await sql`
      SELECT symbol, side, entry_price, exit_price, pnl_pct, status, metadata, 
             COALESCE(exit_time, created_at) AS ts
      FROM simulations ORDER BY COALESCE(exit_time, created_at) DESC LIMIT 8
    `;

    // 4) Get agent heartbeat metadata (contains counters)
    const heartbeats = await sql`
      SELECT agent_name, status, metadata, last_heartbeat FROM agent_heartbeat
      WHERE agent_name NOT IN ('system_resources') ORDER BY agent_name
    `;

    // 5) Get recent learning log entries
    const learning = await sql`
      SELECT signal_type, was_correct, pnl_pct, created_at
      FROM brain_learning_log ORDER BY created_at DESC LIMIT 5
    `;

    // Build feed lines
    const feed: { ts: number; text: string; level: string }[] = [];

    // Event bus entries (already have data_summary from Python)
    for (const ev of events) {
      feed.push({
        ts: ev.timestamp || 0,
        text: ev.data_summary || `${ev.source}: ${ev.type}`,
        level: ev.type?.includes('warning') || ev.type?.includes('rejected') ? 'warn' :
               ev.type?.includes('error') ? 'error' :
               ev.type?.includes('approved') || ev.type?.includes('opened') || ev.type?.includes('closed') ? 'success' : 'info',
      });
    }

    // Agent heartbeats translated
    const agentTr: Record<string, string> = {
      scout: 'Keşifçi', strategist: 'Stratejist', ghost_simulator: 'Simülatör',
      auditor: 'Denetçi', brain: 'Beyin', pattern_matcher: 'Örüntü Eşleştirici',
      llm_brain: 'Gemma Omurga', chat_engine: 'Sohbet'
    };
    for (const hb of heartbeats) {
      const m = typeof hb.metadata === 'string' ? (() => { try { return JSON.parse(hb.metadata); } catch { return {}; } })() : (hb.metadata || {});
      const name = agentTr[hb.agent_name] || hb.agent_name;
      const parts: string[] = [];
      if (m.trade_counter != null) parts.push(`${m.trade_counter} işlem`);
      if (m.signals_generated != null) parts.push(`${m.signals_generated} sinyal üretildi`);
      if (m.active_simulations != null) parts.push(`${m.active_simulations} aktif sim`);
      if (m.audit_count != null) parts.push(`${m.audit_count} denetim`);
      if (m.scan_count != null) parts.push(`${m.scan_count} tarama`);
      if (m.match_count != null) parts.push(`${m.match_count} eşleşme`);
      if (m.active_model) parts.push(`model: ${m.active_model}`);
      if (m.total_calls != null) parts.push(`${m.total_calls} LLM çağrısı`);
      if (m.avg_latency_ms != null) parts.push(`ort: ${Math.round(m.avg_latency_ms)}ms`);

      const status = hb.status === 'running' ? '✓ Aktif' : hb.status === 'degraded' ? '⚠ Kısıtlı' : '✗ Kapalı';
      const hbTs = hb.last_heartbeat ? new Date(hb.last_heartbeat).getTime() / 1000 : 0;
      feed.push({
        ts: hbTs,
        text: `🤖 [${name}] ${status} — ${parts.join(' | ') || 'bekleniyor'}`,
        level: hb.status === 'running' ? 'info' : 'warn',
      });
    }

    // Recent learning
    for (const l of learning) {
      const lr = l.was_correct ? '✅ Doğru tahmin' : '❌ Yanlış tahmin';
      const lTs = l.created_at ? new Date(l.created_at).getTime() / 1000 : 0;
      feed.push({
        ts: lTs,
        text: `🧠 Beyin öğrenme: ${l.signal_type} — ${lr}, K/Z: %${Number(l.pnl_pct || 0).toFixed(2)}`,
        level: l.was_correct ? 'success' : 'warn',
      });
    }

    // Sort by timestamp desc
    feed.sort((a, b) => b.ts - a.ts);

    res.json({
      feed: feed.slice(0, 60),
      total_events: feed.length,
      agents_online: heartbeats.filter((h: any) => h.status === 'running').length,
      agents_total: heartbeats.length,
    });
  } catch (e: any) {
    res.json({ feed: [], total_events: 0, error: e.message });
  }
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
    // Forward to Python agents on port 3002
    // Note: Agents process runs in same system, using localhost:3002
    const controller = new AbortController();
    const timeoutId = setTimeout(() => controller.abort(), 90000);
    
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
          message: data.message || "Gemma response generated",
          timestamp: data.timestamp || new Date().toISOString(),
        });
      }
    } catch (fetchError) {
      clearTimeout(timeoutId);
      // Timeout or connection error - agents might be warming up
      console.warn("Agents connection attempt:", fetchError instanceof Error ? fetchError.message : "timeout");
    }

    // Fallback: Agents unavailable
    res.json({
      success: true,
      message: "⏳ AI asistan şu an meşgul, lütfen birkaç saniye sonra tekrar deneyin.",
      status: "agents_unavailable",
      timestamp: new Date().toISOString(),
    });
  } catch (error) {
    res.status(500).json({
      error: String(error),
      message: "Chat processing temporarily unavailable",
    });
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
