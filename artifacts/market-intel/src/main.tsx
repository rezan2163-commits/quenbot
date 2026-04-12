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
type Tab = "overview" | "terminal" | "signals" | "simulations" | "brain" | "mastercontrol" | "system" | "chat";

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
const getSignalTargets = (s: R): R => {
  const meta = safeMeta(s.metadata);
  if (meta.targets && typeof meta.targets === 'object') return meta.targets;
  const price = sn(s.price);
  const dir = getDir(s) || 'long';
  const basePct = Math.max(sn(meta.target_pct, 0.02), 0.01);
  const plan: Array<[string, number, number]> = [['15m', 15, 1.0], ['1h', 60, 1.6], ['4h', 240, 2.4], ['8h', 480, 3.0], ['1d', 1440, 3.6]];
  const out: R = {};
  for (const [k, m, mul] of plan) {
    const pct = Math.min(basePct * mul, 0.20);
    out[k] = { minutes: m, target_pct: pct, target_price: dir === 'long' ? price * (1 + pct) : price * (1 - pct) };
  }
  return out;
};
const sigLabel = (t: string): string => { if (!t) return '—'; const m: R = { evolutionary_similarity: 'Evrimsel Benzerlik', momentum: 'Momentum', brain_pattern: 'Beyin Örüntüsü', price_action: 'Fiyat Hareketi', signature: 'İmza', historical_signature: 'Tarihsel İmza', intel: 'İstihbarat' }; for (const [k, v] of Object.entries(m)) { if (t.includes(k)) return v as string; } return t.replace(/_/g, ' '); };
const classifyAgent = (st: string): string => { const t = (st || '').toLowerCase(); if (t.startsWith('signature_')) return 'scout'; if (t.startsWith('intel_') || t.startsWith('evolutionary_') || t.startsWith('momentum_') || t.startsWith('price_action_')) return 'strategist'; if (t.startsWith('brain_')) return 'brain'; return 'strategist'; };
const AGENT_LABELS_TR: R = { scout: 'Keşifçi', strategist: 'Stratejist', ghost_simulator: 'Test Simülatörü', auditor: 'Denetçi', brain: 'Beyin', pattern_matcher: 'Örüntü Eşleştirici', chat_engine: 'Sohbet Motoru', llm_brain: 'Yapay Zeka Omurgası' };
const stInfo = (st: string) => { if (st === 'pending') return { l: 'Bekliyor', c: 'badge-w' }; if (st === 'processed') return { l: 'İşlendi', c: 'badge-g' }; if (st?.startsWith('risk_')) return { l: 'Risk Red', c: 'badge-r' }; if (st?.startsWith('filtered')) return { l: 'Filtrelendi', c: 'badge-w' }; return { l: st || '—', c: '' }; };

const API_BASE = (import.meta as any).env?.VITE_API_BASE_URL || `${window.location.protocol}//${window.location.hostname}:3001`;

const apiFetch = async (url: string, timeoutMs = 2500) => {
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), timeoutMs);
  try {
    const fullUrl = url.startsWith("http") ? url : `${API_BASE}${url}`;
    const r = await fetch(fullUrl, { signal: ctrl.signal });
    return r.ok ? r.json() : null;
  } catch {
    return null;
  } finally {
    clearTimeout(timer);
  }
};

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

/* CandleChart kaldırıldı — kullanıcı isteği */

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
  const [sysResources, setSysResources] = useState<R | null>(null);
  // Signal filters
  const [sigFilter, setSigFilter] = useState<'all' | 'pending' | 'processed' | 'rejected'>('all');
  const [sigDir, setSigDir] = useState<'all' | 'long' | 'short'>('all');
  const [sigMkt, setSigMkt] = useState<'all' | 'spot' | 'futures'>('all');
  const [simDir, setSimDir] = useState<'all' | 'long' | 'short'>('all');
  const [agentSignalStats, setAgentSignalStats] = useState<R[]>([]);
  const [selectedAgent, setSelectedAgent] = useState('scout');
  const [agentSignals, setAgentSignals] = useState<R[]>([]);
  const [simAnalysis, setSimAnalysis] = useState<R[]>([]);
  const [expandedSigId, setExpandedSigId] = useState<number | null>(null);
  const [dataFolders, setDataFolders] = useState<R | null>(null);
  const [modelCoord, setModelCoord] = useState<R | null>(null);
  const [gemmaFeed, setGemmaFeed] = useState<R[]>([]);
  // Position calculator
  const [calcEntry, setCalcEntry] = useState(""); const [calcSL, setCalcSL] = useState(""); const [calcRisk, setCalcRisk] = useState("100");
  // Chat
  const [chatMsg, setChatMsg] = useState(""); const [chatHistory, setChatHistory] = useState<Array<{role: string, message: string}>>([]); const [chatLoading, setChatLoading] = useState(false);
  const [initialLoading, setInitialLoading] = useState(true);

  const prevPrices = useRef<Record<string, number>>({});

  /* ── Tiered Fetching ── */
  const fetchFast = useCallback(async () => {
    const [s, p, tm] = await Promise.all([
      apiFetch("/api/dashboard/summary", 1800),
      apiFetch("/api/live/prices", 1800),
      apiFetch("/api/analytics/top-movers", 1800),
    ]);
    if (s) setSummary(s);
    if (p) { prevPrices.current = Object.fromEntries(prices.map(x => [x.symbol, x.price])); setPrices(p); }
    if (tm) setMovers(tm);
    setLastUp(new Date());
  }, [prices]);

  const fetchMed = useCallback(async () => {
    if (tab === "overview") {
      const [b, of, tl, vd, sigSum, gf] = await Promise.all([
        apiFetch("/api/bot/summary", 2200),
        apiFetch("/api/analytics/order-flow", 2200),
        apiFetch("/api/analytics/trade-timeline", 2200),
        apiFetch("/api/analytics/volume-by-exchange", 2200),
        apiFetch("/api/signals/summary", 2200),
        apiFetch("/api/gemma/activity-feed", 2500),
      ]);
      if (b) setBotSum(b);
      if (of) setOrderFlow(of);
      if (tl) setTimeline(tl);
      if (vd) setVolumes(vd);
      if (sigSum) setSigSummary(Array.isArray(sigSum) ? sigSum : (sigSum?.by_type ?? []));
      if (gf?.feed) setGemmaFeed(gf.feed);
      return;
    }

    if (tab === "terminal") {
      const [tr, mv, ch, ls, agS] = await Promise.all([
        apiFetch("/api/scout/trades?limit=30", 2200),
        apiFetch("/api/scout/movements?limit=20", 2200),
        apiFetch(`/api/analytics/price-history/${selSym}`, 2200),
        apiFetch("/api/live/data-stream", 2200),
        apiFetch("/api/agents/status", 2200),
      ]);
      if (tr) setTrades(tr);
      if (mv) setMovements(mv);
      if (ch) setCandles(ch);
      if (ls) setLiveStream(ls);
      if (agS?.agents) setAgentSt(agS.agents);
      return;
    }

    if (tab === "signals") {
      const [sig, sigSum] = await Promise.all([
        apiFetch("/api/signals", 2200),
        apiFetch("/api/signals/summary", 2200),
      ]);
      if (sig) setSignals(sig);
      if (sigSum) setSigSummary(Array.isArray(sigSum) ? sigSum : (sigSum?.by_type ?? []));
      return;
    }

    if (tab === "simulations") {
      const [sim, pt, sa] = await Promise.all([
        apiFetch(`/api/simulations?side=${simDir}&limit=200`, 2200),
        apiFetch("/api/analytics/pnl-timeline", 2200),
        apiFetch(`/api/simulations/analysis?side=${simDir}&limit=120`, 2200),
      ]);
      if (sim) setSims(sim);
      if (pt) setPnlTL(pt);
      if (sa) setSimAnalysis(Array.isArray(sa) ? sa : []);
      return;
    }

    if (tab === "system") {
      const [agS, wl] = await Promise.all([
        apiFetch("/api/agents/status", 2200),
        apiFetch("/api/watchlist", 2200),
      ]);
      if (agS?.agents) setAgentSt(agS.agents);
      if (wl) setWatchlist(wl);
      return;
    }
  }, [selSym, tab]);

  const fetchSlow = useCallback(async () => {
    if (tab === "overview") {
      const ss = await apiFetch("/api/analytics/system-stats", 2800);
      if (ss) setSysStats(ss);
      return;
    }

    if (tab === "brain") {
      const [bs, bp, lst, sig2] = await Promise.all([
        apiFetch("/api/brain/status", 2800),
        apiFetch("/api/brain/patterns?limit=30", 2800),
        apiFetch("/api/brain/learning-stats", 2800),
        apiFetch("/api/signatures?limit=20", 2800),
      ]);
      const [ass, asg] = await Promise.all([
        apiFetch("/api/agents/signal-stats", 2800),
        apiFetch(`/api/agents/${selectedAgent}/signals?limit=120`, 3200),
      ]);
      if (bs) setBrainSt(bs);
      if (bp) setBrainPat(bp);
      if (lst) setLearnSt(lst);
      if (sig2) setSignatures(Array.isArray(sig2) ? sig2 : []);
      if (ass) setAgentSignalStats(Array.isArray(ass) ? ass : []);
      if (asg) setAgentSignals(Array.isArray(asg) ? asg : []);
      return;
    }

    if (tab === "mastercontrol") {
      const [dr, lr, qr, av, sr] = await Promise.all([
        apiFetch("/api/directives", 2800),
        apiFetch("/api/llm/status", 2800),
        apiFetch("/api/llm/queue", 2800),
        apiFetch("/api/audit/validate", 2800),
        apiFetch("/api/system/resources", 5000),
      ]);
      if (dr?.master_directive !== undefined && !dirSaving) setMasterDir(dr.master_directive);
      if (lr) setLlmSt(lr);
      if (qr) setQueueSt(qr);
      if (av) setAuditValidation(av);
      if (sr && !sr.error) setSysResources(sr);
      return;
    }

    if (tab === "system") {
      const [ts, ar, fa, rr, rs, cor, sr, df, mc] = await Promise.all([
        apiFetch("/api/admin/table-stats", 2800),
        apiFetch("/api/admin/audit-records?limit=20", 2800),
        apiFetch("/api/admin/failure-analysis?limit=20", 2800),
        apiFetch("/api/rca/results", 2800),
        apiFetch("/api/rca/stats", 2800),
        apiFetch("/api/corrections", 2800),
        apiFetch("/api/system/resources", 5000),
        apiFetch("/api/system/data-folders", 5000),
        apiFetch("/api/system/model-koordinasyon", 5000),
      ]);
      if (ts) setTableStats(ts);
      if (ar) setAuditRec(ar);
      if (fa) setFailAn(fa);
      if (rr) setRcaResults(Array.isArray(rr) ? rr : []);
      if (rs) setRcaStats(Array.isArray(rs) ? rs : (rs?.distribution ?? []));
      if (cor) setCorrections(Array.isArray(cor) ? cor : []);
      if (sr && !sr.error) setSysResources(sr);
      if (df && !df.error) setDataFolders(df);
      if (mc && !mc.error) setModelCoord(mc);
      return;
    }
  }, [dirSaving, selectedAgent, simDir, tab]);

  useEffect(() => {
    // First paint should not wait too long even if API is slow.
    const loadingTimeout = setTimeout(() => setInitialLoading(false), 1200);

    // Stagger initial loads: fast first, then medium, then slow.
    fetchFast().finally(() => setInitialLoading(false));
    const t1 = setTimeout(() => fetchMed(), 250);
    const t2 = setTimeout(() => fetchSlow(), 900);

    // Poll less aggressively to reduce UI and network pressure.
    const f1 = setInterval(fetchFast, 5000);
    const f2 = setInterval(fetchMed, 15000);
    const f3 = setInterval(fetchSlow, 60000);

    return () => {
      clearTimeout(loadingTimeout);
      clearInterval(f1);
      clearInterval(f2);
      clearInterval(f3);
      clearTimeout(t1);
      clearTimeout(t2);
    };
  }, [selSym, tab]);

  /* ── Derived ── */
  const totalVol = useMemo(() => volumes.reduce((s, v) => s + (v.total_volume || 0), 0), [volumes]);
  const openSims = useMemo(() => sims.filter(s => s.status === 'open' && (simDir === 'all' || s.side === simDir)), [sims, simDir]);
  const closedSims = useMemo(() => sims.filter(s => s.status === 'closed' && (simDir === 'all' || s.side === simDir)), [sims, simDir]);

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

  const handleChat = async () => {
    if (!chatMsg.trim()) return;
    setChatLoading(true);
    const userMsg = chatMsg;
    setChatHistory(prev => [...prev, { role: 'user', message: userMsg }]);
    setChatMsg("");
    
    try {
      const chatCtrl = new AbortController();
      const chatTimer = setTimeout(() => chatCtrl.abort(), 90000);
      const res = await fetch(`${API_BASE}/api/chat`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ message: userMsg }),
        signal: chatCtrl.signal,
      });
      clearTimeout(chatTimer);
      const data = await res.json();
      setChatHistory(prev => [...prev, { role: 'gemma', message: data.message || "Gemma yanıt veremedi" }]);
    } catch (e) {
      setChatHistory(prev => [...prev, { role: 'system', message: "❌ Chat bağlantısı hatası" }]);
    } finally {
      setChatLoading(false);
    }
  };

  const activeAgents = Object.values(agentSt).filter((a: any) => a.status === 'running').length;
  const totalAgents = Math.max(Object.keys(agentSt).length, 5);

  const TABS: { key: Tab; label: string; icon: string }[] = [
    { key: "overview", label: "Genel Bakış", icon: "📊" },
    { key: "signals", label: "Sinyaller", icon: "📡" },
    { key: "simulations", label: "Simülasyonlar", icon: "👻" },
    { key: "brain", label: "Yapay Zeka Beyin", icon: "🧠" },
    { key: "mastercontrol", label: "Kontrol", icon: "🎯" },
    { key: "system", label: "Sistem", icon: "⚙️" },
    { key: "chat", label: "Sohbet", icon: "💬" },
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
                {t.key === 'signals' && Number(summary?.active_signals || 0) > 0 && <span className="tab-badge">{Number(summary?.active_signals || 0)}</span>}
                {t.key === 'simulations' && Number(summary?.open_simulations || 0) > 0 && <span className="tab-badge">{Number(summary?.open_simulations || 0)}</span>}
              </button>
            ))}
          </nav>
        </div>
        <div className="topnav-right">
          {llmSt && <div className="nav-stat"><span className={cls("dot-live", !llmSt.healthy && "dot-warn")} /><span>{llmSt.system_mode === 'degraded' ? 'Kısıtlı' : llmSt.healthy ? 'Yapay Zeka Aktif' : 'Yapay Zeka Kapalı'}</span></div>}
          <div className="nav-stat"><span className="dot-live" /><span>{activeAgents}/{totalAgents} Ajan</span></div>
          {sysResources && <div className="nav-stat">{`Bellek %${fmt(sysResources.ram_percent ?? 0, 0)}`}</div>}
          <div className="nav-stat">{sysStats ? `${fmt(sysStats.trades_per_minute, 0)} i/dk` : "—"}</div>
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
              <div key={p.symbol} className={cls("ticker-item", dir && `tk-${dir}`)} onClick={() => { setSelSym(p.symbol); setTab("signals"); }}>
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
            <KPI label="Toplam İşlem" value={summary ? fmt(summary.total_trades, 0) : "—"} icon="💹" />
            <KPI label="İşlem/dk" value={sysStats ? fmt(sysStats.trades_per_minute, 0) : "—"} icon="⚡" accent="b" />
            <KPI label="Hacim (1sa)" value={`$${fmt(totalVol, 0)}`} icon="📊" accent="p" />
            <KPI label="Aktif Sinyal" value={summary ? String(summary.active_signals) : "0"} icon="📡" accent={summary && summary.active_signals > 0 ? "w" : undefined} />
            <KPI label="Açık Sim." value={String(Number(summary?.open_simulations ?? openSims.length))} icon="👻" accent={Number(summary?.open_simulations ?? openSims.length) > 0 ? "g" : undefined} />
            <KPI label="Kazanç Oranı" value={botSum ? `${fmt(botSum.win_rate, 1)}%` : "0%"} icon="🏆" accent={botSum && botSum.win_rate >= 50 ? "g" : "r"} />
            <KPI label="Toplam K/Z" value={summary ? fmtUsd(summary.total_pnl) : "$0"} icon="💰" accent={summary && summary.total_pnl > 0 ? "g" : summary && summary.total_pnl < 0 ? "r" : undefined} />
          </div>

          <div className="g2">
            <div className="card"><div className="card-h"><h3>Hacim Trendi (60dk)</h3><span className="badge badge-b">{fmt(timeline.reduce((s, t) => s + t.volume, 0), 0)} USD</span></div><MiniBar data={timeline.map(t => t.volume || 0)} h={64} color="var(--c)" /></div>
            <div className="card"><div className="card-h"><h3>İşlem Sayısı (60dk)</h3><span className="badge badge-g">{fmt(timeline.reduce((s, t) => s + t.count, 0), 0)}</span></div><MiniBar data={timeline.map(t => t.count || 0)} h={64} color="var(--g)" /></div>
          </div>

          <div className="g2">
            <div className="card"><div className="card-h"><h3>Fiyatlar</h3><span className="badge badge-live">CANLI</span></div>
              <div className="price-grid">
                {prices.map(p => {
                  const prev = prevPrices.current[p.symbol]; const dir = prev ? (p.price > prev ? "up" : p.price < prev ? "dn" : "") : "";
                  const mv = movers.find(m => m.symbol === p.symbol); const chg = mv?.change_pct ?? 0;
                  return (
                    <div key={p.symbol} className={cls("ptile", dir && `pf-${dir}`)} onClick={() => { setSelSym(p.symbol); setTab("signals"); }}>
                      <div className="ptile-top"><span className="ptile-sym">{p.symbol.replace("USDT", "")}<span className="t-m">/USDT</span></span><span className={cls("chg-badge", chg >= 0 ? "chg-up" : "chg-dn")}>{fmtPct(chg)}</span></div>
                      <div className="ptile-price">{fmtUsd(p.price)}</div>
                      <div className="ptile-meta"><span>{p.exchange}</span><span>{fmtTime(p.timestamp)}</span></div>
                    </div>
                  );
                })}
              </div>
            </div>

            <div className="card gemma-terminal-card"><div className="card-h"><h3>🧠 Gemma Ajan Terminali</h3><span className="badge badge-live">CANLI</span></div>
              <div className="gemma-terminal">
                {gemmaFeed.length > 0 ? gemmaFeed.slice(0, 30).map((line, i) => {
                  const ts = line.ts ? new Date(line.ts * 1000) : null;
                  const timeStr = ts ? ts.toLocaleTimeString('tr-TR', { hour: '2-digit', minute: '2-digit', second: '2-digit' }) : '';
                  return (
                    <div key={i} className={cls("gt-line", `gt-${line.level || 'info'}`)}>
                      <span className="gt-time">{timeStr}</span>
                      <span className="gt-text">{line.text}</span>
                    </div>
                  );
                }) : (
                  <div className="gt-line gt-info"><span className="gt-time">--:--:--</span><span className="gt-text">⏳ Gemma ajan aktivitesi bekleniyor...</span></div>
                )}
              </div>
            </div>
          </div>

          <div className="card"><div className="card-h"><h3>Borsa Bazında Hacim</h3></div>
            <div className="ex-grid">
              {volumes.map((v, i) => (<div key={i} className="ex-card"><div className="ex-name">{v.exchange.toUpperCase()} <span className="badge badge-sm">{v.market_type}</span></div><div className="ex-vol">${fmt(v.total_volume, 0)}</div><div className="t-m text-xs">{fmt(v.trade_count, 0)} işlem</div></div>))}
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

          {pnlTL.length > 0 && <div className="card"><div className="card-h"><h3>Kümülatif Kar/Zarar</h3></div><MiniBar data={pnlTL.map(p => p.cumulative_pnl ?? 0)} h={64} color={pnlTL.length > 0 && (pnlTL[pnlTL.length - 1]?.cumulative_pnl ?? 0) >= 0 ? "var(--g)" : "var(--r)"} /></div>}
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
              {/* Grafik bileşeni kaldırıldı */}
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
              <thead><tr><th>Coin</th><th>Yön</th><th>Veriliş</th><th>Giriş Fiyatı</th><th>Strateji</th><th>Güven</th><th>15dk Hedef</th><th>1sa Hedef</th><th>4sa Hedef</th><th>8sa Hedef</th><th>1g Hedef</th><th>Tahmini Süre</th><th>Piyasa</th><th>Durum</th></tr></thead>
              <tbody>
                {filteredSignals.slice(0, 40).map((s, i) => {
                  const m = safeMeta(s.metadata), dir = getDir(s), conf = safeConf(s.confidence), price = sn(s.price);
                  const targets = getSignalTargets(s);
                  const t15 = targets['15m'];
                  const t1h = targets['1h'];
                  const t4h = targets['4h'];
                  const t8h = targets['8h'];
                  const t1d = targets['1d'];
                  const primaryMinutes = sn(m.primary_target_minutes, sn(t15?.minutes, 15));
                  const mkt = (m.market_type || s.market_type || 'spot').toUpperCase();
                  const si = stInfo(s.status);
                  const isExp = expandedSigId === (s.id || i);
                  const lev = sn(s.leverage_x || m.leverage_x || m.leverage || 1, 1);
                  const aciklama = s.aciklama_tr || '';
                  const benzerlik = s.benzerlik_etiketi || null;
                  const benzerOrnek = s.benzer_ornek || null;
                  return (
                    <React.Fragment key={s.id || i}>
                    <tr className={cls(dir === 'long' ? 'row-l' : dir === 'short' ? 'row-s' : '', isExp && 'row-expanded')} onClick={() => setExpandedSigId(isExp ? null : (s.id || i))} style={{ cursor: 'pointer' }}>
                      <td><div className="coin-cell"><span className="coin-dot" style={{ background: dir === 'long' ? 'var(--g)' : dir === 'short' ? 'var(--r)' : 'var(--m)' }} /><strong>{(s.symbol || '').replace('USDT', '')}</strong><span className="t-m">/USDT</span></div></td>
                      <td><span className={cls("dir-badge", dir === "long" ? "dir-l" : dir === "short" ? "dir-s" : "dir-n")}>{dir === 'long' ? 'LONG' : dir === 'short' ? 'SHORT' : '—'}</span></td>
                      <td className="t-m">{fmtDT(s.timestamp || s.created_at || '')}</td>
                      <td className="mono">{price > 0 ? fmtUsd(price) : '—'}</td>
                      <td><span className="strat-badge">{sigLabel(s.signal_type)}</span></td>
                      <td><div className="conf-cell"><div className="conf-bar"><div className="conf-fill" style={{ width: `${Math.min(conf, 100)}%`, background: conf >= 70 ? 'var(--g)' : conf >= 50 ? 'var(--w)' : 'var(--r)' }} /></div><span className="conf-txt">{conf.toFixed(0)}%</span></div></td>
                      <td className="t-g mono">{t15?.target_price ? fmtUsd(t15.target_price) : '—'}</td>
                      <td className="t-g mono">{t1h?.target_price ? fmtUsd(t1h.target_price) : '—'}</td>
                      <td className="t-g mono">{t4h?.target_price ? fmtUsd(t4h.target_price) : '—'}</td>
                      <td className="t-g mono">{t8h?.target_price ? fmtUsd(t8h.target_price) : '—'}</td>
                      <td className="t-g mono">{t1d?.target_price ? fmtUsd(t1d.target_price) : '—'}</td>
                      <td className="mono">{primaryMinutes >= 1440 ? `${Math.round(primaryMinutes / 1440)}g` : primaryMinutes >= 60 ? `${Math.round(primaryMinutes / 60)}sa` : `${primaryMinutes}dk`}</td>
                      <td><span className="badge badge-sm">{mkt}</span></td>
                      <td><span className={cls("badge badge-sm", si.c)}>{si.l}</span></td>
                    </tr>
                    {isExp && <tr className="expanded-detail-row"><td colSpan={14}>
                      <div className="sig-detail-panel">
                        <div className="sig-detail-grid">
                          <div className="sig-detail-section">
                            <h4>Sinyal Açıklaması (Türkçe)</h4>
                            <p>{aciklama || 'Bu sinyal, piyasa verilerindeki fiyat hareketleri ve hacim örüntüleri analiz edilerek üretilmiştir.'}</p>
                            {m.trend && <p><strong>Piyasa Eğilimi:</strong> {String(m.trend) === 'up' ? 'Yükseliş' : String(m.trend) === 'down' ? 'Düşüş' : String(m.trend)}</p>}
                            {sn(m.rsi) > 0 && <p><strong>Göreceli Güç Endeksi (RSI):</strong> {sn(m.rsi).toFixed(1)}</p>}
                            {sn(m.cosine_similarity || m.avg_similarity || m.similarity_score) > 0 && <p><strong>Benzerlik Puanı:</strong> {sn(m.cosine_similarity || m.avg_similarity || m.similarity_score).toFixed(4)}</p>}
                          </div>
                          <div className="sig-detail-section">
                            <h4>Pozisyon Detayları</h4>
                            <div className="detail-row"><span>Yön:</span><span className={dir === 'long' ? 't-g' : 't-r'}>{dir === 'long' ? 'Uzun (Long)' : 'Kısa (Short)'}</span></div>
                            <div className="detail-row"><span>Kaldıraç:</span><span className="mono">{lev.toFixed(1)}x</span></div>
                            <div className="detail-row"><span>Giriş Fiyatı:</span><span className="mono">{fmtUsd(price)}</span></div>
                            <div className="detail-row"><span>Ajan:</span><span>{s.agent || classifyAgent(s.signal_type)}</span></div>
                            <div className="detail-row"><span>Zaman Dilimi:</span><span>{m.primary_timeframe || m.timeframe || '15dk'}</span></div>
                            <div className="detail-row"><span>Piyasa:</span><span>{mkt}</span></div>
                          </div>
                          <div className="sig-detail-section">
                            <h4>Hedef Fiyatları</h4>
                            {[['15 Dakika', t15], ['1 Saat', t1h], ['4 Saat', t4h], ['8 Saat', t8h], ['1 Gün', t1d]].map(([label, t]: any) => t?.target_price ? (
                              <div key={label} className="detail-row"><span>{label}:</span><span className="mono t-g">{fmtUsd(t.target_price)} <span className="t-m">(%{(sn(t.target_pct) * 100).toFixed(2)})</span></span></div>
                            ) : null)}
                          </div>
                          {benzerlik && <div className="sig-detail-section">
                            <h4>Benzerlik Etiketi</h4>
                            <span className="badge badge-p">{benzerlik}</span>
                            {benzerOrnek && <div style={{ marginTop: 8 }}>
                              <div className="detail-row"><span>Önceki Görülme:</span><span>{benzerOrnek.sembol} - {benzerOrnek.yon === 'long' ? 'Yükseliş' : 'Düşüş'}</span></div>
                              <div className="detail-row"><span>Zaman Dilimi:</span><span>{benzerOrnek.zaman_dilimi}</span></div>
                              <div className="detail-row"><span>Görülme Zamanı:</span><span>{benzerOrnek.gorulme_zamani ? fmtDT(benzerOrnek.gorulme_zamani) : '—'}</span></div>
                              <div className="detail-row"><span>Sonraki Hareket:</span><span className={sn(benzerOrnek.sonraki_hareket_yuzde) >= 0 ? 't-g' : 't-r'}>%{sn(benzerOrnek.sonraki_hareket_yuzde).toFixed(2)}</span></div>
                            </div>}
                          </div>}
                        </div>
                      </div>
                    </td></tr>}
                    </React.Fragment>
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

          <div className="filter-bar" style={{ marginBottom: 10 }}>
            <div className="filter-group">
              {(['all', 'long', 'short'] as const).map(d => <button key={d} className={cls("fbtn", simDir === d && "fbtn-a")} onClick={() => setSimDir(d)}>{d === 'all' ? 'Tüm Yönler' : d === 'long' ? '↑ Long Pozisyonlar' : '↓ Short Pozisyonlar'}</button>)}
            </div>
          </div>

          <div className="kpi-row">
            <KPI label="Toplam" value={botSum ? String(botSum.total_simulations) : "0"} icon="📊" />
            <KPI label="Açık" value={String(openSims.length)} icon="🟢" accent="g" />
            <KPI label="Kapalı" value={String(closedSims.length)} icon="📕" />
            <KPI label="Kazanan" value={botSum ? String(botSum.wins) : "0"} icon="✅" accent="g" />
            <KPI label="Kaybeden" value={botSum ? String(botSum.losses) : "0"} icon="❌" accent="r" />
            <KPI label="Kazanç Oranı" value={botSum ? `${fmt(botSum.win_rate, 1)}%` : "0%"} icon="🏆" accent={botSum && botSum.win_rate >= 50 ? "g" : "r"} />
            <KPI label="Ort. K/Z" value={botSum ? `${fmt(botSum.average_pnl_pct, 2)}%` : "0%"} icon="💱" accent={botSum && botSum.average_pnl_pct > 0 ? "g" : "r"} />
          </div>

          <div className="g2">
            <div className="card card-center"><h3>Kazanç Oranı</h3><Ring value={sn(botSum?.win_rate)} size={130} stroke={10} color={botSum && botSum.win_rate >= 50 ? "var(--g)" : "var(--r)"} /><div className="t-m" style={{ marginTop: 12 }}>{botSum?.wins ?? 0} Kazanç / {botSum?.losses ?? 0} Kayıp</div></div>
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
                      <div><span className="t-m">Kaldıraç:</span> <span className="mono">{sn(s.leverage_x, 1).toFixed(1)}x</span></div>
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

          <div className="card"><div className="card-h"><h3>Simülasyon Geçmişi</h3><span className="badge">{openSims.length + closedSims.length}</span></div>
            <div className="tbl-wrap"><table className="tbl"><thead><tr><th>Coin</th><th>Yön</th><th>Giriş</th><th>Kaldıraç</th><th>Hedef</th><th>Çıkış</th><th>K/Z</th><th>K/Z %</th><th>Güven</th><th>Giriş Zamanı</th><th>Çıkış Zamanı</th><th>Durum</th></tr></thead><tbody>
              {[...openSims, ...closedSims].slice(0, 40).map((s, i) => {
                const pnl = sn(s.pnl), pnlP = sn(s.pnl_pct), tp = sn(s.take_profit_price || s.target_price), conf = safeConf(s.confidence);
                return <tr key={s.id || i}><td><strong>{(s.symbol || '').replace('USDT', '')}</strong></td><td><span className={cls("dir-sm", s.side === 'long' ? "dir-l" : "dir-s")}>{s.side === 'long' ? 'LONG' : 'SHORT'}</span></td><td className="mono">{fmtUsd(sn(s.entry_price))}</td><td className="mono">{sn(s.leverage_x, 1).toFixed(1)}x</td><td className="mono">{tp > 0 ? fmtUsd(tp) : '—'}</td><td className="mono">{s.exit_price ? fmtUsd(sn(s.exit_price)) : '—'}</td><td className={pnl >= 0 ? "t-g" : "t-r"} style={{ fontWeight: 700 }}>{s.pnl != null ? `${pnl >= 0 ? '+' : ''}${fmtUsd(pnl)}` : '—'}</td><td className={pnlP >= 0 ? "t-g" : "t-r"} style={{ fontWeight: 700 }}>{s.pnl_pct != null ? `${pnlP >= 0 ? '+' : ''}${pnlP.toFixed(2)}%` : '—'}</td><td className="mono">{conf > 0 ? `${conf.toFixed(0)}%` : '—'}</td><td className="t-m">{s.entry_time ? fmtDT(s.entry_time) : '—'}</td><td className="t-m">{s.exit_time ? fmtDT(s.exit_time) : '—'}</td><td><span className={cls("badge badge-sm", s.status === 'open' ? 'badge-g' : s.status === 'closed' ? 'badge-b' : '')}>{s.status === 'open' ? 'Açık' : s.status === 'closed' ? 'Kapalı' : s.status}</span></td></tr>;
              })}</tbody></table>
              {(openSims.length + closedSims.length) === 0 && <div className="empty">Henüz simülasyon yok</div>}
            </div>
          </div>

          {simAnalysis.length > 0 && <div className="card"><div className="card-h"><h3>Pozisyon Sonrası Neden Analizi</h3><span className="badge badge-b">{simAnalysis.length}</span></div>
            <div className="tbl-wrap"><table className="tbl"><thead><tr><th>Coin</th><th>Yön</th><th>Kaldıraç</th><th>K/Z %</th><th>Sonuç Açıklaması</th><th>Ders Notu</th><th>RCA Detayı</th></tr></thead><tbody>
              {simAnalysis.slice(0, 40).map((r, i) => <tr key={i}><td><strong>{(r.symbol || '').replace('USDT', '')}</strong></td><td>{r.side === 'long' ? 'LONG' : 'SHORT'}</td><td className="mono">{sn(r.leverage_x, 1).toFixed(1)}x</td><td className={sn(r.pnl_pct) >= 0 ? 't-g' : 't-r'}>{sn(r.pnl_pct) >= 0 ? '+' : ''}{sn(r.pnl_pct).toFixed(2)}%</td><td>{r.sonuc_aciklamasi_tr || '—'}</td><td>{r.ders_notu_tr || '—'}</td><td>{r.rca_aciklamasi_tr || '—'}</td></tr>)}
            </tbody></table></div>
          </div>}

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
          <div className="section-header"><h2>Yapay Zeka Beyin Merkezi</h2><span className="badge badge-p">Öğrenme Sistemi</span></div>

          <div className="card"><div className="card-h"><h3>Ajan Koordinasyonu</h3><span className="badge badge-live">Canlı</span></div>
            <div className="agent-row">
              {[{ n: "Keşifçi", k: "scout", i: "🔍" }, { n: "Stratejist", k: "strategist", i: "📡" }, { n: "Test Sim.", k: "ghost_simulator", i: "👻" }, { n: "Denetçi", k: "auditor", i: "📋" }, { n: "Beyin", k: "brain", i: "🧠" }].map(a => {
                const d = agentSt[a.k]; const ok = d?.status === 'running'; const stale = d?.status === 'stale';
                const sigCount = agentSignalStats.find((x: R) => x.agent === a.k)?.signal_count ?? 0;
                return <div key={a.k} className={cls("agent-chip", ok ? "ac-ok" : stale ? "ac-warn" : "ac-off", selectedAgent === a.k && "ac-selected")} onClick={() => setSelectedAgent(a.k)} style={{ cursor: 'pointer' }}><span className="agent-icon">{a.i}</span><div><div className="agent-name">{a.n} <span className="badge badge-sm badge-b" style={{ marginLeft: 4 }}>{sigCount} sinyal</span></div><div className="agent-st">{ok ? '✓ Aktif' : stale ? '⚠ Yanıtlamıyor' : '— Bekleniyor'}</div></div></div>;
              })}
            </div>
          </div>

          {/* Seçili Ajanın Sinyalleri */}
          {agentSignals.length > 0 && <div className="card"><div className="card-h"><h3>{AGENT_LABELS_TR[selectedAgent] || selectedAgent} Sinyalleri</h3><span className="badge badge-b">{agentSignals.length}</span></div>
            <div className="tbl-wrap"><table className="tbl"><thead><tr><th>Coin</th><th>Yön</th><th>Kaldıraç</th><th>Giriş</th><th>Güven</th><th>Strateji</th><th>Zaman</th><th>Açıklama</th><th>Benzerlik</th></tr></thead><tbody>
              {agentSignals.slice(0, 30).map((s: R, i: number) => {
                const m = safeMeta(s.metadata); const dir = getDir(s); const conf = safeConf(s.confidence); const lev = sn(s.leverage_x, 1);
                return <tr key={s.id || i} className={dir === 'long' ? 'row-l' : dir === 'short' ? 'row-s' : ''}>
                  <td><strong>{(s.symbol || '').replace('USDT', '')}</strong></td>
                  <td><span className={cls("dir-badge", dir === 'long' ? "dir-l" : "dir-s")}>{dir === 'long' ? 'UZUN' : 'KISA'}</span></td>
                  <td className="mono">{lev.toFixed(1)}x</td>
                  <td className="mono">{fmtUsd(sn(s.price))}</td>
                  <td className="mono">{conf.toFixed(0)}%</td>
                  <td>{sigLabel(s.signal_type)}</td>
                  <td className="t-m">{fmtDT(s.timestamp || s.created_at)}</td>
                  <td style={{ maxWidth: 220, fontSize: 11 }}>{s.aciklama_tr || '—'}</td>
                  <td>{s.benzerlik_etiketi ? <span className="badge badge-sm badge-p">{s.benzerlik_etiketi}</span> : '—'}{s.benzer_ornek ? <div className="t-m" style={{ fontSize: 10, marginTop: 2 }}>{s.benzer_ornek.sembol} %{sn(s.benzer_ornek.sonraki_hareket_yuzde).toFixed(2)}</div> : null}</td>
                </tr>;
              })}
            </tbody></table></div>
            {agentSignals.length === 0 && <div className="empty">Bu ajandan sinyal üretilmemiş</div>}
          </div>}

          <div className="kpi-row">
            <KPI label="Örüntü" value={brainSt ? fmt(brainSt.pattern_count, 0) : "0"} icon="🧬" accent="p" />
            <KPI label="Doğruluk" value={brainSt ? `${sn(brainSt.learning.accuracy).toFixed(1)}%` : "0%"} icon="🎯" accent={brainSt && sn(brainSt.learning.accuracy) >= 50 ? "g" : "w"} />
            <KPI label="Toplam Öngörü" value={brainSt ? fmt(brainSt.learning.total, 0) : "0"} icon="📊" />
            <KPI label="Doğru" value={brainSt ? fmt(brainSt.learning.correct, 0) : "0"} icon="✅" accent="g" />
            <KPI label="Ort. K/Z" value={brainSt ? `${sn(brainSt.learning.avg_pnl).toFixed(2)}%` : "0%"} icon="💰" accent={brainSt && sn(brainSt.learning.avg_pnl) > 0 ? "g" : "r"} />
          </div>

          <div className="g2">
            <div className="card card-center"><h3>Öğrenme Doğruluğu</h3>
              {brainSt && sn(brainSt.learning.total) > 0 ? <>
                <Ring value={sn(brainSt.learning.accuracy)} size={140} stroke={12} color={sn(brainSt.learning.accuracy) >= 50 ? "var(--g)" : "var(--w)"} />
                <div className="t-m" style={{ marginTop: 16 }}>{brainSt.learning.correct} doğru / {brainSt.learning.total} toplam</div>
              </> : <div className="empty" style={{ padding: 20 }}><div style={{ fontSize: 48 }}>🧠</div><p><strong>Öğrenme Başlamadı</strong></p><p className="t-m text-xs">Örüntü: {brainSt?.pattern_count ?? 0}</p></div>}
            </div>
            <div className="card"><div className="card-h"><h3>Sinyal Tipi Başarıları</h3></div>
              {brainSt && brainSt.signal_type_stats.length > 0 ?
                <div className="tbl-wrap"><table className="tbl"><thead><tr><th>Tip</th><th>Toplam</th><th>Doğru</th><th>Başarı</th><th>Ort K/Z</th></tr></thead><tbody>
                  {brainSt.signal_type_stats.map((s, i) => <tr key={i}><td>{s.signal_type}</td><td>{s.total}</td><td>{s.correct}</td><td className={sn(s.total) > 0 && sn(s.correct) / sn(s.total) >= 0.5 ? "t-g" : "t-r"}>{sn(s.total) > 0 ? `${(sn(s.correct) / sn(s.total) * 100).toFixed(0)}%` : '—'}</td><td className={sn(s.avg_pnl) >= 0 ? "t-g" : "t-r"}>{sn(s.avg_pnl) >= 0 ? '+' : ''}{sn(s.avg_pnl).toFixed(2)}%</td></tr>)}
                </tbody></table></div>
                : <div className="empty">Sinyal istatistikleri oluşmadı</div>}
            </div>
          </div>

          {learnSt?.daily_accuracy && learnSt.daily_accuracy.length > 0 && <div className="card"><div className="card-h"><h3>Günlük Öğrenme Trendi</h3></div>
            <div className="tbl-wrap"><table className="tbl"><thead><tr><th>Tarih</th><th>Toplam</th><th>Doğru</th><th>Doğruluk</th></tr></thead><tbody>
              {learnSt.daily_accuracy.map((d: any, i: number) => { const acc = sn(d.total) > 0 ? (sn(d.correct) / sn(d.total) * 100) : 0; return <tr key={i}><td>{d.day ? new Date(d.day).toLocaleDateString("tr-TR") : '—'}</td><td>{d.total}</td><td>{d.correct}</td><td className={acc >= 50 ? "t-g" : "t-r"}>{acc.toFixed(1)}%</td></tr>; })}
            </tbody></table></div>
          </div>}

          <div className="card"><div className="card-h"><h3>Kayıtlı Örüntüler</h3><span className="badge badge-p">{brainPat.length}</span></div>
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
              {[["Veri Toplama", "Keşifçi ajan 4 borsadan gerçek zamanlı alım-satım verisi toplar"], ["Örüntü Tespiti", "Stratejist ajan 4 zaman diliminde fiyat örüntüsü bulur"], ["Eşleştirme", "Beyin modeli kosinüs benzerliği ile geçmiş verilerle karşılaştırır"], ["Simülasyon", "Test simülatörü en az %2 hedefle kağıt üzerinde pozisyon açar"], ["Geri Bildirim", "Sonuçlar Beyin modeline beslenir, doğruluk oranı artar"]].map(([t, d], i) =>
                <div key={i} className="step"><span className="step-n">{i + 1}</span><div><strong>{t}</strong><p className="t-m">{d}</p></div></div>
              )}
            </div>
          </div>
        </>}

        {/* ══════ MASTER CONTROL ══════ */}
        {tab === "mastercontrol" && <>
          <div className="section-header"><h2>Ana Kontrol</h2><span className="badge badge-p">Yapay Zeka Kontrolü</span></div>
          <div className="kpi-row">
            <KPI label="Yapay Zeka" value={llmSt?.healthy ? "Aktif" : "Kapalı"} icon="🧠" accent={llmSt?.healthy ? "g" : "r"} />
            <KPI label="İstek" value={llmSt?.call_count != null ? fmt(llmSt.call_count, 0) : "—"} icon="📡" accent="b" />
            <KPI label="Gecikme" value={llmSt?.llm_stats?.avg_latency_ms != null ? `${fmt(llmSt.llm_stats.avg_latency_ms, 0)}ms` : "—"} icon="⏱" />
            <KPI label="Kuyruk" value={queueSt?.queue_size != null ? fmt(queueSt.queue_size, 0) : "—"} icon="📋" accent="w" />
          </div>

          <div className="card"><div className="card-h"><h3>Kalıcı Yönergeler</h3><span className="badge badge-w">TÜM AJANLARA</span></div>
            <div style={{ padding: 20 }}>
              <p className="t-m text-xs" style={{ marginBottom: 12 }}>Her yapay zeka çağrısına ana sistem komutu olarak eklenir.</p>
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

          <div className="card"><div className="card-h"><h3>PnL Yönetimi</h3><span className="badge badge-r">Tehlikeli</span></div>
            <div style={{ padding: 20 }}>
              <p className="t-m text-xs" style={{ marginBottom: 12 }}>Tüm simülasyon ve denetim kayıtları silinerek PnL sıfırdan başlatılır. Beyin öğrenme verileri korunur.</p>
              <div className="dir-actions">
                <button className="btn btn-danger" onClick={async () => {
                  if (!confirm("TÜM simülasyonlar silinecek ve PnL sıfırlanacak. Emin misiniz?")) return;
                  try {
                    const r = await fetch("/api/admin/reset-pnl", { method: "POST" });
                    const d = await r.json();
                    if (r.ok) { alert(`✅ ${d.message} — ${d.deleted_simulations} simülasyon silindi`); window.location.reload(); }
                    else alert(`❌ Hata: ${d.error}`);
                  } catch { alert("❌ Bağlantı hatası"); }
                }}>🗑 PnL Sıfırla</button>
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
              <div className="stat-card"><div className="stat-name">Aktif Model</div><div className="stat-val" style={{ fontSize: 14 }}>{llmSt.active_model ?? llmSt.llm_stats?.model ?? '—'}</div></div>
              <div className="stat-card"><div className="stat-name">Mevcut Modeller</div><div className="stat-val" style={{ fontSize: 12 }}>{llmSt.available_models?.length > 0 ? llmSt.available_models.join(', ') : '—'}</div></div>
              <div className="stat-card"><div className="stat-name">Bağlantı Noktası</div><div className="stat-val" style={{ fontSize: 12 }}>{llmSt.llm_stats?.base_url ?? '—'}</div></div>
              <div className="stat-card"><div className="stat-name">İstek</div><div className="stat-val">{fmt(llmSt.llm_stats?.total_calls ?? llmSt.call_count ?? 0, 0)}</div></div>
              <div className="stat-card"><div className="stat-name">Hata</div><div className="stat-val">{fmt(llmSt.llm_stats?.total_errors ?? 0, 0)}</div></div>
              <div className="stat-card"><div className="stat-name">Ort. Gecikme</div><div className="stat-val">{llmSt.llm_stats?.avg_latency_ms != null ? `${fmt(llmSt.llm_stats.avg_latency_ms, 0)}ms` : '—'}</div></div>
            </div>
          </div>}
        </>}

        {/* ══════ SYSTEM ══════ */}
        {tab === "system" && <>
          <div className="section-header"><h2>Sistem Durumu</h2>
            {sysResources?.system_mode && <span className={cls("badge", sysResources.system_mode === 'healthy' ? 'badge-g' : 'badge-w')}>{sysResources.system_mode === 'healthy' ? '✓ TAM MOD' : '⚠ Kısıtlı (Kural Tabanlı)'}</span>}
          </div>
          <div className="kpi-row">
            <KPI label="Veritabanı" value={sysStats ? `${sysStats.db_size_mb} MB` : "—"} icon="💾" />
            <KPI label="Toplam İşlem" value={sysStats ? fmt(sysStats.total_trades, 0) : "—"} icon="📦" />
            <KPI label="İşlem/dk" value={sysStats ? fmt(sysStats.trades_per_minute, 0) : "—"} icon="⚡" accent="b" />
            <KPI label="Çalışma Süresi" value={sysResources?.uptime_seconds ? ago(Math.floor(sysResources.uptime_seconds / 60)) : sysStats ? ago(sysStats.uptime_minutes) : "—"} icon="⏱" accent="g" />
          </div>

          {/* ═══ SOL VE SAĞ PANEL ═══ */}
          <div className="sys-split">

            {/* ─── SOL PANEL: Altyapı & Kaynaklar ─── */}
            <div className="sys-col">
              <h3 className="sys-col-title">🖥 Altyapı & Kaynaklar</h3>

              {/* Kaynak Kullanımı */}
              {sysResources && <div className="card"><div className="card-h"><h3>Kaynak Kullanımı</h3><span className="badge badge-live">CANLI</span></div>
                <div className="kpi-row" style={{ padding: 12 }}>
                  <KPI label="İşlemci" value={`%${fmt(sysResources.cpu_percent ?? 0, 0)}`} icon="🖥" accent={sn(sysResources.cpu_percent) > 85 ? "r" : sn(sysResources.cpu_percent) > 60 ? "w" : "g"} />
                  <KPI label="Bellek" value={`%${fmt(sysResources.ram_percent ?? 0, 0)}`} icon="🧮" accent={sn(sysResources.ram_percent) > 90 ? "r" : sn(sysResources.ram_percent) > 80 ? "w" : "g"} />
                  <KPI label="Bellek Detay" value={`${fmt(sysResources.ram_used_mb ?? 0, 0)}/${fmt(sysResources.ram_total_mb ?? 0, 0)} MB`} icon="📊" />
                  <KPI label="Disk Kullanımı" value={`%${fmt(sysResources.disk_percent ?? 0, 0)}`} icon="💿" accent={sn(sysResources.disk_percent) > 85 ? "r" : "g"} />
                  <KPI label="Depolama" value={`${fmt(sysResources.disk_used_gb ?? 0, 1)}/${fmt(sysResources.disk_total_gb ?? 0, 1)} GB`} icon="🗄" />
                  <KPI label="Bellek Bütçe (24GB)" value={`%${fmt(sysResources.ram_budget_percent ?? 0, 0)}`} icon="🧠" accent={sn(sysResources.ram_budget_percent) > 90 ? "r" : sn(sysResources.ram_budget_percent) > 75 ? "w" : "g"} />
                  <KPI label="Süreç Belleği" value={`${fmt(sysResources.process_rss_mb ?? 0, 0)} MB`} icon="🐍" />
                  <KPI label="Yük Ort." value={`${sn(sysResources.load_avg_1m ?? 0).toFixed(1)}`} icon="📈" accent={sn(sysResources.load_avg_1m) > 3 ? "w" : "g"} />
                </div>
                {sysResources.resource_history?.length > 1 && <div style={{ padding: '0 16px 12px', display: 'flex', alignItems: 'flex-end', gap: 2, height: 50 }}>
                  {sysResources.resource_history.slice(-30).map((h: R, i: number) => {
                    const v = sn(h.ram_percent ?? 0);
                    return <div key={i} style={{ flex: 1, height: `${Math.max(v, 2)}%`, background: v > 90 ? 'var(--r)' : v > 80 ? 'var(--w)' : 'var(--g)', borderRadius: 2, opacity: 0.7, minWidth: 2 }} title={`Bellek: %${v.toFixed(0)}`} />;
                  })}
                </div>}
              </div>}

              {/* Kaynak Uyarıları */}
              {sysResources?.warnings && sysResources.warnings.length > 0 && <div className="card"><div className="card-h"><h3>Kaynak Uyarıları</h3><span className="badge badge-r">{sysResources.warnings.length} Uyarı</span></div>
                <div style={{ padding: 16 }}>
                  {sysResources.warnings.map((w: R, i: number) => (
                    <div key={i} style={{ padding: '8px 12px', marginBottom: 8, borderRadius: 8, background: w.level === 'critical' ? 'rgba(255,59,48,0.15)' : 'rgba(255,204,0,0.15)', border: `1px solid ${w.level === 'critical' ? 'var(--r)' : 'var(--w)'}` }}>
                      <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 4 }}>
                        <span>{w.level === 'critical' ? '🚨' : '⚠️'}</span>
                        <strong style={{ color: w.level === 'critical' ? 'var(--r)' : 'var(--w)' }}>{w.component}</strong>
                      </div>
                      <div className="t-m" style={{ fontSize: 12 }}>{w.message}</div>
                    </div>
                  ))}
                </div>
              </div>}

              {/* Olay Yolu */}
              {sysResources?.event_bus && <div className="card"><div className="card-h"><h3>Olay Yolu</h3><span className="badge badge-b">{fmt(sysResources.event_bus.total_events ?? 0, 0)} olay</span></div>
                <div className="kpi-row" style={{ padding: 12 }}>
                  <KPI label="Toplam Olay" value={fmt(sysResources.event_bus.total_events ?? 0, 0)} icon="📨" />
                  <KPI label="Abone" value={fmt(sysResources.event_bus.subscriber_count ?? 0, 0)} icon="🔗" />
                </div>
                {sysResources.event_bus.topics && Object.keys(sysResources.event_bus.topics).length > 0 && <div style={{ padding: '0 16px 12px' }}>
                  <div className="t-m" style={{ fontSize: 11, marginBottom: 6 }}>Olay Kanalları:</div>
                  <div style={{ display: 'flex', flexWrap: 'wrap', gap: 4 }}>
                    {Object.entries(sysResources.event_bus.topics).map(([k, v]) => <span key={k} className="badge badge-sm badge-b">{k}: {String(v)}</span>)}
                  </div>
                </div>}
              </div>}

              {/* Bağlantılar */}
              <div className="card"><div className="card-h"><h3>Bağlantılar</h3></div>
                <div className="conn-list">{['Binance Spot', 'Binance Vadeli', 'Bybit Spot', 'Bybit Vadeli', 'Veritabanı', 'Yapay Zeka', 'Beyin Modeli'].map((c, i) => <div key={i} className="conn-item"><span className="c-dot c-ok" />{c}</div>)}</div>
              </div>

              {/* Veri Tazeliği */}
              {liveStream && liveStream.exchange_freshness?.length > 0 && <div className="card"><div className="card-h"><h3>Veri Tazeliği</h3><span className="badge badge-live">CANLI</span></div>
                <div className="stream-grid">
                  {liveStream.exchange_freshness.map((ef, i) => {
                    const age = sn(ef.age_seconds, 999); const fresh = age < 10 ? 'sf' : age < 60 ? 'ss' : 'sd';
                    return <div key={i} className={cls("stream-card", fresh)}><div className="stream-name">{ef.exchange?.toUpperCase()} {ef.market_type === 'spot' ? 'Spot' : ef.market_type === 'futures' ? 'Vadeli' : ef.market_type}</div><div className="stream-age">{age.toFixed(0)}sn önce</div><div className="t-m text-xs">{ef.trades_5min ?? 0} işlem/5dk</div></div>;
                  })}
                </div>
              </div>}

              {/* Zaman Bilgisi */}
              <div className="card"><div className="card-h"><h3>Zaman Bilgisi</h3></div>
                <div className="info-list"><div><span className="t-m">İlk İşlem:</span> {sysStats?.oldest_trade ? new Date(sysStats.oldest_trade).toLocaleString('tr-TR') : '—'}</div><div><span className="t-m">Son İşlem:</span> {sysStats?.newest_trade ? new Date(sysStats.newest_trade).toLocaleString('tr-TR') : '—'}</div><div><span className="t-m">Şimdi:</span> {new Date().toLocaleString('tr-TR')}</div></div>
              </div>

              {/* Tablo İstatistikleri */}
              {tableStats.length > 0 && <div className="card"><div className="card-h"><h3>Tablo İstatistikleri</h3></div>
                <div className="stat-grid">{tableStats.map((t, i) => {
                  const tblTr: R = { trades: 'İşlemler', price_movements: 'Fiyat Hareketleri', signals: 'Sinyaller', simulations: 'Simülasyonlar', historical_signatures: 'Tarihsel İmzalar', rca_results: 'Kök Neden Sonuçları', correction_notes: 'Düzeltme Notları', agent_heartbeat: 'Ajan Kalp Atışı', pattern_records: 'Örüntü Kayıtları', brain_learning_log: 'Beyin Öğrenme Günlüğü', watchlist: 'İzleme Listesi' };
                  return <div key={i} className="stat-card"><div className="stat-name">{tblTr[t.table_name] || t.table_name}</div><div className="stat-val">{fmt(t.row_count, 0)}</div></div>;
                })}</div>
              </div>}
            </div>

            {/* ─── SAĞ PANEL: Ajanlar & Veri Haritası ─── */}
            <div className="sys-col">
              <h3 className="sys-col-title">🤖 Ajanlar & Veri Yapısı</h3>

              {/* Ajan Durumları */}
              <div className="card"><div className="card-h"><h3>Ajan Durumları</h3><span className="badge badge-g">{activeAgents} aktif</span></div>
                <div className="agent-list">
                  {Object.keys(agentSt).length > 0 ? Object.entries(agentSt).map(([n, a]: [string, any]) => {
                    const ok = a.status === 'running', stale = a.status === 'stale';
                    return <div key={n} className="agent-item"><div className={cls("a-dot", ok ? "d-ok" : stale ? "d-warn" : "d-off")} /><div><div className="agent-name">{AGENT_LABELS_TR[n] || n}</div><div className="t-m text-xs">{ok ? 'Çalışıyor' : stale ? 'Yanıtlamıyor' : a.status}{a.age_seconds != null ? ` · ${Math.round(sn(a.age_seconds))}sn` : ''}</div></div></div>;
                  }) : ['Keşifçi', 'Stratejist', 'Test Simülatörü', 'Denetçi', 'Beyin'].map((n, i) => <div key={i} className="agent-item"><div className="a-dot d-off" /><div><div className="agent-name">{n}</div><div className="t-m text-xs">Bekleniyor</div></div></div>)}
                </div>
              </div>

              {/* Ajan Yeniden Başlatma */}
              {sysResources?.agent_restarts && Object.values(sysResources.agent_restarts).some((v: any) => v > 0) && <div className="card"><div className="card-h"><h3>Ajan Yeniden Başlatma</h3><span className="badge badge-w">Hata Kurtarma</span></div>
                <div className="stat-grid" style={{ padding: 12 }}>
                  {Object.entries(sysResources.agent_restarts).map(([k, v]) => (
                    <div key={k} className="stat-card"><div className="stat-name">{AGENT_LABELS_TR[k] || k}</div><div className="stat-val" style={{ color: sn(v as number) > 3 ? 'var(--r)' : sn(v as number) > 0 ? 'var(--w)' : 'var(--g)' }}>{String(v)}</div></div>
                  ))}
                </div>
              </div>}

              {/* Veri Denetimi */}
              {auditValidation && <div className="card"><div className="card-h"><h3>Veri Denetimi</h3><span className={cls("badge", auditValidation.status === 'ok' ? 'badge-g' : auditValidation.status === 'error' ? 'badge-r' : 'badge-w')}>{auditValidation.status === 'ok' ? '✓ Sorun Yok' : `${auditValidation.total_issues} Sorun`}</span></div>
                {auditValidation.issues?.length > 0 ? <div className="tbl-wrap"><table className="tbl"><thead><tr><th>Alan</th><th>Sorun</th><th>Önem</th><th>Adet</th></tr></thead><tbody>
                  {auditValidation.issues.map((iss: R, i: number) => <tr key={i}><td className="mono" style={{ fontSize: 11 }}>{iss.field}</td><td>{iss.issue}</td><td><span className={cls("audit-badge", iss.severity === 'error' ? 'audit-err' : 'audit-warn')}>{iss.severity === 'error' ? 'Hata' : 'Uyarı'}</span></td><td>{iss.count}</td></tr>)}
                </tbody></table></div> : <div className="empty" style={{ padding: 16 }}>✅ Tüm veriler tutarlı</div>}
                <div className="t-m" style={{ padding: '8px 16px', fontSize: 11 }}>Son kontrol: {auditValidation.timestamp ? fmtDT(auditValidation.timestamp) : '—'}</div>
              </div>}

              {/* İzleme Listesi */}
              <div className="card"><div className="card-h"><h3>İzleme Listesi</h3><span className="badge badge-b">{watchlist.length}</span></div>
                <div className="wl-form">
                  <input placeholder="Sembol (BTCUSDT)" value={wlSym} onChange={e => setWlSym(e.target.value)} onKeyDown={e => e.key === 'Enter' && addWL()} />
                  <select value={wlEx} onChange={e => setWlEx(e.target.value)}><option value="all">Tümü</option><option value="binance">Binance</option><option value="bybit">Bybit</option></select>
                  <select value={wlMT} onChange={e => setWlMT(e.target.value)}><option value="spot">Spot</option><option value="futures">Vadeli</option></select>
                  <button className="btn btn-primary" onClick={addWL}>+ Ekle</button>
                </div>
                {watchlist.length > 0 && <div className="wl-list">{watchlist.map(w => <div key={w.id} className="wl-item"><span className="wl-sym">{w.symbol}</span><span className="badge badge-sm">{w.exchange}</span><span className="badge badge-sm badge-b">{w.market_type === 'spot' ? 'Spot' : w.market_type === 'futures' ? 'Vadeli' : w.market_type}</span><button className="wl-rm" onClick={() => rmWL(w)}>✕</button></div>)}</div>}
              </div>

              {/* Veri Haritası */}
              {dataFolders && <div className="card"><div className="card-h"><h3>Veri Haritası ve Klasör Açıklamaları</h3><span className="badge badge-p">{dataFolders.klasor_sayisi || 0} klasör</span></div>
                <div style={{ padding: 16 }}>
                  <h4 style={{ marginBottom: 8, color: 'var(--text)' }}>📁 Klasörler ve Görevleri</h4>
                  <div className="tbl-wrap"><table className="tbl"><thead><tr><th>Klasör Yolu</th><th>Görevi</th></tr></thead><tbody>
                    {(dataFolders.klasorler || []).map((f: R, i: number) => <tr key={i}><td className="mono" style={{ fontSize: 12 }}>{f.path}</td><td>{f.gorev}</td></tr>)}
                  </tbody></table></div>
                  <h4 style={{ marginTop: 20, marginBottom: 8, color: 'var(--text)' }}>🗄 Veritabanı Tabloları ve Biriken Veriler</h4>
                  <div className="tbl-wrap"><table className="tbl"><thead><tr><th>Tablo Adı</th><th>Sakladığı Veri</th></tr></thead><tbody>
                    {(dataFolders.veri_haritasi || []).map((t: R, i: number) => <tr key={i}><td className="mono" style={{ fontSize: 12 }}>{t.tablo}</td><td>{t.biriken_veri}</td></tr>)}
                  </tbody></table></div>
                  {dataFolders.ust_klasorler && <div style={{ marginTop: 16 }}>
                    <span className="t-m" style={{ fontSize: 11 }}>Üst düzey klasörler: </span>
                    <div style={{ display: 'flex', flexWrap: 'wrap', gap: 4, marginTop: 4 }}>
                      {dataFolders.ust_klasorler.map((f: string, i: number) => <span key={i} className="badge badge-sm">{f}</span>)}
                    </div>
                  </div>}
                </div>
              </div>}

              {/* Model Eşgüdüm */}
              {modelCoord && <div className="card"><div className="card-h"><h3>Model Eşgüdüm Durumu</h3><span className={cls("badge", modelCoord.model_durumu === 'Tam eşgüdüm' ? 'badge-g' : 'badge-w')}>{modelCoord.model_durumu}</span></div>
                <div className="kpi-row" style={{ padding: 12 }}>
                  <KPI label="Çalışan Ajan" value={`${modelCoord.calisan_ajan || 0}/${modelCoord.toplam_ajan || 0}`} icon="🤖" accent="g" />
                  <KPI label="Son 24sa Sinyal" value={fmt(modelCoord.son_24s_sinyal || 0, 0)} icon="📡" accent="b" />
                  <KPI label="Son 24sa Simülasyon" value={fmt(modelCoord.son_24s_simulasyon || 0, 0)} icon="👻" accent="p" />
                </div>
                <div style={{ padding: '0 16px 12px' }}>
                  <p className="t-m" style={{ fontSize: 11 }}>{modelCoord.aciklama || ''}</p>
                </div>
              </div>}

              {/* Denetim Kayıtları */}
              {auditRec.length > 0 && <div className="card"><div className="card-h"><h3>Denetim Kayıtları</h3></div>
                <div className="tbl-wrap"><table className="tbl"><thead><tr><th>Zaman</th><th>Toplam</th><th>Başarılı</th><th>Başarısız</th><th>Başarı %</th></tr></thead><tbody>
                  {auditRec.map((r, i) => <tr key={i}><td>{r.timestamp ? fmtDT(r.timestamp) : '—'}</td><td>{r.total_simulations ?? '—'}</td><td className="t-g">{r.successful_simulations ?? '—'}</td><td className="t-r">{r.failed_simulations ?? '—'}</td><td className={safeConf(r.success_rate) >= 50 ? "t-g" : "t-r"}>{r.success_rate != null ? `${safeConf(r.success_rate).toFixed(1)}%` : '—'}</td></tr>)}
                </tbody></table></div>
              </div>}
            </div>
          </div>
        </>}

        {/* ══════ CHAT ══════ */}
        {tab === "chat" && <>
          <div className="card"><div className="card-h"><h3>💬 QuenBot Yapay Zeka Sohbet</h3><span className="badge badge-g">Çevrimiçi</span></div>
            <div style={{ padding: '16px', display: 'flex', flexDirection: 'column', gap: '12px', height: 'calc(100vh - 300px)' }}>
              <div style={{ flex: 1, overflowY: 'auto', border: '1px solid var(--border)', borderRadius: 'var(--radius2)', padding: '12px', background: 'var(--bg3)' }}>
                {chatHistory.length === 0 ? (
                  <div style={{ textAlign: 'center', color: 'var(--text3)' }}>
                    <p>💬 Strateji komutlarını doğal dilde yazın</p>
                    <p style={{ fontSize: 11, marginTop: 8 }}>Örnek: "Selam!", "BTC ne durumda?", "Piyasa analizi yap", "Strateji öner"</p>
                  </div>
                ) : (
                  chatHistory.map((msg, i) => (
                    <div key={i} style={{ marginBottom: '8px', display: 'flex', justifyContent: msg.role === 'user' ? 'flex-end' : 'flex-start' }}>
                      <div style={{ background: msg.role === 'user' ? 'var(--c)' : 'var(--bg2)', color: msg.role === 'user' ? 'var(--bg)' : 'var(--text)', padding: '8px 12px', borderRadius: 'var(--radius2)', maxWidth: '70%', fontSize: 12 }}>
                        {msg.message}
                      </div>
                    </div>
                  ))
                )}
                {chatLoading && <div style={{ textAlign: 'center', color: 'var(--text3)' }}>⏳ Gemma işleniyor...</div>}
              </div>
              <div style={{ display: 'flex', gap: '8px' }}>
                <input type="text" value={chatMsg} onChange={e => setChatMsg(e.target.value)} onKeyDown={e => e.key === 'Enter' && handleChat()} placeholder="Mesajınızı yazın..." style={{ flex: 1, padding: '8px 12px', background: 'var(--bg2)', border: '1px solid var(--border)', borderRadius: 'var(--radius2)', color: 'var(--text)', fontFamily: 'var(--font)' }} />
                <button onClick={handleChat} disabled={chatLoading || !chatMsg} style={{ padding: '8px 16px', background: 'var(--c)', color: 'var(--bg)', border: 'none', borderRadius: 'var(--radius2)', cursor: 'pointer', fontWeight: 600 }}>Gönder</button>
              </div>
            </div>
          </div>
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
