import React, { useEffect, useMemo, useState, useCallback, useRef } from "react";
import ReactDOM from "react-dom/client";
import "./index.css";

/* ───────── Types ───────── */
type RecordRow = Record<string, any>;
type DashboardSummary = { total_trades: number; total_movements: number; active_signals: number; open_simulations: number; total_pnl: number; win_rate: number; closed_simulations: number; winning_simulations: number; losing_simulations: number };
type LivePrice = { symbol: string; exchange: string; price: number; timestamp: string };
type BotSummary = { total_simulations: number; open_simulations: number; closed_simulations: number; wins: number; losses: number; win_rate: number; average_pnl: number; average_pnl_pct: number };
type TopMover = { symbol: string; open_price: number; current_price: number; change_pct: number; timestamp: string };
type OrderFlow = { symbol: string; buy_volume: number; sell_volume: number; buy_count: number; sell_count: number };
type TimelineEntry = { minute: string; count: number; volume: number };
type VolumeEntry = { exchange: string; market_type: string; trade_count: number; total_volume: number };
type SystemStats = { db_size_mb: number; trades_per_minute: number; total_trades: number; oldest_trade: string; newest_trade: string; uptime_minutes: number };
type Candle = { minute: string; open: number; high: number; low: number; close: number; volume: number };
type ChatMsg = { id: number; role: string; message: string; agent_name: string; created_at: string };
type WatchlistItem = { id: number; symbol: string; exchange: string; market_type: string; active: boolean };
type BrainStatus = { pattern_count: number; learning: { total: number; correct: number; accuracy: number; avg_pnl: number }; recent_patterns: RecordRow[]; signal_type_stats: RecordRow[] };
type LiveDataStream = { latest_trades: RecordRow[]; exchange_freshness: RecordRow[]; five_min_breakdown: RecordRow[] };
type TableStats = { table_name: string; row_count: number }[];
type Tab = "overview" | "markets" | "orderflow" | "bot" | "livedata" | "brain" | "chat" | "admin" | "system";

/* ───────── Helpers ───────── */
const fmt = (v: number, d = 2) => new Intl.NumberFormat("en-US", { maximumFractionDigits: d }).format(v);
const fmtUsd = (v: number) => `$${fmt(v)}`;
const fmtPct = (v: number) => `${v >= 0 ? "+" : ""}${v.toFixed(2)}%`;
const fmtTime = (s: string) => { try { return new Date(s).toLocaleTimeString("en-US", { hour: "2-digit", minute: "2-digit", second: "2-digit" }); } catch { return "—"; } };
const fmtTimeShort = (s: string) => { try { return new Date(s).toLocaleTimeString("en-US", { hour: "2-digit", minute: "2-digit" }); } catch { return "—"; } };
const cls = (...c: (string | false | undefined | null)[]) => c.filter(Boolean).join(" ");
const ago = (minutes: number) => { if (minutes < 60) return `${minutes}m`; if (minutes < 1440) return `${Math.floor(minutes / 60)}h ${minutes % 60}m`; return `${Math.floor(minutes / 1440)}d ${Math.floor((minutes % 1440) / 60)}h`; };

/* ───────── Mini Bar Chart ───────── */
function MiniBarChart({ data, height = 48, color = "var(--accent)" }: { data: number[]; height?: number; color?: string }) {
  const max = Math.max(...data, 1);
  return (
    <div className="mini-bar-chart" style={{ height }}>
      {data.map((v, i) => (
        <div key={i} className="mini-bar" style={{ height: `${(v / max) * 100}%`, background: color }} />
      ))}
    </div>
  );
}

/* ───────── Progress Ring ───────── */
function ProgressRing({ value, size = 80, stroke = 6, color = "var(--accent)" }: { value: number; size?: number; stroke?: number; color?: string }) {
  const r = (size - stroke) / 2;
  const circ = 2 * Math.PI * r;
  const offset = circ - (Math.min(value, 100) / 100) * circ;
  return (
    <svg width={size} height={size} style={{ display: "block" }}>
      <circle cx={size / 2} cy={size / 2} r={r} fill="none" stroke="rgba(148,163,184,0.1)" strokeWidth={stroke} />
      <circle cx={size / 2} cy={size / 2} r={r} fill="none" stroke={color} strokeWidth={stroke} strokeDasharray={circ} strokeDashoffset={offset} strokeLinecap="round" transform={`rotate(-90 ${size / 2} ${size / 2})`} />
      <text x="50%" y="50%" dominantBaseline="central" textAnchor="middle" fill="currentColor" fontSize="14" fontWeight="700">{value.toFixed(0)}%</text>
    </svg>
  );
}

/* ───────── Flow Bar ───────── */
function FlowBar({ buy, sell }: { buy: number; sell: number }) {
  const total = buy + sell || 1;
  const pct = (buy / total) * 100;
  return (
    <div className="flow-bar-wrap">
      <div className="flow-bar"><div className="flow-bar-buy" style={{ width: `${pct}%` }} /></div>
      <div className="flow-bar-labels"><span className="text-green">{pct.toFixed(0)}% Alış</span><span className="text-red">{(100 - pct).toFixed(0)}% Satış</span></div>
    </div>
  );
}

/* ───────── Candle Chart ───────── */
function CandleChart({ candles, height = 200 }: { candles: Candle[]; height?: number }) {
  if (candles.length === 0) return <div className="empty-state">Henüz yeterli veri yok</div>;
  const allPrices = candles.flatMap(c => [c.high, c.low]);
  const min = Math.min(...allPrices); const max = Math.max(...allPrices); const range = max - min || 1;
  const w = 800; const candleW = Math.max(2, w / candles.length - 2);
  const y = (p: number) => height - 10 - ((p - min) / range) * (height - 20);
  return (
    <div className="candle-chart-wrap">
      <svg width="100%" viewBox={`0 0 ${w} ${height}`} preserveAspectRatio="none">
        {candles.map((c, i) => {
          const x = (i / candles.length) * w + candleW / 2;
          const bullish = c.close >= c.open;
          const col = bullish ? "#22c55e" : "#ef4444";
          const bTop = y(Math.max(c.open, c.close)); const bBot = y(Math.min(c.open, c.close));
          return (<g key={i}><line x1={x} x2={x} y1={y(c.high)} y2={y(c.low)} stroke={col} strokeWidth={1} /><rect x={x - candleW / 2} y={bTop} width={candleW} height={Math.max(1, bBot - bTop)} fill={col} rx={1} /></g>);
        })}
      </svg>
      <div className="candle-axis"><span>{fmtUsd(min)}</span><span>{fmtUsd(max)}</span></div>
    </div>
  );
}

/* ═══════════════════ MAIN APP ═══════════════════ */
function App() {
  const [tab, setTab] = useState<Tab>("overview");
  const [summary, setSummary] = useState<DashboardSummary | null>(null);
  const [livePrices, setLivePrices] = useState<LivePrice[]>([]);
  const [prevPrices, setPrevPrices] = useState<Record<string, number>>({});
  const [botSummary, setBotSummary] = useState<BotSummary | null>(null);
  const [topMovers, setTopMovers] = useState<TopMover[]>([]);
  const [orderFlow, setOrderFlow] = useState<OrderFlow[]>([]);
  const [timeline, setTimeline] = useState<TimelineEntry[]>([]);
  const [volumeData, setVolumeData] = useState<VolumeEntry[]>([]);
  const [systemStats, setSystemStats] = useState<SystemStats | null>(null);
  const [signals, setSignals] = useState<RecordRow[]>([]);
  const [simulations, setSimulations] = useState<RecordRow[]>([]);
  const [trades, setTrades] = useState<RecordRow[]>([]);
  const [movements, setMovements] = useState<RecordRow[]>([]);
  const [selectedSymbol, setSelectedSymbol] = useState("BTCUSDT");
  const [candles, setCandles] = useState<Candle[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [lastUpdate, setLastUpdate] = useState(new Date());
  const [refreshCount, setRefreshCount] = useState(0);
  const [chatMessages, setChatMessages] = useState<ChatMsg[]>([]);
  const [chatInput, setChatInput] = useState("");
  const [chatSending, setChatSending] = useState(false);
  const [watchlist, setWatchlist] = useState<WatchlistItem[]>([]);
  const [brainStatus, setBrainStatus] = useState<BrainStatus | null>(null);
  const [wlSymbol, setWlSymbol] = useState("");
  const [wlExchange, setWlExchange] = useState("all");
  const [wlMarketType, setWlMarketType] = useState("spot");
  const chatEndRef = useRef<HTMLDivElement>(null);
  // Yeni state'ler
  const [liveStream, setLiveStream] = useState<LiveDataStream | null>(null);
  const [tableStats, setTableStats] = useState<TableStats>([]);
  const [agentConfigs, setAgentConfigs] = useState<RecordRow[]>([]);
  const [auditRecords, setAuditRecords] = useState<RecordRow[]>([]);
  const [failureAnalysis, setFailureAnalysis] = useState<RecordRow[]>([]);
  const [pnlTimeline, setPnlTimeline] = useState<RecordRow[]>([]);
  const [brainPatterns, setBrainPatterns] = useState<RecordRow[]>([]);
  const [learningStats, setLearningStats] = useState<RecordRow | null>(null);
  const [agentStatuses, setAgentStatuses] = useState<Record<string, any>>({});

  const fetchAll = useCallback(async () => {
    try {
      const ep = [
        "/api/dashboard/summary", "/api/live/prices", "/api/bot/summary",
        "/api/analytics/top-movers", "/api/analytics/order-flow",
        "/api/analytics/trade-timeline", "/api/analytics/volume-by-exchange",
        "/api/analytics/system-stats", "/api/signals", "/api/simulations",
        "/api/scout/trades?limit=30", "/api/scout/movements?limit=20",
        `/api/analytics/price-history/${selectedSymbol}`,
        "/api/chat/messages?limit=50", "/api/watchlist", "/api/brain/status",
        "/api/live/data-stream", "/api/admin/table-stats",
        "/api/admin/config", "/api/admin/audit-records?limit=20",
        "/api/admin/failure-analysis?limit=20",
        "/api/analytics/pnl-timeline",
        "/api/brain/patterns?limit=30", "/api/brain/learning-stats",
        "/api/agents/status"
      ];
      const results = await Promise.all(ep.map(async url => { try { const r = await fetch(url); return r.ok ? r.json() : null; } catch { return null; } }));
      const [s, p, b, tm, of2, tl, vd, ss, sig, sim, tr, mv, ch, chatM, wl, bs, ls, ts, ac, ar, fa, pt, bp, lst, agSt] = results;
      if (s) setSummary(s);
      if (p) { setPrevPrices(Object.fromEntries(livePrices.map(lp => [lp.symbol, lp.price]))); setLivePrices(p); }
      if (b) setBotSummary(b);
      if (tm) setTopMovers(tm);
      if (of2) setOrderFlow(of2);
      if (tl) setTimeline(tl);
      if (vd) setVolumeData(vd);
      if (ss) setSystemStats(ss);
      if (sig) setSignals(sig);
      if (sim) setSimulations(sim);
      if (tr) setTrades(tr);
      if (mv) setMovements(mv);
      if (ch) setCandles(ch);
      if (chatM) setChatMessages(chatM);
      if (wl) setWatchlist(wl);
      if (bs) setBrainStatus(bs);
      if (ls) setLiveStream(ls);
      if (ts) setTableStats(ts);
      if (ac) setAgentConfigs(ac);
      if (ar) setAuditRecords(ar);
      if (fa) setFailureAnalysis(fa);
      if (pt) setPnlTimeline(pt);
      if (bp) setBrainPatterns(bp);
      if (lst) setLearningStats(lst);
      if (agSt?.agents) setAgentStatuses(agSt.agents);
      setError(null); setLastUpdate(new Date()); setRefreshCount(c => c + 1);
    } catch (err) { setError(err instanceof Error ? err.message : String(err)); }
    finally { setLoading(false); }
  }, [selectedSymbol, livePrices]);

  useEffect(() => { fetchAll(); const id = setInterval(fetchAll, 5000); return () => clearInterval(id); }, [selectedSymbol]);
  useEffect(() => { chatEndRef.current?.scrollIntoView({ behavior: "smooth" }); }, [chatMessages]);

  const sendChatMessage = async () => {
    if (!chatInput.trim() || chatSending) return;
    setChatSending(true);
    try {
      await fetch("/api/chat/send", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ message: chatInput.trim() }) });
      setChatInput("");
      setTimeout(fetchAll, 1500);
    } catch (e) { console.error(e); }
    finally { setChatSending(false); }
  };

  const addToWatchlist = async () => {
    if (!wlSymbol.trim()) return;
    try {
      await fetch("/api/watchlist/add", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ symbol: wlSymbol.trim().toUpperCase(), exchange: wlExchange, market_type: wlMarketType }) });
      setWlSymbol("");
      fetchAll();
    } catch (e) { console.error(e); }
  };

  const removeFromWatchlist = async (item: WatchlistItem) => {
    try {
      await fetch("/api/watchlist/remove", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ symbol: item.symbol, exchange: item.exchange, market_type: item.market_type }) });
      fetchAll();
    } catch (e) { console.error(e); }
  };

  const totalVolume1h = useMemo(() => volumeData.reduce((s, v) => s + (v.total_volume || 0), 0), [volumeData]);
  const tradeVolumes = useMemo(() => timeline.map(t => t.volume || 0), [timeline]);
  const tradeCounts = useMemo(() => timeline.map(t => t.count || 0), [timeline]);

  const navSections: { title: string; items: { key: Tab; label: string; icon: string; badge?: string }[] }[] = [
    { title: "Pano", items: [
      { key: "overview", label: "Genel Bakış", icon: "📊" },
      { key: "livedata", label: "Canlı Veri Akışı", icon: "🔴", badge: liveStream ? String(liveStream.latest_trades?.length ?? 0) : undefined },
    ]},
    { title: "Analiz", items: [
      { key: "markets", label: "Piyasalar", icon: "📈" },
      { key: "orderflow", label: "Order Flow", icon: "⚡" },
    ]},
    { title: "Bot", items: [
      { key: "bot", label: "Sinyaller & Simülasyon", icon: "🤖" },
      { key: "brain", label: "AI Beyin", icon: "🧠" },
      { key: "chat", label: "Sohbet", icon: "💬" },
    ]},
    { title: "Yönetim", items: [
      { key: "admin", label: "Admin Panel", icon: "🛠" },
      { key: "system", label: "Sistem", icon: "⚙️" },
    ]},
  ];

  return (
    <div className="app">
      {/* ── Sidebar ── */}
      <aside className="sidebar">
        <div className="sidebar-brand"><div className="brand-icon">Q</div><div className="brand-text"><strong>QuenBot</strong><span>Market Intelligence v2</span></div></div>
        <nav className="sidebar-nav">
          {navSections.map(sec => (
            <React.Fragment key={sec.title}>
              <div className="nav-section">{sec.title}</div>
              {sec.items.map(t => (
                <button key={t.key} className={cls("nav-btn", tab === t.key && "nav-btn-active")} onClick={() => setTab(t.key)}>
                  <span className="nav-icon">{t.icon}</span><span>{t.label}</span>
                  {t.badge && <span className="nav-badge">{t.badge}</span>}
                </button>
              ))}
            </React.Fragment>
          ))}
        </nav>
        <div className="sidebar-footer">
          <div className="live-indicator"><span className="live-dot" /><span>Canlı Veri Akışı</span></div>
          <div className="text-muted text-xs">Son: {fmtTime(lastUpdate.toISOString())} • #{refreshCount}</div>

        </div>
      </aside>

      {/* ── Main ── */}
      <main className="main-content">
        {error && <div className="toast toast-error">⚠ API Hatası: {error}</div>}

        {/* ═══ OVERVIEW ═══ */}
        {tab === "overview" && (<>
          <div className="kpi-strip">
            <KPI label="Toplam Trade" value={summary ? fmt(summary.total_trades, 0) : "—"} sub="Veritabanında" icon="💹" />
            <KPI label="Trade/dk" value={systemStats ? fmt(systemStats.trades_per_minute, 0) : "—"} sub="Son 1 dakika" icon="⚡" accent="blue" />
            <KPI label="Hacim (1s)" value={totalVolume1h > 0 ? `$${fmt(totalVolume1h, 0)}` : "$0"} sub="USD toplam" icon="📊" accent="purple" />
            <KPI label="Aktif Sinyal" value={summary ? fmt(summary.active_signals, 0) : "0"} sub="Bekleyen" icon="🎯" accent={summary && summary.active_signals > 0 ? "amber" : undefined} />
            <KPI label="Açık Sim." value={summary ? fmt(summary.open_simulations, 0) : "0"} sub="Ghost trader" icon="👻" accent={summary && summary.open_simulations > 0 ? "green" : undefined} />
            <KPI label="Toplam PnL" value={summary ? fmtUsd(summary.total_pnl) : "$0"} sub="Kapalı sim." icon="💰" accent={summary && summary.total_pnl > 0 ? "green" : summary && summary.total_pnl < 0 ? "red" : undefined} />
          </div>

          <div className="grid-2">
            <div className="card"><div className="card-header"><h3>Trade Hacmi (Son 60dk)</h3><span className="badge badge-blue">Dakikalık</span></div><MiniBarChart data={tradeVolumes} height={80} color="var(--cyan)" /></div>
            <div className="card"><div className="card-header"><h3>Trade Sayısı (Son 60dk)</h3><span className="badge badge-green">Aktif</span></div><MiniBarChart data={tradeCounts} height={80} color="var(--green)" /></div>
          </div>

          <div className="grid-2">
            <div className="card">
              <div className="card-header"><h3>Canlı Fiyatlar</h3><span className="badge badge-live">CANLI</span></div>
              <div className="price-grid">
                {livePrices.map(p => {
                  const prev = prevPrices[p.symbol]; const dir = prev ? (p.price > prev ? "up" : p.price < prev ? "down" : "") : "";
                  const mover = topMovers.find(m => m.symbol === p.symbol); const changePct = mover?.change_pct ?? 0;
                  return (
                    <div key={p.symbol} className={cls("price-tile", dir && `price-flash-${dir}`)} onClick={() => { setSelectedSymbol(p.symbol); setTab("markets"); }}>
                      <div className="price-tile-top"><span className="symbol-name">{p.symbol.replace("USDT", "")}</span><span className={cls("change-badge", changePct >= 0 ? "change-up" : "change-down")}>{fmtPct(changePct)}</span></div>
                      <div className="price-tile-price">{fmtUsd(p.price)}</div>
                      <div className="price-tile-meta"><span className="text-muted">{p.exchange}</span><span className="text-muted">{fmtTime(p.timestamp)}</span></div>
                    </div>);
                })}
              </div>
            </div>
            <div className="card">
              <div className="card-header"><h3>En Çok Hareket Edenler</h3><span className="badge badge-amber">1 Saat</span></div>
              <div className="movers-list">
                {topMovers.slice(0, 10).map(m => (
                  <div key={m.symbol} className="mover-row" onClick={() => { setSelectedSymbol(m.symbol); setTab("markets"); }}>
                    <div className="mover-symbol">{m.symbol.replace("USDT", "")}<span className="text-muted">/USDT</span></div>
                    <div className="mover-prices"><span>{fmtUsd(m.current_price)}</span></div>
                    <span className={cls("change-badge change-badge-lg", m.change_pct >= 0 ? "change-up" : "change-down")}>{fmtPct(m.change_pct)}</span>
                  </div>))}
              </div>
            </div>
          </div>

          <div className="card"><div className="card-header"><h3>Borsa Bazında Hacim (Son 1 Saat)</h3></div>
            <div className="exchange-grid">
              {volumeData.map((v, i) => (<div key={i} className="exchange-card"><div className="exchange-name">{v.exchange.toUpperCase()} <span className="badge badge-sm">{v.market_type}</span></div><div className="exchange-vol">${fmt(v.total_volume, 0)}</div><div className="text-muted text-xs">{fmt(v.trade_count, 0)} trade</div></div>))}
            </div>
          </div>

          {/* PnL Timeline */}
          {pnlTimeline.length > 0 && (
            <div className="card">
              <div className="card-header"><h3>Kümülatif PnL Zaman Çizgisi</h3><span className="badge badge-green">Simülasyonlar</span></div>
              <MiniBarChart data={pnlTimeline.map(p => p.cumulative_pnl ?? 0)} height={80} color={pnlTimeline.length > 0 && (pnlTimeline[pnlTimeline.length - 1]?.cumulative_pnl ?? 0) >= 0 ? "var(--green)" : "var(--red)"} />
            </div>
          )}
        </>)}

        {/* ═══ LIVE DATA STREAM ═══ */}
        {tab === "livedata" && (<>
          <div className="tab-header"><h2>Canlı Veri Akışı Doğrulama</h2><span className="badge badge-live">CANLI</span></div>

          {/* Exchange Freshness */}
          <div className="card">
            <div className="card-header"><h3>Borsa Veri Tazeliği</h3><span className="badge badge-cyan">Gerçek Zamanlı</span></div>
            {liveStream && liveStream.exchange_freshness?.length > 0 ? (
              <div className="stream-grid">
                {liveStream.exchange_freshness.map((ef, i) => {
                  const ageSec = ef.age_seconds ?? 999;
                  const freshClass = ageSec < 10 ? "stream-card-fresh" : ageSec < 60 ? "stream-card-stale" : "stream-card-dead";
                  const dotColor = ageSec < 10 ? "var(--green)" : ageSec < 60 ? "var(--amber)" : "var(--red)";
                  return (
                    <div key={i} className={cls("stream-card", freshClass)}>
                      <div className="stream-dot" style={{ background: dotColor }} />
                      <div className="stream-symbol">{ef.exchange?.toUpperCase()} {ef.market_type}</div>
                      <div className="stream-meta">
                        <span>Son: {ageSec.toFixed(0)}sn önce</span>
                        <span>Son 5dk: {ef.trades_5min ?? 0} trade</span>
                        <span>{ef.latest_time ? fmtTime(ef.latest_time) : "—"}</span>
                      </div>
                    </div>
                  );
                })}
              </div>
            ) : <div className="empty-state">Tazelik verisi yükleniyor...</div>}
          </div>

          {/* 5-Min Breakdown */}
          <div className="card">
            <div className="card-header"><h3>5 Dakikalık Veri Akışı Dağılımı</h3></div>
            {liveStream && liveStream.five_min_breakdown?.length > 0 ? (
              <div className="table-wrap"><table className="tbl"><thead><tr><th>Periyot</th><th>Trade Sayısı</th><th>Toplam Hacim</th></tr></thead><tbody>
                {liveStream.five_min_breakdown.map((row, i) => (
                  <tr key={i}>
                    <td>{row.period ? fmtTime(row.period) : "—"}</td>
                    <td><strong>{fmt(row.trade_count ?? 0, 0)}</strong></td>
                    <td>${fmt(row.total_volume ?? 0, 0)}</td>
                  </tr>
                ))}
              </tbody></table></div>
            ) : <div className="empty-state">5 dakikalık döküm bekleniyor...</div>}
          </div>

          {/* Latest Trades */}
          <div className="card">
            <div className="card-header"><h3>Son Gelen Trade Verileri</h3><span className="badge badge-blue">Ham Veri</span></div>
            {liveStream && liveStream.latest_trades?.length > 0 ? (
              <div className="table-wrap"><table className="tbl"><thead><tr><th>Sembol</th><th>Borsa</th><th>Fiyat</th><th>Miktar</th><th>Taraf</th><th>Zaman</th></tr></thead><tbody>
                {liveStream.latest_trades.map((t, i) => (
                  <tr key={i}>
                    <td><strong>{t.symbol}</strong></td>
                    <td>{t.exchange}</td>
                    <td className="text-mono">{fmtUsd(Number(t.price || 0))}</td>
                    <td className="text-mono">{fmt(Number(t.quantity || 0), 6)}</td>
                    <td><span className={cls("side-badge", t.side === "buy" ? "side-long" : "side-short")}>{t.side === "buy" ? "ALIŞ" : "SATIŞ"}</span></td>
                    <td>{t.timestamp ? fmtTime(t.timestamp) : "—"}</td>
                  </tr>
                ))}
              </tbody></table></div>
            ) : <div className="empty-state">Trade verisi bekleniyor...</div>}
          </div>

          {/* Summary */}
          <div className="kpi-strip">
            <KPI label="Toplam Borsa Bağlantısı" value={String(liveStream?.exchange_freshness?.length ?? 0)} icon="🔗" accent="cyan" />
            <KPI label="Son 5dk Trade" value={liveStream ? fmt((liveStream.five_min_breakdown ?? []).reduce((s, r) => s + (r.trade_count ?? 0), 0), 0) : "—"} icon="📦" accent="blue" />
            <KPI label="Aktif Akış" value={liveStream ? String((liveStream.exchange_freshness ?? []).filter(e => (e.age_seconds ?? 999) < 30).length) : "0"} sub="< 30sn" icon="✅" accent="green" />
            <KPI label="Uyarı" value={liveStream ? String((liveStream.exchange_freshness ?? []).filter(e => (e.age_seconds ?? 999) >= 60).length) : "0"} sub="> 60sn" icon="⚠️" accent={liveStream && (liveStream.exchange_freshness ?? []).some(e => (e.age_seconds ?? 999) >= 60) ? "red" : undefined} />
          </div>
        </>)}

        {/* ═══ MARKETS ═══ */}
        {tab === "markets" && (<>
          <div className="tab-header">
            <h2>{selectedSymbol.replace("USDT", "")} / USDT</h2>
            <div className="symbol-selector">
              {livePrices.map(p => (<button key={p.symbol} className={cls("sym-btn", selectedSymbol === p.symbol && "sym-btn-active")} onClick={() => setSelectedSymbol(p.symbol)}>{p.symbol.replace("USDT", "")}</button>))}
            </div>
          </div>
          {(() => { const p = livePrices.find(lp => lp.symbol === selectedSymbol); const mover = topMovers.find(m => m.symbol === selectedSymbol); if (!p) return null; return (
            <div className="grid-3">
              <div className="card card-highlight"><div className="big-price">{fmtUsd(p.price)}</div><div className={cls("big-change", (mover?.change_pct ?? 0) >= 0 ? "text-green" : "text-red")}>{fmtPct(mover?.change_pct ?? 0)} <span className="text-muted text-xs">(1s)</span></div></div>
              <div className="card"><div className="card-header"><h3>Açılış (1s)</h3></div><div className="stat-big">{fmtUsd(mover?.open_price ?? 0)}</div></div>
              <div className="card"><div className="card-header"><h3>Son Güncelleme</h3></div><div className="stat-big">{fmtTime(p.timestamp)}</div><div className="text-muted" style={{ padding: "0 20px 16px" }}>{p.exchange}</div></div>
            </div>); })()}
          <div className="card"><div className="card-header"><h3>{selectedSymbol} Fiyat Grafiği (1dk mum, 60dk)</h3><span className="badge badge-blue">{candles.length} mum</span></div><CandleChart candles={candles} height={240} /></div>
          {(() => { const flow = orderFlow.find(o => o.symbol === selectedSymbol); if (!flow) return null; return (
            <div className="grid-2">
              <div className="card"><div className="card-header"><h3>Alış / Satış Basıncı (30dk)</h3></div><FlowBar buy={flow.buy_volume} sell={flow.sell_volume} /><div className="flow-stats"><div><span className="text-green">Alış:</span> ${fmt(flow.buy_volume, 0)} ({flow.buy_count} trade)</div><div><span className="text-red">Satış:</span> ${fmt(flow.sell_volume, 0)} ({flow.sell_count} trade)</div></div></div>
              <div className="card"><div className="card-header"><h3>Son Trade'ler</h3></div><div className="trade-feed">{trades.filter(t => t.symbol === selectedSymbol).slice(0, 12).map((t, i) => (<div key={i} className={cls("trade-row", t.side === "buy" ? "trade-buy" : "trade-sell")}><span className="trade-side">{t.side === "buy" ? "▲" : "▼"}</span><span>{fmtUsd(Number(t.price))}</span><span className="text-muted">{fmt(Number(t.quantity), 4)}</span><span className="text-muted">{fmtTime(t.timestamp)}</span></div>))}</div></div>
            </div>); })()}
        </>)}

        {/* ═══ ORDER FLOW ═══ */}
        {tab === "orderflow" && (<>
          <div className="tab-header"><h2>Order Flow Analizi</h2><span className="badge badge-amber">Son 30 Dakika</span></div>
          <div className="of-grid">
            {orderFlow.map(o => { const total = o.buy_volume + o.sell_volume; const buyPct = total > 0 ? (o.buy_volume / total) * 100 : 50; const pressure = buyPct > 60 ? "Güçlü Alış" : buyPct < 40 ? "Güçlü Satış" : "Dengeli"; const pressureColor = buyPct > 60 ? "text-green" : buyPct < 40 ? "text-red" : "text-muted"; const price = livePrices.find(p => p.symbol === o.symbol); return (
              <div key={o.symbol} className="card of-card">
                <div className="of-card-header"><span className="symbol-name">{o.symbol.replace("USDT", "")}</span>{price && <span className="of-price">{fmtUsd(price.price)}</span>}</div>
                <FlowBar buy={o.buy_volume} sell={o.sell_volume} />
                <div className="of-stats-row"><div><span className="text-green">${fmt(o.buy_volume, 0)}</span><br /><span className="text-xs text-muted">{o.buy_count} alış</span></div><div className={cls("of-pressure", pressureColor)}>{pressure}</div><div className="text-right"><span className="text-red">${fmt(o.sell_volume, 0)}</span><br /><span className="text-xs text-muted">{o.sell_count} satış</span></div></div>
              </div>); })}
          </div>
        </>)}

        {/* ═══ BOT ═══ */}
        {tab === "bot" && (<>
          <div className="tab-header"><h2>Bot Performansı & Sinyaller</h2></div>
          <div className="kpi-strip">
            <KPI label="Toplam Sim." value={botSummary ? String(botSummary.total_simulations) : "0"} icon="🔄" />
            <KPI label="Açık" value={botSummary ? String(botSummary.open_simulations) : "0"} icon="🟢" accent="green" />
            <KPI label="Kapalı" value={botSummary ? String(botSummary.closed_simulations) : "0"} icon="🔒" />
            <KPI label="Kazanç" value={botSummary ? String(botSummary.wins) : "0"} icon="✅" accent="green" />
            <KPI label="Kayıp" value={botSummary ? String(botSummary.losses) : "0"} icon="❌" accent="red" />
            <KPI label="Ort. PnL %" value={botSummary ? `${fmt(botSummary.average_pnl_pct)}%` : "0%"} icon="📊" accent={botSummary && botSummary.average_pnl_pct > 0 ? "green" : "red"} />
          </div>
          <div className="grid-2">
            <div className="card card-center"><h3>Win Rate</h3><ProgressRing value={botSummary?.win_rate ?? 0} size={120} stroke={10} color={botSummary && botSummary.win_rate >= 50 ? "var(--green)" : "var(--red)"} /><div className="text-muted" style={{ marginTop: 12 }}>{botSummary?.wins ?? 0}W / {botSummary?.losses ?? 0}L</div></div>
            <div className="card"><div className="card-header"><h3>Son Sinyaller</h3><span className="badge">{signals.length}</span></div><div className="table-wrap"><table className="tbl"><thead><tr><th>Sembol</th><th>Tip</th><th>Güven</th><th>Fiyat</th><th>Durum</th><th>Zaman</th></tr></thead><tbody>{signals.slice(0, 10).map((s, i) => (<tr key={i}><td><strong>{s.symbol}</strong></td><td>{s.signal_type}</td><td>{(Number(s.confidence) * 100).toFixed(0)}%</td><td>{fmtUsd(Number(s.price))}</td><td><span className={cls("status-badge", `status-${s.status}`)}>{s.status}</span></td><td>{fmtTime(s.timestamp)}</td></tr>))}</tbody></table>{signals.length === 0 && <div className="empty-state">Henüz sinyal üretilmedi</div>}</div></div>
          </div>
          <div className="card"><div className="card-header"><h3>Simülasyonlar</h3><span className="badge">{simulations.length}</span></div><div className="table-wrap"><table className="tbl"><thead><tr><th>Sembol</th><th>Yön</th><th>Giriş</th><th>Çıkış</th><th>PnL</th><th>PnL %</th><th>Durum</th></tr></thead><tbody>{simulations.slice(0, 15).map((s, i) => (<tr key={i}><td><strong>{s.symbol}</strong></td><td><span className={cls("side-badge", s.side === "long" ? "side-long" : "side-short")}>{s.side}</span></td><td>{fmtUsd(Number(s.entry_price))}</td><td>{s.exit_price ? fmtUsd(Number(s.exit_price)) : "—"}</td><td className={Number(s.pnl || 0) >= 0 ? "text-green" : "text-red"}>{s.pnl ? fmtUsd(Number(s.pnl)) : "—"}</td><td>{s.pnl_pct ? `${Number(s.pnl_pct).toFixed(2)}%` : "—"}</td><td><span className={cls("status-badge", `status-${s.status}`)}>{s.status}</span></td></tr>))}</tbody></table>{simulations.length === 0 && <div className="empty-state">Ghost simulator henüz aktif değil</div>}</div></div>
          <div className="card"><div className="card-header"><h3>Tespit Edilen Hareketler</h3><span className="badge badge-amber">{movements.length}</span></div><div className="table-wrap"><table className="tbl"><thead><tr><th>Sembol</th><th>Borsa</th><th>Tip</th><th>Değişim</th><th>Yön</th><th>Hacim</th><th>Başlangıç</th><th>Bitiş</th></tr></thead><tbody>{movements.slice(0, 12).map((m, i) => (<tr key={i}><td><strong>{m.symbol}</strong></td><td>{m.exchange}</td><td><span className="badge badge-sm">{m.market_type}</span></td><td className={Number(m.change_pct) >= 0 ? "text-green" : "text-red"}>{fmtPct(Number(m.change_pct) * 100)}</td><td>{m.direction}</td><td>{fmt(Number(m.volume), 2)}</td><td>{fmtTimeShort(m.start_time)}</td><td>{fmtTimeShort(m.end_time)}</td></tr>))}</tbody></table>{movements.length === 0 && <div className="empty-state">Henüz %2'den büyük hareket tespit edilmedi</div>}</div></div>
        </>)}

        {/* ═══ BRAIN ═══ */}
        {tab === "brain" && (<>
          <div className="tab-header"><h2>AI Beyin Merkezi</h2><span className="badge badge-purple">Öğrenme Sistemi</span></div>
          <div className="kpi-strip">
            <KPI label="Öğrenilen Pattern" value={brainStatus ? fmt(brainStatus.pattern_count, 0) : "0"} icon="🧬" accent="purple" />
            <KPI label="Tahmin Doğruluğu" value={brainStatus ? `${brainStatus.learning.accuracy.toFixed(1)}%` : "0%"} icon="🎯" accent={brainStatus && brainStatus.learning.accuracy >= 50 ? "green" : "amber"} />
            <KPI label="Toplam Tahmin" value={brainStatus ? fmt(brainStatus.learning.total, 0) : "0"} icon="📊" />
            <KPI label="Doğru Tahmin" value={brainStatus ? fmt(brainStatus.learning.correct, 0) : "0"} icon="✅" accent="green" />
            <KPI label="Ort. PnL" value={brainStatus ? `${brainStatus.learning.avg_pnl.toFixed(2)}%` : "0%"} icon="💰" accent={brainStatus && brainStatus.learning.avg_pnl > 0 ? "green" : "red"} />
          </div>

          <div className="grid-2">
            <div className="card card-center">
              <h3>Öğrenme Doğruluğu</h3>
              <ProgressRing value={brainStatus?.learning.accuracy ?? 0} size={140} stroke={12} color={brainStatus && brainStatus.learning.accuracy >= 50 ? "var(--green)" : "var(--amber)"} />
              <div className="text-muted" style={{ marginTop: 16 }}>{brainStatus ? `${brainStatus.learning.correct} doğru / ${brainStatus.learning.total} toplam` : "Veri bekleniyor..."}</div>
            </div>
            <div className="card">
              <div className="card-header"><h3>Sinyal Tipi Başarıları</h3></div>
              {brainStatus && brainStatus.signal_type_stats.length > 0 ? (
                <div className="table-wrap"><table className="tbl"><thead><tr><th>Sinyal Tipi</th><th>Toplam</th><th>Doğru</th><th>Başarı</th><th>Ort PnL</th></tr></thead><tbody>
                  {brainStatus.signal_type_stats.map((s, i) => (
                    <tr key={i}><td>{s.signal_type}</td><td>{s.total}</td><td>{s.correct}</td>
                    <td className={s.total > 0 && s.correct / s.total >= 0.5 ? "text-green" : "text-red"}>{s.total > 0 ? `${(s.correct / s.total * 100).toFixed(0)}%` : "—"}</td>
                    <td className={s.avg_pnl >= 0 ? "text-green" : "text-red"}>{s.avg_pnl?.toFixed(2)}%</td></tr>
                  ))}
                </tbody></table></div>
              ) : <div className="empty-state">Henüz sinyal tipi istatistiği yok</div>}
            </div>
          </div>

          {/* Günlük Öğrenme Doğruluğu */}
          {learningStats?.daily_accuracy && learningStats.daily_accuracy.length > 0 && (
            <div className="card">
              <div className="card-header"><h3>Günlük Öğrenme Trendi</h3><span className="badge badge-cyan">Son 14 Gün</span></div>
              <div className="table-wrap"><table className="tbl"><thead><tr><th>Tarih</th><th>Toplam</th><th>Doğru</th><th>Doğruluk</th></tr></thead><tbody>
                {learningStats.daily_accuracy.map((d: any, i: number) => {
                  const acc = d.total > 0 ? (d.correct / d.total * 100) : 0;
                  return (
                    <tr key={i}>
                      <td>{d.day ? new Date(d.day).toLocaleDateString("tr-TR") : "—"}</td><td>{d.total}</td><td>{d.correct}</td>
                      <td className={acc >= 50 ? "text-green" : "text-red"}>{acc.toFixed(1)}%</td>
                    </tr>
                  );
                })}
              </tbody></table></div>
            </div>
          )}

          {/* Brain Patterns */}
          <div className="card">
            <div className="card-header"><h3>Bellek: Kayıtlı Patternlar</h3><span className="badge badge-purple">{brainPatterns.length}</span></div>
            {brainPatterns.length > 0 ? (
              <div className="table-wrap"><table className="tbl"><thead><tr><th>Sembol</th><th>15dk</th><th>1s</th><th>4s</th><th>1g</th><th>Kayıt</th></tr></thead><tbody>
                {brainPatterns.map((p, i) => (
                  <tr key={i}>
                    <td><strong>{p.symbol}</strong></td>
                    <td className={p.outcome_15m > 0 ? "text-green" : p.outcome_15m < 0 ? "text-red" : "text-muted"}>{p.outcome_15m != null ? `${(p.outcome_15m * 100).toFixed(2)}%` : "⏳"}</td>
                    <td className={p.outcome_1h > 0 ? "text-green" : p.outcome_1h < 0 ? "text-red" : "text-muted"}>{p.outcome_1h != null ? `${(p.outcome_1h * 100).toFixed(2)}%` : "⏳"}</td>
                    <td className={p.outcome_4h > 0 ? "text-green" : p.outcome_4h < 0 ? "text-red" : "text-muted"}>{p.outcome_4h != null ? `${(p.outcome_4h * 100).toFixed(2)}%` : "⏳"}</td>
                    <td className={p.outcome_1d > 0 ? "text-green" : p.outcome_1d < 0 ? "text-red" : "text-muted"}>{p.outcome_1d != null ? `${(p.outcome_1d * 100).toFixed(2)}%` : "⏳"}</td>
                    <td className="text-muted">{fmtTime(p.created_at)}</td>
                  </tr>
                ))}
              </tbody></table></div>
            ) : <div className="empty-state">Pattern verisi birikiyor...</div>}
          </div>

          <div className="card">
            <div className="card-header"><h3>AI Öğrenme Nasıl Çalışır?</h3></div>
            <div className="brain-info">
              <div className="brain-step"><span className="step-num">1</span><div><strong>Veri Toplama</strong><p>Scout bot, tüm borsalardan gerçek zamanlı trade verisi toplar</p></div></div>
              <div className="brain-step"><span className="step-num">2</span><div><strong>Pattern Tespiti</strong><p>Strategist bot, 15dk/1s/4s/1g zaman dilimlerinde fiyat pattern'ları tespit eder</p></div></div>
              <div className="brain-step"><span className="step-num">3</span><div><strong>Eşleştirme</strong><p>Brain modülü, mevcut pattern'ları geçmiş verilerle karşılaştırır (cosine similarity)</p></div></div>
              <div className="brain-step"><span className="step-num">4</span><div><strong>Simülasyon</strong><p>Ghost bot, yüksek güvenli sinyallerle kağıt üstü trade açar (min %2 hedef)</p></div></div>
              <div className="brain-step"><span className="step-num">5</span><div><strong>Geri Bildirim</strong><p>Sonuçlar Brain'e geri beslenir, doğruluk oranı sürekli iyileştirilir</p></div></div>
            </div>
          </div>
        </>)}

        {/* ═══ CHAT ═══ */}
        {tab === "chat" && (<>
          <div className="tab-header"><h2>Bot Sohbet</h2><span className="badge badge-green">AI Asistan</span></div>
          <div className="chat-container">
            <div className="chat-messages">
              {chatMessages.length === 0 && (
                <div className="chat-welcome">
                  <div className="chat-welcome-icon">🤖</div>
                  <h3>QuenBot AI'a Hoşgeldiniz</h3>
                  <p>Botlarla doğal dilde konuşabilirsiniz. İstediğinizi yazın!</p>
                  <div className="chat-examples">
                    <button onClick={() => setChatInput("BTC fiyatı ne?")} className="chat-example-btn">💰 BTC fiyatı ne?</button>
                    <button onClick={() => setChatInput("Sistem nasıl çalışıyor?")} className="chat-example-btn">📊 Sistem nasıl?</button>
                    <button onClick={() => setChatInput("Açık sinyal var mı?")} className="chat-example-btn">📡 Sinyaller</button>
                    <button onClick={() => setChatInput("Ghost trader ne durumda?")} className="chat-example-btn">👻 Simülasyonlar</button>
                    <button onClick={() => setChatInput("Beyin ne öğrendi?")} className="chat-example-btn">🧠 AI Beyin</button>
                    <button onClick={() => setChatInput("Veri akışı sağlıklı mı?")} className="chat-example-btn">🔴 Veri akışı</button>
                    <button onClick={() => setChatInput("Piyasada ne oluyor?")} className="chat-example-btn">📈 Piyasa durumu</button>
                    <button onClick={() => setChatInput("Yardım")} className="chat-example-btn">❓ Yardım</button>
                  </div>
                </div>
              )}
              {chatMessages.map(msg => (
                <div key={msg.id} className={cls("chat-msg", msg.role === "user" ? "chat-msg-user" : "chat-msg-bot")}>
                  <div className="chat-msg-header">
                    <span className="chat-msg-name">{msg.role === "user" ? "Sen" : `🤖 ${msg.agent_name || "QuenBot AI"}`}</span>
                    <span className="chat-msg-time">{fmtTime(msg.created_at)}</span>
                  </div>
                  <div className="chat-msg-body">{msg.message.split('\n').map((line, i) => <div key={i}>{line || <br />}</div>)}</div>
                </div>
              ))}
              <div ref={chatEndRef} />
            </div>
            <div className="chat-input-bar">
              <input className="chat-input" placeholder="Botlara bir mesaj yazın... (doğal dilde konuşabilirsiniz)" value={chatInput} onChange={e => setChatInput(e.target.value)} onKeyDown={e => e.key === "Enter" && sendChatMessage()} disabled={chatSending} />
              <button className="chat-send-btn" onClick={sendChatMessage} disabled={chatSending || !chatInput.trim()}>{chatSending ? "..." : "Gönder"}</button>
            </div>
          </div>
        </>)}

        {/* ═══ ADMIN ═══ */}
        {tab === "admin" && (<>
          <div className="tab-header"><h2>Yönetim Paneli</h2><span className="badge badge-red">Admin</span></div>

          {/* DB Stats */}
          <div className="card">
            <div className="card-header"><h3>Veritabanı Tablo İstatistikleri</h3><span className="badge badge-blue">{tableStats.length} tablo</span></div>
            {tableStats.length > 0 ? (
              <div className="db-stats-grid">
                {tableStats.map((t, i) => (
                  <div key={i} className="db-stat-card">
                    <div className="db-stat-name">{t.table_name}</div>
                    <div className="db-stat-value">{fmt(t.row_count, 0)}</div>
                  </div>
                ))}
              </div>
            ) : <div className="empty-state">Tablo istatistikleri yükleniyor...</div>}
          </div>

          {/* Agent Config */}
          <div className="card">
            <div className="card-header"><h3>Agent Konfigürasyonları</h3><span className="badge badge-cyan">{agentConfigs.length}</span></div>
            {agentConfigs.length > 0 ? (
              <div className="table-wrap"><table className="tbl"><thead><tr><th>Agent</th><th>Anahtar</th><th>Değer</th><th>Güncelleme</th></tr></thead><tbody>
                {agentConfigs.map((c, i) => (
                  <tr key={i}>
                    <td><strong>{c.agent_name}</strong></td>
                    <td>{c.config_key}</td>
                    <td className="text-mono">{c.config_value}</td>
                    <td className="text-muted">{c.updated_at ? fmtTime(c.updated_at) : "—"}</td>
                  </tr>
                ))}
              </tbody></table></div>
            ) : <div className="empty-state">Henüz agent konfigürasyonu yok. Sistem varsayılan değerleri kullanıyor.</div>}
          </div>

          {/* Audit Records */}
          <div className="card">
            <div className="card-header"><h3>Denetim Kayıtları</h3><span className="badge badge-amber">{auditRecords.length}</span></div>
            {auditRecords.length > 0 ? (
              <div className="table-wrap"><table className="tbl"><thead><tr><th>Zaman</th><th>Toplam Sim.</th><th>Başarılı</th><th>Başarısız</th><th>Başarı %</th><th>Ort. Kazanç</th><th>Ort. Kayıp</th></tr></thead><tbody>
                {auditRecords.map((r, i) => (
                  <tr key={i}>
                    <td>{r.timestamp ? fmtTime(r.timestamp) : "—"}</td>
                    <td>{r.total_simulations ?? "—"}</td>
                    <td className="text-green">{r.successful_simulations ?? "—"}</td>
                    <td className="text-red">{r.failed_simulations ?? "—"}</td>
                    <td className={Number(r.success_rate || 0) >= 0.5 ? "text-green" : "text-red"}>{r.success_rate != null ? `${(Number(r.success_rate) * 100).toFixed(1)}%` : "—"}</td>
                    <td className="text-green">{r.avg_win_pct != null ? `${Number(r.avg_win_pct).toFixed(2)}%` : "—"}</td>
                    <td className="text-red">{r.avg_loss_pct != null ? `${Number(r.avg_loss_pct).toFixed(2)}%` : "—"}</td>
                  </tr>
                ))}
              </tbody></table></div>
            ) : <div className="empty-state">Henüz denetim kaydı yok</div>}
          </div>

          {/* Failure Analysis */}
          <div className="card">
            <div className="card-header"><h3>Başarısızlık Analizi</h3><span className="badge badge-red">{failureAnalysis.length}</span></div>
            {failureAnalysis.length > 0 ? (
              <div className="table-wrap"><table className="tbl"><thead><tr><th>Sinyal Tipi</th><th>Başarısızlık Sayısı</th><th>Ort. Kayıp %</th><th>Öneri</th><th>Zaman</th></tr></thead><tbody>
                {failureAnalysis.map((f, i) => (
                  <tr key={i}>
                    <td><strong>{f.signal_type ?? "—"}</strong></td>
                    <td>{f.failure_count ?? "—"}</td>
                    <td className="text-red">{f.avg_loss_pct != null ? `${Number(f.avg_loss_pct).toFixed(2)}%` : "—"}</td>
                    <td className="text-xs" style={{ maxWidth: 300, overflow: "hidden", textOverflow: "ellipsis" }}>{f.recommendation || "—"}</td>
                    <td className="text-muted">{f.timestamp ? fmtTime(f.timestamp) : "—"}</td>
                  </tr>
                ))}
              </tbody></table></div>
            ) : <div className="empty-state">Henüz başarısızlık kaydı yok - bu iyi bir işaret!</div>}
          </div>
        </>)}

        {/* ═══ SYSTEM ═══ */}
        {tab === "system" && (<>
          <div className="tab-header"><h2>Sistem Durumu</h2></div>
          <div className="kpi-strip">
            <KPI label="Veritabanı" value={systemStats ? `${systemStats.db_size_mb} MB` : "—"} icon="💾" />
            <KPI label="Toplam Trade" value={systemStats ? fmt(systemStats.total_trades, 0) : "—"} icon="📦" />
            <KPI label="Trade/dk" value={systemStats ? fmt(systemStats.trades_per_minute, 0) : "—"} icon="⚡" accent="blue" />
            <KPI label="Çalışma Süresi" value={systemStats ? ago(systemStats.uptime_minutes) : "—"} icon="⏱" accent="green" />
          </div>
          <div className="grid-2">
            <div className="card"><div className="card-header"><h3>Agent Durumları</h3><span className="badge badge-green">{Object.values(agentStatuses).filter(a => a.status === 'running').length} aktif</span></div><div className="agent-grid">
              {Object.keys(agentStatuses).length > 0 ? (
                Object.entries(agentStatuses).map(([name, agent]) => {
                  const isHealthy = agent.status === "running";
                  const isStale = agent.status === "stale";
                  const dotClass = isHealthy ? "dot-ok" : isStale ? "dot-warn" : "dot-unknown";
                  return (
                    <div key={name} className="agent-card">
                      <div className={cls("agent-status-dot", dotClass)} />
                      <div>
                        <div className="agent-name">{name}</div>
                        <div className="text-muted text-xs">
                          {isHealthy ? "Çalışıyor" : isStale ? "Yanıtlamıyor" : agent.status}
                          {agent.age_seconds != null && ` · ${agent.age_seconds < 60 ? `${Math.round(agent.age_seconds)}sn` : `${Math.floor(agent.age_seconds / 60)}dk`} önce`}
                        </div>
                      </div>
                    </div>
                  );
                })
              ) : (
                ["Scout Agent", "Strategist Agent", "Ghost Simulator", "Auditor Agent", "Brain Module", "Chat Engine"].map((name, i) => (
                  <div key={i} className="agent-card"><div className="agent-status-dot dot-unknown" /><div><div className="agent-name">{name}</div><div className="text-muted text-xs">Durum bilinmiyor</div></div></div>
                ))
              )}
            </div></div>
            <div className="card"><div className="card-header"><h3>Bağlantılar</h3></div><div className="conn-list"><div className="conn-item"><span className="conn-dot conn-ok" />Binance Spot WebSocket</div><div className="conn-item"><span className="conn-dot conn-ok" />Binance Futures WebSocket</div><div className="conn-item"><span className="conn-dot conn-ok" />Bybit Spot WebSocket</div><div className="conn-item"><span className="conn-dot conn-ok" />Bybit Futures WebSocket</div><div className="conn-item"><span className="conn-dot conn-ok" />PostgreSQL</div><div className="conn-item"><span className="conn-dot conn-ok" />Brain AI Module</div><div className="conn-item"><span className="conn-dot conn-ok" />Chat Engine v2</div></div></div>
          </div>

          {/* Watchlist Yönetimi */}
          <div className="card">
            <div className="card-header"><h3>İzleme Listesi Yönetimi</h3><span className="badge badge-blue">{watchlist.length} aktif</span></div>
            <div className="wl-add-form">
              <input className="wl-input" placeholder="Sembol (örn: BTCUSDT)" value={wlSymbol} onChange={e => setWlSymbol(e.target.value)} onKeyDown={e => e.key === "Enter" && addToWatchlist()} />
              <select className="wl-select" value={wlExchange} onChange={e => setWlExchange(e.target.value)}>
                <option value="all">Tüm Borsalar</option><option value="binance">Binance</option><option value="bybit">Bybit</option>
              </select>
              <select className="wl-select" value={wlMarketType} onChange={e => setWlMarketType(e.target.value)}>
                <option value="spot">Spot</option><option value="futures">Futures</option>
              </select>
              <button className="wl-add-btn" onClick={addToWatchlist}>+ Ekle</button>
            </div>
            {watchlist.length > 0 ? (
              <div className="wl-list">
                {watchlist.map(w => (
                  <div key={w.id} className="wl-item">
                    <span className="wl-symbol">{w.symbol}</span>
                    <span className="badge badge-sm">{w.exchange}</span>
                    <span className="badge badge-sm badge-blue">{w.market_type}</span>
                    <button className="wl-remove-btn" onClick={() => removeFromWatchlist(w)}>✕</button>
                  </div>
                ))}
              </div>
            ) : <div className="empty-state">Henüz özel izleme listesi oluşturulmadı</div>}
          </div>

          <div className="card"><div className="card-header"><h3>İzlenen Semboller</h3></div><div className="symbol-chips">{livePrices.map(p => (<div key={p.symbol} className="symbol-chip"><span>{p.symbol}</span><span className="text-muted">{fmtUsd(p.price)}</span></div>))}</div></div>
          <div className="card"><div className="card-header"><h3>Zaman Bilgisi</h3></div><div className="info-grid"><div><span className="text-muted">İlk Trade:</span> {systemStats?.oldest_trade ? new Date(systemStats.oldest_trade).toLocaleString() : "—"}</div><div><span className="text-muted">Son Trade:</span> {systemStats?.newest_trade ? new Date(systemStats.newest_trade).toLocaleString() : "—"}</div><div><span className="text-muted">Şu an:</span> {new Date().toLocaleString()}</div></div></div>
        </>)}
      </main>
    </div>
  );
}

function KPI({ label, value, sub, icon, accent }: { label: string; value: string; sub?: string; icon?: string; accent?: string }) {
  return (<div className={cls("kpi", accent && `kpi-${accent}`)}>{icon && <span className="kpi-icon">{icon}</span>}<div><div className="kpi-value">{value}</div><div className="kpi-label">{label}</div>{sub && <div className="kpi-sub">{sub}</div>}</div></div>);
}

const root = ReactDOM.createRoot(document.getElementById("root")!);
root.render(<React.StrictMode><App /></React.StrictMode>);