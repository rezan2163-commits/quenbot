"use client";

import { useEffect, useState } from "react";
import { X, RefreshCw, Activity, Zap, Network, History, Cpu, AlertTriangle } from "lucide-react";
import {
  AutopsyBundle,
  STATUS_COLORS,
  fetchAutopsy,
  restartModule,
  formatAge,
  ORGAN_LABELS,
} from "@/lib/missionControl";

interface Props {
  moduleId: string | null;
  onClose: () => void;
}

function fmtSeconds(s: number | null): string {
  if (s == null || !Number.isFinite(s)) return "—";
  if (s < 60) return `${Math.round(s)}s`;
  if (s < 3600) return `${Math.round(s / 60)}dk`;
  return `${(s / 3600).toFixed(1)}sa`;
}

function Sparkline({ values }: { values: number[] }) {
  if (!values.length) return null;
  const max = Math.max(...values, 0.0001);
  const w = 160;
  const h = 28;
  const step = w / Math.max(1, values.length - 1);
  const pts = values.map((v, i) => `${(i * step).toFixed(1)},${(h - (v / max) * h).toFixed(1)}`).join(" ");
  return (
    <svg width={w} height={h} className="text-accent">
      <polyline points={pts} fill="none" stroke="currentColor" strokeWidth={1.5} />
    </svg>
  );
}

export function AutopsyDrawer({ moduleId, onClose }: Props) {
  const [bundle, setBundle] = useState<AutopsyBundle | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [restartState, setRestartState] = useState<string | null>(null);

  useEffect(() => {
    if (!moduleId) {
      setBundle(null);
      setError(null);
      return;
    }
    let cancelled = false;
    let pollTimer: ReturnType<typeof setTimeout> | null = null;
    setLoading(true);
    setError(null);
    setBundle(null);

    const load = (silent: boolean) => {
      if (!silent) setLoading(true);
      fetchAutopsy(moduleId)
        .then((b) => {
          if (cancelled) return;
          setBundle(b);
          setError(null);
          // If Qwen diagnosis is still being generated in the background,
          // poll again in 4s until it resolves (or we give up after ~120s).
          if (b.qwen_pending && !b.qwen_diagnosis) {
            pollTimer = setTimeout(() => load(true), 4000);
          }
        })
        .catch((e) => {
          if (!cancelled) setError(e instanceof Error ? e.message : "Hata");
        })
        .finally(() => {
          if (!cancelled && !silent) setLoading(false);
        });
    };
    load(false);

    return () => {
      cancelled = true;
      if (pollTimer) clearTimeout(pollTimer);
    };
  }, [moduleId]);

  if (!moduleId) return null;

  const status = bundle?.status ?? "unknown";
  const statusCfg = STATUS_COLORS[status];

  const handleRestart = async () => {
    if (!moduleId) return;
    setRestartState("Yeniden başlatılıyor…");
    const token = typeof window !== "undefined" ? window.prompt("Admin token (boş bırak gerekmiyorsa):") || undefined : undefined;
    try {
      const res = await restartModule(moduleId, token);
      setRestartState(res.ok ? "Komut gönderildi" : `Hata: ${res.message || "bilinmiyor"}`);
    } catch (e) {
      setRestartState(`Hata: ${e instanceof Error ? e.message : "bilinmiyor"}`);
    }
    setTimeout(() => setRestartState(null), 4000);
  };

  const tp = bundle?.timeline_5min?.throughput?.map((p) => p.v) ?? [];
  const upstream = bundle?.collaborators?.upstream ?? [];
  const downstream = bundle?.collaborators?.downstream ?? [];
  const diag = bundle?.qwen_diagnosis ?? null;
  const activity = bundle?.current_activity;
  const mission = bundle?.mission_summary;

  return (
    <div className="fixed inset-0 z-40 flex">
      <div className="flex-1 bg-black/60" onClick={onClose} aria-label="Kapat" />
      <aside className="h-full w-full max-w-lg overflow-y-auto border-l border-surface-border bg-surface shadow-2xl">
        <div className="sticky top-0 z-10 flex items-center justify-between border-b border-surface-border bg-surface px-4 py-3">
          <div>
            <div className="text-xs uppercase tracking-wide text-gray-500">
              Otopsi{bundle ? ` · ${ORGAN_LABELS[bundle.organ] || bundle.organ}` : ""}
            </div>
            <h2 className="text-base font-semibold text-gray-100">{bundle?.display_name || moduleId}</h2>
            <div className="font-mono text-[10px] text-gray-500">{moduleId}</div>
          </div>
          <button
            onClick={onClose}
            className="rounded-md border border-surface-border p-1.5 text-gray-400 hover:text-gray-200"
            aria-label="Kapat"
          >
            <X size={14} />
          </button>
        </div>

        {loading && <div className="p-4 text-xs text-gray-400">Yükleniyor… (ilk çağrıda Qwen tanısı ~20 sn sürebilir)</div>}
        {error && (
          <div className="m-4 rounded border border-red-500/40 bg-red-500/10 p-3 text-xs text-red-300">
            <div className="mb-1 flex items-center gap-1.5 font-semibold">
              <AlertTriangle size={12} /> Otopsi alınamadı
            </div>
            <div className="text-red-200/80">{error}</div>
            <div className="mt-1 text-red-300/60">Bu bir ağ veya LLM uyanma gecikmesi olabilir — paneli kapatıp tekrar açın.</div>
          </div>
        )}

        {bundle && (
          <div className="p-4 space-y-4">
            {/* Status + score + organ */}
            <div className={`rounded-lg border px-3 py-2 ${statusCfg.border} ${statusCfg.bg}`}>
              <div className="flex items-center justify-between">
                <span className={`text-xs font-bold ${statusCfg.fg}`}>{statusCfg.label}</span>
                <span className="text-xs text-gray-300 tabular-nums">Sağlık: {bundle.current_health}/100</span>
              </div>
            </div>

            {/* Description (role) */}
            {bundle.description_tr && (
              <div className="rounded-lg border border-surface-border bg-black/20 p-3">
                <h3 className="mb-1 flex items-center gap-1.5 text-[10px] uppercase tracking-wide text-gray-500">
                  <Cpu size={11} /> Görevi
                </h3>
                <p className="text-xs text-gray-200">{bundle.description_tr}</p>
              </div>
            )}

            {/* Current activity */}
            {activity && (
              <div className={`rounded-lg border p-3 ${activity.is_active ? "border-emerald-500/30 bg-emerald-500/5" : "border-slate-500/30 bg-slate-500/5"}`}>
                <h3 className="mb-1 flex items-center gap-1.5 text-[10px] uppercase tracking-wide text-gray-400">
                  <Zap size={11} /> Şu Anki Aktivite
                </h3>
                <p className="text-xs text-gray-100">{activity.description_tr}</p>
                <div className="mt-2 grid grid-cols-2 gap-2 text-[10px]">
                  <div>
                    <div className="text-gray-500">Son olay</div>
                    <div className="font-mono text-gray-200">{activity.last_event_type || "—"}</div>
                  </div>
                  <div>
                    <div className="text-gray-500">Yaşı</div>
                    <div className="font-mono text-gray-200">{fmtSeconds(activity.seconds_since_last_event)}</div>
                  </div>
                  <div className="col-span-2">
                    <div className="text-gray-500">Önizleme</div>
                    <div className="truncate font-mono text-gray-300">{activity.last_event_preview || "—"}</div>
                  </div>
                  <div className="col-span-2">
                    <div className="text-gray-500">Beklenen periyot</div>
                    <div className="font-mono text-gray-300">{activity.expected_period_sec.toFixed(1)}s</div>
                  </div>
                </div>
              </div>
            )}

            {/* Mission summary + sparkline */}
            {mission && (
              <div className="rounded-lg border border-surface-border bg-black/20 p-3">
                <div className="mb-2 flex items-center justify-between">
                  <h3 className="flex items-center gap-1.5 text-[10px] uppercase tracking-wide text-gray-500">
                    <History size={11} /> Son 5 dk Görev Özeti
                  </h3>
                  <Sparkline values={tp} />
                </div>
                <div className="grid grid-cols-2 gap-2 text-[10px]">
                  <div>
                    <div className="text-gray-500">Toplam olay</div>
                    <div className="font-mono text-gray-100 tabular-nums">{mission.total_events_5min.toLocaleString("tr-TR")}</div>
                  </div>
                  <div>
                    <div className="text-gray-500">Son 1 dk</div>
                    <div className="font-mono text-gray-100 tabular-nums">{mission.events_last_minute.toLocaleString("tr-TR")}</div>
                  </div>
                  <div>
                    <div className="text-gray-500">Ortalama</div>
                    <div className="font-mono text-gray-100 tabular-nums">{mission.avg_throughput_per_sec.toFixed(2)}/sn</div>
                  </div>
                  <div>
                    <div className="text-gray-500">Zirve</div>
                    <div className="font-mono text-gray-100 tabular-nums">{mission.peak_throughput_per_sec.toFixed(2)}/sn</div>
                  </div>
                </div>
              </div>
            )}

            {/* Qwen diagnosis */}
            <div className="rounded-lg border border-purple-500/30 bg-purple-500/5 p-3">
              <h3 className="flex items-center gap-1.5 text-[10px] uppercase tracking-wide text-purple-300">
                <Activity size={11} /> Qwen Tanısı
              </h3>
              {diag ? (
                <>
                  <p className="mt-1.5 whitespace-pre-wrap text-xs text-gray-200">{diag.summary_tr}</p>
                  {diag.suggested_actions_tr.length > 0 && (
                    <ul className="mt-2 space-y-1">
                      {diag.suggested_actions_tr.map((a, i) => (
                        <li key={i} className="text-xs text-purple-200">• {a}</li>
                      ))}
                    </ul>
                  )}
                  <div className="mt-2 text-[9px] text-purple-400/60">
                    Güven: {(diag.confidence * 100).toFixed(0)}% · {formatAge(diag.generated_at)}
                  </div>
                </>
              ) : bundle.qwen_pending ? (
                <p className="mt-1.5 flex items-center gap-2 text-xs text-purple-200/80">
                  <span className="inline-block h-2 w-2 animate-pulse rounded-full bg-purple-300" />
                  Qwen tanı üretiyor… (arka planda, ~30 sn)
                </p>
              ) : (
                <p className="mt-1.5 text-xs text-gray-500">
                  Qwen yanıtı alınamadı (LLM erişilemiyor). Metrikler aşağıda.
                </p>
              )}
            </div>

            {/* Collaborators */}
            {(upstream.length > 0 || downstream.length > 0) && (
              <div className="rounded-lg border border-surface-border bg-black/20 p-3">
                <h3 className="mb-2 flex items-center gap-1.5 text-[10px] uppercase tracking-wide text-gray-500">
                  <Network size={11} /> İşbirlikçi Hücreler
                </h3>
                {upstream.length > 0 && (
                  <div className="mb-2">
                    <div className="mb-1 text-[9px] uppercase text-gray-600">Üst Akış (bu hücre onlardan besleniyor)</div>
                    <div className="space-y-1">
                      {upstream.map((c) => {
                        const cfg = (STATUS_COLORS as any)[c.status] ?? STATUS_COLORS.unknown;
                        return (
                          <div key={c.id} className={`flex items-center justify-between rounded border px-2 py-1 text-xs ${cfg.border} ${cfg.bg}`}>
                            <div className="min-w-0">
                              <div className="truncate text-gray-200">{c.display_name}</div>
                              <div className="font-mono text-[9px] text-gray-500">{c.id}</div>
                            </div>
                            <div className="text-right text-[10px]">
                              <div className={`tabular-nums ${cfg.fg}`}>{c.health_score}/100</div>
                              <div className="text-gray-500">{formatAge(c.last_event_at)}</div>
                            </div>
                          </div>
                        );
                      })}
                    </div>
                  </div>
                )}
                {downstream.length > 0 && (
                  <div>
                    <div className="mb-1 text-[9px] uppercase text-gray-600">Alt Akış (onlar bu hücreden besleniyor)</div>
                    <div className="space-y-1">
                      {downstream.map((c) => {
                        const cfg = (STATUS_COLORS as any)[c.status] ?? STATUS_COLORS.unknown;
                        return (
                          <div key={c.id} className={`flex items-center justify-between rounded border px-2 py-1 text-xs ${cfg.border} ${cfg.bg}`}>
                            <div className="min-w-0">
                              <div className="truncate text-gray-200">{c.display_name}</div>
                              <div className="font-mono text-[9px] text-gray-500">{c.id}</div>
                            </div>
                            <div className="text-right text-[10px]">
                              <div className={`tabular-nums ${cfg.fg}`}>{c.health_score}/100</div>
                              <div className="text-gray-500">{formatAge(c.last_event_at)}</div>
                            </div>
                          </div>
                        );
                      })}
                    </div>
                  </div>
                )}
              </div>
            )}

            {/* Warnings */}
            {bundle.warnings.length > 0 && (
              <div className="space-y-1">
                <h3 className="text-[10px] uppercase tracking-wide text-gray-500">Uyarılar</h3>
                <ul className="space-y-1">
                  {bundle.warnings.map((w, i) => (
                    <li key={i} className="text-xs text-amber-300">• {w}</li>
                  ))}
                </ul>
              </div>
            )}

            {/* Recent events */}
            {bundle.recent_events.length > 0 && (
              <div>
                <h3 className="mb-1 text-[10px] uppercase tracking-wide text-gray-500">Son Olaylar ({bundle.recent_events.length})</h3>
                <div className="max-h-64 space-y-1 overflow-y-auto rounded border border-surface-border bg-black/30 p-2 font-mono text-[10px]">
                  {bundle.recent_events.slice().reverse().map((ev, i) => (
                    <div key={i} className="text-gray-300">
                      <span className="text-gray-500">{formatAge(ev.ts)}</span>{" "}
                      <span className="text-accent">{ev.type}</span>{" "}
                      <span className="text-gray-400">{ev.preview}</span>
                    </div>
                  ))}
                </div>
              </div>
            )}

            {/* Actions */}
            <div className="flex items-center gap-2 border-t border-surface-border pt-2">
              <button
                onClick={handleRestart}
                className="inline-flex items-center gap-1 rounded border border-accent/40 bg-accent/10 px-3 py-1.5 text-xs font-medium text-accent hover:bg-accent/20"
              >
                <RefreshCw size={11} />
                Yeniden Başlat
              </button>
              {restartState && <span className="text-[10px] text-gray-400">{restartState}</span>}
            </div>
          </div>
        )}
      </aside>
    </div>
  );
}
