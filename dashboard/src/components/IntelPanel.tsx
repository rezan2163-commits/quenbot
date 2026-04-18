"use client";
/**
 * IntelPanel — Phase 1-5 Intel Upgrade cockpit.
 * SaaS-style UI with clickable sidebar, shadcn primitives, interactive charts.
 */
import { useState } from "react";
import {
  Activity, Brain, GitBranch, Gauge, Sparkles, Zap, Network, Database,
  TrendingUp, TrendingDown, AlertCircle, CheckCircle2, Circle, Cpu,
  LineChart as LineChartIcon, Target, Flame,
} from "lucide-react";
import {
  useIntelSummary, useFastBrain, useDecisionRouter, useCrossAssetGraph,
  useCrossAssetNeighbors, useConfluence, useOnlineLearning,
  useOracleSummary, useOracleChannels,
  useOracleFactorGraph, useOracleBrainDirectives, useOracleBrainTraces,
  useRuntimeStatus,
} from "@/lib/intel";
import { useWatchlist } from "@/lib/api";
import {
  Card, CardHeader, CardTitle, CardDescription, CardContent,
  Badge, Stat, EmptyState, cn,
} from "./ui/primitives";
import { Sparkline, HBar, CalibrationChart, Donut } from "./ui/charts";

type PhaseKey = "overview" | "fast_brain" | "decision_router" | "cross_asset" | "confluence" | "online_learning" | "oracle";

const PHASES: Array<{
  key: PhaseKey; label: string; phase: string; icon: any; desc: string;
}> = [
  { key: "overview",        label: "Genel Bakış",     phase: "Phase 1-6", icon: Activity,  desc: "Tüm modüllerin sağlık özeti" },
  { key: "fast_brain",      label: "Fast Brain",      phase: "Phase 3",   icon: Zap,       desc: "LightGBM hızlı tahmin" },
  { key: "decision_router", label: "Decision Router", phase: "Phase 3",   icon: GitBranch, desc: "Gemma vs FastBrain yönlendirme" },
  { key: "cross_asset",     label: "Cross-Asset Graph", phase: "Phase 2", icon: Network,   desc: "Lead/lag bağımlılık grafiği" },
  { key: "confluence",      label: "Confluence",      phase: "Phase 1",   icon: Sparkles,  desc: "Çok sinyalli Bayesian skor" },
  { key: "online_learning", label: "Online Learning", phase: "Phase 4",   icon: Gauge,     desc: "Rolling kalibrasyon + hit rate" },
  { key: "oracle",          label: "Oracle Stack",    phase: "Phase 6",   icon: Flame,     desc: "8 dedektör + sinyal veriyolu" },
];

function StatusPill({ enabled, healthy }: { enabled: boolean; healthy?: boolean }) {
  if (!enabled) return <Badge variant="muted">Dormant</Badge>;
  if (healthy === false) return <Badge variant="danger">Error</Badge>;
  return <Badge variant="success"><Circle size={8} className="fill-current" />Canlı</Badge>;
}

export default function IntelPanel() {
  const [active, setActive] = useState<PhaseKey>("overview");
  const [symbol, setSymbol] = useState<string>("BTCUSDT");
  const { data: watchlist } = useWatchlist();

  const symbols = Array.from(new Set(["BTCUSDT", "ETHUSDT", ...(watchlist || []).map(w => w.symbol)]));

  const activePhase = PHASES.find(p => p.key === active);

  return (
    <div className="flex h-full min-h-0 w-full flex-col overflow-hidden bg-surface text-gray-200">
      {/* Top header — compact, mobile-safe */}
      <header className="flex items-center justify-between gap-2 border-b border-surface-border bg-surface-card/40 px-3 py-2">
        <div className="flex min-w-0 items-center gap-2">
          <Brain size={14} className="shrink-0 text-accent" />
          <div className="min-w-0">
            <div className="truncate text-[11px] font-semibold">Intel Upgrade</div>
            <div className="truncate text-[9px] text-gray-500">{activePhase?.desc}</div>
          </div>
        </div>
        {active !== "overview" && active !== "decision_router" && (
          <select
            value={symbol}
            onChange={(e) => setSymbol(e.target.value)}
            className="max-w-[7.5rem] shrink-0 rounded-md border border-surface-border bg-surface-card px-1.5 py-1 text-[11px] text-gray-200 focus:outline-none focus:ring-1 focus:ring-accent"
          >
            {symbols.map((s) => <option key={s} value={s}>{s}</option>)}
          </select>
        )}
      </header>

      {/* Horizontal sub-nav — scrollable, same style as RightPanel tabs */}
      <nav className="flex shrink-0 overflow-x-auto border-b border-surface-border bg-surface-card/30 custom-scrollbar">
        {PHASES.map((p) => {
          const Icon = p.icon;
          const act = active === p.key;
          return (
            <button
              key={p.key}
              onClick={() => setActive(p.key)}
              className={cn(
                "flex shrink-0 items-center gap-1 whitespace-nowrap px-2.5 py-2 text-[10px] font-medium transition-colors",
                act
                  ? "border-b-2 border-accent text-accent"
                  : "border-b-2 border-transparent text-gray-500 hover:text-gray-300"
              )}
              title={`${p.label} · ${p.phase}`}
            >
              <Icon size={11} />
              {p.label}
            </button>
          );
        })}
      </nav>

      {/* Content */}
      <main className="flex-1 min-h-0 min-w-0 overflow-y-auto">
        <div className="p-3">
          {active === "overview" && <OverviewView onNavigate={setActive} />}
          {active === "fast_brain" && <FastBrainView symbol={symbol} />}
          {active === "decision_router" && <DecisionRouterView />}
          {active === "cross_asset" && <CrossAssetView symbol={symbol} />}
          {active === "confluence" && <ConfluenceView symbol={symbol} />}
          {active === "online_learning" && <OnlineLearningView symbol={symbol} />}
          {active === "oracle" && <OracleView symbol={symbol} />}
        </div>
      </main>
    </div>
  );
}

/* ══════════════ Views ══════════════ */

function OverviewView({ onNavigate }: { onNavigate: (k: PhaseKey) => void }) {
  const { data, error } = useIntelSummary();

  if (error) {
    return <EmptyState title="Intel API erişilemiyor" description={String(error)} icon={<AlertCircle />} />;
  }
  if (!data) {
    return <EmptyState title="Yükleniyor..." icon={<Cpu className="animate-pulse" />} />;
  }

  const modules: Array<{ key: PhaseKey | null; name: string; data?: any; icon: any; phase: string }> = [
    { key: null,               name: "Feature Store",  data: data.feature_store,  icon: Database,   phase: "1" },
    { key: null,               name: "OFI",            data: data.ofi,            icon: TrendingUp, phase: "1" },
    { key: null,               name: "Multi-Horizon",  data: data.multi_horizon,  icon: LineChartIcon, phase: "1" },
    { key: "confluence",       name: "Confluence",     data: data.confluence,     icon: Sparkles,   phase: "1" },
    { key: "cross_asset",      name: "Cross-Asset",    data: data.cross_asset,    icon: Network,    phase: "2" },
    { key: "fast_brain",       name: "Fast Brain",     data: data.fast_brain,     icon: Zap,        phase: "3" },
    { key: "decision_router",  name: "Decision Router", data: data.decision_router, icon: GitBranch, phase: "3" },
    { key: "online_learning",  name: "Online Learning", data: data.online_learning, icon: Gauge,    phase: "4" },
    { key: "oracle",           name: "Oracle Stack",    data: data.oracle,         icon: Flame,    phase: "6" },
  ];

  const enabled = modules.filter(m => m.data?.enabled).length;
  const healthy = modules.filter(m => m.data?.enabled && m.data?.health?.healthy !== false).length;
  const dormant = enabled - healthy;

  return (
    <div className="flex flex-col gap-3">
      {/* Top KPIs */}
      <div className="grid grid-cols-2 gap-2">
        <Stat label="Aktif Modül" value={`${enabled}/${modules.length}`}
              icon={<Activity size={14} />} tone={enabled >= 4 ? "bull" : "warn"} />
        <Stat label={dormant > 0 ? `Sağlıklı (${dormant} dormant)` : "Sağlıklı"}
              value={`${healthy}/${enabled || 1}`}
              icon={<CheckCircle2 size={14} />}
              tone={dormant === 0 ? "bull" : "warn"} />
        <Stat label="Faz" value="1→5" hint="Tüm fazlar deploy edildi"
              icon={<Flame size={14} />} tone="bull" />
        <Stat label="Shadow Log"
              value={data.decision_router?.health?.log_rows ?? 0}
              hint="router JSONL satırı"
              icon={<LineChartIcon size={14} />} />
      </div>

      {/* Module grid */}
      <div className="grid grid-cols-1 gap-2 xl:grid-cols-2">
        {modules.map((m) => {
          const Icon = m.icon;
          const enabled = !!m.data?.enabled;
          const healthy = enabled && m.data?.health?.healthy !== false;
          const clickable = !!m.key;
          return (
            <Card
              key={m.name}
              onClick={clickable ? () => onNavigate(m.key as PhaseKey) : undefined}
              className={cn(
                "flex min-w-0 flex-col gap-2 p-2.5 transition-all",
                clickable && "cursor-pointer hover:border-accent/60 hover:bg-surface-hover/50"
              )}
            >
              <div className="flex min-w-0 items-start justify-between gap-2">
                <div className="flex min-w-0 items-center gap-2">
                  <div className={cn(
                    "shrink-0 rounded-md p-1.5",
                    enabled ? "bg-accent/15 text-accent" : "bg-gray-700/30 text-gray-500"
                  )}>
                    <Icon size={13} />
                  </div>
                  <div className="min-w-0">
                    <div className="truncate text-[11px] font-semibold">{m.name}</div>
                    <div className="text-[9px] text-gray-500">Phase {m.phase}</div>
                  </div>
                </div>
                <StatusPill enabled={enabled} healthy={healthy} />
              </div>
              {enabled && m.data?.health && (
                <div className="flex flex-wrap gap-1">
                  {Object.entries(m.data.health)
                    .filter(([k, v]) => typeof v === "number" && !k.endsWith("_ts"))
                    .slice(0, 3)
                    .map(([k, v]) => (
                      <Badge key={k} variant="outline">
                        <span className="text-gray-500">{k}:</span>
                        <span className="font-mono text-gray-200">{fmtNum(v as number)}</span>
                      </Badge>
                    ))}
                </div>
              )}
              {m.data?.error && <div className="break-words text-[10px] text-bear">{m.data.error}</div>}
            </Card>
          );
        })}
      </div>
    </div>
  );
}

function FastBrainView({ symbol }: { symbol: string }) {
  const { data, error } = useFastBrain(symbol);
  if (error) return <EmptyState title="FastBrain API erişilemiyor" icon={<AlertCircle />} />;
  if (!data) return <EmptyState title="Yükleniyor..." />;

  if (!data.enabled) {
    return (
      <div className="flex flex-col gap-3">
        <Card className="p-4">
          <div className="flex items-start gap-3">
            <div className="rounded-md bg-warn/15 p-2 text-warn"><Zap size={16} /></div>
            <div>
              <div className="text-sm font-semibold">FastBrain Dormant</div>
              <div className="mt-1 text-[11px] text-gray-500">
                {data.reason ?? "Model dosyası yok veya flag kapalı"}
              </div>
              <div className="mt-2 flex gap-2">
                <Badge variant="warn">QUENBOT_FAST_BRAIN_ENABLED=false</Badge>
                <Badge variant="outline">python_agents/.models/fast_brain_latest.lgb</Badge>
              </div>
            </div>
          </div>
        </Card>
        <Card className="p-4">
          <CardTitle className="mb-2">Aktivasyon Adımları</CardTitle>
          <ol className="list-decimal space-y-1 pl-4 text-[11px] text-gray-400">
            <li>Sunucuda: <code className="text-accent">pip install --break-system-packages lightgbm</code></li>
            <li><code className="text-accent">python python_agents/scripts/train_fast_brain.py --days 30</code></li>
            <li>.env'ye: <code className="text-accent">QUENBOT_FAST_BRAIN_ENABLED=true</code></li>
            <li><code className="text-accent">pm2 restart all</code></li>
          </ol>
        </Card>
      </div>
    );
  }

  const p = data.prediction;
  if (!p) {
    return <EmptyState title="Tahmin mevcut değil"
                       description={data.reason ?? "Yeterli feature toplanmadı"}
                       icon={<Zap />} />;
  }
  const tone: "bull" | "bear" | "warn" = p.direction === "up" ? "bull" : p.direction === "down" ? "bear" : "warn";

  return (
    <div className="flex flex-col gap-3">
      <div className="grid grid-cols-[auto_1fr] items-center gap-3">
        <Card className="flex flex-col items-center justify-center gap-2 p-3">
          <Donut value={p.probability} size={92}
                 tone={tone} label={
            <div>
              <div className={cn("text-base font-bold",
                tone === "bull" ? "text-bull" : tone === "bear" ? "text-bear" : "text-warn")}>
                {(p.probability * 100).toFixed(1)}%
              </div>
              <div className="text-[9px] text-gray-500 uppercase">{p.direction}</div>
            </div>
          } />
          <Badge variant={tone === "bull" ? "success" : tone === "bear" ? "danger" : "warn"}>
            {p.direction === "up" ? <TrendingUp size={10} /> : p.direction === "down" ? <TrendingDown size={10} /> : <Target size={10} />}
            {p.direction.toUpperCase()}
          </Badge>
        </Card>
        <div className="grid min-w-0 grid-cols-2 gap-2">
          <Stat label="Confidence" value={`${(p.confidence * 100).toFixed(1)}%`} tone={tone} />
          <Stat label="Raw" value={p.raw_score.toFixed(3)} hint="pre-calib" />
          <Stat label="Latency" value={`${p.latency_ms.toFixed(2)}ms`} tone="bull" />
          <Stat label="Features" value={`${p.features_used}`} hint={`${p.missing_features.length} eksik`} />
        </div>
      </div>

      {p.missing_features.length > 0 && (
        <Card className="p-3">
          <CardTitle className="mb-2 text-xs">Eksik Feature'lar</CardTitle>
          <div className="flex flex-wrap gap-1">
            {p.missing_features.map(f => (
              <Badge key={f} variant="muted">{f}</Badge>
            ))}
          </div>
        </Card>
      )}
    </div>
  );
}

function DecisionRouterView() {
  const { data } = useDecisionRouter();
  if (!data) return <EmptyState title="Yükleniyor..." />;
  if (!data.enabled) {
    return (
      <Card className="p-4">
        <div className="flex items-start gap-3">
          <div className="rounded-md bg-warn/15 p-2 text-warn"><GitBranch size={16} /></div>
          <div>
            <div className="text-sm font-semibold">Decision Router Dormant</div>
            <p className="mt-1 text-[11px] text-gray-500">
              Flag kapalı. Aktifken Gemma + FastBrain kararları shadow modda karşılaştırılır.
            </p>
            <div className="mt-2 flex gap-2">
              <Badge variant="warn">QUENBOT_DECISION_ROUTER_ENABLED=false</Badge>
            </div>
          </div>
        </div>
      </Card>
    );
  }

  const h = data.health!;
  const lastList = Object.entries(data.last_decisions || {}).slice(0, 20);

  return (
    <div className="flex flex-col gap-3">
      <div className="grid grid-cols-2 gap-2">
        <Stat label="Mod" value={h.shadow ? "SHADOW" : "ACTIVE"}
              tone={h.shadow ? "warn" : "bull"}
              hint={h.shadow ? "değiştirilmiyor" : "fast override"} />
        <Stat label="Routed" value={h.routed_total} />
        <Stat label="Agree" value={h.agree_total}
              hint={h.routed_total > 0 ? `%${((h.agree_total / h.routed_total) * 100).toFixed(0)}` : "—"}
              tone="bull" />
        <Stat label="Disagree" value={h.disagree_total} tone="bear" />
      </div>

      <Card>
        <CardHeader>
          <CardTitle>Son Kararlar</CardTitle>
          <CardDescription>{lastList.length} sembol izleniyor</CardDescription>
        </CardHeader>
        <CardContent>
          {lastList.length === 0 ? (
            <EmptyState title="Henüz karar yok"
                        description="Gemma kararı ürettiğinde burada görünecek" />
          ) : (
            <div className="flex flex-col gap-1.5">
              {lastList.map(([sym, d]: any) => (
                <div key={sym} className="flex items-center gap-2 rounded-md bg-surface-card/60 p-2">
                  <span className="w-20 font-mono text-[11px] text-gray-200">{sym}</span>
                  <Badge variant={d.chosen_by === "fast_brain" ? "info" : "default"}>
                    {d.chosen_by}
                  </Badge>
                  <Badge variant="outline">{d.action}</Badge>
                  <span className="text-[10px] text-gray-500">gemma:{d.gemma_action}</span>
                  <span className="text-[10px] text-gray-500">fast:{d.fast_direction}</span>
                  {d.fast_probability != null && (
                    <span className="text-[10px] font-mono text-gray-400">p={d.fast_probability.toFixed(2)}</span>
                  )}
                  <span className={cn(
                    "ml-auto rounded-full px-1.5 py-0.5 text-[9px]",
                    d.agreed ? "bg-bull/15 text-bull" : "bg-bear/15 text-bear"
                  )}>
                    {d.agreed ? "agreed" : "diverged"}
                  </span>
                </div>
              ))}
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  );
}

function CrossAssetView({ symbol }: { symbol: string }) {
  const { data: graph } = useCrossAssetGraph();
  const { data: neighbors } = useCrossAssetNeighbors(symbol);

  if (graph?.error) return <EmptyState title="Cross-Asset dormant" description={graph.error} icon={<Network />} />;
  if (!graph) return <EmptyState title="Yükleniyor..." />;

  const maxRho = Math.max(0.01, ...(neighbors?.leaders || []).map(l => Math.abs(l.rho)),
                              ...(neighbors?.followers || []).map(f => Math.abs(f.rho)));

  return (
    <div className="flex flex-col gap-3">
      <div className="grid grid-cols-2 gap-2">
        <Stat label="Nodes" value={graph.nodes?.length ?? 0} icon={<Network size={14} />} />
        <Stat label="Edges" value={graph.edges?.length ?? 0} />
        <Stat label="Tracked" value={graph.tracked_symbols ?? 0} />
        <Stat label="Spillover"
              value={(neighbors?.active_spillover ?? 0).toFixed(3)}
              tone={(neighbors?.active_spillover ?? 0) > 0 ? "bull" : (neighbors?.active_spillover ?? 0) < 0 ? "bear" : "default"} />
      </div>

      <div className="grid grid-cols-1 gap-2 xl:grid-cols-2">
        <Card>
          <CardHeader>
            <CardTitle>Leaders of {symbol}</CardTitle>
            <CardDescription>{symbol} hareketine öncülük eden semboller</CardDescription>
          </CardHeader>
          <CardContent>
            {(neighbors?.leaders || []).length === 0 ? (
              <EmptyState title="Henüz leader yok" />
            ) : (
              <div className="flex flex-col gap-2">
                {neighbors!.leaders.map((l) => (
                  <HBar key={l.symbol} label={l.symbol} value={Math.abs(l.rho)} max={maxRho}
                        tone={l.rho >= 0 ? "bull" : "bear"}
                        right={`${l.rho.toFixed(2)} · ${l.lag_sec}s`} />
                ))}
              </div>
            )}
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>Followers of {symbol}</CardTitle>
            <CardDescription>{symbol}'i takip eden semboller</CardDescription>
          </CardHeader>
          <CardContent>
            {(neighbors?.followers || []).length === 0 ? (
              <EmptyState title="Henüz follower yok" />
            ) : (
              <div className="flex flex-col gap-2">
                {neighbors!.followers.map((f) => (
                  <HBar key={f.symbol} label={f.symbol} value={Math.abs(f.rho)} max={maxRho}
                        tone={f.rho >= 0 ? "bull" : "bear"}
                        right={`${f.rho.toFixed(2)} · ${f.lag_sec}s`} />
                ))}
              </div>
            )}
          </CardContent>
        </Card>
      </div>

      <Card>
        <CardHeader>
          <CardTitle>Edge List (Top 20)</CardTitle>
          <CardDescription>Lead/lag ilişkileri, |ρ| büyükten küçüğe</CardDescription>
        </CardHeader>
        <CardContent>
          {(graph.edges || []).length === 0 ? (
            <EmptyState title="Henüz edge yok"
                        description="İlk rebuild 15 dakika sonra oluşur" />
          ) : (
            <div className="flex flex-col gap-1">
              {(graph.edges || []).slice().sort((a, b) => Math.abs(b.rho) - Math.abs(a.rho)).slice(0, 20).map((e, i) => (
                <div key={i} className="flex items-center gap-2 rounded-md bg-surface-card/60 px-2 py-1 text-[11px] font-mono">
                  <span className="w-20 truncate text-gray-300">{e.source}</span>
                  <span className="text-gray-500">→</span>
                  <span className="w-20 truncate text-gray-300">{e.target}</span>
                  <span className={cn("ml-auto", e.rho >= 0 ? "text-bull" : "text-bear")}>
                    ρ={e.rho.toFixed(3)}
                  </span>
                  <span className="w-12 text-right text-gray-500">{e.lag_sec}s</span>
                </div>
              ))}
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  );
}

function ConfluenceView({ symbol }: { symbol: string }) {
  const { data, error, isLoading } = useConfluence(symbol);

  // 1) Ağ/HTTP hatası — ErrorBoundary'ye düşmek yerine anlamlı mesaj göster.
  if (error) {
    const msg = String((error as any)?.message || error || "");
    const is503 = msg.includes("503");
    return (
      <Card className="p-4">
        <div className="flex items-start gap-3">
          <div className="rounded-md bg-warn/15 p-2 text-warn"><AlertCircle size={16} /></div>
          <div className="min-w-0">
            <div className="text-sm font-semibold">
              {is503 ? "Confluence Dormant" : "Confluence erişilemiyor"}
            </div>
            <p className="mt-1 break-words text-[11px] text-gray-500">
              {is503
                ? "confluence_engine kapalı. .env içinde QUENBOT_CONFLUENCE_ENABLED=true ile açıp pm2 restart."
                : `API hatası: ${msg || "bilinmiyor"}`}
            </p>
            {is503 && <Badge variant="warn" className="mt-2">QUENBOT_CONFLUENCE_ENABLED=false</Badge>}
          </div>
        </div>
      </Card>
    );
  }

  if (isLoading || !data) return <EmptyState title="Yükleniyor..." />;

  // 2) Backend `{"error": "..."}` şeklinde 200/5xx dönebilir — güvenli parse.
  if (typeof data !== "object" || (data as any).error) {
    return (
      <Card className="p-4">
        <div className="flex items-start gap-3">
          <div className="rounded-md bg-warn/15 p-2 text-warn"><AlertCircle size={16} /></div>
          <div className="min-w-0">
            <div className="text-sm font-semibold">Confluence veri yok</div>
            <p className="mt-1 break-words text-[11px] text-gray-500">
              {String((data as any)?.error || "Backend confluence sonucu döndüremedi.")}
            </p>
          </div>
        </div>
      </Card>
    );
  }

  // 3) top_contributors hem tuple hem Contribution dict hem {k:v} olabilir.
  const raw = (data as any).top_contributors ?? (data as any).contributors ?? [];
  const normalized: Array<[string, number]> = [];
  try {
    if (Array.isArray(raw)) {
      for (const item of raw) {
        if (Array.isArray(item) && item.length >= 2) {
          normalized.push([String(item[0] ?? ""), Number(item[1]) || 0]);
        } else if (item && typeof item === "object") {
          const obj = item as Record<string, unknown>;
          const name = String(obj.feature ?? obj.name ?? obj.symbol ?? "");
          const val = Number(
            obj.log_odds ?? obj.contribution ?? obj.value ?? obj.weight ?? 0,
          );
          if (name) normalized.push([name, Number.isFinite(val) ? val : 0]);
        }
      }
    } else if (raw && typeof raw === "object") {
      for (const [k, v] of Object.entries(raw as Record<string, unknown>)) {
        normalized.push([String(k), Number(v) || 0]);
      }
    }
  } catch {
    /* normalize başarısız — boş gösterim */
  }
  const sorted = normalized
    .filter(([name]) => !!name)
    .sort((a, b) => Math.abs(b[1]) - Math.abs(a[1]));
  const maxAbs = sorted.length > 0
    ? Math.max(0.01, ...sorted.map(([, v]) => Math.abs(v)))
    : 0.01;

  // 4) Backend confluence_score ∈ [0,1] üretir (σ(log_odds)). Donut 0-1 alır.
  const rawScore = Number((data as any).confluence_score);
  const score = Number.isFinite(rawScore) ? rawScore : 0;
  const rawLogOdds = Number((data as any).log_odds);
  const logOdds = Number.isFinite(rawLogOdds) ? rawLogOdds : 0;
  // Yön göstergesi: log_odds yönü (0'ın üstü/altı)
  const scoreTone: "bull" | "bear" | "warn" =
    logOdds > 0.1 ? "bull" : logOdds < -0.1 ? "bear" : "warn";

  return (
    <div className="flex flex-col gap-3">
      <div className="grid grid-cols-[auto_1fr] items-center gap-3">
        <Card className="flex flex-col items-center p-3">
          <Donut value={score} size={92}
                 tone={scoreTone}
                 label={<div className="text-base font-bold font-mono">{score.toFixed(2)}</div>} />
          <Badge className="mt-2" variant={scoreTone === "bull" ? "success" : scoreTone === "bear" ? "danger" : "warn"}>
            Score
          </Badge>
        </Card>
        <div className="grid min-w-0 grid-cols-1 gap-2">
          <Stat label="Log Odds" value={logOdds.toFixed(3)} />
          <Stat label="Contributors" value={sorted.length} />
          <Stat label="Symbol" value={String((data as any).symbol || symbol)} />
        </div>
      </div>

      <Card>
        <CardHeader>
          <CardTitle>Contributor Breakdown</CardTitle>
          <CardDescription>Her sinyalin ağırlıklı katkısı</CardDescription>
        </CardHeader>
        <CardContent>
          {sorted.length === 0 ? (
            <EmptyState title="Veri toplanıyor..."
                        description="Henüz yeterli sinyal yok. Feature store dolduktan sonra gelir." />
          ) : (
            <div className="flex flex-col gap-2">
              {sorted.slice(0, 12).map(([name, val], idx) => {
                const n = Number(val) || 0;
                return (
                  <HBar key={`${name}-${idx}`} label={name} value={Math.abs(n)} max={maxAbs}
                        tone={n >= 0 ? "bull" : "bear"}
                        right={n.toFixed(3)} />
                );
              })}
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  );
}

function OnlineLearningView({ symbol }: { symbol: string }) {
  const { data } = useOnlineLearning(symbol);
  const { data: summary } = useIntelSummary();
  if (!data) return <EmptyState title="Yükleniyor..." />;
  if (!data.enabled) {
    return (
      <Card className="p-4">
        <div className="flex items-start gap-3">
          <div className="rounded-md bg-warn/15 p-2 text-warn"><Gauge size={16} /></div>
          <div>
            <div className="text-sm font-semibold">Online Learning Dormant</div>
            <p className="mt-1 text-[11px] text-gray-500">
              Decision Router shadow log'u olmadan kalibrasyon hesaplanamaz.
            </p>
            <Badge variant="warn" className="mt-2">QUENBOT_ONLINE_LEARNING_ENABLED=false</Badge>
          </div>
        </div>
      </Card>
    );
  }
  const r = data.rolling;
  if (!r || r.samples === 0) {
    // Gerçek kök sebebini tespit et: FastBrain dormant mı? Router log boş mu?
    const fbDormant = summary?.fast_brain?.health?.model_loaded === false
                   || summary?.fast_brain?.health?.message?.toString().toLowerCase().includes("dormant");
    const routerLogRows = Number(summary?.decision_router?.health?.log_rows ?? 0);
    const reason = fbDormant
      ? "FastBrain modeli henüz yüklenmedi (dormant). Router karar üretmiyor → evaluator örnek bulamıyor."
      : routerLogRows === 0
        ? "Decision Router shadow log'u boş. Router ilk kararını üretince değerlendirme başlar."
        : "Horizon süresi (60dk) henüz dolmadı. Olgun kararlar otomatik skorlanır.";
    return <EmptyState title="Henüz değerlendirilmiş örnek yok"
                       description={reason} />;
  }

  const fastHit = r.fast_brain?.directional_hit_rate ?? null;
  const gemmaHit = r.gemma?.directional_hit_rate ?? null;
  const agreeHit = r.agreement?.hit_rate_when_agreed ?? null;
  const ece = r.ece ?? null;

  return (
    <div className="flex flex-col gap-3">
      <div className="grid grid-cols-2 gap-2">
        <Stat label="Samples" value={r.samples} />
        <Stat label="FastBrain Hit" value={fastHit == null ? "—" : `${(fastHit * 100).toFixed(1)}%`}
              tone={fastHit != null && fastHit > 0.55 ? "bull" : fastHit != null && fastHit < 0.45 ? "bear" : "warn"} />
        <Stat label="Gemma Hit" value={gemmaHit == null ? "—" : `${(gemmaHit * 100).toFixed(1)}%`}
              tone={gemmaHit != null && gemmaHit > 0.55 ? "bull" : "default"} />
        <Stat label="ECE" value={ece == null ? "—" : ece.toFixed(3)}
              hint="kalibrasyon (düşük iyi)"
              tone={ece != null && ece < 0.1 ? "bull" : "warn"} />
      </div>

      <div className="grid grid-cols-1 gap-2 xl:grid-cols-2">
        <Card>
          <CardHeader>
            <CardTitle>Kalibrasyon Diyagramı</CardTitle>
            <CardDescription>Tahmin olasılığı vs gerçekleşme. Diyagonal = mükemmel.</CardDescription>
          </CardHeader>
          <CardContent>
            <div className="flex justify-center">
              <CalibrationChart bins={r.calibration_bins || []} width={320} height={220} />
            </div>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>Agreement Analizi</CardTitle>
            <CardDescription>FastBrain & Gemma aynı yönü verdiğinde doğruluk</CardDescription>
          </CardHeader>
          <CardContent>
            <div className="flex flex-col gap-3">
              <div className="flex items-center gap-3">
                <Donut value={r.agreement?.rate ?? 0} size={80}
                       label={`${((r.agreement?.rate ?? 0) * 100).toFixed(0)}%`} tone="accent" />
                <div>
                  <div className="text-[11px] text-gray-500">Anlaşma oranı</div>
                  <div className="text-[10px] text-gray-400">{r.agreement?.n ?? 0} karar</div>
                </div>
              </div>
              <div className="flex items-center gap-3">
                <Donut value={agreeHit ?? 0} size={80}
                       label={agreeHit == null ? "—" : `${(agreeHit * 100).toFixed(0)}%`}
                       tone={agreeHit != null && agreeHit > 0.55 ? "bull" : "warn"} />
                <div>
                  <div className="text-[11px] text-gray-500">Anlaşınca doğruluk</div>
                  <div className="text-[10px] text-gray-400">ek sinyal gücü</div>
                </div>
              </div>
            </div>
          </CardContent>
        </Card>
      </div>
    </div>
  );
}

/* ───────── Phase 6: Oracle Stack ───────── */
function OracleView({ symbol }: { symbol: string }) {
  const { data: summary, error: sumErr } = useOracleSummary();
  const { data: channels, error: chErr } = useOracleChannels(symbol);
  const { data: fg } = useOracleFactorGraph(symbol);
  const { data: brain } = useOracleBrainDirectives();
  const { data: traces } = useOracleBrainTraces(10);
  const { data: runtime } = useRuntimeStatus();

  if (sumErr) return <EmptyState title="Oracle API erişilemiyor" description={String(sumErr)} icon={<AlertCircle />} />;
  if (!summary) return <EmptyState title="Yükleniyor..." icon={<Cpu className="animate-pulse" />} />;

  if (!summary.enabled) {
    return (
      <div className="flex flex-col gap-3">
        <Card className="p-4">
          <div className="flex items-start gap-3">
            <div className="rounded-md bg-warn/15 p-2 text-warn"><Flame size={16} /></div>
            <div>
              <div className="text-sm font-semibold">Oracle Stack Dormant</div>
              <div className="mt-1 text-[11px] text-gray-500">
                Signal bus başlatılmadı veya tüm dedektörler OFF.
              </div>
              <div className="mt-2 flex flex-wrap gap-1">
                <Badge variant="warn">QUENBOT_ORACLE_BUS_ENABLED=1</Badge>
                <Badge variant="outline">QUENBOT_BOCPD_ENABLED=1</Badge>
                <Badge variant="outline">QUENBOT_HAWKES_ENABLED=1</Badge>
                <Badge variant="outline">QUENBOT_LOB_THERMO_ENABLED=1</Badge>
                <Badge variant="outline">QUENBOT_WASSERSTEIN_ENABLED=1</Badge>
                <Badge variant="outline">QUENBOT_PATH_SIGNATURE_ENABLED=1</Badge>
                <Badge variant="outline">QUENBOT_MIRROR_FLOW_ENABLED=1</Badge>
                <Badge variant="outline">QUENBOT_TDA_ENABLED=1</Badge>
                <Badge variant="outline">QUENBOT_ONCHAIN_ENABLED=1</Badge>
              </div>
            </div>
          </div>
        </Card>
      </div>
    );
  }

  const channelMap = (channels?.channels ?? {}) as Record<string, any>;

  return (
    <div className="flex flex-col gap-3">
      {/* KPIs */}
      <div className="grid grid-cols-3 gap-2">
        <Stat label="Dedektör" value={summary.detectors.length} icon={<Flame size={14} />} tone="bull" />
        <Stat label="Kanal" value={summary.channels.length} icon={<Network size={14} />} />
        <Stat label="Aktif" value={symbol} hint="okunan sembol" icon={<Target size={14} />} />
      </div>

      {/* §12 Runtime supervisor mini-status */}
      {runtime?.enabled && runtime.status && (
        <Card className="p-2">
          <div className="flex flex-wrap items-center gap-2 text-[11px]">
            <Badge variant={runtime.status.running ? "bull" as any : "warn" as any}>
              Supervisor: {runtime.status.running ? "running" : "stopped"}
            </Badge>
            <Badge variant="outline">
              components: {runtime.status.components.length}
            </Badge>
            <Badge variant="outline">
              cycles: {runtime.status.stats?.cycles ?? 0}
            </Badge>
            <Badge variant="outline">
              failures: {runtime.status.stats?.failures ?? 0}
            </Badge>
            <Badge variant="outline">
              restarts: {runtime.status.stats?.restarts_requested ?? 0}
            </Badge>
            <span className="ml-auto text-[9px] text-gray-500">
              last tick {runtime.status.last_cycle_ts ?
                new Date(runtime.status.last_cycle_ts * 1000).toLocaleTimeString() : "—"}
            </span>
          </div>
        </Card>
      )}

      {/* Channels for current symbol */}
      <Card className="p-3">
        <CardTitle className="mb-2 flex items-center gap-2 text-[12px]">
          <Sparkles size={12} /> Oracle Kanalları — {symbol}
        </CardTitle>
        {chErr && <div className="text-[10px] text-bear">{String(chErr)}</div>}
        {!chErr && summary.channels.length === 0 && (
          <div className="text-[10px] text-gray-500">Henüz kanal kaydedilmedi.</div>
        )}
        <div className="grid grid-cols-1 gap-1.5 md:grid-cols-2">
          {summary.channels.map((ch) => {
            const entry = channelMap[ch];
            const val = typeof entry === "number" ? entry : (entry?.value ?? null);
            const hasVal = typeof val === "number" && Number.isFinite(val);
            const width = hasVal ? Math.min(100, Math.abs(val) * 100) : 0;
            const tone = !hasVal ? "bg-gray-700/40" : val >= 0 ? "bg-accent/60" : "bg-bear/60";
            return (
              <div key={ch} className="rounded-md border border-surface-border bg-surface-card/40 p-2">
                <div className="flex items-center justify-between gap-2">
                  <code className="truncate text-[10px] text-gray-300">{ch}</code>
                  <span className="shrink-0 font-mono text-[11px] text-gray-200">
                    {hasVal ? (val as number).toFixed(3) : "—"}
                  </span>
                </div>
                <div className="mt-1 h-1 overflow-hidden rounded bg-surface-border/40">
                  <div className={cn("h-full transition-all", tone)} style={{ width: `${width}%` }} />
                </div>
              </div>
            );
          })}
        </div>
      </Card>

      {/* §10 Factor Graph — IFI gauge */}
      {fg?.enabled && fg.snapshot && (
        <Card className="p-3">
          <CardTitle className="mb-2 flex items-center gap-2 text-[12px]">
            <Network size={12} /> Factor Graph — IFI ({symbol})
          </CardTitle>
          <div className="grid grid-cols-3 gap-2">
            <Stat
              label="IFI"
              value={(fg.snapshot.ifi ?? 0).toFixed(3)}
              hint="Invisible Footprint Index"
              tone={fg.snapshot.ifi >= 0.75 ? "bull" : fg.snapshot.ifi >= 0.5 ? "warn" : undefined}
              icon={<Flame size={14} />}
            />
            <Stat
              label="Yön"
              value={(fg.snapshot.direction ?? 0).toFixed(2)}
              hint="-1 short · +1 long"
              tone={fg.snapshot.direction > 0.2 ? "bull" : fg.snapshot.direction < -0.2 ? "bear" : undefined}
              icon={<Target size={14} />}
            />
            <Stat
              label="Kanal"
              value={Object.keys(fg.snapshot.channels || {}).length}
              hint="fuse input"
              icon={<Cpu size={14} />}
            />
          </div>
          <div className="mt-2 h-2 overflow-hidden rounded bg-surface-border/40">
            <div
              className={cn("h-full transition-all",
                fg.snapshot.ifi >= 0.75 ? "bg-bear/70" :
                fg.snapshot.ifi >= 0.5 ? "bg-warn/70" : "bg-accent/60")}
              style={{ width: `${Math.min(100, (fg.snapshot.ifi ?? 0) * 100)}%` }}
            />
          </div>
        </Card>
      )}

      {/* §11 Oracle Brain — directives per symbol */}
      {brain?.enabled && (
        <Card className="p-3">
          <CardTitle className="mb-2 flex items-center gap-2 text-[12px]">
            <Sparkles size={12} /> Oracle Brain Direktifleri
            {brain.shadow && <Badge variant="outline">shadow</Badge>}
          </CardTitle>
          {Object.keys(brain.directives ?? {}).length === 0 && (
            <div className="text-[10px] text-gray-500">Henüz direktif üretilmedi.</div>
          )}
          <div className="grid grid-cols-1 gap-1.5 md:grid-cols-2">
            {Object.entries(brain.directives ?? {}).map(([sym, d]) => {
              const sevVariant =
                d.severity === "critical" ? "bear" :
                d.severity === "high" ? "warn" :
                d.severity === "medium" ? "warn" :
                d.severity === "low" ? "outline" : "outline";
              return (
                <div key={sym} className="rounded-md border border-surface-border bg-surface-card/40 p-2">
                  <div className="flex items-center justify-between gap-2">
                    <div className="min-w-0">
                      <div className="truncate text-[11px] font-semibold">{sym}</div>
                      <code className="truncate text-[9px] text-gray-500">{d.action}</code>
                    </div>
                    <Badge variant={sevVariant as any}>{d.severity}</Badge>
                  </div>
                  <div className="mt-1 text-[10px] text-gray-400">{d.rationale}</div>
                  <div className="mt-1 flex flex-wrap gap-1 text-[9px] text-gray-500">
                    <Badge variant="outline">conf: {(d.confidence ?? 0).toFixed(2)}</Badge>
                    <Badge variant="outline">ttl: {d.ttl_sec}s</Badge>
                  </div>
                </div>
              );
            })}
          </div>
        </Card>
      )}

      {/* §11 Oracle Brain — recent traces */}
      {brain?.enabled && (traces?.traces?.length ?? 0) > 0 && (
        <Card className="p-3">
          <CardTitle className="mb-2 flex items-center gap-2 text-[12px]">
            <LineChartIcon size={12} /> Son Reasoning Trace'ler
          </CardTitle>
          <div className="flex flex-col gap-1">
            {(traces?.traces ?? []).slice(-6).reverse().map((t) => (
              <div key={t.trace_id} className="rounded-md border border-surface-border bg-surface-card/30 p-2">
                <div className="flex items-center justify-between gap-2">
                  <code className="text-[10px] text-gray-300">{t.symbol}</code>
                  <span className="text-[9px] text-gray-500">
                    {new Date(t.ts * 1000).toLocaleTimeString()}
                  </span>
                </div>
                <div className="mt-1 text-[10px] text-gray-400">
                  {t.directive?.action} · {t.directive?.severity}
                  {t.shadow && <span className="ml-1 text-gray-600">· shadow</span>}
                </div>
              </div>
            ))}
          </div>
        </Card>
      )}

      {/* Detectors list */}
      <Card className="p-3">
        <CardTitle className="mb-2 flex items-center gap-2 text-[12px]">
          <Cpu size={12} /> Dedektörler
        </CardTitle>
        <div className="grid grid-cols-1 gap-2 xl:grid-cols-2">
          {summary.detectors.map((d) => {
            const healthy = d.health?.healthy !== false && !d.error;
            return (
              <div key={d.name} className="rounded-md border border-surface-border bg-surface-card/40 p-2">
                <div className="flex items-center justify-between gap-2">
                  <div className="min-w-0">
                    <div className="truncate text-[11px] font-semibold capitalize">{d.name.replace(/_/g, " ")}</div>
                    {d.channel && (
                      <code className="truncate text-[9px] text-gray-500">{d.channel}</code>
                    )}
                  </div>
                  <StatusPill enabled={true} healthy={healthy} />
                </div>
                {d.health && (
                  <div className="mt-1.5 flex flex-wrap gap-1">
                    {Object.entries(d.health)
                      .filter(([k, v]) => typeof v === "number" && !k.endsWith("_ts"))
                      .slice(0, 4)
                      .map(([k, v]) => (
                        <Badge key={k} variant="outline">
                          <span className="text-gray-500">{k}:</span>
                          <span className="font-mono text-gray-200">{fmtNum(v as number)}</span>
                        </Badge>
                      ))}
                  </div>
                )}
                {d.error && <div className="mt-1 break-words text-[10px] text-bear">{d.error}</div>}
              </div>
            );
          })}
        </div>
      </Card>
    </div>
  );
}

/* ───────── helpers ───────── */
function fmtNum(v: number): string {
  if (!Number.isFinite(v)) return "—";
  if (Math.abs(v) >= 1000) return v.toFixed(0);
  if (Math.abs(v) >= 1) return v.toFixed(2);
  return v.toFixed(3);
}
