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
type Tab = "overview" | "markets" | "orderflow" | "bot" | "livedata" | "brain" | "chat" | "admin" | "system" | "mastercontrol";

/* ───────── Helpers ───────── */
const fmt = (v: any, d = 2) => { const n = Number(v); return new Intl.NumberFormat("en-US", { maximumFractionDigits: d }).format(isNaN(n) ? 0 : n); };
const fmtUsd = (v: any) => `$${fmt(v)}`;
const fmtPct = (v: any) => { const n = Number(v); return `${isNaN(n) ? 0 : n >= 0 ? "+" : ""}${(isNaN(n) ? 0 : n).toFixed(2)}%`; };
const fmtTime = (s: string) => { try { return new Date(s).toLocaleTimeString("en-US", { hour: "2-digit", minute: "2-digit", second: "2-digit" }); } catch { return "—"; } };
const fmtTimeShort = (s: string) => { try { return new Date(s).toLocaleTimeString("en-US", { hour: "2-digit", minute: "2-digit" }); } catch { return "—"; } };
const cls = (...c: (string | false | undefined | null)[]) => c.filter(Boolean).join(" ");
const ago = (minutes: any) => { const m = safeNum(minutes); if (m < 60) return `${m}m`; if (m < 1440) return `${Math.floor(m / 60)}h ${m % 60}m`; return `${Math.floor(m / 1440)}d ${Math.floor((m % 1440) / 60)}h`; };
const safeNum = (v: any, d = 0): number => { const n = Number(v); return isNaN(n) ? d : n; };
const fmtDateTime = (s: string) => { try { const d = new Date(s); return `${d.toLocaleDateString("tr-TR", { day: "2-digit", month: "short" })} ${d.toLocaleTimeString("tr-TR", { hour: "2-digit", minute: "2-digit" })}`; } catch { return "—"; } };
const safeMeta = (m: any): Record<string, any> => { if (!m) return {}; if (typeof m === 'string') { try { const p = JSON.parse(m); return (p && typeof p === 'object') ? p : {}; } catch { return {}; } } return (typeof m === 'object') ? m : {}; };
const getDir = (s: RecordRow): 'long' | 'short' | null => { const meta = safeMeta(s.metadata); if (meta.position_bias === 'long' || meta.position_bias === 'short') return meta.position_bias; const t = s.signal_type || ''; if (t.endsWith('_long') || t.includes('long')) return 'long'; if (t.endsWith('_short') || t.includes('short')) return 'short'; return null; };
const sigLabel = (t: string): string => { if (!t) return '—'; const m: Record<string, string> = { evolutionary_similarity: 'Evrimsel Benzerlik', momentum: 'Momentum', brain_pattern: 'Brain Pattern', price_action: 'Fiyat Aksiyon', signature: 'İmza Eşleşme', historical_signature: 'Tarihsel İmza' }; for (const [k, v] of Object.entries(m)) { if (t.includes(k)) return v; } return t.replace(/_/g, ' '); };
const stInfo = (st: string): { label: string; c: string } => { if (st === 'pending') return { label: 'Bekliyor', c: 'badge-amber' }; if (st === 'processed') return { label: 'İşlendi', c: 'badge-green' }; if (st === 'filtered_duplicate') return { label: 'Duplikat', c: 'badge-purple' }; if (st === 'filtered_low_return') return { label: 'Düşük Getiri', c: 'badge-purple' }; if (st?.startsWith('risk_')) return { label: 'Risk Red', c: 'badge-red' }; if (st?.startsWith('filtered_')) return { label: 'Filtrelendi', c: 'badge-amber' }; return { label: st || '—', c: '' }; };

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
function ProgressRing({ value, size = 80, stroke = 6, color = "var(--accent)" }: { value: any; size?: number; stroke?: number; color?: string }) {
  const val = safeNum(value);
  const r = (size - stroke) / 2;
  const circ = 2 * Math.PI * r;
  const offset = circ - (Math.min(val, 100) / 100) * circ;
  return (
    <svg width={size} height={size} style={{ display: "block" }}>
      <circle cx={size / 2} cy={size / 2} r={r} fill="none" stroke="rgba(148,163,184,0.1)" strokeWidth={stroke} />
      <circle cx={size / 2} cy={size / 2} r={r} fill="none" stroke={color} strokeWidth={stroke} strokeDasharray={circ} strokeDashoffset={offset} strokeLinecap="round" transform={`rotate(-90 ${size / 2} ${size / 2})`} />
      <text x="50%" y="50%" dominantBaseline="central" textAnchor="middle" fill="currentColor" fontSize="14" fontWeight="700">{val.toFixed(0)}%</text>
    </svg>
  );
}

/* ───────── Flow Bar ───────── */
function FlowBar({ buy, sell }: { buy: number; sell: number }) {
  const total = safeNum(buy) + safeNum(sell) || 1;
  const pct = (safeNum(buy) / total) * 100;
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

/* ───────── Error Boundary ───────── */
class ErrorBoundary extends React.Component<{children: React.ReactNode}, {hasError: boolean; error: string}> {
  constructor(props: any) { super(props); this.state = { hasError: false, error: '' }; }
  static getDerivedStateFromError(error: Error) { return { hasError: true, error: error.message }; }
  render() {
    if (this.state.hasError) {
      return <div style={{ padding: 40, color: '#ff3d57', background: '#0a0f1e', minHeight: '100vh', fontFamily: 'Inter, sans-serif' }}>
        <h2>⚠ Görüntüleme Hatası</h2>
        <p style={{ color: '#8b9cc0', marginTop: 12 }}>{this.state.error}</p>
        <button onClick={() => { this.setState({ hasError: false, error: '' }); }} style={{ marginTop: 20, padding: '10px 24px', background: '#448aff', color: '#fff', border: 'none', borderRadius: 8, cursor: 'pointer', fontWeight: 700 }}>Yeniden Dene</button>
      </div>;
    }
    return this.props.children;
  }
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
  const [rcaResults, setRcaResults] = useState<RecordRow[]>([]);
  const [rcaStats, setRcaStats] = useState<RecordRow[]>([]);
  const [corrections, setCorrections] = useState<RecordRow[]>([]);
  const [signatures, setSignatures] = useState<RecordRow[]>([]);
  const [signalSummary, setSignalSummary] = useState<RecordRow[]>([]);
  const [masterDirective, setMasterDirective] = useState("");
  const [directiveSaving, setDirectiveSaving] = useState(false);
  const [directiveStatus, setDirectiveStatus] = useState<string | null>(null);
  const [llmStatus, setLlmStatus] = useState<RecordRow | null>(null);
  const [queueStatus, setQueueStatus] = useState<RecordRow | null>(null);

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
        "/api/agents/status",
        "/api/rca/results", "/api/rca/stats",
        "/api/corrections", "/api/signatures?limit=20",
        "/api/signals/summary"
      ];
      const results = await Promise.all(ep.map(async url => { try { const r = await fetch(url); return r.ok ? r.json() : null; } catch { return null; } }));
      const [s, p, b, tm, of2, tl, vd, ss, sig, sim, tr, mv, ch, chatM, wl, bs, ls, ts, ac, ar, fa, pt, bp, lst, agSt, rcaR, rcaS, corr, sigs, sigSum] = results;
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
      if (rcaR) setRcaResults(Array.isArray(rcaR) ? rcaR : []);
      if (rcaS) setRcaStats(Array.isArray(rcaS) ? rcaS : (rcaS?.distribution ?? []));
      if (corr) setCorrections(Array.isArray(corr) ? corr : []);
      if (sigs) setSignatures(Array.isArray(sigs) ? sigs : []);
      if (sigSum) setSignalSummary(Array.isArray(sigSum) ? sigSum : (sigSum?.by_type ?? []));

      // Fetch LLM/directive data from port 3002
      try {
        const [dirRes, llmRes, qRes] = await Promise.all([
          fetch("/api/directives").then(r => r.ok ? r.json() : null).catch(() => null),
          fetch("/api/llm/status").then(r => r.ok ? r.json() : null).catch(() => null),
          fetch("/api/llm/queue").then(r => r.ok ? r.json() : null).catch(() => null),
        ]);
        if (dirRes && dirRes.master_directive !== undefined && !directiveSaving) setMasterDirective(dirRes.master_directive);
        if (llmRes) setLlmStatus(llmRes);
        if (qRes) setQueueStatus(qRes);
      } catch {}

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
      { key: "mastercontrol" as Tab, label: "Master Kontrol", icon: "🎯" },
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
                  const ageSec = safeNum(ef.age_seconds, 999);
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
          <div className="tab-header"><h2>Sinyal İstihbarat Merkezi</h2><span className="badge badge-live">CANLI</span></div>

          {/* ── KPI Strip ── */}
          <div className="kpi-strip">
            <KPI label="Toplam Sinyal" value={String(signals.length)} icon="📡" />
            <KPI label="Bekleyen" value={String(signals.filter(s => s.status === 'pending').length)} icon="⏳" accent="amber" />
            <KPI label="İşlenen" value={String(signals.filter(s => s.status === 'processed').length)} icon="✅" accent="green" />
            <KPI label="Reddedilen" value={String(signals.filter(s => s.status?.startsWith('risk_') || s.status?.startsWith('filtered')).length)} icon="🛡" accent="red" />
            <KPI label="Açık Sim." value={botSummary ? String(botSummary.open_simulations) : "0"} icon="👻" accent="green" />
            <KPI label="Win Rate" value={botSummary ? `${fmt(botSummary.win_rate ?? 0, 1)}%` : "0%"} icon="🏆" accent={botSummary && (botSummary.win_rate ?? 0) >= 50 ? "green" : "amber"} />
          </div>

          {/* ── Win Rate + Direction Summary ── */}
          <div className="grid-2">
            <div className="card card-center">
              <h3>Win Rate</h3>
              <ProgressRing value={botSummary?.win_rate ?? 0} size={120} stroke={10} color={botSummary && (botSummary.win_rate ?? 0) >= 50 ? "var(--green)" : "var(--red)"} />
              <div className="text-muted" style={{ marginTop: 12 }}>{botSummary?.wins ?? 0}W / {botSummary?.losses ?? 0}L • Ort. PnL: {botSummary ? `${fmt(botSummary.average_pnl_pct ?? 0)}%` : "0%"}</div>
            </div>
            <div className="card">
              <div className="card-header"><h3>Yön Dağılımı</h3><span className="badge badge-cyan">LONG vs SHORT</span></div>
              {(() => {
                const longs = signals.filter(s => getDir(s) === 'long').length;
                const shorts = signals.filter(s => getDir(s) === 'short').length;
                const total = longs + shorts || 1;
                const longPct = (longs / total * 100);
                return (
                  <div style={{ padding: 22 }}>
                    <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 8, fontWeight: 700 }}>
                      <span className="text-green">↑ YUKARIŞ ({longs})</span>
                      <span className="text-red">↓ DÜŞÜŞ ({shorts})</span>
                    </div>
                    <div style={{ height: 14, background: 'var(--red-dim)', borderRadius: 99, overflow: 'hidden' }}>
                      <div style={{ width: `${longPct}%`, height: '100%', background: 'linear-gradient(90deg, var(--green), rgba(0,230,118,0.6))', borderRadius: '99px 0 0 99px', transition: 'width 0.5s' }} />
                    </div>
                    <div style={{ display: 'flex', justifyContent: 'space-between', marginTop: 12, fontSize: 12 }}>
                      <span className="text-muted">{longPct.toFixed(0)}% Yükseliş</span>
                      <span className="text-muted">{(100 - longPct).toFixed(0)}% Düşüş</span>
                    </div>
                    <div style={{ marginTop: 16, display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: 12 }}>
                      <div style={{ padding: '10px 12px', background: 'var(--bg3)', borderRadius: 8, textAlign: 'center' }}>
                        <div className="text-muted" style={{ fontSize: 10, textTransform: 'uppercase', letterSpacing: '0.06em' }}>Toplam Sim.</div>
                        <div style={{ fontSize: 20, fontWeight: 800, marginTop: 4 }}>{botSummary?.total_simulations ?? 0}</div>
                      </div>
                      <div style={{ padding: '10px 12px', background: 'var(--bg3)', borderRadius: 8, textAlign: 'center' }}>
                        <div className="text-muted" style={{ fontSize: 10, textTransform: 'uppercase', letterSpacing: '0.06em' }}>Kazanç</div>
                        <div style={{ fontSize: 20, fontWeight: 800, marginTop: 4, color: 'var(--green)' }}>{botSummary?.wins ?? 0}</div>
                      </div>
                      <div style={{ padding: '10px 12px', background: 'var(--bg3)', borderRadius: 8, textAlign: 'center' }}>
                        <div className="text-muted" style={{ fontSize: 10, textTransform: 'uppercase', letterSpacing: '0.06em' }}>Kayıp</div>
                        <div style={{ fontSize: 20, fontWeight: 800, marginTop: 4, color: 'var(--red)' }}>{botSummary?.losses ?? 0}</div>
                      </div>
                    </div>
                  </div>
                );
              })()}
            </div>
          </div>

          {/* ── PROFESSIONAL SIGNAL TABLE ── */}
          <div className="card">
            <div className="card-header">
              <h3>📡 Sinyal Akışı — Detaylı Görünüm</h3>
              <span className="badge badge-cyan">{signals.length} kayıt</span>
            </div>
            <div className="table-wrap">
              <table className="tbl">
                <thead>
                  <tr>
                    <th>Coin</th>
                    <th>Yön</th>
                    <th>Fiyat</th>
                    <th>Strateji</th>
                    <th>Güven</th>
                    <th>Hedef</th>
                    <th>Piyasa</th>
                    <th>Durum</th>
                    <th>Tarih / Saat</th>
                  </tr>
                </thead>
                <tbody>
                  {signals.slice(0, 30).map((s, i) => {
                    const meta = safeMeta(s.metadata);
                    const dir = getDir(s);
                    const price = safeNum(s.price);
                    const conf = safeNum(s.confidence) * 100;
                    const target = meta.target_pct != null ? safeNum(meta.target_pct) : null;
                    const mkt = (meta.market_type || s.market_type || 'spot').toUpperCase();
                    const si = stInfo(s.status);
                    const ts = s.timestamp || s.created_at || '';
                    return (
                      <tr key={s.id || i} className={dir === 'long' ? 'sig-row-long' : dir === 'short' ? 'sig-row-short' : ''}>
                        <td>
                          <div className="coin-cell">
                            <span className="coin-icon">{dir === 'long' ? '🟢' : dir === 'short' ? '🔴' : '⚪'}</span>
                            <div>
                              <strong className="coin-name">{(s.symbol || '').replace('USDT', '')}</strong>
                              <span className="coin-pair">/USDT</span>
                            </div>
                          </div>
                        </td>
                        <td>
                          <span className={cls("dir-badge", dir === "long" ? "dir-long" : dir === "short" ? "dir-short" : "dir-neutral")}>
                            {dir === 'long' ? '↑ YUKARIŞ' : dir === 'short' ? '↓ DÜŞÜŞ' : '—'}
                          </span>
                        </td>
                        <td className="text-mono" style={{ fontWeight: 700 }}>{price > 0 ? fmtUsd(price) : '—'}</td>
                        <td><span className="strat-badge">{sigLabel(s.signal_type)}</span></td>
                        <td>
                          <div className="conf-cell">
                            <div className="conf-bar"><div className="conf-fill" style={{ width: `${Math.min(conf, 100)}%`, background: conf >= 70 ? 'var(--green)' : conf >= 50 ? 'var(--amber)' : 'var(--red)' }} /></div>
                            <span className="conf-text">{conf.toFixed(0)}%</span>
                          </div>
                        </td>
                        <td className="text-green" style={{ fontWeight: 700 }}>{target != null ? `%${target.toFixed(1)}` : '—'}</td>
                        <td><span className="badge badge-sm">{mkt}</span></td>
                        <td><span className={cls("badge badge-sm", si.c)}>{si.label}</span></td>
                        <td>
                          <div className="time-cell">
                            <span className="time-main">{fmtDateTime(ts)}</span>
                          </div>
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
              {signals.length === 0 && (
                <div className="empty-state">
                  <div style={{ fontSize: 48, marginBottom: 12 }}>📡</div>
                  <p><strong>Henüz sinyal üretilmedi</strong></p>
                  <p className="text-xs text-muted" style={{ marginTop: 8 }}>Sistem veri topluyor ve pattern arıyor. İlk sinyaller kısa süre içinde gelecek.</p>
                </div>
              )}
            </div>
          </div>

          {/* ── ACTIVE SIMULATIONS ── */}
          {simulations.filter(s => s.status === 'open').length > 0 && (
            <div className="card">
              <div className="card-header"><h3>👻 Aktif Simülasyonlar</h3><span className="badge badge-green">{simulations.filter(s => s.status === 'open').length} açık</span></div>
              <div className="sim-grid">
                {simulations.filter(s => s.status === 'open').map((s, i) => {
                  const entry = safeNum(s.entry_price);
                  const lp = livePrices.find(p => p.symbol === s.symbol);
                  const current = lp ? lp.price : entry;
                  const pnlEst = entry > 0 ? ((s.side === 'long' ? (current - entry) : (entry - current)) / entry * 100) : 0;
                  const dur = s.entry_time ? Math.floor((Date.now() - new Date(s.entry_time).getTime()) / 60000) : 0;
                  return (
                    <div key={s.id || i} className={cls("sim-card", pnlEst >= 0 ? "sim-card-win" : "sim-card-loss")}>
                      <div className="sim-card-top">
                        <div className="sim-coin">
                          <strong>{(s.symbol || '').replace('USDT', '')}</strong>
                          <span className={cls("dir-badge dir-sm", s.side === 'long' ? "dir-long" : "dir-short")}>{s.side === 'long' ? '↑ LONG' : '↓ SHORT'}</span>
                        </div>
                        <div className={cls("sim-pnl", pnlEst >= 0 ? "text-green" : "text-red")}>
                          {pnlEst >= 0 ? '+' : ''}{pnlEst.toFixed(2)}%
                        </div>
                      </div>
                      <div className="sim-card-body">
                        <div className="sim-detail"><span className="text-muted">Giriş:</span> <span className="text-mono">{fmtUsd(entry)}</span></div>
                        <div className="sim-detail"><span className="text-muted">Güncel:</span> <span className="text-mono">{fmtUsd(current)}</span></div>
                        <div className="sim-detail"><span className="text-muted">Süre:</span> {ago(dur)}</div>
                        <div className="sim-detail"><span className="text-muted">Açılış:</span> {s.entry_time ? fmtDateTime(s.entry_time) : '—'}</div>
                      </div>
                    </div>
                  );
                })}
              </div>
            </div>
          )}

          {/* ── CLOSED SIMULATIONS ── */}
          <div className="card">
            <div className="card-header"><h3>Simülasyon Geçmişi</h3><span className="badge">{simulations.length} toplam</span></div>
            <div className="table-wrap">
              <table className="tbl">
                <thead>
                  <tr><th>Coin</th><th>Yön</th><th>Giriş Fiyatı</th><th>Çıkış Fiyatı</th><th>PnL</th><th>PnL %</th><th>Giriş Zamanı</th><th>Çıkış Zamanı</th><th>Durum</th></tr>
                </thead>
                <tbody>
                  {simulations.slice(0, 20).map((s, i) => {
                    const pnlVal = safeNum(s.pnl);
                    const pnlPctVal = safeNum(s.pnl_pct);
                    return (
                      <tr key={s.id || i}>
                        <td><strong>{(s.symbol || '').replace('USDT', '')}</strong><span className="text-muted">/USDT</span></td>
                        <td><span className={cls("dir-badge dir-sm", s.side === "long" ? "dir-long" : "dir-short")}>{s.side === 'long' ? '↑ LONG' : '↓ SHORT'}</span></td>
                        <td className="text-mono">{fmtUsd(safeNum(s.entry_price))}</td>
                        <td className="text-mono">{s.exit_price ? fmtUsd(safeNum(s.exit_price)) : <span className="text-muted">—</span>}</td>
                        <td className={pnlVal >= 0 ? "text-green" : "text-red"} style={{ fontWeight: 700 }}>{s.pnl != null ? `${pnlVal >= 0 ? '+' : ''}${fmtUsd(pnlVal)}` : "—"}</td>
                        <td className={pnlPctVal >= 0 ? "text-green" : "text-red"} style={{ fontWeight: 700 }}>{s.pnl_pct != null ? `${pnlPctVal >= 0 ? '+' : ''}${pnlPctVal.toFixed(2)}%` : "—"}</td>
                        <td className="text-muted">{s.entry_time ? fmtDateTime(s.entry_time) : '—'}</td>
                        <td className="text-muted">{s.exit_time ? fmtDateTime(s.exit_time) : '—'}</td>
                        <td><span className={cls("status-badge", `status-${s.status || 'unknown'}`)}>{s.status || '—'}</span></td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
              {simulations.length === 0 && <div className="empty-state">Ghost simulator henüz aktif değil</div>}
            </div>
          </div>

          {/* ── Market Movements ── */}
          <div className="card">
            <div className="card-header"><h3>Tespit Edilen Piyasa Hareketleri</h3><span className="badge badge-amber">{movements.length}</span></div>
            <div className="table-wrap">
              <table className="tbl">
                <thead><tr><th>Coin</th><th>Borsa</th><th>Tip</th><th>Değişim</th><th>Yön</th><th>Hacim</th><th>Başlangıç</th><th>Bitiş</th></tr></thead>
                <tbody>
                  {movements.slice(0, 12).map((m, i) => (
                    <tr key={i}>
                      <td><strong>{(m.symbol || '').replace('USDT', '')}</strong><span className="text-muted">/USDT</span></td>
                      <td>{m.exchange || '—'}</td>
                      <td><span className="badge badge-sm">{m.market_type || '—'}</span></td>
                      <td className={safeNum(m.change_pct) >= 0 ? "text-green" : "text-red"} style={{ fontWeight: 700 }}>{fmtPct(safeNum(m.change_pct) * 100)}</td>
                      <td>{m.direction || '—'}</td>
                      <td className="text-mono">{fmt(safeNum(m.volume), 2)}</td>
                      <td className="text-muted">{m.start_time ? fmtTimeShort(m.start_time) : '—'}</td>
                      <td className="text-muted">{m.end_time ? fmtTimeShort(m.end_time) : '—'}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
              {movements.length === 0 && <div className="empty-state">Henüz %2'den büyük hareket tespit edilmedi</div>}
            </div>
          </div>

          {/* ── Signal Type Distribution ── */}
          {signalSummary.length > 0 && (
            <div className="card">
              <div className="card-header"><h3>Sinyal Tipi Dağılımı</h3><span className="badge badge-purple">{signalSummary.reduce((a, r) => a + safeNum(r.total || r.count), 0)} toplam</span></div>
              <div className="db-stats-grid">
                {signalSummary.map((s, i) => (
                  <div key={i} className="db-stat-card">
                    <div className="db-stat-name">{sigLabel(s.signal_type)}</div>
                    <div className="db-stat-value">{fmt(safeNum(s.total || s.count), 0)}</div>
                    <div className="text-muted text-xs">
                      {safeNum(s.pending) > 0 && <span className="text-amber">{s.pending} bekleyen · </span>}
                      {safeNum(s.processed) > 0 && <span className="text-green">{s.processed} işlendi · </span>}
                      güven: {s.avg_confidence != null ? `${(safeNum(s.avg_confidence) * 100).toFixed(0)}%` : "—"}
                    </div>
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* ── Historical Signatures ── */}
          {signatures.length > 0 && (
            <div className="card">
              <div className="card-header"><h3>Tarihsel İmzalar (Cosine Benzerlik)</h3><span className="badge badge-cyan">{signatures.length}</span></div>
              <div className="table-wrap"><table className="tbl"><thead><tr><th>Coin</th><th>Piyasa</th><th>Zaman Dilimi</th><th>Yön</th><th>Değişim %</th><th>Kayıt</th></tr></thead><tbody>
                {signatures.map((s, i) => (
                  <tr key={i}>
                    <td><strong>{(s.symbol || '').replace('USDT', '')}</strong><span className="text-muted">/USDT</span></td>
                    <td><span className="badge badge-sm">{s.market_type || '—'}</span></td>
                    <td>{s.timeframe || '—'}</td>
                    <td><span className={cls("dir-badge dir-sm", s.direction === "up" ? "dir-long" : "dir-short")}>{s.direction === "up" ? "↑ YUKARIŞ" : "↓ DÜŞÜŞ"}</span></td>
                    <td className={safeNum(s.change_pct) >= 0 ? "text-green" : "text-red"} style={{ fontWeight: 700 }}>{fmtPct(safeNum(s.change_pct) * 100)}</td>
                    <td className="text-muted">{s.created_at ? fmtDateTime(s.created_at) : "—"}</td>
                  </tr>
                ))}
              </tbody></table></div>
            </div>
          )}

          {/* ── RCA Results ── */}
          {rcaResults.length > 0 && (
            <div className="card">
              <div className="card-header"><h3>Kök Neden Analizi (RCA)</h3><span className="badge badge-red">{rcaResults.length}</span></div>
              <div className="table-wrap"><table className="tbl"><thead><tr><th>Coin</th><th>Başarısızlık</th><th>Güven</th><th>Tahmin Vol.</th><th>Gerçek Vol.</th><th>Öneri</th><th>Zaman</th></tr></thead><tbody>
                {rcaResults.slice(0, 15).map((r, i) => (
                  <tr key={i}>
                    <td><strong>{(r.symbol || '—').replace('USDT', '')}</strong></td>
                    <td><span className="badge badge-sm badge-red">{r.failure_type || "—"}</span></td>
                    <td>{r.confidence != null ? `${(safeNum(r.confidence) * 100).toFixed(0)}%` : "—"}</td>
                    <td className="text-mono">{r.predicted_volatility != null ? safeNum(r.predicted_volatility).toFixed(4) : "—"}</td>
                    <td className="text-mono">{r.actual_volatility != null ? safeNum(r.actual_volatility).toFixed(4) : "—"}</td>
                    <td className="text-xs" style={{ maxWidth: 250, overflow: "hidden", textOverflow: "ellipsis" }}>{r.recommendation || "—"}</td>
                    <td className="text-muted">{r.created_at ? fmtDateTime(r.created_at) : "—"}</td>
                  </tr>
                ))}
              </tbody></table></div>
            </div>
          )}

          {/* ── RCA Stats ── */}
          {rcaStats.length > 0 && (
            <div className="card">
              <div className="card-header"><h3>RCA Başarısızlık Dağılımı</h3><span className="badge badge-amber">İstatistik</span></div>
              <div className="db-stats-grid">
                {rcaStats.map((s, i) => (
                  <div key={i} className="db-stat-card">
                    <div className="db-stat-name">{s.failure_type || '—'}</div>
                    <div className="db-stat-value">{fmt(safeNum(s.count), 0)}</div>
                    <div className="text-muted text-xs">ort. güven: {s.avg_confidence != null ? `${(safeNum(s.avg_confidence) * 100).toFixed(0)}%` : "—"}</div>
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* ── Correction Notes ── */}
          {corrections.length > 0 && (
            <div className="card">
              <div className="card-header"><h3>Oto-Düzeltme Notları</h3><span className="badge badge-green">{corrections.filter(c => c.applied).length} uygulandı</span></div>
              <div className="table-wrap"><table className="tbl"><thead><tr><th>Sinyal Tipi</th><th>Başarısızlık</th><th>Ayar</th><th>Değer</th><th>Durum</th><th>Sebep</th><th>Zaman</th></tr></thead><tbody>
                {corrections.slice(0, 20).map((c, i) => (
                  <tr key={i}>
                    <td>{c.signal_type || "—"}</td>
                    <td><span className="badge badge-sm badge-red">{c.failure_type || "—"}</span></td>
                    <td className="text-mono">{c.adjustment_key || "—"}</td>
                    <td className="text-mono">{c.adjustment_value || "—"}</td>
                    <td>{c.applied ? <span className="badge badge-sm badge-green">Uygulandı</span> : <span className="badge badge-sm badge-amber">Bekliyor</span>}</td>
                    <td className="text-xs" style={{ maxWidth: 200, overflow: "hidden", textOverflow: "ellipsis" }}>{c.reason || "—"}</td>
                    <td className="text-muted">{c.created_at ? fmtDateTime(c.created_at) : "—"}</td>
                  </tr>
                ))}
              </tbody></table></div>
            </div>
          )}
        </>)}

        {/* ═══ BRAIN ═══ */}
        {tab === "brain" && (<>
          <div className="tab-header"><h2>AI Beyin Merkezi</h2><span className="badge badge-purple">Öğrenme Sistemi</span></div>

          {/* Agent Status Overview */}
          <div className="card" style={{ marginBottom: 16 }}>
            <div className="card-header"><h3>Agent Koordinasyonu</h3><span className="badge badge-live">Canlı</span></div>
            <div className="agent-grid" style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(200px, 1fr))", gap: 12, padding: 16 }}>
              {[
                { name: "Scout", key: "scout", icon: "🔍", desc: "Veri toplama" },
                { name: "Strategist", key: "strategist", icon: "📡", desc: "Sinyal üretimi" },
                { name: "Ghost Sim.", key: "ghost_simulator", icon: "👻", desc: "Simülasyon" },
                { name: "Auditor", key: "auditor", icon: "📋", desc: "Denetim" },
                { name: "Brain", key: "brain", icon: "🧠", desc: "AI öğrenme" },
              ].map(a => {
                const agentData = agentStatuses[a.key];
                const isOk = agentData?.status === "running";
                const isStale = agentData?.status === "stale";
                return (
                  <div key={a.key} style={{ display: "flex", alignItems: "center", gap: 10, padding: "10px 12px", borderRadius: 8, background: isOk ? "rgba(34,197,94,0.08)" : isStale ? "rgba(245,158,11,0.08)" : "rgba(148,163,184,0.05)" }}>
                    <span style={{ fontSize: 22 }}>{a.icon}</span>
                    <div>
                      <div style={{ fontWeight: 600, fontSize: 13 }}>{a.name}</div>
                      <div style={{ fontSize: 11, color: isOk ? "var(--green)" : isStale ? "var(--amber)" : "var(--muted)", marginTop: 2 }}>
                        {isOk ? "✓ Aktif" : isStale ? "⚠ Yanıtlamıyor" : "— Bekleniyor"}
                        {agentData?.age_seconds != null && ` · ${Math.round(agentData.age_seconds)}sn`}
                      </div>
                    </div>
                  </div>
                );
              })}
            </div>
          </div>

          <div className="kpi-strip">
            <KPI label="Öğrenilen Pattern" value={brainStatus ? fmt(brainStatus.pattern_count, 0) : "0"} icon="🧬" accent="purple" />
            <KPI label="Tahmin Doğruluğu" value={brainStatus ? `${safeNum(brainStatus.learning.accuracy).toFixed(1)}%` : "0%"} icon="🎯" accent={brainStatus && safeNum(brainStatus.learning.accuracy) >= 50 ? "green" : "amber"} />
            <KPI label="Toplam Tahmin" value={brainStatus ? fmt(brainStatus.learning.total, 0) : "0"} icon="📊" />
            <KPI label="Doğru Tahmin" value={brainStatus ? fmt(brainStatus.learning.correct, 0) : "0"} icon="✅" accent="green" />
            <KPI label="Ort. PnL" value={brainStatus ? `${safeNum(brainStatus.learning.avg_pnl).toFixed(2)}%` : "0%"} icon="💰" accent={brainStatus && safeNum(brainStatus.learning.avg_pnl) > 0 ? "green" : "red"} />
          </div>

          <div className="grid-2">
            <div className="card card-center">
              <h3>Öğrenme Doğruluğu</h3>
              {brainStatus && safeNum(brainStatus.learning.total) > 0 ? (
                <>
                  <ProgressRing value={safeNum(brainStatus.learning.accuracy)} size={140} stroke={12} color={safeNum(brainStatus.learning.accuracy) >= 50 ? "var(--green)" : "var(--amber)"} />
                  <div className="text-muted" style={{ marginTop: 16 }}>{brainStatus.learning.correct} doğru / {brainStatus.learning.total} toplam</div>
                </>
              ) : (
                <div className="empty-state" style={{ padding: "30px 16px" }}>
                  <div style={{ fontSize: 48, marginBottom: 12 }}>🧠</div>
                  <p><strong>Öğrenme Başlamadı</strong></p>
                  <p className="text-xs text-muted" style={{ marginTop: 8 }}>Brain modülü sinyal → simülasyon → kapanış döngüsünden sonra öğrenmeye başlar. Pattern sayısı: <strong>{brainStatus?.pattern_count ?? 0}</strong></p>
                </div>
              )}
            </div>
            <div className="card">
              <div className="card-header"><h3>Sinyal Tipi Başarıları</h3></div>
              {brainStatus && brainStatus.signal_type_stats.length > 0 ? (
                <div className="table-wrap"><table className="tbl"><thead><tr><th>Sinyal Tipi</th><th>Toplam</th><th>Doğru</th><th>Başarı</th><th>Ort PnL</th></tr></thead><tbody>
                  {brainStatus.signal_type_stats.map((s, i) => (
                    <tr key={i}><td>{s.signal_type}</td><td>{s.total}</td><td>{s.correct}</td>
                    <td className={safeNum(s.total) > 0 && safeNum(s.correct) / safeNum(s.total) >= 0.5 ? "text-green" : "text-red"}>{safeNum(s.total) > 0 ? `${(safeNum(s.correct) / safeNum(s.total) * 100).toFixed(0)}%` : "—"}</td>
                    <td className={safeNum(s.avg_pnl) >= 0 ? "text-green" : "text-red"}>{safeNum(s.avg_pnl).toFixed(2)}%</td></tr>
                  ))}
                </tbody></table></div>
              ) : <div className="empty-state">
                <p>📊 Sinyal istatistikleri henüz oluşmadı.</p>
                <p className="text-xs text-muted" style={{ marginTop: 8 }}>Veriler, Ghost Simulator simülasyonları kapandıktan sonra toplanır. Sistem ilk sinyalleri üretip simüle ettikçe burada sonuçlar görünecek.</p>
              </div>}
            </div>
          </div>

          {/* Günlük Öğrenme Doğruluğu */}
          {learningStats?.daily_accuracy && learningStats.daily_accuracy.length > 0 && (
            <div className="card">
              <div className="card-header"><h3>Günlük Öğrenme Trendi</h3><span className="badge badge-cyan">Son 14 Gün</span></div>
              <div className="table-wrap"><table className="tbl"><thead><tr><th>Tarih</th><th>Toplam</th><th>Doğru</th><th>Doğruluk</th></tr></thead><tbody>
                {learningStats.daily_accuracy.map((d: any, i: number) => {
                  const acc = safeNum(d.total) > 0 ? (safeNum(d.correct) / safeNum(d.total) * 100) : 0;
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
                    <td className={safeNum(p.outcome_15m) > 0 ? "text-green" : safeNum(p.outcome_15m) < 0 ? "text-red" : "text-muted"}>{p.outcome_15m != null ? `${(safeNum(p.outcome_15m) * 100).toFixed(2)}%` : "⏳"}</td>
                    <td className={safeNum(p.outcome_1h) > 0 ? "text-green" : safeNum(p.outcome_1h) < 0 ? "text-red" : "text-muted"}>{p.outcome_1h != null ? `${(safeNum(p.outcome_1h) * 100).toFixed(2)}%` : "⏳"}</td>
                    <td className={safeNum(p.outcome_4h) > 0 ? "text-green" : safeNum(p.outcome_4h) < 0 ? "text-red" : "text-muted"}>{p.outcome_4h != null ? `${(safeNum(p.outcome_4h) * 100).toFixed(2)}%` : "⏳"}</td>
                    <td className={safeNum(p.outcome_1d) > 0 ? "text-green" : safeNum(p.outcome_1d) < 0 ? "text-red" : "text-muted"}>{p.outcome_1d != null ? `${(safeNum(p.outcome_1d) * 100).toFixed(2)}%` : "⏳"}</td>
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

        {/* ═══ MASTER CONTROL ═══ */}
        {tab === "mastercontrol" && (<>
          <div className="tab-header"><h2>Master Kontrol Merkezi</h2><span className="badge badge-purple">AI Beyin Kontrolü</span></div>

          {/* LLM Status */}
          <div className="kpi-strip">
            <KPI label="LLM Durumu" value={llmStatus?.healthy ? "Aktif" : "Devre Dışı"} icon="🧠" accent={llmStatus?.healthy ? "green" : "red"} />
            <KPI label="Toplam Çağrı" value={llmStatus?.call_count != null ? fmt(llmStatus.call_count, 0) : "—"} icon="📡" accent="blue" />
            <KPI label="Ort. Gecikme" value={llmStatus?.llm_stats?.avg_latency_ms != null ? `${fmt(llmStatus.llm_stats.avg_latency_ms, 0)}ms` : "—"} icon="⏱" />
            <KPI label="Kuyruk" value={queueStatus?.queue_size != null ? fmt(queueStatus.queue_size, 0) : "—"} sub={queueStatus ? `${queueStatus.total_completed ?? 0} tamamlandı` : ""} icon="📋" accent="amber" />
          </div>

          {/* Permanent Directives */}
          <div className="card">
            <div className="card-header"><h3>Kalıcı Direktifler (Master System Prompt)</h3><span className="badge badge-amber">TÜM AJANLARA</span></div>
            <p style={{color:"var(--text-secondary)",fontSize:"0.85rem",margin:"0 0 12px"}}>
              Buraya yazdığınız direktifler her LLM çağrısına master system prompt olarak eklenir.
              Örnek: "Risk toleransını artır", "Momentum sinyallerine öncelik ver", "BTC dışındaki coinlerde temkinli ol"
            </p>
            <textarea
              className="directive-textarea"
              value={masterDirective}
              onChange={(e) => setMasterDirective(e.target.value)}
              placeholder="Kalıcı direktiflerinizi buraya yazın... Bu direktifler tüm ajan çağrılarına sistem promptu olarak eklenir."
              rows={6}
              style={{width:"100%",background:"var(--glass)",border:"1px solid var(--border)",borderRadius:8,padding:12,color:"var(--text-primary)",fontFamily:"inherit",fontSize:"0.9rem",resize:"vertical"}}
            />
            <div style={{display:"flex",gap:8,marginTop:8,alignItems:"center"}}>
              <button
                className="btn btn-primary"
                disabled={directiveSaving}
                onClick={async () => {
                  setDirectiveSaving(true); setDirectiveStatus(null);
                  try {
                    const res = await fetch("/api/directives", {
                      method: "POST", headers: {"Content-Type":"application/json"},
                      body: JSON.stringify({ master_directive: masterDirective })
                    });
                    if (res.ok) { setDirectiveStatus("✅ Kaydedildi"); }
                    else { setDirectiveStatus("❌ Hata oluştu"); }
                  } catch { setDirectiveStatus("❌ Bağlantı hatası (port 3002)"); }
                  finally { setDirectiveSaving(false); setTimeout(() => setDirectiveStatus(null), 3000); }
                }}
              >
                {directiveSaving ? "Kaydediliyor..." : "💾 Direktifi Kaydet"}
              </button>
              <button
                className="btn btn-danger"
                onClick={async () => {
                  if (!confirm("Tüm direktifler silinecek. Emin misiniz?")) return;
                  try {
                    await fetch("/api/directives", { method: "DELETE" });
                    setMasterDirective(""); setDirectiveStatus("🗑 Temizlendi");
                    setTimeout(() => setDirectiveStatus(null), 3000);
                  } catch { setDirectiveStatus("❌ Hata"); }
                }}
              >
                🗑 Temizle
              </button>
              {directiveStatus && <span style={{fontSize:"0.85rem",color:"var(--text-secondary)"}}>{directiveStatus}</span>}
            </div>
          </div>

          {/* Task Queue Status */}
          {queueStatus && (
            <div className="card">
              <div className="card-header"><h3>Görev Kuyruğu</h3><span className="badge badge-blue">Async İşleme</span></div>
              <div className="kpi-strip">
                <KPI label="Bekleyen" value={fmt(queueStatus.queue_size ?? 0, 0)} icon="⏳" />
                <KPI label="Aktif" value={fmt(queueStatus.active_tasks ?? 0, 0)} icon="🔄" accent="blue" />
                <KPI label="Tamamlanan" value={fmt(queueStatus.total_completed ?? 0, 0)} icon="✅" accent="green" />
                <KPI label="Başarısız" value={fmt(queueStatus.total_failed ?? 0, 0)} icon="❌" accent="red" />
                <KPI label="Düşen" value={fmt(queueStatus.total_dropped ?? 0, 0)} icon="🚫" />
                <KPI label="Ort. Süre" value={queueStatus.avg_duration_ms != null ? `${fmt(queueStatus.avg_duration_ms, 0)}ms` : "—"} icon="⏱" />
              </div>
              {queueStatus.recent_tasks && queueStatus.recent_tasks.length > 0 && (
                <div className="table-wrap" style={{marginTop:12}}>
                  <table className="tbl"><thead><tr><th>Ajan</th><th>Görev</th><th>Durum</th><th>Süre</th></tr></thead><tbody>
                    {queueStatus.recent_tasks.slice(-10).reverse().map((t: RecordRow, i: number) => (
                      <tr key={i}>
                        <td>{t.agent}</td>
                        <td style={{maxWidth:200,overflow:"hidden",textOverflow:"ellipsis"}}>{t.description}</td>
                        <td><span className={cls("badge", t.status === "completed" ? "badge-green" : t.status === "failed" ? "badge-red" : "badge-amber")}>{t.status}</span></td>
                        <td>{t.duration_ms ? `${fmt(t.duration_ms, 0)}ms` : "—"}</td>
                      </tr>
                    ))}
                  </tbody></table>
                </div>
              )}
            </div>
          )}

          {/* LLM Model Info */}
          {llmStatus && (
            <div className="card">
              <div className="card-header"><h3>LLM Model Bilgisi</h3><span className={cls("badge", llmStatus.healthy ? "badge-green" : "badge-red")}>{llmStatus.healthy ? "Bağlı" : "Bağlantı Yok"}</span></div>
              <div className="db-stats-grid">
                <div className="db-stat-card"><div className="db-stat-name">Model</div><div className="db-stat-value">{llmStatus.llm_stats?.model ?? "—"}</div></div>
                <div className="db-stat-card"><div className="db-stat-name">Endpoint</div><div className="db-stat-value">{llmStatus.llm_stats?.base_url ?? "—"}</div></div>
                <div className="db-stat-card"><div className="db-stat-name">Toplam Çağrı</div><div className="db-stat-value">{fmt(llmStatus.llm_stats?.total_calls ?? 0, 0)}</div></div>
                <div className="db-stat-card"><div className="db-stat-name">Hatalar</div><div className="db-stat-value">{fmt(llmStatus.llm_stats?.total_errors ?? 0, 0)}</div></div>
              </div>
            </div>
          )}
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
                          {agent.age_seconds != null && (() => { const a = safeNum(agent.age_seconds); return ` · ${a < 60 ? `${Math.round(a)}sn` : `${Math.floor(a / 60)}dk`} önce`; })()}
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
root.render(<React.StrictMode><ErrorBoundary><App /></ErrorBoundary></React.StrictMode>);