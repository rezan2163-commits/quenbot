import React, { useEffect, useMemo, useState, useCallback, useRef } from "react";
import ReactDOM from "react-dom/client";
import "./index.css";

/* ─── Types ─── */
type R = Record<string, any>;
type Summary = { total_trades: number; total_movements: number; active_signals: number; open_simulations: number; total_pnl: number; win_rate: number; closed_simulations: number; winning_simulations: number; losing_simulations: number };
type LivePrice = { symbol: string; exchange: string; price: number; timestamp: string };
type BotSummary = { total_simulations: number; open_simulations: number; closed_simulations: number; wins: number; losses: number; win_rate: number; average_pnl: number; average_pnl_pct: number };
type TopMover = { symbol: string; open_price: number; current_price: number; change_pct: number; timestamp: string };
type OrderFlow = { symbol: string; buy_volume: number; sell_volume: number; buy_count: number; sell_count: number };
type TimelineEntry = { minute: string; count: number; volume: number };
type VolumeEntry = { exchange: string; market_type: string; trade_count: number; total_volume: number };
type SystemStats = { db_size_mb: number; trades_per_minute: number; total_trades: number; oldest_trade: string; newest_trade: string; uptime_minutes: number };
type Candle = { minute: string; open: number; high: number; low: number; close: number; volume: number };
type LiveStream = { latest_trades: R[]; exchange_freshness: R[]; five_min_breakdown: R[] };
type BrainStatus = { pattern_count: number; learning: { total: number; correct: number; accuracy: number; avg_pnl: number }; recent_patterns: R[]; signal_type_stats: R[] };
type Tab = "overview" | "terminal" | "signals" | "simulations" | "brain" | "mastercontrol" | "system";

/* ─── Helpers ─── */
const fmt = (v: any, d = 2) => { const n = Number(v); return isNaN(n) ? "0" : new Intl.NumberFormat("en-US", { maximumFractionDigits: d, minimumFractionDigits: d }).format(n); };
const smartDecimals = (v: any): number => { const n = Math.abs(Number(v)); if (isNaN(n) || n === 0) return 2; if (n >= 100) return 2; if (n >= 1) return 4; if (n >= 0.01) return 6; return 8; };
const fmtUsd = (v: any) => { const n = Number(v); return isNaN(n) ? '$0.00' : `$${fmt(v, smartDecimals(v))}`; };
const fmtPct = (v: any) => { const n = Number(v); return `${isNaN(n) ? 0 : n >= 0 ? "+" : ""}${(isNaN(n) ? 0 : n).toFixed(2)}%`; };
const fmtTime = (s: string) => { try { return new Date(s).toLocaleTimeString("tr-TR", { hour: "2-digit", minute: "2-digit", second: "2-digit" }); } catch { return "—"; } };
const fmtDT = (s: string) => { try { const d = new Date(s); return `${d.toLocaleDateString("tr-TR", { day: "2-digit", month: "2-digit", year: "numeric" })} ${d.toLocaleTimeString("tr-TR", { hour: "2-digit", minute: "2-digit", second: "2-digit" })}`; } catch { return "—"; } };
const safeConf = (v: any): number => { const n = Number(v); if (isNaN(n)) return 0; return n > 1 ? Math.min(n, 100) : Math.min(n * 100, 100); };
const cls = (...c: (string | false | undefined | null)[]) => c.filter(Boolean).join(" ");
const sn = (v: any, d = 0): number => { const n = Number(v); return isNaN(n) ? d : n; };
const ago = (m: number) => { if (m < 60) return `${m}dk`; if (m < 1440) return `${Math.floor(m / 60)}sa ${m % 60}dk`; return `${Math.floor(m / 1440)}g`; };
const safeMeta = (m: any): R => { if (!m) return {}; if (typeof m === 'string') { try { const p = JSON.parse(m); return p && typeof p === 'object' ? p : {}; } catch { return {}; } } return typeof m === 'object' ? m : {}; };
const getDir = (s: R): 'long' | 'short' | null => { const m = safeMeta(s.metadata); if (m.position_bias === 'long' || m.position_bias === 'short') return m.position_bias; const t = s.signal_type || ''; if (t.includes('long')) return 'long'; if (t.includes('short')) return 'short'; return null; };
const sigLabel = (t: string): string => { if (!t) return '—'; const m: R = { evolutionary_similarity: 'Evrimsel', momentum: 'Momentum', brain_pattern: 'Brain', price_action: 'Price Action', signature: 'İmza', historical_signature: 'Tarihsel İmza' }; for (const [k, v] of Object.entries(m)) { if (t.includes(k)) return v as string; } return t.replace(/_/g, ' '); };
const stInfo = (st: string) => { if (st === 'pending') return { l: 'Bekliyor', c: 'badge-w' }; if (st === 'processed') return { l: 'İşlendi', c: 'badge-g' }; if (st?.startsWith('risk_')) return { l: 'Risk Red', c: 'badge-r' }; if (st?.startsWith('filtered')) return { l: 'Filtrelendi', c: 'badge-w' }; return { l: st || '—', c: '' }; };

const apiFetch = async (url: string) => { try { const r = await fetch(url); return r.ok ? r.json() : null; } catch { return null; } };

/* ─── Skeleton Loader ─── */
function Skeleton({ w = '100%', h = 20 }: { w?: string | number; h?: number }) {
  return <div className="skeleton" style={{ width: w, height: h }} />;
}
function KPISkeleton() { return <div className="kpi"><Skeleton w={60} h={28} /><Skeleton w={80} h={14} /></div>; }

/* ─── Sparkline ─── */
function Spark({ data, color = "var(--c)", h = 32, w = 120 }: { data: number[]; color?: string; h?: number; w?: number }) {
  if (data.length < 2) return null;
  const min = Math.min(...data), max = Math.max(...data), range = max - min || 1;
  const pts = data.map((v, i) => `${(i / (data.length - 1)) * w},${h - ((v - min) / range) * h}`).join(' ');
  return <svg width={w} height={h} className="spark"><polyline points={pts} fill="none" stroke={color} strokeWidth="1.5" /></svg>;
}

/* ─── MiniBar ─── */
function MiniBar({ data, h = 48, color = "var(--c)" }: { data: number[]; h?: number; color?: string }) {
  const max = Math.max(...data, 1);
  return <div className="mini-bars" style={{ height: h }}>{data.map((v, i) => <div key={i} className="mbar" style={{ height: `${(v / max) * 100}%`, background: color }} />)}</div>;
}

/* ─── ProgressRing ─── */
function Ring({ value, size = 80, stroke = 6, color = "var(--c)" }: { value: number; size?: number; stroke?: number; color?: string }) {
  const r = (size - stroke) / 2, circ = 2 * Math.PI * r, off = circ - (Math.min(value, 100) / 100) * circ;
  return (
    <svg width={size} height={size}><circle cx={size / 2} cy={size / 2} r={r} fill="none" stroke="rgba(148,163,184,0.08)" strokeWidth={stroke} />
      <circle cx={size / 2} cy={size / 2} r={r} fill="none" stroke={color} strokeWidth={stroke} strokeDasharray={circ} strokeDashoffset={off} strokeLinecap="round" transform={`rotate(-90 ${size / 2} ${size / 2})`} />
      <text x="50%" y="50%" dominantBaseline="central" textAnchor="middle" fill="currentColor" fontSize="13" fontWeight="700">{value.toFixed(0)}%</text></svg>
  );
}

/* ─── CandleChart ─── */
function CandleChart({ candles, height = 200 }: { candles: Candle[]; height?: number }) {
  if (candles.length < 2) return <div className="empty">Grafik verisi bekleniyor...</div>;
  const all = candles.flatMap(c => [c.high, c.low]);
  const min = Math.min(...all), max = Math.max(...all), range = max - min || 1;
  const w = 900, cw = Math.max(3, w / candles.length - 2);
  const y = (p: number) => height - 10 - ((p - min) / range) * (height - 20);
  return (
    <div className="chart-wrap">
      <svg width="100%" viewBox={`0 0 ${w} ${height}`} preserveAspectRatio="none">
        {candles.map((c, i) => {
          const x = (i / candles.length) * w + cw / 2, bull = c.close >= c.open, col = bull ? "#0ecb81" : "#f6465d";
          const bT = y(Math.max(c.open, c.close)), bB = y(Math.min(c.open, c.close));
          return <g key={i}><line x1={x} x2={x} y1={y(c.high)} y2={y(c.low)} stroke={col} strokeWidth={1} /><rect x={x - cw / 2} y={bT} width={cw} height={Math.max(1, bB - bT)} fill={col} /></g>;
        })}
      </svg>
      <div className="chart-axis"><span>{fmtUsd(min)}</span><span>{fmtUsd((min + max) / 2)}</span><span>{fmtUsd(max)}</span></div>
    </div>
  );
}

/* ─── FlowBar ─── */
function FlowBar({ buy, sell }: { buy: number; sell: number }) {
  const t = sn(buy) + sn(sell) || 1, p = (sn(buy) / t) * 100;
  return (
    <div className="flow-wrap">
      <div className="flow-bar"><div className="flow-fill" style={{ width: `${p}%` }} /></div>
      <div className="flow-labels"><span className="t-g">{p.toFixed(0)}% Alış</span><span className="t-r">{(100 - p).toFixed(0)}% Satış</span></div>
    </div>
  );
}

/* ─── Error Boundary ─── */
class ErrorBoundary extends React.Component<{ children: React.ReactNode }, { err: boolean; msg: string }> {
  constructor(p: any) { super(p); this.state = { err: false, msg: '' }; }
  static getDerivedStateFromError(e: Error) { return { err: true, msg: e.message }; }
  render() {
    if (this.state.err) return <div className="err-screen"><h2>⚠ Hata</h2><p>{this.state.msg}</p><button onClick={() => this.setState({ err: false, msg: '' })}>Yeniden Dene</button></div>;
    return this.props.children;
  }
}

/* ═══════════════════════════════════════════════════════════ */
/*                          MAIN APP                          */
/* ═══════════════════════════════════════════════════════════ */
function App() {
  const [tab, setTab] = useState<Tab>("overview");
  const [summary, setSummary] = useState<Summary | null>(null);
  const [prices, setPrices] = useState<LivePrice[]>([]);
  const [botSum, setBotSum] = useState<BotSummary | null>(null);
  const [movers, setMovers] = useState<TopMover[]>([]);
  const [orderFlow, setOrderFlow] = useState<OrderFlow[]>([]);
  const [timeline, setTimeline] = useState<TimelineEntry[]>([]);
  const [volumes, setVolumes] = useState<VolumeEntry[]>([]);
  const [sysStats, setSysStats] = useState<SystemStats | null>(null);
  const [signals, setSignals] = useState<R[]>([]);
  const [sims, setSims] = useState<R[]>([]);
  const [trades, setTrades] = useState<R[]>([]);
  const [movements, setMovements] = useState<R[]>([]);
  const [selSym, setSelSym] = useState("BTCUSDT");
  const [candles, setCandles] = useState<Candle[]>([]);
  const [lastUp, setLastUp] = useState(new Date());
  const [brainSt, setBrainSt] = useState<BrainStatus | null>(null);
  const [liveStream, setLiveStream] = useState<LiveStream | null>(null);
  const [agentSt, setAgentSt] = useState<R>({});
  const [rcaResults, setRcaResults] = useState<R[]>([]);
  const [rcaStats, setRcaStats] = useState<R[]>([]);
  const [corrections, setCorrections] = useState<R[]>([]);
  const [signatures, setSignatures] = useState<R[]>([]);
  const [sigSummary, setSigSummary] = useState<R[]>([]);
  const [brainPat, setBrainPat] = useState<R[]>([]);
  const [learnSt, setLearnSt] = useState<R | null>(null);
  const [tableStats, setTableStats] = useState<R[]>([]);
  const [auditRec, setAuditRec] = useState<R[]>([]);
  const [failAn, setFailAn] = useState<R[]>([]);
  const [pnlTL, setPnlTL] = useState<R[]>([]);
  const [watchlist, setWatchlist] = useState<R[]>([]);
  const [wlSym, setWlSym] = useState(""); const [wlEx, setWlEx] = useState("all"); const [wlMT, setWlMT] = useState("spot");
  // Master Control
  const [masterDir, setMasterDir] = useState(""); const [dirSaving, setDirSaving] = useState(false); const [dirStatus, setDirStatus] = useState<string | null>(null);
  const [llmSt, setLlmSt] = useState<R | null>(null); const [queueSt, setQueueSt] = useState<R | null>(null);
  const [auditValidation, setAuditValidation] = useState<R | null>(null);
  // Signal filters
  const [sigFilter, setSigFilter] = useState<'all' | 'pending' | 'processed' | 'rejected'>('all');
  const [sigDir, setSigDir] = useState<'all' | 'long' | 'short'>('all');
  const [sigMkt, setSigMkt] = useState<'all' | 'spot' | 'futures'>('all');
  // Position calculator
  const [calcEntry, setCalcEntry] = useState(""); const [calcSL, setCalcSL] = useState(""); const [calcRisk, setCalcRisk] = useState("100");
  const [initialLoading, setInitialLoading] = useState(true);

  const prevPrices = useRef<Record<string, number>>({});

  /* ── Tiered Fetching ── */
  const fetchFast = useCallback(async () => {
    const [s, p, tm] = await Promise.all([
      apiFetch("/api/dashboard/summary"),
      apiFetch("/api/live/prices"),
      apiFetch("/api/analytics/top-movers"),
    ]);
    if (s) setSummary(s);
    if (p) { prevPrices.current = Object.fromEntries(prices.map(x => [x.symbol, x.price])); setPrices(p); }
    if (tm) setMovers(tm);
    setLastUp(new Date());
  }, [prices]);

  const fetchMed = useCallback(async () => {
    const [b, of, tl, vd, sig, sim, tr, mv, ch, ls, agS, wl, pt, sigSum] = await Promise.all([
      apiFetch("/api/bot/summary"),
      apiFetch("/api/analytics/order-flow"),
      apiFetch("/api/analytics/trade-timeline"),
      apiFetch("/api/analytics/volume-by-exchange"),
      apiFetch("/api/signals"),
      apiFetch("/api/simulations"),
      apiFetch("/api/scout/trades?limit=30"),
      apiFetch("/api/scout/movements?limit=20"),
      apiFetch(`/api/analytics/price-history/${selSym}`),
      apiFetch("/api/live/data-stream"),
      apiFetch("/api/agents/status"),
      apiFetch("/api/watchlist"),
      apiFetch("/api/analytics/pnl-timeline"),
      apiFetch("/api/signals/summary"),
    ]);
    if (b) setBotSum(b);
    if (of) setOrderFlow(of);
    if (tl) setTimeline(tl);
    if (vd) setVolumes(vd);
    if (sig) setSignals(sig);
    if (sim) setSims(sim);
    if (tr) setTrades(tr);
    if (mv) setMovements(mv);
    if (ch) setCandles(ch);
    if (ls) setLiveStream(ls);
    if (agS?.agents) setAgentSt(agS.agents);
    if (wl) setWatchlist(wl);
    if (pt) setPnlTL(pt);
    if (sigSum) setSigSummary(Array.isArray(sigSum) ? sigSum : (sigSum?.by_type ?? []));
  }, [selSym]);

  const fetchSlow = useCallback(async () => {
    const [ss, bs, bp, lst, ts, ar, fa, rr, rs, cor, sig2] = await Promise.all([
      apiFetch("/api/analytics/system-stats"),
      apiFetch("/api/brain/status"),
      apiFetch("/api/brain/patterns?limit=30"),
      apiFetch("/api/brain/learning-stats"),
      apiFetch("/api/admin/table-stats"),
      apiFetch("/api/admin/audit-records?limit=20"),
      apiFetch("/api/admin/failure-analysis?limit=20"),
      apiFetch("/api/rca/results"),
      apiFetch("/api/rca/stats"),
      apiFetch("/api/corrections"),
      apiFetch("/api/signatures?limit=20"),
    ]);
    if (ss) setSysStats(ss);
    if (bs) setBrainSt(bs);
    if (bp) setBrainPat(bp);
    if (lst) setLearnSt(lst);
    if (ts) setTableStats(ts);
    if (ar) setAuditRec(ar);
    if (fa) setFailAn(fa);
    if (rr) setRcaResults(Array.isArray(rr) ? rr : []);
    if (rs) setRcaStats(Array.isArray(rs) ? rs : (rs?.distribution ?? []));
    if (cor) setCorrections(Array.isArray(cor) ? cor : []);
    if (sig2) setSignatures(Array.isArray(sig2) ? sig2 : []);
    try {
      const [dr, lr, qr, av] = await Promise.all([
        apiFetch("/api/directives"), apiFetch("/api/llm/status"), apiFetch("/api/llm/queue"), apiFetch("/api/audit/validate"),
      ]);
      if (dr?.master_directive !== undefined && !dirSaving) setMasterDir(dr.master_directive);
      if (lr) setLlmSt(lr);
      if (qr) setQueueSt(qr);
      if (av) setAuditValidation(av);
    } catch {}
  }, [dirSaving]);

  useEffect(() => {
    // Stagger initial loads: fast first, then medium, then slow
    fetchFast().then(() => setInitialLoading(false));
    const t1 = setTimeout(() => fetchMed(), 300);
    const t2 = setTimeout(() => fetchSlow(), 800);
    const f1 = setInterval(fetchFast, 3000);
    const f2 = setInterval(fetchMed, 10000);
    const f3 = setInterval(fetchSlow, 30000);
    return () => { clearInterval(f1); clearInterval(f2); clearInterval(f3); clearTimeout(t1); clearTimeout(t2); };
  }, [selSym]);

  /* ── Derived ── */
  const totalVol = useMemo(() => volumes.reduce((s, v) => s + (v.total_volume || 0), 0), [volumes]);
  const openSims = useMemo(() => sims.filter(s => s.status === 'open'), [sims]);
  const closedSims = useMemo(() => sims.filter(s => s.status === 'closed'), [sims]);

  const filteredSignals = useMemo(() => {
    let f = [...signals];
    if (sigFilter === 'pending') f = f.filter(s => s.status === 'pending');
    else if (sigFilter === 'processed') f = f.filter(s => s.status === 'processed');
    else if (sigFilter === 'rejected') f = f.filter(s => s.status?.startsWith('risk_') || s.status?.startsWith('filtered'));
    if (sigDir !== 'all') f = f.filter(s => getDir(s) === sigDir);
    if (sigMkt !== 'all') { const mt = sigMkt; f = f.filter(s => (safeMeta(s.metadata).market_type || s.market_type || 'spot') === mt); }
    return f;
  }, [signals, sigFilter, sigDir, sigMkt]);

  const calcResult = useMemo(() => {
    const e = parseFloat(calcEntry), sl = parseFloat(calcSL), risk = parseFloat(calcRisk);
    if (!e || !sl || !risk || e === sl) return null;
    const dist = Math.abs(e - sl), pct = (dist / e) * 100;
    const posSize = risk / dist, posValue = posSize * e;
    const rr1 = e + (e > sl ? dist : -dist), rr2 = e + (e > sl ? dist * 2 : -dist * 2), rr3 = e + (e > sl ? dist * 3 : -dist * 3);
    return { dist, pct, posSize, posValue, direction: e > sl ? 'LONG' : 'SHORT', rr1, rr2, rr3 };
  }, [calcEntry, calcSL, calcRisk]);

  const addWL = async () => {
    if (!wlSym.trim()) return;
    await fetch("/api/watchlist/add", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ symbol: wlSym.trim().toUpperCase(), exchange: wlEx, market_type: wlMT }) });
    setWlSym(""); fetchMed();
  };
  const rmWL = async (w: R) => {
    await fetch("/api/watchlist/remove", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ symbol: w.symbol, exchange: w.exchange, market_type: w.market_type }) });
    fetchMed();
  };

  const activeAgents = Object.values(agentSt).filter((a: any) => a.status === 'running').length;
  const totalAgents = Math.max(Object.keys(agentSt).length, 5);

  const TABS: { key: Tab; label: string; icon: string }[] = [
    { key: "overview", label: "Genel Bakış", icon: "📊" },
    { key: "terminal", label: "Terminal", icon: "📈" },
    { key: "signals", label: "Sinyaller", icon: "📡" },
    { key: "simulations", label: "Simülasyonlar", icon: "👻" },
    { key: "brain", label: "AI Beyin", icon: "🧠" },
    { key: "mastercontrol", label: "Kontrol", icon: "🎯" },
    { key: "system", label: "Sistem", icon: "⚙️" },
  ];

  return (
    <div className="app">
      {initialLoading && <div className="loading-overlay"><div className="loading-spinner" /><div className="loading-text">QuenBot PRO yükleniyor...</div></div>}
      {/* ═══ TOP NAV ═══ */}
      <header className="topnav">
        <div className="topnav-left">
          <div className="logo"><span className="logo-icon">Q</span><div className="logo-text"><strong>QuenBot</strong><span>PRO</span></div></div>
          <nav className="tabs">
            {TABS.map(t => (
              <button key={t.key} className={cls("tab-btn", tab === t.key && "tab-active")} onClick={() => setTab(t.key)}>
                <span className="tab-icon">{t.icon}</span>{t.label}
                {t.key === 'signals' && signals.filter(s => s.status === 'pending').length > 0 && <span className="tab-badge">{signals.filter(s => s.status === 'pending').length}</span>}
                {t.key === 'simulations' && openSims.length > 0 && <span className="tab-badge">{openSims.length}</span>}
              </button>
            ))}
          </nav>
        </div>
        <div className="topnav-right">
          <div className="nav-stat"><span className="dot-live" /><span>{activeAgents}/{totalAgents} Agent</span></div>
          <div className="nav-stat">{sysStats ? `${fmt(sysStats.trades_per_minute, 0)} t/dk` : "—"}</div>
          <div className="nav-stat t-m">{fmtTime(lastUp.toISOString())}</div>
        </div>
      </header>

      {/* ═══ TICKER BAR ═══ */}
      <div className="ticker-bar">
        <div className="ticker-scroll">
          {prices.map(p => {
            const prev = prevPrices.current[p.symbol]; const dir = prev ? (p.price > prev ? "up" : p.price < prev ? "dn" : "") : "";
            const mv = movers.find(m => m.symbol === p.symbol); const chg = mv?.change_pct ?? 0;
            return (
              <div key={p.symbol} className={cls("ticker-item", dir && `tk-${dir}`)} onClick={() => { setSelSym(p.symbol); setTab("terminal"); }}>
                <span className="ticker-sym">{p.symbol.replace("USDT", "")}</span>
                <span className="ticker-price">{fmtUsd(p.price)}</span>
                <span className={cls("ticker-chg", chg >= 0 ? "t-g" : "t-r")}>{fmtPct(chg)}</span>
              </div>
            );
          })}
        </div>
      </div>

      {/* ═══ MAIN CONTENT ═══ */}
      <main className="content">

        {/* ══════ OVERVIEW ══════ */}
        {tab === "overview" && <>
          <div className="kpi-row">
            <KPI label="Toplam Trade" value={summary ? fmt(summary.total_trades, 0) : "—"} icon="💹" />
            <KPI label="Trade/dk" value={sysStats ? fmt(sysStats.trades_per_minute, 0) : "—"} icon="⚡" accent="b" />
            <KPI label="Hacim (1sa)" value={`$${fmt(totalVol, 0)}`} icon="📊" accent="p" />
            <KPI label="Aktif Sinyal" value={summary ? String(summary.active_signals) : "0"} icon="📡" accent={summary && summary.active_signals > 0 ? "w" : undefined} />
            <KPI label="Açık Sim." value={String(openSims.length)} icon="👻" accent={openSims.length > 0 ? "g" : undefined} />
            <KPI label="Win Rate" value={botSum ? `${fmt(botSum.win_rate, 1)}%` : "0%"} icon="🏆" accent={botSum && botSum.win_rate >= 50 ? "g" : "r"} />
            <KPI label="Toplam PnL" value={summary ? fmtUsd(summary.total_pnl) : "$0"} icon="💰" accent={summary && summary.total_pnl > 0 ? "g" : summary && summary.total_pnl < 0 ? "r" : undefined} />
          </div>

          <div className="g2">
            <div className="card"><div className="card-h"><h3>Hacim Trendi (60dk)</h3><span className="badge badge-b">{fmt(timeline.reduce((s, t) => s + t.volume, 0), 0)} USD</span></div><MiniBar data={timeline.map(t => t.volume || 0)} h={64} color="var(--c)" /></div>
            <div className="card"><div className="card-h"><h3>Trade Sayısı (60dk)</h3><span className="badge badge-g">{fmt(timeline.reduce((s, t) => s + t.count, 0), 0)}</span></div><MiniBar data={timeline.map(t => t.count || 0)} h={64} color="var(--g)" /></div>
          </div>

          <div className="g2">
            <div className="card"><div className="card-h"><h3>Fiyatlar</h3><span className="badge badge-live">CANLI</span></div>
              <div className="price-grid">
                {prices.map(p => {
                  const prev = prevPrices.current[p.symbol]; const dir = prev ? (p.price > prev ? "up" : p.price < prev ? "dn" : "") : "";
                  const mv = movers.find(m => m.symbol === p.symbol); const chg = mv?.change_pct ?? 0;
                  return (
                    <div key={p.symbol} className={cls("ptile", dir && `pf-${dir}`)} onClick={() => { setSelSym(p.symbol); setTab("terminal"); }}>
                      <div className="ptile-top"><span className="ptile-sym">{p.symbol.replace("USDT", "")}<span className="t-m">/USDT</span></span><span className={cls("chg-badge", chg >= 0 ? "chg-up" : "chg-dn")}>{fmtPct(chg)}</span></div>
                      <div className="ptile-price">{fmtUsd(p.price)}</div>
                      <div className="ptile-meta"><span>{p.exchange}</span><span>{fmtTime(p.timestamp)}</span></div>
                    </div>
                  );
                })}
              </div>
            </div>

            <div className="card"><div className="card-h"><h3>En Hareketli</h3><span className="badge badge-w">1 Saat</span></div>
              <div className="mover-list">
                {movers.slice(0, 10).map((m, i) => (
                  <div key={m.symbol} className="mover-row" onClick={() => { setSelSym(m.symbol); setTab("terminal"); }}>
                    <span className="mover-rank">#{i + 1}</span>
                    <span className="mover-sym">{m.symbol.replace("USDT", "")}<span className="t-m">/USDT</span></span>
                    <span className="mover-price">{fmtUsd(m.current_price)}</span>
                    <span className={cls("chg-badge chg-lg", m.change_pct >= 0 ? "chg-up" : "chg-dn")}>{fmtPct(m.change_pct)}</span>
                  </div>
                ))}
              </div>
            </div>
          </div>

          <div className="card"><div className="card-h"><h3>Borsa Bazında Hacim</h3></div>
            <div className="ex-grid">
              {volumes.map((v, i) => (<div key={i} className="ex-card"><div className="ex-name">{v.exchange.toUpperCase()} <span className="badge badge-sm">{v.market_type}</span></div><div className="ex-vol">${fmt(v.total_volume, 0)}</div><div className="t-m text-xs">{fmt(v.trade_count, 0)} trade</div></div>))}
            </div>
          </div>

          {openSims.length > 0 && <div className="card"><div className="card-h"><h3>Aktif Simülasyonlar</h3><span className="badge badge-g">{openSims.length} açık</span></div>
            <div className="sim-grid">
              {openSims.map((s, i) => {
                const entry = sn(s.entry_price), lp = prices.find(p => p.symbol === s.symbol), cur = lp ? lp.price : entry;
                const pnl = entry > 0 ? ((s.side === 'long' ? (cur - entry) : (entry - cur)) / entry * 100) : 0;
                return (
                  <div key={s.id || i} className={cls("sim-card", pnl >= 0 ? "sim-win" : "sim-loss")}>
                    <div className="sim-top"><div className="sim-coin"><strong>{(s.symbol || '').replace('USDT', '')}</strong><span className={cls("dir-sm", s.side === 'long' ? "dir-l" : "dir-s")}>{s.side === 'long' ? '↑ LONG' : '↓ SHORT'}</span></div><div className={cls("sim-pnl", pnl >= 0 ? "t-g" : "t-r")}>{pnl >= 0 ? '+' : ''}{pnl.toFixed(2)}%</div></div>
                    <div className="sim-body"><div><span className="t-m">Giriş:</span> <span className="mono">{fmtUsd(entry)}</span></div><div><span className="t-m">Güncel:</span> <span className="mono">{fmtUsd(cur)}</span></div></div>
                  </div>
                );
              })}
            </div>
          </div>}

          {pnlTL.length > 0 && <div className="card"><div className="card-h"><h3>Kümülatif PnL</h3></div><MiniBar data={pnlTL.map(p => p.cumulative_pnl ?? 0)} h={64} color={pnlTL.length > 0 && (pnlTL[pnlTL.length - 1]?.cumulative_pnl ?? 0) >= 0 ? "var(--g)" : "var(--r)"} /></div>}
        </>}

        {/* ══════ TERMINAL ══════ */}
        {tab === "terminal" && <>
          <div className="terminal-header">
            <div className="sym-selector">
              {prices.map(p => <button key={p.symbol} className={cls("sym-btn", selSym === p.symbol && "sym-active")} onClick={() => setSelSym(p.symbol)}>{p.symbol.replace("USDT", "")}</button>)}
            </div>
          </div>
          {(() => {
            const p = prices.find(x => x.symbol === selSym); const mv = movers.find(m => m.symbol === selSym); const flow = orderFlow.find(o => o.symbol === selSym);
            if (!p) return <div className="empty">Sembol yükleniyor...</div>;
            const chg = mv?.change_pct ?? 0;
            return <>
              <div className="terminal-info">
                <div className="ti-main">
                  <h2>{selSym.replace("USDT", "")}<span className="t-m">/USDT</span></h2>
                  <div className="ti-price">{fmtUsd(p.price)}</div>
                  <span className={cls("chg-badge chg-lg", chg >= 0 ? "chg-up" : "chg-dn")}>{fmtPct(chg)}</span>
                </div>
                <div className="ti-stats">
                  <div><span className="t-m">Açılış</span><span className="mono">{fmtUsd(mv?.open_price ?? 0)}</span></div>
                  <div><span className="t-m">Borsa</span><span>{p.exchange}</span></div>
                  <div><span className="t-m">Güncelleme</span><span>{fmtTime(p.timestamp)}</span></div>
                </div>
              </div>
              <div className="card"><div className="card-h"><h3>Fiyat Grafiği (1dk mum)</h3><span className="badge badge-b">{candles.length} mum</span></div><CandleChart candles={candles} height={260} /></div>
              <div className="g2">
                {flow && <div className="card"><div className="card-h"><h3>Alış / Satış Basıncı</h3></div>
                  <FlowBar buy={flow.buy_volume} sell={flow.sell_volume} />
                  <div className="flow-stats"><div><span className="t-g">Alış:</span> ${fmt(flow.buy_volume, 0)} ({flow.buy_count})</div><div><span className="t-r">Satış:</span> ${fmt(flow.sell_volume, 0)} ({flow.sell_count})</div></div>
                </div>}
                <div className="card"><div className="card-h"><h3>Son İşlemler</h3></div>
                  <div className="trade-feed">{trades.filter(t => t.symbol === selSym).slice(0, 15).map((t, i) => (
                    <div key={i} className={cls("trade-row", t.side === "buy" ? "tr-buy" : "tr-sell")}><span className="tr-side">{t.side === "buy" ? "▲" : "▼"}</span><span className="mono">{fmtUsd(Number(t.price))}</span><span className="t-m">{fmt(Number(t.quantity), 4)}</span><span className="t-m">{fmtTime(t.timestamp)}</span></div>
                  ))}</div>
                </div>
              </div>

              <div className="card"><div className="card-h"><h3>Pozisyon Hesaplayıcı</h3><span className="badge badge-p">Risk Yönetimi</span></div>
                <div className="calc-grid">
                  <div className="calc-inputs">
                    <div className="calc-field"><label>Giriş Fiyatı</label><input type="number" value={calcEntry} onChange={e => setCalcEntry(e.target.value)} placeholder={String(p.price)} /></div>
                    <div className="calc-field"><label>Stop Loss</label><input type="number" value={calcSL} onChange={e => setCalcSL(e.target.value)} placeholder="0.00" /></div>
                    <div className="calc-field"><label>Risk ($)</label><input type="number" value={calcRisk} onChange={e => setCalcRisk(e.target.value)} placeholder="100" /></div>
                  </div>
                  {calcResult && <div className="calc-results">
                    <div className="calc-dir"><span className={cls("dir-badge", calcResult.direction === 'LONG' ? "dir-l" : "dir-s")}>{calcResult.direction}</span></div>
                    <div className="calc-row"><span>SL Mesafesi:</span><span className="mono">{fmtUsd(calcResult.dist)} ({calcResult.pct.toFixed(2)}%)</span></div>
                    <div className="calc-row"><span>Pozisyon Büyüklüğü:</span><span className="mono t-c">{fmt(calcResult.posSize, 4)} adet</span></div>
                    <div className="calc-row"><span>Pozisyon Değeri:</span><span className="mono">{fmtUsd(calcResult.posValue)}</span></div>
                    <div className="calc-tp">
                      <div className="calc-row"><span>TP1 (1:1):</span><span className="mono t-g">{fmtUsd(calcResult.rr1)}</span></div>
                      <div className="calc-row"><span>TP2 (1:2):</span><span className="mono t-g">{fmtUsd(calcResult.rr2)}</span></div>
                      <div className="calc-row"><span>TP3 (1:3):</span><span className="mono t-g">{fmtUsd(calcResult.rr3)}</span></div>
                    </div>
                  </div>}
                </div>
              </div>
            </>;
          })()}
        </>}

        {/* ══════ SIGNALS ══════ */}
        {tab === "signals" && <>
          <div className="section-header"><h2>Sinyal Merkezi</h2>
            <div className="filter-bar">
              <div className="filter-group">
                {(['all', 'pending', 'processed', 'rejected'] as const).map(f => <button key={f} className={cls("fbtn", sigFilter === f && "fbtn-a")} onClick={() => setSigFilter(f)}>{f === 'all' ? 'Tümü' : f === 'pending' ? 'Bekleyen' : f === 'processed' ? 'İşlenen' : 'Reddedilen'}</button>)}
              </div>
              <div className="filter-group">
                {(['all', 'long', 'short'] as const).map(d => <button key={d} className={cls("fbtn", sigDir === d && "fbtn-a")} onClick={() => setSigDir(d)}>{d === 'all' ? 'Tüm Yön' : d === 'long' ? '↑ Long' : '↓ Short'}</button>)}
              </div>
              <div className="filter-group">
                {(['all', 'spot', 'futures'] as const).map(m => <button key={m} className={cls("fbtn", sigMkt === m && "fbtn-a")} onClick={() => setSigMkt(m)}>{m === 'all' ? 'Tüm Piyasa' : m.toUpperCase()}</button>)}
              </div>
            </div>
          </div>

          <div className="kpi-row">
            <KPI label="Toplam" value={String(signals.length)} icon="📡" />
            <KPI label="Bekleyen" value={String(signals.filter(s => s.status === 'pending').length)} icon="⏳" accent="w" />
            <KPI label="İşlenen" value={String(signals.filter(s => s.status === 'processed').length)} icon="✅" accent="g" />
            <KPI label="Reddedilen" value={String(signals.filter(s => s.status?.startsWith('risk_') || s.status?.startsWith('filtered')).length)} icon="🛡" accent="r" />
            <KPI label="Long" value={String(signals.filter(s => getDir(s) === 'long').length)} icon="↑" accent="g" />
            <KPI label="Short" value={String(signals.filter(s => getDir(s) === 'short').length)} icon="↓" accent="r" />
          </div>

          <div className="card">
            <div className="card-h"><h3>Sinyal Akışı</h3><span className="badge">{filteredSignals.length} / {signals.length}</span></div>
            <div className="tbl-wrap"><table className="tbl">
              <thead><tr><th>Coin</th><th>Yön</th><th>Fiyat</th><th>Strateji</th><th>Güven</th><th>Hedef</th><th>Piyasa</th><th>Durum</th><th>Zaman</th></tr></thead>
              <tbody>
                {filteredSignals.slice(0, 40).map((s, i) => {
                  const m = safeMeta(s.metadata), dir = getDir(s), conf = safeConf(s.confidence), price = sn(s.price);
                  const target = m.target_pct != null ? sn(m.target_pct) : null;
                  const mkt = (m.market_type || s.market_type || 'spot').toUpperCase();
                  const si = stInfo(s.status);
                  return (
                    <tr key={s.id || i} className={dir === 'long' ? 'row-l' : dir === 'short' ? 'row-s' : ''}>
                      <td><div className="coin-cell"><span className="coin-dot" style={{ background: dir === 'long' ? 'var(--g)' : dir === 'short' ? 'var(--r)' : 'var(--m)' }} /><strong>{(s.symbol || '').replace('USDT', '')}</strong><span className="t-m">/USDT</span></div></td>
                      <td><span className={cls("dir-badge", dir === "long" ? "dir-l" : dir === "short" ? "dir-s" : "dir-n")}>{dir === 'long' ? 'LONG' : dir === 'short' ? 'SHORT' : '—'}</span></td>
                      <td className="mono">{price > 0 ? fmtUsd(price) : '—'}</td>
                      <td><span className="strat-badge">{sigLabel(s.signal_type)}</span></td>
                      <td><div className="conf-cell"><div className="conf-bar"><div className="conf-fill" style={{ width: `${Math.min(conf, 100)}%`, background: conf >= 70 ? 'var(--g)' : conf >= 50 ? 'var(--w)' : 'var(--r)' }} /></div><span className="conf-txt">{conf.toFixed(0)}%</span></div></td>
                      <td className="t-g mono">{target != null ? `%${target.toFixed(1)}` : '—'}</td>
                      <td><span className="badge badge-sm">{mkt}</span></td>
                      <td><span className={cls("badge badge-sm", si.c)}>{si.l}</span></td>
                      <td className="t-m">{fmtDT(s.timestamp || s.created_at || '')}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
              {filteredSignals.length === 0 && <div className="empty">Filtrelerle eşleşen sinyal yok</div>}
            </div>
          </div>

          {sigSummary.length > 0 && <div className="card"><div className="card-h"><h3>Sinyal Tipi Dağılımı</h3></div>
            <div className="stat-grid">{sigSummary.map((s, i) => (
              <div key={i} className="stat-card"><div className="stat-name">{sigLabel(s.signal_type)}</div><div className="stat-val">{fmt(sn(s.total || s.count), 0)}</div></div>
            ))}</div>
          </div>}

          {movements.length > 0 && <div className="card"><div className="card-h"><h3>Piyasa Hareketleri</h3><span className="badge badge-w">{movements.length}</span></div>
            <div className="tbl-wrap"><table className="tbl"><thead><tr><th>Coin</th><th>Borsa</th><th>Tip</th><th>Değişim</th><th>Yön</th><th>Hacim</th><th>Zaman</th></tr></thead><tbody>
              {movements.slice(0, 15).map((m, i) => (
                <tr key={i}><td><strong>{(m.symbol || '').replace('USDT', '')}</strong></td><td>{m.exchange || '—'}</td><td><span className="badge badge-sm">{m.market_type}</span></td>
                  <td className={sn(m.change_pct) >= 0 ? "t-g" : "t-r"} style={{ fontWeight: 700 }}>{fmtPct(sn(m.change_pct) * 100)}</td><td>{m.direction || '—'}</td><td className="mono">{fmt(sn(m.volume), 2)}</td><td className="t-m">{m.start_time ? fmtTime(m.start_time) : '—'}</td></tr>
              ))}</tbody></table></div>
          </div>}
        </>}

        {/* ══════ SIMULATIONS ══════ */}
        {tab === "simulations" && <>
          <div className="section-header"><h2>Simülasyon Merkezi</h2></div>

          <div className="kpi-row">
            <KPI label="Toplam" value={botSum ? String(botSum.total_simulations) : "0"} icon="📊" />
            <KPI label="Açık" value={String(openSims.length)} icon="🟢" accent="g" />
            <KPI label="Kapalı" value={String(closedSims.length)} icon="📕" />
            <KPI label="Kazanan" value={botSum ? String(botSum.wins) : "0"} icon="✅" accent="g" />
            <KPI label="Kaybeden" value={botSum ? String(botSum.losses) : "0"} icon="❌" accent="r" />
            <KPI label="Win Rate" value={botSum ? `${fmt(botSum.win_rate, 1)}%` : "0%"} icon="🏆" accent={botSum && botSum.win_rate >= 50 ? "g" : "r"} />
            <KPI label="Ort. PnL" value={botSum ? `${fmt(botSum.average_pnl_pct, 2)}%` : "0%"} icon="💱" accent={botSum && botSum.average_pnl_pct > 0 ? "g" : "r"} />
          </div>

          <div className="g2">
            <div className="card card-center"><h3>Win Rate</h3><Ring value={sn(botSum?.win_rate)} size={130} stroke={10} color={botSum && botSum.win_rate >= 50 ? "var(--g)" : "var(--r)"} /><div className="t-m" style={{ marginTop: 12 }}>{botSum?.wins ?? 0}W / {botSum?.losses ?? 0}L</div></div>
            <div className="card">
              <div className="card-h"><h3>Yön Dağılımı</h3></div>
              {(() => {
                const l = signals.filter(s => getDir(s) === 'long').length, sh = signals.filter(s => getDir(s) === 'short').length, t = l + sh || 1, lp = (l / t) * 100;
                return <div style={{ padding: 20 }}>
                  <div className="dir-labels"><span className="t-g">↑ LONG ({l})</span><span className="t-r">↓ SHORT ({sh})</span></div>
                  <div className="dir-bar"><div className="dir-fill" style={{ width: `${lp}%` }} /></div>
                  <div className="g3" style={{ marginTop: 16 }}>
                    <div className="mini-stat"><div className="ms-label">Toplam Sim.</div><div className="ms-val">{botSum?.total_simulations ?? 0}</div></div>
                    <div className="mini-stat"><div className="ms-label">Kazanç</div><div className="ms-val t-g">{botSum?.wins ?? 0}</div></div>
                    <div className="mini-stat"><div className="ms-label">Kayıp</div><div className="ms-val t-r">{botSum?.losses ?? 0}</div></div>
                  </div>
                </div>;
              })()}
            </div>
          </div>

          {openSims.length > 0 && <div className="card"><div className="card-h"><h3>Aktif Simülasyonlar</h3><span className="badge badge-g">{openSims.length}</span></div>
            <div className="sim-grid">
              {openSims.map((s, i) => {
                const entry = sn(s.entry_price), lp = prices.find(p => p.symbol === s.symbol), cur = lp ? lp.price : entry;
                const pnl = entry > 0 ? ((s.side === 'long' ? (cur - entry) : (entry - cur)) / entry * 100) : 0;
                const dur = s.entry_time ? Math.floor((Date.now() - new Date(s.entry_time).getTime()) / 60000) : 0;
                const tp = sn(s.take_profit_price || s.target_price);
                const sl = sn(s.stop_loss_price);
                const conf = safeConf(s.confidence);
                return (
                  <div key={s.id || i} className={cls("sim-card", pnl >= 0 ? "sim-win" : "sim-loss")}>
                    <div className="sim-top"><div className="sim-coin"><strong>{(s.symbol || '').replace('USDT', '')}</strong><span className={cls("dir-sm", s.side === 'long' ? "dir-l" : "dir-s")}>{s.side === 'long' ? '↑ LONG' : '↓ SHORT'}</span></div><div className={cls("sim-pnl", pnl >= 0 ? "t-g" : "t-r")}>{pnl >= 0 ? '+' : ''}{pnl.toFixed(2)}%</div></div>
                    <div className="sim-body">
                      <div><span className="t-m">Giriş:</span> <span className="mono">{fmtUsd(entry)}</span></div>
                      <div><span className="t-m">Güncel:</span> <span className="mono">{fmtUsd(cur)}</span></div>
                      {tp > 0 && <div><span className="t-m">Hedef:</span> <span className="mono t-g">{fmtUsd(tp)}</span></div>}
                      {sl > 0 && <div><span className="t-m">Stop:</span> <span className="mono t-r">{fmtUsd(sl)}</span></div>}
                      {conf > 0 && <div><span className="t-m">Güven:</span> <span className="mono">{conf.toFixed(0)}%</span></div>}
                      <div><span className="t-m">Açılış:</span> <span className="mono">{s.entry_time ? fmtDT(s.entry_time) : '—'}</span></div>
                      <div><span className="t-m">Süre:</span> {ago(dur)}</div>
                    </div>
                  </div>
                );
              })}
            </div>
          </div>}

          <div className="card"><div className="card-h"><h3>Simülasyon Geçmişi</h3><span className="badge">{sims.length}</span></div>
            <div className="tbl-wrap"><table className="tbl"><thead><tr><th>Coin</th><th>Yön</th><th>Giriş</th><th>Hedef</th><th>Çıkış</th><th>PnL</th><th>PnL %</th><th>Güven</th><th>Giriş Zamanı</th><th>Çıkış Zamanı</th><th>Durum</th></tr></thead><tbody>
              {sims.slice(0, 25).map((s, i) => {
                const pnl = sn(s.pnl), pnlP = sn(s.pnl_pct), tp = sn(s.take_profit_price || s.target_price), conf = safeConf(s.confidence);
                return <tr key={s.id || i}><td><strong>{(s.symbol || '').replace('USDT', '')}</strong></td><td><span className={cls("dir-sm", s.side === 'long' ? "dir-l" : "dir-s")}>{s.side === 'long' ? 'LONG' : 'SHORT'}</span></td><td className="mono">{fmtUsd(sn(s.entry_price))}</td><td className="mono">{tp > 0 ? fmtUsd(tp) : '—'}</td><td className="mono">{s.exit_price ? fmtUsd(sn(s.exit_price)) : '—'}</td><td className={pnl >= 0 ? "t-g" : "t-r"} style={{ fontWeight: 700 }}>{s.pnl != null ? `${pnl >= 0 ? '+' : ''}${fmtUsd(pnl)}` : '—'}</td><td className={pnlP >= 0 ? "t-g" : "t-r"} style={{ fontWeight: 700 }}>{s.pnl_pct != null ? `${pnlP >= 0 ? '+' : ''}${pnlP.toFixed(2)}%` : '—'}</td><td className="mono">{conf > 0 ? `${conf.toFixed(0)}%` : '—'}</td><td className="t-m">{s.entry_time ? fmtDT(s.entry_time) : '—'}</td><td className="t-m">{s.exit_time ? fmtDT(s.exit_time) : '—'}</td><td><span className={cls("badge badge-sm", s.status === 'open' ? 'badge-g' : s.status === 'closed' ? 'badge-b' : '')}>{s.status}</span></td></tr>;
              })}</tbody></table>
              {sims.length === 0 && <div className="empty">Henüz simülasyon yok</div>}
            </div>
          </div>

          {rcaResults.length > 0 && <div className="card"><div className="card-h"><h3>Kök Neden Analizi (RCA)</h3><span className="badge badge-r">{rcaResults.length}</span></div>
            <div className="tbl-wrap"><table className="tbl"><thead><tr><th>Coin</th><th>Başarısızlık</th><th>Güven</th><th>Tahmin Vol.</th><th>Gerçek Vol.</th><th>Zaman</th></tr></thead><tbody>
              {rcaResults.slice(0, 15).map((r, i) => <tr key={i}><td><strong>{(r.symbol || '').replace('USDT', '')}</strong></td><td><span className="badge badge-sm badge-r">{r.failure_type || '—'}</span></td><td>{r.confidence != null ? `${safeConf(r.confidence).toFixed(0)}%` : '—'}</td><td className="mono">{sn(r.predicted_volatility).toFixed(4)}</td><td className="mono">{sn(r.actual_volatility).toFixed(4)}</td><td className="t-m">{r.created_at ? fmtDT(r.created_at) : '—'}</td></tr>)}
            </tbody></table></div>
          </div>}

          {corrections.length > 0 && <div className="card"><div className="card-h"><h3>Oto-Düzeltmeler</h3><span className="badge badge-g">{corrections.filter(c => c.applied).length} uygulandı</span></div>
            <div className="tbl-wrap"><table className="tbl"><thead><tr><th>Sinyal</th><th>Başarısızlık</th><th>Ayar</th><th>Değer</th><th>Durum</th><th>Zaman</th></tr></thead><tbody>
              {corrections.slice(0, 20).map((c, i) => <tr key={i}><td>{c.signal_type || '—'}</td><td><span className="badge badge-sm badge-r">{c.failure_type || '—'}</span></td><td className="mono">{c.adjustment_key || '—'}</td><td className="mono">{c.adjustment_value || '—'}</td><td>{c.applied ? <span className="badge badge-sm badge-g">Uygulandı</span> : <span className="badge badge-sm badge-w">Bekliyor</span>}</td><td className="t-m">{c.created_at ? fmtDT(c.created_at) : '—'}</td></tr>)}
            </tbody></table></div>
          </div>}
        </>}

        {/* ══════ BRAIN ══════ */}
        {tab === "brain" && <>
          <div className="section-header"><h2>AI Beyin Merkezi</h2><span className="badge badge-p">Öğrenme Sistemi</span></div>

          <div className="card"><div className="card-h"><h3>Agent Koordinasyonu</h3><span className="badge badge-live">Canlı</span></div>
            <div className="agent-row">
              {[{ n: "Scout", k: "scout", i: "🔍" }, { n: "Strategist", k: "strategist", i: "📡" }, { n: "Ghost Sim.", k: "ghost_simulator", i: "👻" }, { n: "Auditor", k: "auditor", i: "📋" }, { n: "Brain", k: "brain", i: "🧠" }].map(a => {
                const d = agentSt[a.k]; const ok = d?.status === 'running'; const stale = d?.status === 'stale';
                return <div key={a.k} className={cls("agent-chip", ok ? "ac-ok" : stale ? "ac-warn" : "ac-off")}><span className="agent-icon">{a.i}</span><div><div className="agent-name">{a.n}</div><div className="agent-st">{ok ? '✓ Aktif' : stale ? '⚠ Yanıtlamıyor' : '— Bekleniyor'}</div></div></div>;
              })}
            </div>
          </div>

          <div className="kpi-row">
            <KPI label="Pattern" value={brainSt ? fmt(brainSt.pattern_count, 0) : "0"} icon="🧬" accent="p" />
            <KPI label="Doğruluk" value={brainSt ? `${sn(brainSt.learning.accuracy).toFixed(1)}%` : "0%"} icon="🎯" accent={brainSt && sn(brainSt.learning.accuracy) >= 50 ? "g" : "w"} />
            <KPI label="Toplam Tahmin" value={brainSt ? fmt(brainSt.learning.total, 0) : "0"} icon="📊" />
            <KPI label="Doğru" value={brainSt ? fmt(brainSt.learning.correct, 0) : "0"} icon="✅" accent="g" />
            <KPI label="Ort. PnL" value={brainSt ? `${sn(brainSt.learning.avg_pnl).toFixed(2)}%` : "0%"} icon="💰" accent={brainSt && sn(brainSt.learning.avg_pnl) > 0 ? "g" : "r"} />
          </div>

          <div className="g2">
            <div className="card card-center"><h3>Öğrenme Doğruluğu</h3>
              {brainSt && sn(brainSt.learning.total) > 0 ? <>
                <Ring value={sn(brainSt.learning.accuracy)} size={140} stroke={12} color={sn(brainSt.learning.accuracy) >= 50 ? "var(--g)" : "var(--w)"} />
                <div className="t-m" style={{ marginTop: 16 }}>{brainSt.learning.correct} doğru / {brainSt.learning.total} toplam</div>
              </> : <div className="empty" style={{ padding: 20 }}><div style={{ fontSize: 48 }}>🧠</div><p><strong>Öğrenme Başlamadı</strong></p><p className="t-m text-xs">Pattern: {brainSt?.pattern_count ?? 0}</p></div>}
            </div>
            <div className="card"><div className="card-h"><h3>Sinyal Tipi Başarıları</h3></div>
              {brainSt && brainSt.signal_type_stats.length > 0 ?
                <div className="tbl-wrap"><table className="tbl"><thead><tr><th>Tip</th><th>Toplam</th><th>Doğru</th><th>Başarı</th><th>Ort PnL</th></tr></thead><tbody>
                  {brainSt.signal_type_stats.map((s, i) => <tr key={i}><td>{s.signal_type}</td><td>{s.total}</td><td>{s.correct}</td><td className={sn(s.total) > 0 && sn(s.correct) / sn(s.total) >= 0.5 ? "t-g" : "t-r"}>{sn(s.total) > 0 ? `${(sn(s.correct) / sn(s.total) * 100).toFixed(0)}%` : '—'}</td><td className={sn(s.avg_pnl) >= 0 ? "t-g" : "t-r"}>{sn(s.avg_pnl).toFixed(2)}%</td></tr>)}
                </tbody></table></div>
                : <div className="empty">Sinyal istatistikleri oluşmadı</div>}
            </div>
          </div>

          {learnSt?.daily_accuracy && learnSt.daily_accuracy.length > 0 && <div className="card"><div className="card-h"><h3>Günlük Öğrenme Trendi</h3></div>
            <div className="tbl-wrap"><table className="tbl"><thead><tr><th>Tarih</th><th>Toplam</th><th>Doğru</th><th>Doğruluk</th></tr></thead><tbody>
              {learnSt.daily_accuracy.map((d: any, i: number) => { const acc = sn(d.total) > 0 ? (sn(d.correct) / sn(d.total) * 100) : 0; return <tr key={i}><td>{d.day ? new Date(d.day).toLocaleDateString("tr-TR") : '—'}</td><td>{d.total}</td><td>{d.correct}</td><td className={acc >= 50 ? "t-g" : "t-r"}>{acc.toFixed(1)}%</td></tr>; })}
            </tbody></table></div>
          </div>}

          <div className="card"><div className="card-h"><h3>Kayıtlı Patternlar</h3><span className="badge badge-p">{brainPat.length}</span></div>
            {brainPat.length > 0 ?
              <div className="tbl-wrap"><table className="tbl"><thead><tr><th>Sembol</th><th>15dk</th><th>1sa</th><th>4sa</th><th>1gün</th><th>Kayıt</th></tr></thead><tbody>
                {brainPat.map((p, i) => <tr key={i}><td><strong>{p.symbol}</strong></td>
                  {['outcome_15m', 'outcome_1h', 'outcome_4h', 'outcome_1d'].map(k => <td key={k} className={sn(p[k]) > 0 ? "t-g" : sn(p[k]) < 0 ? "t-r" : "t-m"}>{p[k] != null ? `${(sn(p[k]) * 100).toFixed(2)}%` : '⏳'}</td>)}
                  <td className="t-m">{fmtTime(p.created_at)}</td></tr>)}
              </tbody></table></div>
              : <div className="empty">Pattern birikiyor...</div>}
          </div>

          <div className="card"><div className="card-h"><h3>Nasıl Çalışır?</h3></div>
            <div className="steps">
              {[["Veri Toplama", "Scout 4 borsadan gerçek zamanlı trade toplar"], ["Pattern Tespiti", "Strategist 4 zaman diliminde pattern bulur"], ["Eşleştirme", "Brain cosine similarity ile geçmişle karşılaştırır"], ["Simülasyon", "Ghost min %2 hedefle kağıt trade açar"], ["Geri Bildirim", "Sonuçlar Brain'e beslenir, doğruluk artar"]].map(([t, d], i) =>
                <div key={i} className="step"><span className="step-n">{i + 1}</span><div><strong>{t}</strong><p className="t-m">{d}</p></div></div>
              )}
            </div>
          </div>
        </>}

        {/* ══════ MASTER CONTROL ══════ */}
        {tab === "mastercontrol" && <>
          <div className="section-header"><h2>Master Kontrol</h2><span className="badge badge-p">AI Beyin Kontrolü</span></div>
          <div className="kpi-row">
            <KPI label="LLM" value={llmSt?.healthy ? "Aktif" : "Kapalı"} icon="🧠" accent={llmSt?.healthy ? "g" : "r"} />
            <KPI label="Çağrı" value={llmSt?.call_count != null ? fmt(llmSt.call_count, 0) : "—"} icon="📡" accent="b" />
            <KPI label="Gecikme" value={llmSt?.llm_stats?.avg_latency_ms != null ? `${fmt(llmSt.llm_stats.avg_latency_ms, 0)}ms` : "—"} icon="⏱" />
            <KPI label="Kuyruk" value={queueSt?.queue_size != null ? fmt(queueSt.queue_size, 0) : "—"} icon="📋" accent="w" />
          </div>

          <div className="card"><div className="card-h"><h3>Kalıcı Direktifler</h3><span className="badge badge-w">TÜM AJANLARA</span></div>
            <div style={{ padding: 20 }}>
              <p className="t-m text-xs" style={{ marginBottom: 12 }}>Her LLM çağrısına master system prompt olarak eklenir.</p>
              <textarea className="dir-input" value={masterDir} onChange={e => setMasterDir(e.target.value)} placeholder="Direktiflerinizi yazın... Örn: Risk toleransını artır, Momentum sinyallerine öncelik ver" rows={5} />
              <div className="dir-actions">
                <button className="btn btn-primary" disabled={dirSaving} onClick={async () => {
                  setDirSaving(true); setDirStatus(null);
                  try { const r = await fetch("/api/directives", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ master_directive: masterDir }) }); setDirStatus(r.ok ? "✅ Kaydedildi" : "❌ Hata"); } catch { setDirStatus("❌ Bağlantı hatası"); }
                  finally { setDirSaving(false); setTimeout(() => setDirStatus(null), 3000); }
                }}>{dirSaving ? "..." : "💾 Kaydet"}</button>
                <button className="btn btn-danger" onClick={async () => {
                  if (!confirm("Direktifler silinecek?")) return;
                  try { await fetch("/api/directives", { method: "DELETE" }); setMasterDir(""); setDirStatus("🗑 Temizlendi"); setTimeout(() => setDirStatus(null), 3000); } catch {}
                }}>🗑 Temizle</button>
                {dirStatus && <span className="t-m">{dirStatus}</span>}
              </div>
            </div>
          </div>

          {queueSt && <div className="card"><div className="card-h"><h3>Görev Kuyruğu</h3></div>
            <div className="kpi-row" style={{ padding: 16 }}>
              <KPI label="Bekleyen" value={fmt(queueSt.queue_size ?? 0, 0)} icon="⏳" />
              <KPI label="Aktif" value={fmt(queueSt.active_tasks ?? 0, 0)} icon="🔄" accent="b" />
              <KPI label="Tamamlanan" value={fmt(queueSt.total_completed ?? 0, 0)} icon="✅" accent="g" />
              <KPI label="Başarısız" value={fmt(queueSt.total_failed ?? 0, 0)} icon="❌" accent="r" />
            </div>
            {queueSt.recent_tasks?.length > 0 && <div className="tbl-wrap"><table className="tbl"><thead><tr><th>Ajan</th><th>Görev</th><th>Durum</th><th>Süre</th></tr></thead><tbody>
              {queueSt.recent_tasks.slice(-10).reverse().map((t: R, i: number) => <tr key={i}><td>{t.agent}</td><td style={{ maxWidth: 200, overflow: 'hidden', textOverflow: 'ellipsis' }}>{t.description}</td><td><span className={cls("badge badge-sm", t.status === 'completed' ? 'badge-g' : t.status === 'failed' ? 'badge-r' : 'badge-w')}>{t.status}</span></td><td>{t.duration_ms ? `${fmt(t.duration_ms, 0)}ms` : '—'}</td></tr>)}
            </tbody></table></div>}
          </div>}

          {llmSt && <div className="card"><div className="card-h"><h3>Model Bilgisi</h3><span className={cls("badge", llmSt.healthy ? "badge-g" : "badge-r")}>{llmSt.healthy ? 'Bağlı' : 'Yok'}</span></div>
            <div className="stat-grid">
              <div className="stat-card"><div className="stat-name">Model</div><div className="stat-val" style={{ fontSize: 16 }}>{llmSt.llm_stats?.model ?? '—'}</div></div>
              <div className="stat-card"><div className="stat-name">Endpoint</div><div className="stat-val" style={{ fontSize: 12 }}>{llmSt.llm_stats?.base_url ?? '—'}</div></div>
              <div className="stat-card"><div className="stat-name">Çağrı</div><div className="stat-val">{fmt(llmSt.llm_stats?.total_calls ?? 0, 0)}</div></div>
              <div className="stat-card"><div className="stat-name">Hata</div><div className="stat-val">{fmt(llmSt.llm_stats?.total_errors ?? 0, 0)}</div></div>
            </div>
          </div>}
        </>}

        {/* ══════ SYSTEM ══════ */}
        {tab === "system" && <>
          <div className="section-header"><h2>Sistem Durumu</h2></div>
          <div className="kpi-row">
            <KPI label="Veritabanı" value={sysStats ? `${sysStats.db_size_mb} MB` : "—"} icon="💾" />
            <KPI label="Toplam Trade" value={sysStats ? fmt(sysStats.total_trades, 0) : "—"} icon="📦" />
            <KPI label="Trade/dk" value={sysStats ? fmt(sysStats.trades_per_minute, 0) : "—"} icon="⚡" accent="b" />
            <KPI label="Uptime" value={sysStats ? ago(sysStats.uptime_minutes) : "—"} icon="⏱" accent="g" />
          </div>

          <div className="g2">
            <div className="card"><div className="card-h"><h3>Agent Durumları</h3><span className="badge badge-g">{activeAgents} aktif</span></div>
              <div className="agent-list">
                {Object.keys(agentSt).length > 0 ? Object.entries(agentSt).map(([n, a]: [string, any]) => {
                  const ok = a.status === 'running', stale = a.status === 'stale';
                  return <div key={n} className="agent-item"><div className={cls("a-dot", ok ? "d-ok" : stale ? "d-warn" : "d-off")} /><div><div className="agent-name">{n}</div><div className="t-m text-xs">{ok ? 'Çalışıyor' : stale ? 'Yanıtlamıyor' : a.status}{a.age_seconds != null ? ` · ${Math.round(sn(a.age_seconds))}sn` : ''}</div></div></div>;
                }) : ['Scout', 'Strategist', 'Ghost Simulator', 'Auditor', 'Brain'].map((n, i) => <div key={i} className="agent-item"><div className="a-dot d-off" /><div><div className="agent-name">{n}</div><div className="t-m text-xs">Bekleniyor</div></div></div>)}
              </div>
            </div>
            <div className="card"><div className="card-h"><h3>Bağlantılar</h3></div>
              <div className="conn-list">{['Binance Spot WS', 'Binance Futures WS', 'Bybit Spot WS', 'Bybit Futures WS', 'PostgreSQL', 'Ollama LLM', 'Brain AI'].map((c, i) => <div key={i} className="conn-item"><span className="c-dot c-ok" />{c}</div>)}</div>
            </div>
          </div>

          {liveStream && liveStream.exchange_freshness?.length > 0 && <div className="card"><div className="card-h"><h3>Veri Tazeliği</h3><span className="badge badge-live">CANLI</span></div>
            <div className="stream-grid">
              {liveStream.exchange_freshness.map((ef, i) => {
                const age = sn(ef.age_seconds, 999); const fresh = age < 10 ? 'sf' : age < 60 ? 'ss' : 'sd';
                return <div key={i} className={cls("stream-card", fresh)}><div className="stream-name">{ef.exchange?.toUpperCase()} {ef.market_type}</div><div className="stream-age">{age.toFixed(0)}sn önce</div><div className="t-m text-xs">{ef.trades_5min ?? 0} trade/5dk</div></div>;
              })}
            </div>
          </div>}

          {auditValidation && <div className="card"><div className="card-h"><h3>Veri Denetimi</h3><span className={cls("badge", auditValidation.status === 'ok' ? 'badge-g' : auditValidation.status === 'error' ? 'badge-r' : 'badge-w')}>{auditValidation.status === 'ok' ? '✓ Sorun Yok' : `${auditValidation.total_issues} Sorun`}</span></div>
            {auditValidation.issues?.length > 0 ? <div className="tbl-wrap"><table className="tbl"><thead><tr><th>Alan</th><th>Sorun</th><th>Önem</th><th>Adet</th></tr></thead><tbody>
              {auditValidation.issues.map((iss: R, i: number) => <tr key={i}><td className="mono" style={{ fontSize: 11 }}>{iss.field}</td><td>{iss.issue}</td><td><span className={cls("audit-badge", iss.severity === 'error' ? 'audit-err' : 'audit-warn')}>{iss.severity === 'error' ? 'Hata' : 'Uyarı'}</span></td><td>{iss.count}</td></tr>)}
            </tbody></table></div> : <div className="empty" style={{ padding: 16 }}>✅ Tüm veriler tutarlı</div>}
            <div className="t-m" style={{ padding: '8px 16px', fontSize: 11 }}>Son kontrol: {auditValidation.timestamp ? fmtDT(auditValidation.timestamp) : '—'}</div>
          </div>}

          <div className="card"><div className="card-h"><h3>İzleme Listesi</h3><span className="badge badge-b">{watchlist.length}</span></div>
            <div className="wl-form">
              <input placeholder="Sembol (BTCUSDT)" value={wlSym} onChange={e => setWlSym(e.target.value)} onKeyDown={e => e.key === 'Enter' && addWL()} />
              <select value={wlEx} onChange={e => setWlEx(e.target.value)}><option value="all">Tümü</option><option value="binance">Binance</option><option value="bybit">Bybit</option></select>
              <select value={wlMT} onChange={e => setWlMT(e.target.value)}><option value="spot">Spot</option><option value="futures">Futures</option></select>
              <button className="btn btn-primary" onClick={addWL}>+ Ekle</button>
            </div>
            {watchlist.length > 0 && <div className="wl-list">{watchlist.map(w => <div key={w.id} className="wl-item"><span className="wl-sym">{w.symbol}</span><span className="badge badge-sm">{w.exchange}</span><span className="badge badge-sm badge-b">{w.market_type}</span><button className="wl-rm" onClick={() => rmWL(w)}>✕</button></div>)}</div>}
          </div>

          {tableStats.length > 0 && <div className="card"><div className="card-h"><h3>Tablo İstatistikleri</h3></div>
            <div className="stat-grid">{tableStats.map((t, i) => <div key={i} className="stat-card"><div className="stat-name">{t.table_name}</div><div className="stat-val">{fmt(t.row_count, 0)}</div></div>)}</div>
          </div>}

          <div className="card"><div className="card-h"><h3>Zaman Bilgisi</h3></div>
            <div className="info-list"><div><span className="t-m">İlk Trade:</span> {sysStats?.oldest_trade ? new Date(sysStats.oldest_trade).toLocaleString('tr-TR') : '—'}</div><div><span className="t-m">Son Trade:</span> {sysStats?.newest_trade ? new Date(sysStats.newest_trade).toLocaleString('tr-TR') : '—'}</div><div><span className="t-m">Şimdi:</span> {new Date().toLocaleString('tr-TR')}</div></div>
          </div>

          {auditRec.length > 0 && <div className="card"><div className="card-h"><h3>Denetim Kayıtları</h3></div>
            <div className="tbl-wrap"><table className="tbl"><thead><tr><th>Zaman</th><th>Toplam</th><th>Başarılı</th><th>Başarısız</th><th>Başarı %</th></tr></thead><tbody>
              {auditRec.map((r, i) => <tr key={i}><td>{r.timestamp ? fmtDT(r.timestamp) : '—'}</td><td>{r.total_simulations ?? '—'}</td><td className="t-g">{r.successful_simulations ?? '—'}</td><td className="t-r">{r.failed_simulations ?? '—'}</td><td className={safeConf(r.success_rate) >= 50 ? "t-g" : "t-r"}>{r.success_rate != null ? `${safeConf(r.success_rate).toFixed(1)}%` : '—'}</td></tr>)}
            </tbody></table></div>
          </div>}
        </>}
      </main>
    </div>
  );
}

function KPI({ label, value, icon, accent }: { label: string; value: string; icon?: string; accent?: string }) {
  return <div className={cls("kpi", accent && `kpi-${accent}`)}>{icon && <span className="kpi-i">{icon}</span>}<div><div className="kpi-v">{value}</div><div className="kpi-l">{label}</div></div></div>;
}

const root = ReactDOM.createRoot(document.getElementById("root")!);
root.render(<ErrorBoundary><App /></ErrorBoundary>);
