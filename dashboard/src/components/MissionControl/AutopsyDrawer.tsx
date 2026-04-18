"use client";

import { useEffect, useState } from "react";
import { X, RefreshCw, Activity } from "lucide-react";
import { AutopsyBundle, STATUS_COLORS, fetchAutopsy, restartModule, formatAge } from "@/lib/missionControl";

interface Props {
  moduleId: string | null;
  onClose: () => void;
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
    setLoading(true);
    setError(null);
    setBundle(null);
    fetchAutopsy(moduleId)
      .then((b) => {
        if (!cancelled) setBundle(b);
      })
      .catch((e) => {
        if (!cancelled) setError(e instanceof Error ? e.message : "Hata");
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
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

  return (
    <div className="fixed inset-0 z-40 flex">
      <div className="flex-1 bg-black/60" onClick={onClose} aria-label="Kapat" />
      <aside className="h-full w-full max-w-lg overflow-y-auto border-l border-surface-border bg-surface shadow-2xl">
        <div className="sticky top-0 z-10 flex items-center justify-between border-b border-surface-border bg-surface px-4 py-3">
          <div>
            <div className="text-xs uppercase tracking-wide text-gray-500">Otopsi</div>
            <h2 className="text-base font-semibold text-gray-100">{bundle?.display_name || moduleId}</h2>
          </div>
          <button
            onClick={onClose}
            className="rounded-md border border-surface-border p-1.5 text-gray-400 hover:text-gray-200"
            aria-label="Kapat"
          >
            <X size={14} />
          </button>
        </div>

        {loading && <div className="p-4 text-xs text-gray-400">Yükleniyor…</div>}
        {error && <div className="p-4 text-xs text-red-400">Hata: {error}</div>}

        {bundle && (
          <div className="p-4 space-y-4">
            {/* Status header */}
            <div className={`rounded-lg border px-3 py-2 ${statusCfg.border} ${statusCfg.bg}`}>
              <div className="flex items-center justify-between">
                <span className={`text-xs font-bold ${statusCfg.fg}`}>{statusCfg.label}</span>
                <span className="text-xs text-gray-300 tabular-nums">Skor: {bundle.health_score}</span>
              </div>
            </div>

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

            {/* Qwen diagnosis */}
            <div className="rounded-lg border border-purple-500/30 bg-purple-500/5 p-3">
              <h3 className="flex items-center gap-1.5 text-[10px] uppercase tracking-wide text-purple-300">
                <Activity size={11} /> Qwen Tanısı
              </h3>
              <p className="mt-1.5 text-xs text-gray-200 whitespace-pre-wrap">
                {bundle.diagnosis || <span className="text-gray-500">Tanı üretilemedi (LLM erişilemiyor).</span>}
              </p>
            </div>

            {/* Dependencies */}
            {bundle.dependencies.length > 0 && (
              <div>
                <h3 className="text-[10px] uppercase tracking-wide text-gray-500 mb-1">Bağımlılıklar</h3>
                <div className="space-y-1">
                  {bundle.dependencies.map((d) => {
                    const c = STATUS_COLORS[d.status] ?? STATUS_COLORS.unknown;
                    return (
                      <div
                        key={d.id}
                        className={`flex items-center justify-between rounded border px-2 py-1 text-xs ${c.border} ${c.bg}`}
                      >
                        <span className="text-gray-200">{d.id}</span>
                        <span className={`tabular-nums ${c.fg}`}>{d.health_score}</span>
                      </div>
                    );
                  })}
                </div>
              </div>
            )}

            {/* Events */}
            {bundle.recent_events.length > 0 && (
              <div>
                <h3 className="text-[10px] uppercase tracking-wide text-gray-500 mb-1">Son Olaylar</h3>
                <div className="max-h-64 overflow-y-auto space-y-1 rounded border border-surface-border bg-black/30 p-2 font-mono text-[10px]">
                  {bundle.recent_events.map((ev, i) => (
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
            <div className="flex items-center gap-2 pt-2 border-t border-surface-border">
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
