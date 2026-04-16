"use client";

import { useState } from "react";
import { clearSignals, dismissSignal, useSignals, useSignalOutcomes } from "@/lib/api";
import { ArrowUpCircle, ArrowDownCircle, Clock3, Target, BadgeInfo, BrainCircuit, Building2, Trash2, X, CheckCircle2, XCircle, Timer, Activity, TrendingUp, Layers } from "lucide-react";
import { formatInQuenbotTimeZone, parseQuenbotDate, toTimestampMs } from "@/lib/time";

function toNumber(value: unknown, fallback = 0) {
  const n = Number(value);
  return Number.isFinite(n) ? n : fallback;
}

function formatPrice(value: unknown) {
  const numeric = Number(value);
  if (!Number.isFinite(numeric)) return "-";
  if (numeric >= 1000) return numeric.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  if (numeric >= 1) return numeric.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 6 });
  return numeric.toLocaleString("en-US", { minimumFractionDigits: 4, maximumFractionDigits: 8 });
}

function formatCountdown(target: Date) {
  const diff = target.getTime() - Date.now();
  if (diff <= 0) return "Süre doldu";
  const totalMinutes = Math.floor(diff / 60000);
  const hours = Math.floor(totalMinutes / 60);
  const minutes = totalMinutes % 60;
  return `${hours}s ${minutes}dk`;
}

function normalizeTargetPct(value: unknown) {
  const numeric = toNumber(value, 0);
  if (numeric <= 0) return 0;
  return numeric > 0.5 ? numeric / 100 : numeric;
}

function horizonCountdown(signalAt: Date, etaMin: number) {
  const deadline = new Date(signalAt.getTime() + etaMin * 60000);
  const diff = deadline.getTime() - Date.now();
  if (diff <= 0) return null; // expired
  const totalMin = Math.floor(diff / 60000);
  const h = Math.floor(totalMin / 60);
  const m = totalMin % 60;
  return h > 0 ? `${h}s ${m}dk` : `${m}dk`;
}

function resolveTargetPct(signal: any) {
  const meta = signal.metadata || {};
  const direct = normalizeTargetPct(signal.target_pct ?? meta.target_pct ?? meta.predicted_magnitude);
  if (direct > 0) return direct;

  const entry = toNumber(signal.entry_price ?? meta.entry_price ?? signal.price, 0);
  const target = toNumber(signal.target_price ?? meta.target_price, 0);
  if (entry > 0 && target > 0) {
    return Math.abs((target - entry) / entry);
  }

  const horizons = Array.isArray(meta.target_horizons) ? meta.target_horizons : [];
  const strongest = horizons.reduce((best: any, item: any) => {
    const strength = toNumber(item?.strength, 0);
    return !best || strength > toNumber(best?.strength, 0) ? item : best;
  }, null);
  return normalizeTargetPct(strongest?.target_pct);
}

function resolvePrimaryTarget(signal: any) {
  const meta = signal.metadata || {};
  const horizons = Array.isArray(meta.target_horizons) ? meta.target_horizons : [];
  const selected = horizons.find((item: any) => item?.label === meta.selected_horizon) || horizons[0] || null;
  const entry = toNumber(signal.entry_price ?? meta.entry_price ?? signal.price, 0);
  const targetPrice = toNumber(signal.target_price ?? meta.target_price ?? selected?.target_price, 0);
  const eta = toNumber(signal.estimated_duration_to_target_minutes ?? meta.estimated_duration_to_target_minutes ?? selected?.eta_minutes, 60);
  const pct = resolveTargetPct(signal);
  return {
    entry,
    targetPrice,
    eta,
    pct,
    selected,
  };
}

export default function ActiveSignals() {
  const { data: signals, mutate } = useSignals();
  const { data: outcomes } = useSignalOutcomes();
  const [busy, setBusy] = useState<number[]>([]);
  const [bulkBusy, setBulkBusy] = useState(false);
  // Server-side filter (isActionableTargetCard) already enforces:
  // status IN (pending/active/open/processed/risk_rejected), confidence >= 0.62, quality >= 0.64,
  // targetPct >= 0.02, age < 24h, source strategist/pattern_matcher
  // Client only filters dismissed statuses as safety net
  const active = (signals || [])
    .filter((s) => {
      const normalizedStatus = String(s.status || "").toLowerCase();
      return !["failed", "expired", "closed", "dismissed", "filtered_duplicate", "filtered_noise"].includes(normalizedStatus);
    })
    .sort((a, b) => toTimestampMs(b.signal_time || b.timestamp) - toTimestampMs(a.signal_time || a.timestamp));
  const movementList = [...active]
    .sort((a, b) => {
      const aScore = toNumber(a.target_pct, 0) * 100 + toNumber(a.confidence) * 10;
      const bScore = toNumber(b.target_pct, 0) * 100 + toNumber(b.confidence) * 10;
      return bScore - aScore;
    })
    .slice(0, 12);

  async function handleDismiss(signalId: number) {
    setBusy((prev) => [...prev, signalId]);
    try {
      await dismissSignal(signalId);
      await mutate((current) => (current || []).filter((item) => item.id !== signalId), { revalidate: false });
    } finally {
      setBusy((prev) => prev.filter((item) => item !== signalId));
      void mutate();
    }
  }

  async function handleClearVisible() {
    if (!active.length) return;
    setBulkBusy(true);
    const ids = active.map((item) => item.id);
    try {
      await clearSignals(ids);
      await mutate((current) => (current || []).filter((item) => !ids.includes(item.id)), { revalidate: false });
    } finally {
      setBulkBusy(false);
      void mutate();
    }
  }

  return (
    <div className="h-full flex flex-col overflow-hidden bg-[radial-gradient(circle_at_top,_rgba(34,197,94,0.08),_transparent_42%),linear-gradient(180deg,rgba(15,23,42,0.94),rgba(2,6,23,0.96))]">
      <div className="flex items-center justify-between px-4 py-3 border-b border-surface-border/70">
        <div>
          <div className="text-xs font-semibold text-gray-200 tracking-[0.18em]">%2+ AKTİF HEDEF KARTLARI</div>
          <div className="text-[10px] text-gray-500 mt-1">24 saat içinde yaşayan uzun ve kısa sinyaller</div>
        </div>
        <div className="flex items-center gap-2">
          <button
            onClick={handleClearVisible}
            disabled={bulkBusy || active.length === 0}
            className="inline-flex items-center gap-1 rounded-full border border-rose-400/20 bg-rose-400/10 px-2.5 py-1 text-[10px] text-rose-200 disabled:cursor-not-allowed disabled:opacity-40"
          >
            <Trash2 size={11} />
            Temizle
          </button>
          <span className="text-[10px] text-emerald-300/90 border border-emerald-400/20 bg-emerald-400/10 rounded-full px-2 py-1">{active.length} canlı kart</span>
        </div>
      </div>

      {/* ── 📊 SONUÇ PANELİ — son 48 saatte kapanan sinyaller ── */}
      {(() => {
        const list = outcomes || [];
        if (!list.length) return null;
        let wins = 0, losses = 0, totalPnl = 0;
        for (const o of list) {
          const hzns = Array.isArray(o?.metadata?.target_horizons) ? o.metadata.target_horizons : [];
          const hit = hzns.some((h: any) => h?.status === 'hit');
          const missed = hzns.length > 0 && hzns.every((h: any) => ['missed', 'expired'].includes(String(h?.status)));
          if (hit) wins++; else if (missed) losses++;
          const primary = hzns[0];
          const change = toNumber(primary?.actual_change_pct, 0);
          totalPnl += change;
        }
        const total = wins + losses;
        const winRate = total > 0 ? (wins / total) * 100 : 0;
        const avgPnl = list.length ? (totalPnl / list.length) * 100 : 0;
        return (
          <div className="border-b border-surface-border/50 px-3 py-2 bg-black/20">
            <div className="grid grid-cols-4 gap-2 text-[10px]">
              <div className="rounded-md bg-emerald-400/10 border border-emerald-400/20 px-2 py-1">
                <div className="text-[9px] text-emerald-300/80">Kazanan</div>
                <div className="text-emerald-200 font-bold text-[12px]">{wins}</div>
              </div>
              <div className="rounded-md bg-rose-400/10 border border-rose-400/20 px-2 py-1">
                <div className="text-[9px] text-rose-300/80">Kaybeden</div>
                <div className="text-rose-200 font-bold text-[12px]">{losses}</div>
              </div>
              <div className="rounded-md bg-amber-400/10 border border-amber-400/20 px-2 py-1">
                <div className="text-[9px] text-amber-300/80">Başarı %</div>
                <div className="text-amber-200 font-bold text-[12px]">%{winRate.toFixed(0)}</div>
              </div>
              <div className={`rounded-md border px-2 py-1 ${avgPnl >= 0 ? 'bg-emerald-400/10 border-emerald-400/20' : 'bg-rose-400/10 border-rose-400/20'}`}>
                <div className="text-[9px] text-gray-400">Ort. PnL</div>
                <div className={`font-bold text-[12px] ${avgPnl >= 0 ? 'text-emerald-200' : 'text-rose-200'}`}>
                  {avgPnl >= 0 ? '+' : ''}{avgPnl.toFixed(2)}%
                </div>
              </div>
            </div>
          </div>
        );
      })()}

      <div className="border-b border-surface-border/50 px-3 py-2">
        <div className="mb-2 text-[10px] font-semibold uppercase tracking-[0.16em] text-gray-500">Hareket Beklenen Coinler</div>
        <div className="flex gap-1.5 overflow-x-auto pb-1 custom-scrollbar">
          {movementList.length === 0 ? (
            <span className="text-[10px] text-gray-600">Liste boş</span>
          ) : (
            movementList.map((signal) => {
              const targetPct = resolveTargetPct(signal) * 100;
              const isLong = (signal.direction || "").toLowerCase() === "long" || (signal.direction || "").toLowerCase() === "buy";
              return (
                <div
                  key={`mover-${signal.id}`}
                  className={`min-w-fit rounded-full border px-2.5 py-1 text-[10px] font-medium whitespace-nowrap ${
                    isLong
                      ? "border-emerald-400/25 bg-emerald-400/10 text-emerald-200"
                      : "border-rose-400/25 bg-rose-400/10 text-rose-200"
                  }`}
                >
                  {signal.symbol} • %{targetPct.toFixed(1)}
                </div>
              );
            })
          )}
        </div>
      </div>

      <div className="flex-1 overflow-y-auto custom-scrollbar">
        {active.length === 0 ? (
          <div className="flex items-center justify-center h-full text-gray-600 text-xs px-6 text-center">Son 24 saatte %2 hedefli aktif sinyal yok</div>
        ) : (
          <div className="grid grid-cols-1 gap-2 p-2 xl:grid-cols-2">
            {active.map((s) => {
              const isLong = (s.direction || "").toLowerCase() === "long" || (s.direction || "").toLowerCase() === "buy";
              const meta = s.metadata || {};
              const learningProfile = meta.learning_profile || null;
              const primaryTarget = resolvePrimaryTarget(s);
              const entry = primaryTarget.entry;
              const target = primaryTarget.targetPrice;
              const currentAtSignal = s.current_price_at_signal ?? meta.current_price_at_signal ?? s.price;
              const etaMin = primaryTarget.eta;
              const reason = meta.reason || s.signal_type;
              const conf = (toNumber(s.confidence) * 100).toFixed(0);
              const signalAt = parseQuenbotDate(s.signal_time || s.timestamp);
              const expiresAt = parseQuenbotDate(s.expires_at || meta.expires_at || signalAt.getTime() + 4 * 3600000);
              const targetPct = primaryTarget.pct * 100;
              const source = s.source || meta.source || meta.signal_provider || "unknown";
              const sourceModel = s.source_model || meta.source_model || "unknown";
              const horizons = Array.isArray(meta.target_horizons) ? meta.target_horizons : [];
              const similarity = toNumber(meta.avg_similarity ?? meta.similarity, 0);
              const matchCount = toNumber(meta.match_count, 0);
              const quality = toNumber(meta.quality_score, 0);
              const density = toNumber(meta.data_density, 0);
              const mamis = meta.mamis_ensemble || meta.mamis_context || null;
              const tfPreds = meta.timeframe_predictions || {};

              return (
                <div
                  key={s.id}
                  className={`rounded-xl border p-2.5 transition-colors shadow-[0_8px_24px_rgba(0,0,0,0.16)] ${
                    isLong
                      ? "border-emerald-400/25 bg-[linear-gradient(135deg,rgba(16,185,129,0.18),rgba(15,23,42,0.55))] hover:bg-[linear-gradient(135deg,rgba(16,185,129,0.24),rgba(15,23,42,0.62))]"
                      : "border-rose-400/25 bg-[linear-gradient(135deg,rgba(244,63,94,0.16),rgba(15,23,42,0.55))] hover:bg-[linear-gradient(135deg,rgba(244,63,94,0.22),rgba(15,23,42,0.62))]"
                  }`}
                >
                  {/* ── HEADER: Symbol + Direction + Badges ── */}
                  <div className="mb-2 flex items-start justify-between gap-2">
                    <div className="flex items-start gap-2">
                      {isLong ? (
                        <ArrowUpCircle size={16} className="text-emerald-300 mt-0.5" />
                      ) : (
                        <ArrowDownCircle size={16} className="text-rose-300 mt-0.5" />
                      )}
                      <div>
                        <div className="flex items-center gap-1.5 flex-wrap">
                          <span className="text-[13px] font-bold text-white tracking-wide">{s.symbol}</span>
                          <span className={`text-[9px] font-bold px-1.5 py-0.5 rounded-full ${isLong ? "text-emerald-200 bg-emerald-400/15" : "text-rose-200 bg-rose-400/15"}`}>
                            {isLong ? "LONG" : "SHORT"}
                          </span>
                          <span className="text-[8px] px-1 py-0.5 rounded-full border border-white/10 text-gray-400 uppercase">{s.market_type || "spot"}</span>
                          <span className="text-[8px] px-1 py-0.5 rounded-full border border-white/10 text-gray-400 uppercase">{s.exchange || "mixed"}</span>
                        </div>
                        <div className="mt-0.5 text-[9px] text-gray-500">{reason}</div>
                      </div>
                    </div>
                    <div className="flex items-start gap-2">
                      <div className="text-right">
                        <div className="text-[11px] text-amber-300 font-bold">%{conf} Güven</div>
                        <div className="text-[9px] text-gray-400">Hedef %{targetPct.toFixed(1)}</div>
                      </div>
                      <button
                        onClick={() => void handleDismiss(s.id)}
                        disabled={busy.includes(s.id)}
                        className="rounded-full border border-white/10 bg-black/20 p-1 text-gray-300 hover:text-white disabled:opacity-40"
                      >
                        <X size={12} />
                      </button>
                    </div>
                  </div>

                  {/* ── FİYAT BİLGİLERİ ── */}
                  <div className="grid grid-cols-3 gap-1 text-[9px] text-gray-300 mb-2">
                    <div className="rounded-lg bg-black/25 border border-white/5 px-2 py-1.5">
                      <div className="text-[8px] text-gray-500">Sinyal Fiyatı</div>
                      <div className="font-mono text-white mt-0.5 text-[10px]">${formatPrice(currentAtSignal)}</div>
                    </div>
                    <div className="rounded-lg bg-black/25 border border-white/5 px-2 py-1.5">
                      <div className="text-[8px] text-gray-500">Giriş</div>
                      <div className="font-mono text-white mt-0.5 text-[10px]">${formatPrice(entry)}</div>
                    </div>
                    <div className="rounded-lg bg-black/25 border border-white/5 px-2 py-1.5">
                      <div className="text-[8px] text-gray-500 flex items-center gap-0.5"><Target size={8} />Hedef</div>
                      <div className="font-mono text-white mt-0.5 text-[10px]">${formatPrice(target)}</div>
                    </div>
                  </div>

                  {/* ── ⏰ 1 SAAT SONUÇ ROZETİ (brain learning) ── */}
                  {(() => {
                    const h1 = horizons.find((h: any) => h.label === "1h");
                    if (!h1) return null;
                    const st = h1.status || "active";
                    const change = toNumber(h1.actual_change_pct, 0) * 100;
                    const remain = horizonCountdown(signalAt, toNumber(h1.eta_minutes, 60));
                    const base = "mb-2 rounded-lg border px-2.5 py-1.5 flex items-center justify-between text-[10px]";
                    if (st === "hit") return (
                      <div className={`${base} bg-emerald-400/15 border-emerald-400/30 text-emerald-200`}>
                        <span className="font-bold">⏰ 1 SAAT SONUÇ</span>
                        <span className="font-bold">✓ KAR +{change.toFixed(2)}% — Ana beyne öğretildi</span>
                      </div>
                    );
                    if (st === "missed") return (
                      <div className={`${base} bg-rose-400/15 border-rose-400/30 text-rose-200`}>
                        <span className="font-bold">⏰ 1 SAAT SONUÇ</span>
                        <span className="font-bold">✗ ZARAR {change >= 0 ? "+" : ""}{change.toFixed(2)}% — Ana beyne öğretildi</span>
                      </div>
                    );
                    if (st === "near_miss") return (
                      <div className={`${base} bg-amber-400/15 border-amber-400/30 text-amber-200`}>
                        <span className="font-bold">⏰ 1 SAAT SONUÇ</span>
                        <span className="font-bold">⚡ YAKIN KAÇIŞ {change >= 0 ? "+" : ""}{change.toFixed(2)}%</span>
                      </div>
                    );
                    return (
                      <div className={`${base} bg-cyan-400/10 border-cyan-400/25 text-cyan-200`}>
                        <span className="font-bold">⏰ 1 SAAT SONUÇ</span>
                        <span>{remain ? `${remain} sonra değerlendirilecek` : "Değerlendiriliyor..."}</span>
                      </div>
                    );
                  })()}

                  {/* ── 🎯 HEDEF ZAMANLARI (15m / 1h / 4h / 24h) ── */}
                  {horizons.length > 0 && (
                    <div className="mb-2 rounded-lg bg-black/30 border border-white/8 p-2">
                      <div className="text-[9px] font-semibold text-gray-400 uppercase tracking-wider mb-1.5 flex items-center gap-1">
                        <Target size={10} className="text-cyan-300" /> Hedef Zamanları
                        {meta.selected_horizon && (
                          <span className="text-[8px] text-amber-300 ml-auto">Ana: {meta.selected_horizon}</span>
                        )}
                      </div>
                      <div className="space-y-1">
                        {horizons.map((h: any) => {
                          const hStatus = h.status || "active";
                          const hTargetPct = normalizeTargetPct(h.target_pct) * 100;
                          const hTargetPrice = toNumber(h.target_price, 0);
                          const remaining = horizonCountdown(signalAt, toNumber(h.eta_minutes, 15));
                          const actualChange = toNumber(h.actual_change_pct, 0) * 100;
                          const actualPrice = toNumber(h.actual_price, 0);
                          const isPrimary = h.label === (meta.selected_horizon || horizons[0]?.label);
                          const isNearMiss = hStatus === "near_miss" || h.near_miss;
                          const closestApproach = toNumber(h.closest_approach_pct, 0);

                          return (
                            <div
                              key={`hz-${s.id}-${h.label}`}
                              className={`flex items-center justify-between rounded-md px-2 py-1.5 text-[10px] ${
                                hStatus === "hit"
                                  ? "bg-emerald-400/12 border border-emerald-400/20"
                                  : hStatus === "missed"
                                  ? "bg-rose-400/10 border border-rose-400/20"
                                  : isNearMiss
                                  ? "bg-amber-400/12 border border-amber-400/25"
                                  : hStatus === "expired"
                                  ? "bg-gray-500/10 border border-gray-500/20"
                                  : isPrimary
                                  ? "bg-cyan-400/10 border border-cyan-400/20"
                                  : "bg-white/4 border border-white/8"
                              }`}
                            >
                              <div className="flex items-center gap-2">
                                {hStatus === "hit" ? (
                                  <CheckCircle2 size={12} className="text-emerald-400" />
                                ) : hStatus === "missed" ? (
                                  <XCircle size={12} className="text-rose-400" />
                                ) : isNearMiss ? (
                                  <Activity size={12} className="text-amber-400" />
                                ) : hStatus === "expired" ? (
                                  <Clock3 size={12} className="text-gray-400" />
                                ) : (
                                  <Timer size={12} className={isPrimary ? "text-cyan-300 animate-pulse" : "text-cyan-300 animate-pulse"} />
                                )}
                                <span className={`font-bold w-8 ${isPrimary ? "text-cyan-200" : "text-white"}`}>{h.label}</span>
                                {isPrimary && <span className="text-[7px] text-cyan-400 bg-cyan-400/10 rounded-full px-1">ANA</span>}
                                <span className="text-gray-400">%{hTargetPct.toFixed(1)}</span>
                                <span className="text-gray-500">→ ${formatPrice(hTargetPrice)}</span>
                              </div>
                              <div className="text-right">
                                {hStatus === "active" && remaining ? (
                                  <span className="text-cyan-300 font-medium">{remaining} kaldı</span>
                                ) : hStatus === "active" ? (
                                  <span className="text-amber-300">Değerlendiriliyor...</span>
                                ) : hStatus === "hit" ? (
                                  <span className="text-emerald-300 font-medium">İSABET +{actualChange.toFixed(2)}%</span>
                                ) : isNearMiss ? (
                                  <span className="text-amber-300 font-medium">⚡ YAKIN KAÇIŞ {closestApproach > 0 ? `%${(closestApproach * 100).toFixed(1)} yaklaştı` : ""}</span>
                                ) : hStatus === "expired" ? (
                                  <span className="text-gray-400">Süre doldu</span>
                                ) : (
                                  <span className="text-rose-300">ISKALANDI {actualChange >= 0 ? "+" : ""}{actualChange.toFixed(2)}%</span>
                                )}
                              </div>
                            </div>
                          );
                        })}
                      </div>
                    </div>
                  )}

                  {/* ── 🧬 ENHANCED INTELLIGENCE (meta-labeler + bandit + conformal + microstructure) ── */}
                  {(() => {
                    const metaL = meta.meta_labeler || null;
                    const banditEv = toNumber(meta.bandit_ev, NaN);
                    const cband = meta.confidence_band || null;
                    const feats = meta.entry_features || {};
                    const ms = feats.microstructure || {};
                    const reg = feats.regime || {};
                    const fp = feats.fingerprint || {};
                    const obi = toNumber(ms.obi, NaN);
                    const vpin = toNumber(ms.vpin, NaN);
                    const regLabel = reg.label || reg.state_label || null;
                    const fpScore = toNumber(fp.fingerprint_score, NaN);
                    const hasAny = metaL || Number.isFinite(banditEv) || cband || Number.isFinite(obi) || Number.isFinite(vpin) || regLabel || Number.isFinite(fpScore);
                    if (!hasAny) return null;
                    return (
                      <div className="mb-2 rounded-lg bg-gradient-to-br from-indigo-500/8 to-violet-500/8 border border-indigo-400/20 px-2 py-1.5">
                        <div className="text-[9px] font-semibold text-indigo-300 uppercase tracking-wider mb-1 flex items-center gap-1">
                          <BrainCircuit size={10} /> Gelişmiş Zeka
                        </div>
                        <div className="flex flex-wrap gap-1 text-[9px]">
                          {metaL && Number.isFinite(toNumber(metaL.proba, NaN)) && (
                            <span className={`px-1.5 py-0.5 rounded-full border ${metaL.accept ? 'border-emerald-400/30 bg-emerald-400/10 text-emerald-200' : 'border-amber-400/30 bg-amber-400/10 text-amber-200'}`}>
                              Meta: %{(toNumber(metaL.proba) * 100).toFixed(0)}{metaL.version ? ` v${metaL.version}` : ''}
                            </span>
                          )}
                          {Number.isFinite(banditEv) && (
                            <span className="px-1.5 py-0.5 rounded-full border border-cyan-400/30 bg-cyan-400/10 text-cyan-200">
                              Bandit EV: {banditEv.toFixed(3)}
                            </span>
                          )}
                          {cband && (
                            <span className="px-1.5 py-0.5 rounded-full border border-violet-400/30 bg-violet-400/10 text-violet-200">
                              Güven bandı: {toNumber(cband.lo).toFixed(2)}–{toNumber(cband.hi).toFixed(2)}
                            </span>
                          )}
                          {Number.isFinite(obi) && (
                            <span className={`px-1.5 py-0.5 rounded-full border ${obi >= 0 ? 'border-emerald-400/25 bg-emerald-400/8 text-emerald-200' : 'border-rose-400/25 bg-rose-400/8 text-rose-200'}`}>
                              OBI {obi >= 0 ? '+' : ''}{obi.toFixed(2)}
                            </span>
                          )}
                          {Number.isFinite(vpin) && (
                            <span className={`px-1.5 py-0.5 rounded-full border ${vpin >= 0.7 ? 'border-rose-400/30 bg-rose-400/10 text-rose-200' : 'border-white/10 bg-white/5 text-gray-300'}`}>
                              VPIN {vpin.toFixed(2)}
                            </span>
                          )}
                          {regLabel && (
                            <span className="px-1.5 py-0.5 rounded-full border border-sky-400/25 bg-sky-400/10 text-sky-200">
                              Rejim: {String(regLabel)}
                            </span>
                          )}
                          {Number.isFinite(fpScore) && fpScore > 0.3 && (
                            <span className="px-1.5 py-0.5 rounded-full border border-fuchsia-400/30 bg-fuchsia-400/10 text-fuchsia-200">
                              İzbırakma: {(fpScore * 100).toFixed(0)}%
                            </span>
                          )}
                        </div>
                      </div>
                    );
                  })()}

                  {/* ── 📊 ANALİZ BİLGİLERİ ── */}
                  <div className="grid grid-cols-2 gap-1 mb-2 text-[9px]">
                    {similarity > 0 && (
                      <div className="rounded-md bg-black/20 border border-white/5 px-2 py-1">
                        <span className="text-gray-500">Benzerlik</span>
                        <span className="text-white font-medium ml-1">%{(similarity * 100).toFixed(0)}</span>
                      </div>
                    )}
                    {matchCount > 0 && (
                      <div className="rounded-md bg-black/20 border border-white/5 px-2 py-1">
                        <span className="text-gray-500">Eşleşme</span>
                        <span className="text-white font-medium ml-1">{matchCount} pattern</span>
                      </div>
                    )}
                    {quality > 0 && (
                      <div className="rounded-md bg-black/20 border border-white/5 px-2 py-1">
                        <span className="text-gray-500">Kalite</span>
                        <span className="text-white font-medium ml-1">%{(quality * 100).toFixed(0)}</span>
                      </div>
                    )}
                    {density > 0 && (
                      <div className="rounded-md bg-black/20 border border-white/5 px-2 py-1">
                        <span className="text-gray-500">Veri</span>
                        <span className="text-white font-medium ml-1">%{(density * 100).toFixed(0)}</span>
                      </div>
                    )}
                    {mamis && (
                      <div className={`col-span-2 rounded-md border px-2 py-1 ${
                        mamis.aligned ? "bg-emerald-400/8 border-emerald-400/15 text-emerald-200"
                          : mamis.opposite ? "bg-rose-400/8 border-rose-400/15 text-rose-200"
                          : "bg-white/5 border-white/10 text-gray-300"
                      }`}>
                        <Activity size={9} className="inline mr-1" />
                        MAMIS: {mamis.direction || mamis.mamis_direction || "—"} {mamis.aligned ? "✓ Uyumlu" : mamis.opposite ? "✗ Çelişkili" : "Nötr"}
                        {toNumber(mamis.confidence ?? mamis.mamis_confidence, 0) > 0 && (
                          <span className="ml-1 text-gray-400">(%{(toNumber(mamis.confidence ?? mamis.mamis_confidence) * 100).toFixed(0)})</span>
                        )}
                      </div>
                    )}
                  </div>

                  {/* ── ZAMAN + KAYNAK ── */}
                  <div className="flex items-center gap-1.5 flex-wrap text-[9px] text-gray-300 mb-1.5">
                    <span className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded-full bg-white/5 border border-white/8">
                      <Clock3 size={8} className="text-sky-300" />
                      {formatInQuenbotTimeZone(signalAt, { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit", second: "2-digit" })}
                    </span>
                    <span className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded-full bg-white/5 border border-white/8">
                      <Timer size={8} className="text-amber-300" />
                      TTL: {formatCountdown(expiresAt)}
                    </span>
                    <span className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded-full bg-violet-400/10 border border-violet-300/15 text-violet-200">
                      <Layers size={8} />
                      {String(source).replace(/_/g, " ")}
                    </span>
                  </div>

                  {/* ── KAYNAK MODEL ── */}
                  <div className="rounded-md bg-black/20 border border-white/5 px-2 py-1 text-[9px]">
                    <span className="text-gray-500"><BrainCircuit size={9} className="inline mr-1" />Model:</span>
                    <span className="text-white ml-1">{sourceModel}</span>
                  </div>

                  {/* ── TIMEFRAME TAHMİNLERİ ── */}
                  {Object.keys(tfPreds).length > 0 && (
                    <div className="mt-1.5 flex items-center gap-1 flex-wrap">
                      {Object.entries(tfPreds).map(([tf, pred]: [string, any]) => (
                        <span key={`tf-${s.id}-${tf}`} className={`inline-flex items-center gap-0.5 px-1.5 py-0.5 rounded-full text-[8px] border ${
                          pred.direction === "long" ? "border-emerald-400/20 bg-emerald-400/8 text-emerald-200" : "border-rose-400/20 bg-rose-400/8 text-rose-200"
                        }`}>
                          <TrendingUp size={8} />
                          {tf}: {pred.direction} %{(Math.abs(toNumber(pred.avg_change_pct)) * 100).toFixed(1)}
                        </span>
                      ))}
                    </div>
                  )}

                  {/* ── ÖĞRENİLMİŞ PROFİL ── */}
                  {learningProfile && (
                    <div className="mt-1.5 rounded-lg bg-emerald-400/8 border border-emerald-300/10 px-2 py-1.5 text-[9px]">
                      <div className="text-emerald-200 flex items-center gap-1 font-medium"><BrainCircuit size={9} />Öğrenilmiş Profil</div>
                      <div className="text-white mt-0.5">
                        Doğruluk %{(toNumber(learningProfile.accuracy) * 100).toFixed(0)} • {toNumber(learningProfile.correct)}/{toNumber(learningProfile.total)} isabet
                      </div>
                      <div className="text-gray-300 mt-0.5">Ort. PnL %{toNumber(learningProfile.avg_pnl).toFixed(2)}</div>
                      {Array.isArray(learningProfile.recent_reasons) && learningProfile.recent_reasons.length > 0 && (
                        <div className="text-gray-400 mt-0.5 line-clamp-1">Son ders: {String(learningProfile.recent_reasons[0])}</div>
                      )}
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        )}
      </div>
    </div>
  );
}
