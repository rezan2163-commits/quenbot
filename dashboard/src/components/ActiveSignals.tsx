"use client";

import { useEffect, useMemo, useState } from "react";
import {
  ArrowUpCircle, ArrowDownCircle, Clock3, Target, BrainCircuit,
  Trash2, X, CheckCircle2, XCircle, Timer, Activity,
  Trophy, Skull, Flame, Zap, Calendar,
} from "lucide-react";
import { clearSignals, dismissSignal, useSignals, useSignalOutcomes, type Signal } from "@/lib/api";
import { formatInQuenbotTimeZone, parseQuenbotDate, toTimestampMs } from "@/lib/time";
import { Badge, cn } from "./ui/primitives";

type TabKey = "active" | "winners" | "losers";

function toNumber(value: unknown, fallback = 0) {
  const n = Number(value);
  return Number.isFinite(n) ? n : fallback;
}

function formatPrice(value: unknown) {
  const n = Number(value);
  if (!Number.isFinite(n) || n <= 0) return "—";
  if (n >= 1000) return n.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  if (n >= 1) return n.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 6 });
  return n.toLocaleString("en-US", { minimumFractionDigits: 4, maximumFractionDigits: 8 });
}

function fmtTime(d: Date) {
  return formatInQuenbotTimeZone(d, { hour: "2-digit", minute: "2-digit" });
}
function fmtShort(d: Date) {
  return formatInQuenbotTimeZone(d, { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" });
}

function normalizeTargetPct(value: unknown) {
  const n = toNumber(value, 0);
  if (n <= 0) return 0;
  return n > 0.5 ? n / 100 : n;
}

function resolveDirection(o: any): "long" | "short" {
  const meta = o?.metadata || {};
  const candidates: string[] = [
    o?.direction,
    meta.direction,
    meta.position_bias,
    meta?.mamis_context?.direction,
    o?.side,
    o?.signal_type,
    meta.signal_type,
  ]
    .filter((x) => x != null)
    .map((x) => String(x).toLowerCase());
  for (const c of candidates) {
    if (c === "short" || c === "sell" || c.includes("short") || c.includes("sell") || c.includes("bear")) return "short";
    if (c === "long" || c === "buy" || c.includes("long") || c.includes("buy") || c.includes("bull")) return "long";
  }
  // Last-ditch: if target_price < entry_price it's a short bias.
  const entry = toNumber(o?.entry_price ?? meta.entry_price ?? o?.price, 0);
  const target = toNumber(o?.target_price ?? meta.target_price, 0);
  if (entry > 0 && target > 0 && target < entry) return "short";
  return "long";
}

function resolveTargetPct(signal: Signal | any) {
  const meta = signal.metadata || {};
  const direct = normalizeTargetPct(signal.target_pct ?? meta.target_pct ?? meta.predicted_magnitude);
  if (direct > 0) return direct;
  const entry = toNumber(signal.entry_price ?? meta.entry_price ?? signal.price, 0);
  const target = toNumber(signal.target_price ?? meta.target_price, 0);
  if (entry > 0 && target > 0) return Math.abs((target - entry) / entry);
  const horizons = Array.isArray(meta.target_horizons) ? meta.target_horizons : [];
  const strongest = horizons.reduce((best: any, item: any) => {
    const s = toNumber(item?.strength, 0);
    return !best || s > toNumber(best?.strength, 0) ? item : best;
  }, null);
  return normalizeTargetPct(strongest?.target_pct);
}

function resolvePrimaryTarget(signal: Signal | any) {
  const meta = signal.metadata || {};
  const horizons = Array.isArray(meta.target_horizons) ? meta.target_horizons : [];
  const selected = horizons.find((h: any) => h?.label === meta.selected_horizon) || horizons[0] || null;
  const entry = toNumber(signal.entry_price ?? meta.entry_price ?? signal.price, 0);
  const targetPrice = toNumber(signal.target_price ?? meta.target_price ?? selected?.target_price, 0);
  const eta = toNumber(signal.estimated_duration_to_target_minutes ?? meta.estimated_duration_to_target_minutes ?? selected?.eta_minutes, 60);
  return { entry, targetPrice, eta, pct: resolveTargetPct(signal), selected };
}

// Kart üzerindeki "süre kalan" geri sayımını canlı tutar.
function useNowTick(intervalMs = 30_000) {
  const [now, setNow] = useState<number>(() => Date.now());
  useEffect(() => {
    const id = setInterval(() => setNow(Date.now()), intervalMs);
    return () => clearInterval(id);
  }, [intervalMs]);
  return now;
}

function formatRemaining(totalMin: number): string {
  if (totalMin <= 0) return "süre doldu";
  const d = Math.floor(totalMin / (60 * 24));
  const h = Math.floor((totalMin - d * 60 * 24) / 60);
  const m = totalMin - d * 60 * 24 - h * 60;
  if (d > 0) return h > 0 ? `${d} gün ${h} saat` : `${d} gün`;
  if (h > 0) return m > 0 ? `${h} saat ${m} dk` : `${h} saat`;
  return `${m} dk`;
}

function classifyOutcome(o: any): { kind: "win" | "loss" | "neutral"; change: number; hitHorizon?: any; resolvedAt: number; direction: "long" | "short"; exitPrice: number } {
  const meta = o?.metadata || {};
  const entry = toNumber(o?.entry_price ?? meta.entry_price ?? o?.price, 0);
  const exit = toNumber(
    o?.exit_price ?? meta.exit_price ?? o?.close_price ?? meta.close_price ?? o?.actual_price ?? meta.actual_price,
    0,
  );
  const direction = resolveDirection(o);
  const isShort = direction === "short";

  // Real close time — backend provides it as `resolved_at` at the row root.
  const hzns = Array.isArray(meta.target_horizons) ? meta.target_horizons : [];
  const hzClose = hzns
    .map((h: any) => toTimestampMs(h?.closed_at || h?.evaluated_at))
    .filter((t: number) => t > 0);
  const horizonCloseTs = hzClose.length ? Math.max(...hzClose) : 0;
  const signalTs = toTimestampMs(o?.signal_time ?? o?.timestamp);
  const rootResolved = toTimestampMs(o?.resolved_at ?? o?.closed_at ?? o?.evaluated_at);
  const metaResolved = toTimestampMs(meta.closed_at ?? meta.exit_time ?? meta.resolved_at ?? meta.evaluated_at);
  const candidates = [rootResolved, metaResolved, horizonCloseTs].filter((t) => t > 0 && t > signalTs);
  const resolvedAt = candidates.length ? candidates[0] : (rootResolved || metaResolved || horizonCloseTs || signalTs);

  // PRIMARY: real price comparison with correct direction.
  if (entry > 0 && exit > 0) {
    const raw = (exit - entry) / entry;
    const change = (isShort ? -raw : raw) * 100;
    if (change > 0) return { kind: "win", change, resolvedAt, direction, exitPrice: exit };
    if (change < 0) return { kind: "loss", change, resolvedAt, direction, exitPrice: exit };
    return { kind: "neutral", change, resolvedAt, direction, exitPrice: exit };
  }

  // Fallback #1: backend-computed resolved_kind + actual_change_pct
  const kindFromBackend = String(o?.resolved_kind ?? "").toLowerCase();
  const backendPct = toNumber(o?.actual_change_pct, 0) * 100;
  if (kindFromBackend === "win") return { kind: "win", change: Math.abs(backendPct) || Math.abs(toNumber(meta.target_pct, 0) * 100), resolvedAt, direction, exitPrice: exit };
  if (kindFromBackend === "loss") return { kind: "loss", change: -Math.abs(backendPct), resolvedAt, direction, exitPrice: exit };

  // Fallback #2: explicit status
  const status = String(o?.status ?? meta.status ?? "").toLowerCase();
  if (status.includes("target_hit") || status.includes("target_reached") || meta.was_correct === true || meta.target_hit === true) {
    return { kind: "win", change: toNumber(o?.target_pct ?? meta.target_pct, 0) * 100, resolvedAt, direction, exitPrice: exit };
  }
  if (
    status.includes("stop_loss") || status.includes("stopped") || status.includes("expired") ||
    status.includes("target_missed") || status.includes("failed") ||
    meta.was_correct === false || meta.close_reason === "stop_loss"
  ) {
    return { kind: "loss", change: -Math.abs(toNumber(meta.target_pct, 0)) * 100, resolvedAt, direction, exitPrice: exit };
  }

  // Fallback #3: horizons
  const hit = hzns.find((h: any) => h?.status === "hit");
  const allMissed = hzns.length > 0 && hzns.every((h: any) => ["missed", "expired"].includes(String(h?.status)));
  const primary = hit || hzns[0];
  const change = toNumber(primary?.actual_change_pct, 0) * 100;
  if (hit) return { kind: "win", change, hitHorizon: hit, resolvedAt, direction, exitPrice: exit };
  if (allMissed) return { kind: "loss", change, resolvedAt, direction, exitPrice: exit };
  return { kind: "neutral", change, resolvedAt, direction, exitPrice: exit };
}

function TabPill({
  active, onClick, icon: Icon, label, count, tone,
}: { active: boolean; onClick: () => void; icon: any; label: string; count: number; tone: "accent" | "bull" | "bear" }) {
  const toneCls = tone === "bull" ? "text-bull" : tone === "bear" ? "text-bear" : "text-accent";
  return (
    <button
      onClick={onClick}
      className={cn(
        "flex shrink-0 items-center gap-1.5 whitespace-nowrap border-b-2 px-3 py-2 text-[11px] font-medium transition-colors",
        active ? `${toneCls} border-current` : "border-transparent text-gray-500 hover:text-gray-300"
      )}
    >
      <Icon size={12} className={active ? "" : "opacity-60"} />
      {label}
      <span className={cn(
        "rounded-full px-1.5 py-px font-mono text-[9px] tabular-nums",
        active ? "bg-white/10" : "bg-surface-card/60 text-gray-500"
      )}>{count}</span>
    </button>
  );
}

function MiniStat({ label, value, tone = "default" }: { label: string; value: React.ReactNode; tone?: "default" | "bull" | "bear" | "warn" }) {
  const color = tone === "bull" ? "text-bull" : tone === "bear" ? "text-bear" : tone === "warn" ? "text-warn" : "text-gray-100";
  const bg = tone === "bull" ? "bg-bull/10 border-bull/25"
           : tone === "bear" ? "bg-bear/10 border-bear/25"
           : tone === "warn" ? "bg-warn/10 border-warn/25"
           : "bg-surface-card/60 border-surface-border";
  return (
    <div className={cn("min-w-0 rounded-md border px-2 py-1", bg)}>
      <div className="truncate text-[9px] uppercase tracking-wide text-gray-500">{label}</div>
      <div className={cn("truncate font-mono text-[12px] font-bold tabular-nums", color)}>{value}</div>
    </div>
  );
}

function PriceCell({ label, value, icon, muted }: { label: string; value: string; icon?: React.ReactNode; muted?: boolean }) {
  return (
    <div className={cn("min-w-0 rounded-md border border-white/5 px-1.5 py-1", muted ? "bg-black/15" : "bg-black/25")}>
      <div className="flex items-center gap-0.5 text-[8px] uppercase tracking-wide text-gray-500">
        {icon}<span className="truncate">{label}</span>
      </div>
      <div className="truncate font-mono text-[10px] text-white">{value}</div>
    </div>
  );
}

export default function ActiveSignals() {
  const { data: signals, mutate } = useSignals();
  const { data: outcomes } = useSignalOutcomes();
  const [tab, setTab] = useState<TabKey>("active");
  const [busy, setBusy] = useState<number[]>([]);
  const [bulkBusy, setBulkBusy] = useState(false);

  const now = Date.now();
  const SEVEN_DAYS = 7 * 24 * 3600 * 1000;
  const ONE_DAY = 24 * 3600 * 1000;

  const active = useMemo(() => (signals || [])
    .filter((s) => {
      const st = String(s.status || "").toLowerCase();
      return !["failed", "expired", "closed", "dismissed", "filtered_duplicate", "filtered_noise"].includes(st);
    })
    .sort((a, b) => toTimestampMs(b.signal_time || b.timestamp) - toTimestampMs(a.signal_time || a.timestamp)),
    [signals]);

  const { winners, losers } = useMemo(() => {
    const winners: any[] = [];
    const losers: any[] = [];
    for (const o of outcomes || []) {
      const c = classifyOutcome(o);
      const age = now - c.resolvedAt;
      if (c.kind === "win" && age <= SEVEN_DAYS) winners.push({ ...o, _outcome: c });
      else if (c.kind === "loss" && age <= ONE_DAY) losers.push({ ...o, _outcome: c });
    }
    winners.sort((a, b) => b._outcome.resolvedAt - a._outcome.resolvedAt);
    losers.sort((a, b) => b._outcome.resolvedAt - a._outcome.resolvedAt);
    return { winners, losers };
  }, [outcomes, now]);

  const kpi = useMemo(() => {
    const list = outcomes || [];
    let wins = 0, losses = 0, totalPnl = 0;
    for (const o of list) {
      const c = classifyOutcome(o);
      if (c.kind === "win") wins++; else if (c.kind === "loss") losses++;
      totalPnl += c.change;
    }
    const total = wins + losses;
    const winRate = total > 0 ? (wins / total) * 100 : 0;
    const avgPnl = list.length ? totalPnl / list.length : 0;
    return { wins, losses, winRate, avgPnl };
  }, [outcomes]);

  async function handleDismiss(id: number) {
    setBusy((p) => [...p, id]);
    try {
      await dismissSignal(id);
      await mutate((cur) => (cur || []).filter((x) => x.id !== id), { revalidate: false });
    } finally {
      setBusy((p) => p.filter((x) => x !== id));
      void mutate();
    }
  }

  async function handleClearVisible() {
    if (!active.length) return;
    setBulkBusy(true);
    const ids = active.map((x) => x.id);
    try {
      await clearSignals(ids);
      await mutate((cur) => (cur || []).filter((x) => !ids.includes(x.id)), { revalidate: false });
    } finally {
      setBulkBusy(false);
      void mutate();
    }
  }

  return (
    <div className="flex h-full min-h-0 w-full flex-col overflow-hidden bg-surface text-gray-200">
      <header className="flex items-center justify-between gap-2 border-b border-surface-border bg-surface-card/40 px-3 py-2">
        <div className="flex min-w-0 items-center gap-2">
          <Flame size={14} className="shrink-0 text-accent" />
          <div className="min-w-0">
            <div className="truncate text-[11px] font-semibold">Hedef Kartları</div>
            <div className="truncate text-[9px] text-gray-500">
              {tab === "active" ? "canlı sinyaller, %2+ hedef"
                : tab === "winners" ? "son 7 gün kazananlar"
                : "son 24 saat kaybedenler"}
            </div>
          </div>
        </div>
        {tab === "active" && (
          <button
            onClick={handleClearVisible}
            disabled={bulkBusy || active.length === 0}
            className="inline-flex shrink-0 items-center gap-1 rounded-md border border-rose-400/25 bg-rose-400/10 px-2 py-1 text-[10px] text-rose-200 disabled:cursor-not-allowed disabled:opacity-40"
          >
            <Trash2 size={10} />
            Temizle
          </button>
        )}
      </header>

      <div className="border-b border-surface-border/60 bg-black/20 px-2 py-2">
        <div className="grid grid-cols-4 gap-1.5">
          <MiniStat label="Kazanan" value={kpi.wins} tone="bull" />
          <MiniStat label="Kaybeden" value={kpi.losses} tone="bear" />
          <MiniStat label="Başarı" value={`%${kpi.winRate.toFixed(0)}`} tone={kpi.winRate >= 55 ? "bull" : kpi.winRate >= 40 ? "warn" : "bear"} />
          <MiniStat label="Ort. PnL" value={`${kpi.avgPnl >= 0 ? "+" : ""}${kpi.avgPnl.toFixed(2)}%`} tone={kpi.avgPnl >= 0 ? "bull" : "bear"} />
        </div>
      </div>

      <nav className="flex shrink-0 overflow-x-auto border-b border-surface-border bg-surface-card/30 custom-scrollbar">
        <TabPill active={tab === "active"}  onClick={() => setTab("active")}  icon={Activity} label="Aktif"       count={active.length}  tone="accent" />
        <TabPill active={tab === "winners"} onClick={() => setTab("winners")} icon={Trophy}   label="Kazananlar"  count={winners.length} tone="bull" />
        <TabPill active={tab === "losers"}  onClick={() => setTab("losers")}  icon={Skull}    label="Kaybedenler" count={losers.length}  tone="bear" />
      </nav>

      <div className="flex-1 min-h-0 overflow-y-auto custom-scrollbar">
        {tab === "active"  && <ActiveList signals={active} busy={busy} onDismiss={handleDismiss} />}
        {tab === "winners" && <OutcomeList items={winners} kind="win" />}
        {tab === "losers"  && <OutcomeList items={losers}  kind="loss" />}
      </div>
    </div>
  );
}

function ActiveList({ signals, busy, onDismiss }: { signals: Signal[]; busy: number[]; onDismiss: (id: number) => void }) {
  if (signals.length === 0) {
    return (
      <div className="flex h-full items-center justify-center px-6 text-center text-xs text-gray-600">
        Son 24 saatte %2+ hedefli aktif sinyal yok
      </div>
    );
  }
  return (
    <div className="grid grid-cols-1 gap-2 p-2 xl:grid-cols-2">
      {signals.map((s) => (
        <ActiveCard key={s.id} s={s} dismissing={busy.includes(s.id)} onDismiss={() => onDismiss(s.id)} />
      ))}
    </div>
  );
}

function ActiveCard({ s, dismissing, onDismiss }: { s: Signal; dismissing: boolean; onDismiss: () => void }) {
  const isLong = ["long", "buy"].includes((s.direction || "").toLowerCase());
  const meta = (s.metadata || {}) as any;
  const { entry, targetPrice, eta, pct } = resolvePrimaryTarget(s);
  const signalAt = parseQuenbotDate(s.signal_time || s.timestamp);
  // Hedef süresi tek kaynağa bağlı: sinyalin kendi horizon'u (eta, dakika).
  // Backend artık expires_at'i horizon'a göre yazıyor; fakat eski kayıtlarda
  // expires_at = timestamp+24h olduğu için UI'da çelişkili görünüyordu.
  // Tutarlılık için hem "hedef" etiketi hem de "kalan" geri sayımı aynı eta'dan türetiliyor.
  const targetAt = new Date(signalAt.getTime() + eta * 60000);
  const now = useNowTick(30_000);
  const totalMs = targetAt.getTime() - signalAt.getTime();
  const progress = totalMs > 0 ? Math.min(1, Math.max(0, (now - signalAt.getTime()) / totalMs)) : 0;
  const remainMin = Math.max(0, Math.floor((targetAt.getTime() - now) / 60000));
  const remainLabel = formatRemaining(remainMin);

  const currentPrice = toNumber(s.current_price_at_signal ?? meta.current_price_at_signal ?? s.price, 0);
  const conf = (toNumber(s.confidence) * 100).toFixed(0);
  const source = String(s.source || meta.source || "—").replace(/_/g, " ");
  const horizons: any[] = Array.isArray(meta.target_horizons) ? meta.target_horizons : [];
  const reason = meta.reason || s.signal_type;

  const cardTone = isLong
    ? "border-emerald-400/25 bg-gradient-to-br from-emerald-500/10 via-surface-card/60 to-transparent"
    : "border-rose-400/25 bg-gradient-to-br from-rose-500/10 via-surface-card/60 to-transparent";

  return (
    <div className={cn("relative flex min-w-0 flex-col gap-2 rounded-lg border p-2.5 shadow-sm transition-colors", cardTone)}>
      <div className="flex min-w-0 items-start justify-between gap-2">
        <div className="flex min-w-0 items-start gap-1.5">
          {isLong ? <ArrowUpCircle size={16} className="mt-0.5 shrink-0 text-bull" />
                  : <ArrowDownCircle size={16} className="mt-0.5 shrink-0 text-bear" />}
          <div className="min-w-0">
            <div className="flex flex-wrap items-center gap-1">
              <span className="truncate text-[13px] font-bold tracking-wide text-white">{s.symbol}</span>
              <Badge variant={isLong ? "success" : "danger"}>{isLong ? "LONG" : "SHORT"}</Badge>
              <Badge variant="muted">{(s.market_type || "spot").toUpperCase()}</Badge>
              {(() => {
                const st = String(s.status || "").toLowerCase();
                if (st === "risk_rejected") return <Badge variant="warn" title="Paper sim açılmadı (risk limiti); kart ETA boyunca gözlemde.">gözlem • risk</Badge>;
                if (st === "filtered_duplicate") return <Badge variant="warn" title="Aynı sembolde aktif kart var; bu sinyal kopyası gözlemde.">gözlem • kopya</Badge>;
                return null;
              })()}
            </div>
            <div className="truncate text-[9px] text-gray-500">{reason}</div>
          </div>
        </div>
        <div className="flex shrink-0 items-start gap-1">
          <div className="text-right leading-tight">
            <div className="font-mono text-[11px] font-bold text-warn">%{conf}</div>
            <div className="text-[9px] text-gray-400">hedef %{(pct * 100).toFixed(1)}</div>
          </div>
          <button
            onClick={onDismiss}
            disabled={dismissing}
            className="rounded-md border border-white/10 bg-black/20 p-1 text-gray-400 transition-colors hover:text-white disabled:opacity-40"
            title="Kartı kapat"
          >
            <X size={11} />
          </button>
        </div>
      </div>

      <div className="grid grid-cols-3 gap-1">
        <PriceCell label="Giriş" value={`$${formatPrice(entry || currentPrice)}`} />
        <PriceCell label="Anlık" value={`$${formatPrice(currentPrice)}`} muted />
        <PriceCell label="Hedef" value={`$${formatPrice(targetPrice)}`} icon={<Target size={8} />} />
      </div>

      <div className="rounded-md border border-white/5 bg-black/30 px-2 py-1.5">
        <div className="flex items-center justify-between text-[9px] text-gray-400">
          <div className="flex items-center gap-1">
            <Calendar size={9} className="text-sky-300" />
            <span className="font-mono text-[10px] text-white">{fmtTime(signalAt)}</span>
            <span className="text-gray-500">başladı</span>
          </div>
          <div className="flex items-center gap-1">
            <span className="text-gray-500">hedef</span>
            <span className="font-mono text-[10px] text-white">{fmtTime(targetAt)}</span>
            <Target size={9} className="text-cyan-300" />
          </div>
        </div>
        <div className="mt-1 h-1 overflow-hidden rounded-full bg-white/5">
          <div
            className={cn("h-full transition-all", isLong ? "bg-bull/70" : "bg-bear/70")}
            style={{ width: `${progress * 100}%` }}
          />
        </div>
        <div className="mt-1 flex items-center justify-between text-[9px]">
          <span className="text-gray-500">{fmtShort(signalAt)}</span>
          <span
            className={cn(
              "inline-flex items-center gap-1 rounded-full border px-1.5 py-0.5 font-mono tabular-nums",
              remainMin <= 0
                ? "border-rose-400/30 bg-rose-500/10 text-rose-300"
                : remainMin <= 30
                ? "border-warn/30 bg-warn/10 text-warn"
                : "border-cyan-400/30 bg-cyan-400/10 text-cyan-300",
            )}
            title={`Süre kalan: ${remainLabel}`}
          >
            <Timer size={9} />
            <span>{remainMin <= 0 ? "süre doldu" : `süre kalan: ${remainLabel}`}</span>
          </span>
        </div>
      </div>

      {horizons.length > 0 && (
        <div className="flex flex-wrap gap-1">
          {horizons.map((h: any) => {
            const st = h.status || "active";
            const tonePill = st === "hit" ? "border-bull/30 bg-bull/10 text-bull"
                          : st === "missed" ? "border-bear/30 bg-bear/10 text-bear"
                          : st === "expired" ? "border-gray-500/30 bg-gray-500/10 text-gray-400"
                          : st === "near_miss" ? "border-warn/30 bg-warn/10 text-warn"
                          : "border-cyan-400/30 bg-cyan-400/10 text-cyan-300";
            const icon = st === "hit" ? <CheckCircle2 size={9} />
                       : st === "missed" ? <XCircle size={9} />
                       : st === "near_miss" ? <Zap size={9} />
                       : st === "expired" ? <Clock3 size={9} />
                       : <Timer size={9} />;
            const change = toNumber(h.actual_change_pct, 0) * 100;
            const hPct = normalizeTargetPct(h.target_pct) * 100;
            return (
              <span key={h.label} className={cn("inline-flex items-center gap-1 rounded-full border px-1.5 py-0.5 text-[9px] font-medium", tonePill)}>
                {icon}
                <span className="font-bold">{h.label}</span>
                <span className="opacity-70">%{hPct.toFixed(1)}</span>
                {st === "hit" && <span>✓ +{change.toFixed(2)}%</span>}
                {st === "missed" && <span>{change >= 0 ? "+" : ""}{change.toFixed(2)}%</span>}
              </span>
            );
          })}
        </div>
      )}

      <div className="flex flex-wrap items-center gap-1 text-[9px]">
        <Badge variant="info"><BrainCircuit size={9} />{source}</Badge>
        {toNumber(meta.avg_similarity ?? meta.similarity, 0) > 0 && (
          <Badge variant="outline">sim %{(toNumber(meta.avg_similarity ?? meta.similarity) * 100).toFixed(0)}</Badge>
        )}
        {toNumber(meta.match_count, 0) > 0 && <Badge variant="outline">{toNumber(meta.match_count)} pattern</Badge>}
        {toNumber(meta.quality_score, 0) > 0 && <Badge variant="outline">Q %{(toNumber(meta.quality_score) * 100).toFixed(0)}</Badge>}
        {meta.mamis_ensemble?.aligned && <Badge variant="success">MAMIS ✓</Badge>}
        {meta.mamis_ensemble?.opposite && <Badge variant="danger">MAMIS ✗</Badge>}
      </div>
    </div>
  );
}

function OutcomeList({ items, kind }: { items: any[]; kind: "win" | "loss" }) {
  if (items.length === 0) {
    const msg = kind === "win" ? "Son 7 günde kazanan kart yok" : "Son 24 saatte kaybeden kart yok";
    return (
      <div className="flex h-full items-center justify-center px-6 text-center text-xs text-gray-600">{msg}</div>
    );
  }
  return (
    <div className="grid grid-cols-1 gap-2 p-2 xl:grid-cols-2">
      {items.map((o, i) => <OutcomeCard key={`${o.id ?? i}-${o._outcome.resolvedAt}`} o={o} kind={kind} />)}
    </div>
  );
}

function OutcomeCard({ o, kind }: { o: any; kind: "win" | "loss" }) {
  const isLong = o._outcome.direction === "long";
  const meta = (o.metadata || {}) as any;
  const { entry, targetPrice, eta } = resolvePrimaryTarget(o);
  const signalAt = parseQuenbotDate(o.signal_time || o.timestamp);
  const resolvedAt = new Date(o._outcome.resolvedAt || signalAt.getTime() + eta * 60000);
  const conf = (toNumber(o.confidence) * 100).toFixed(0);
  const change = o._outcome.change;
  const horizons: any[] = Array.isArray(meta.target_horizons) ? meta.target_horizons : [];
  const hitH = o._outcome.hitHorizon;
  const exitPrice = toNumber(o._outcome.exitPrice, 0) || toNumber(hitH?.actual_price, 0);

  const cardTone = kind === "win"
    ? "border-emerald-400/30 bg-gradient-to-br from-emerald-500/12 via-surface-card/50 to-transparent"
    : "border-rose-400/30 bg-gradient-to-br from-rose-500/12 via-surface-card/50 to-transparent";

  const now = Date.now();
  const ttl = kind === "win" ? 7 * 24 * 3600 * 1000 : 24 * 3600 * 1000;
  const expiresAt = o._outcome.resolvedAt + ttl;
  const remain = Math.max(0, expiresAt - now);
  const remainH = Math.floor(remain / 3600000);
  const remainD = Math.floor(remainH / 24);
  const retentionLabel = kind === "win"
    ? (remainD >= 1 ? `${remainD}g kaldı` : `${remainH}s kaldı`)
    : `${remainH}s kaldı`;

  const reason = meta.reason || o.signal_type;

  return (
    <div className={cn("flex min-w-0 flex-col gap-2 rounded-lg border p-2.5 shadow-sm", cardTone)}>
      <div className="flex min-w-0 items-start justify-between gap-2">
        <div className="flex min-w-0 items-start gap-1.5">
          {kind === "win"
            ? <Trophy size={16} className="mt-0.5 shrink-0 text-bull" />
            : <Skull  size={16} className="mt-0.5 shrink-0 text-bear" />}
          <div className="min-w-0">
            <div className="flex flex-wrap items-center gap-1">
              <span className="truncate text-[13px] font-bold text-white">{o.symbol}</span>
              <Badge variant={isLong ? "success" : "danger"}>{isLong ? "LONG" : "SHORT"}</Badge>
              {hitH && <Badge variant="info">{hitH.label}</Badge>}
            </div>
            <div className="truncate text-[9px] text-gray-500">{reason}</div>
          </div>
        </div>
        <div className="text-right leading-tight">
          <div className={cn("font-mono text-sm font-bold tabular-nums",
            kind === "win" ? "text-bull" : "text-bear")}>
            {change >= 0 ? "+" : ""}{change.toFixed(2)}%
          </div>
          <div className="text-[9px] text-gray-500">güven %{conf}</div>
        </div>
      </div>

      <div className="grid grid-cols-3 gap-1">
        <PriceCell label="Giriş" value={`$${formatPrice(entry)}`} />
        <PriceCell label={kind === "win" ? "Ulaştı" : "Kapanış"} value={`$${formatPrice(exitPrice || 0)}`} muted />
        <PriceCell label="Hedef" value={`$${formatPrice(targetPrice)}`} icon={<Target size={8} />} />
      </div>

      <div className="rounded-md border border-white/5 bg-black/30 px-2 py-1.5">
        <div className="flex items-center justify-between text-[9px]">
          <div className="flex items-center gap-1">
            <Calendar size={9} className="text-sky-300" />
            <span className="font-mono text-[10px] text-white">{fmtShort(signalAt)}</span>
            <span className="text-gray-500">giriş</span>
          </div>
          <div className="flex items-center gap-1">
            <span className="text-gray-500">kapanış</span>
            <span className="font-mono text-[10px] text-white">{fmtShort(resolvedAt)}</span>
            {kind === "win"
              ? <CheckCircle2 size={9} className="text-bull" />
              : <XCircle size={9} className="text-bear" />}
          </div>
        </div>
      </div>

      {horizons.length > 0 && (
        <div className="flex flex-wrap gap-1">
          {horizons.map((h: any) => {
            const st = h.status || "—";
            const tone = st === "hit" ? "success" : st === "missed" ? "danger" : st === "near_miss" ? "warn" : "muted";
            return (
              <Badge key={h.label} variant={tone as any}>
                {h.label} · {st}
              </Badge>
            );
          })}
        </div>
      )}

      <div className="flex items-center justify-between border-t border-white/5 pt-1.5 text-[9px]">
        <div className="flex items-center gap-1">
          <BrainCircuit size={9} className="text-accent" />
          <span className="text-gray-400">ana beyne öğretildi</span>
        </div>
        <div className="flex items-center gap-1">
          <Timer size={9} className="text-gray-500" />
          <span className={cn("font-mono", kind === "win" ? "text-bull/80" : "text-bear/80")}>{retentionLabel}</span>
        </div>
      </div>
    </div>
  );
}
