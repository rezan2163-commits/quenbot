"use client";

import { useEffect, useMemo, useState } from "react";
import { applyCodeTask, createCodeTask, useCodeOperatorStatus, useCodeOperatorTasks } from "@/lib/api";
import { AlertTriangle, Bot, CheckCircle2, ChevronDown, ChevronUp, Code2, FileWarning, Play, RefreshCw, Send, Wrench, X } from "lucide-react";

function statusTone(status: string) {
  switch (status) {
    case "preview_ready":
    case "applied":
      return "border-emerald-400/30 bg-emerald-400/10 text-emerald-200";
    case "applied_with_warnings":
    case "needs_clarification":
      return "border-amber-400/30 bg-amber-400/10 text-amber-100";
    case "failed":
      return "border-rose-400/30 bg-rose-400/10 text-rose-200";
    case "planning":
    case "applying":
      return "border-sky-400/30 bg-sky-400/10 text-sky-200";
    default:
      return "border-surface-border bg-white/5 text-gray-300";
  }
}

function shortTime(value?: string) {
  if (!value) return "-";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat("tr-TR", {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    day: "2-digit",
    month: "2-digit",
  }).format(date);
}

export default function CodeOperatorPanel() {
  const [open, setOpen] = useState(false);
  const [prompt, setPrompt] = useState("");
  const [mode, setMode] = useState<"preview" | "apply">("preview");
  const [busy, setBusy] = useState(false);
  const [feedback, setFeedback] = useState<string | null>(null);
  const [expandedTaskId, setExpandedTaskId] = useState<string | null>(null);
  const { data: status, mutate: mutateStatus } = useCodeOperatorStatus();
  const { data: tasksRes, mutate: mutateTasks } = useCodeOperatorTasks(12);

  const tasks = tasksRes?.items || [];
  const latestPreviewable = useMemo(
    () => tasks.find((task) => task.status === "preview_ready" && task.preview?.length),
    [tasks],
  );

  useEffect(() => {
    if (!expandedTaskId && tasks[0]?.id) {
      setExpandedTaskId(tasks[0].id);
    }
  }, [expandedTaskId, tasks]);

  const handleSubmit = async () => {
    const text = prompt.trim();
    if (!text || busy) return;
    setBusy(true);
    setFeedback(null);
    try {
      const task = await createCodeTask(text, mode);
      setPrompt("");
      setFeedback(`Gorev olusturuldu: ${task.id}`);
      await Promise.all([mutateTasks(), mutateStatus()]);
    } catch {
      setFeedback("Kod operatoru gorevi olusturulamadi.");
    } finally {
      setBusy(false);
    }
  };

  const handleApplyLatest = async () => {
    if (!latestPreviewable || busy) return;
    setBusy(true);
    try {
      await applyCodeTask(latestPreviewable.id);
      setFeedback(`Apply kuyruga alindi: ${latestPreviewable.id}`);
      await Promise.all([mutateTasks(), mutateStatus()]);
    } catch {
      setFeedback("Preview gorevi apply moduna gecirilemedi.");
    } finally {
      setBusy(false);
    }
  };

  const handleApplyTask = async (taskId: string) => {
    if (busy) return;
    setBusy(true);
    setFeedback(null);
    try {
      await applyCodeTask(taskId);
      setFeedback(`Apply kuyruga alindi: ${taskId}`);
      await Promise.all([mutateTasks(), mutateStatus()]);
    } catch {
      setFeedback("Secili preview gorevi apply moduna gecirilemedi.");
    } finally {
      setBusy(false);
    }
  };

  if (!open) {
    return (
      <button
        onClick={() => setOpen(true)}
        className="fixed bottom-36 right-3 z-50 flex items-center gap-2 rounded-xl border border-surface-border bg-surface-card px-3 py-2 text-sm font-medium text-gray-200 shadow-lg transition-colors hover:bg-surface-hover sm:bottom-20 sm:right-4 sm:px-4 sm:py-2.5"
      >
        <Code2 size={16} className="text-accent" />
        Kod Operatoru
      </button>
    );
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm">
      <div className="mx-3 max-h-[90svh] w-full max-w-3xl overflow-hidden rounded-2xl border border-surface-border bg-surface-card shadow-2xl sm:mx-4">
        <div className="flex items-center justify-between border-b border-surface-border px-4 py-3">
          <div className="flex items-center gap-2">
            <Bot size={16} className="text-accent" />
            <div>
              <div className="text-sm font-semibold text-gray-200">SuperGemma Kod Operatoru</div>
              <div className="text-[11px] text-gray-500">Dogal dil ile kod gorevi, preview ve apply akisı</div>
            </div>
          </div>
          <button onClick={() => setOpen(false)} className="rounded-md border border-surface-border p-1 text-gray-400">
            <X size={14} />
          </button>
        </div>

        <div className="grid gap-4 p-4 lg:grid-cols-[1.15fr,0.85fr]">
          <div className="space-y-3">
            <div className="rounded-xl border border-surface-border bg-surface/50 p-3">
              <div className="mb-2 text-xs font-semibold uppercase tracking-[0.16em] text-gray-500">Yeni Gorev</div>
              <textarea
                value={prompt}
                onChange={(e) => setPrompt(e.target.value)}
                placeholder="Ornek: dashboard chat paneline code operator gorev durumu karti ekle ve api tiplerini guncelle"
                className="min-h-36 w-full rounded-lg border border-surface-border bg-surface px-3 py-2 text-sm text-gray-200 placeholder:text-gray-600 focus:outline-none focus:border-accent/50"
              />
              <div className="mt-3 flex flex-wrap items-center gap-2">
                <button
                  onClick={() => setMode("preview")}
                  className={`rounded-full px-3 py-1 text-xs ${mode === "preview" ? "bg-accent text-white" : "border border-surface-border text-gray-400"}`}
                >
                  Preview
                </button>
                <button
                  onClick={() => setMode("apply")}
                  className={`rounded-full px-3 py-1 text-xs ${mode === "apply" ? "bg-emerald-500 text-white" : "border border-surface-border text-gray-400"}`}
                >
                  Direkt Apply
                </button>
                <button
                  onClick={handleSubmit}
                  disabled={busy || !prompt.trim()}
                  className="ml-auto inline-flex items-center gap-2 rounded-lg bg-accent px-3 py-2 text-sm font-medium text-white disabled:opacity-50"
                >
                  <Send size={14} />
                  Gonder
                </button>
              </div>
              {feedback ? <div className="mt-2 text-xs text-amber-300">{feedback}</div> : null}
            </div>

            <div className="rounded-xl border border-surface-border bg-surface/50 p-3">
              <div className="mb-2 flex items-center justify-between">
                <div className="text-xs font-semibold uppercase tracking-[0.16em] text-gray-500">Son Gorevler</div>
                <button onClick={() => { void mutateTasks(); void mutateStatus(); }} className="text-gray-500 hover:text-gray-300">
                  <RefreshCw size={14} />
                </button>
              </div>
              <div className="max-h-[26rem] space-y-2 overflow-y-auto pr-1 custom-scrollbar">
                {tasks.length === 0 ? (
                  <div className="rounded-lg border border-dashed border-surface-border px-3 py-3 text-xs text-gray-500">Henüz kod gorevi yok.</div>
                ) : (
                  tasks.map((task) => (
                    <div key={task.id} className="rounded-lg border border-surface-border bg-surface px-3 py-2">
                      <div className="flex items-start justify-between gap-2">
                        <div>
                          <div className="text-xs font-semibold text-gray-200">{task.id}</div>
                          <div className="mt-0.5 text-[11px] text-gray-400">{task.summary || task.prompt.slice(0, 120)}</div>
                          <div className="mt-1 text-[10px] text-gray-500">{shortTime(task.updated_at || task.created_at)} • {task.mode}</div>
                        </div>
                        <div className="flex items-center gap-2">
                          <span className={`rounded-full border px-2 py-1 text-[10px] ${statusTone(task.status)}`}>{task.status}</span>
                          <button
                            onClick={() => setExpandedTaskId(expandedTaskId === task.id ? null : task.id)}
                            className="rounded-md border border-surface-border p-1 text-gray-400"
                          >
                            {expandedTaskId === task.id ? <ChevronUp size={12} /> : <ChevronDown size={12} />}
                          </button>
                        </div>
                      </div>
                      {expandedTaskId === task.id ? (
                        <div className="mt-3 space-y-3 border-t border-white/5 pt-3">
                          <div className="rounded-lg border border-white/5 bg-black/20 px-3 py-2 text-[11px] text-gray-300">
                            <div className="mb-1 font-medium text-gray-200">Istek</div>
                            <div className="text-gray-400">{task.prompt}</div>
                          </div>

                          {task.error ? (
                            <div className="rounded-lg border border-rose-400/30 bg-rose-400/10 px-3 py-2 text-[11px] text-rose-100">
                              <div className="mb-1 flex items-center gap-2 font-medium"><FileWarning size={12} /> Hata Nedeni</div>
                              <div>{task.error}</div>
                            </div>
                          ) : null}

                          {task.clarification ? (
                            <div className="rounded-lg border border-amber-400/30 bg-amber-400/10 px-3 py-2 text-[11px] text-amber-50">
                              <div className="mb-1 flex items-center gap-2 font-medium"><AlertTriangle size={12} /> Clarification</div>
                              <div>{task.clarification}</div>
                            </div>
                          ) : null}

                          {task.selected_files?.length ? (
                            <div>
                              <div className="mb-1 text-[10px] font-semibold uppercase tracking-[0.16em] text-gray-500">Secilen Dosyalar</div>
                              <div className="flex flex-wrap gap-1">
                                {task.selected_files.map((file) => (
                                  <span key={file} className="rounded-full bg-white/5 px-2 py-1 text-[10px] text-gray-400">{file}</span>
                                ))}
                              </div>
                            </div>
                          ) : null}

                          {task.plan?.validation_commands?.length || task.validation_commands?.length ? (
                            <div>
                              <div className="mb-1 text-[10px] font-semibold uppercase tracking-[0.16em] text-gray-500">Validation Komutlari</div>
                              <div className="space-y-1">
                                {(task.validation_commands || task.plan?.validation_commands || []).map((command) => (
                                  <div key={`${task.id}-${command}`} className="rounded bg-black/20 px-2 py-1 text-[10px] text-gray-400">{command}</div>
                                ))}
                              </div>
                            </div>
                          ) : null}

                          {task.preview?.length ? (
                            <div className="space-y-2">
                              <div className="text-[10px] font-semibold uppercase tracking-[0.16em] text-gray-500">Preview</div>
                              {task.preview.map((item) => (
                                <div key={`${task.id}-${item.path}`} className="rounded-md border border-white/5 bg-black/20 px-3 py-2 text-[11px] text-gray-300">
                                  <div className="font-medium text-gray-200">{item.path}</div>
                                  <div className="mt-1 text-gray-400">{item.reason || "Degisiklik onerisi"}</div>
                                  {item.old_preview ? <pre className="mt-2 overflow-x-auto rounded bg-rose-950/30 px-2 py-2 text-[10px] text-rose-100">{item.old_preview}</pre> : null}
                                  {item.new_preview ? <pre className="mt-2 overflow-x-auto rounded bg-emerald-950/30 px-2 py-2 text-[10px] text-emerald-100">{item.new_preview}</pre> : null}
                                </div>
                              ))}
                            </div>
                          ) : null}

                          {task.validation?.length ? (
                            <div className="space-y-2">
                              <div className="text-[10px] font-semibold uppercase tracking-[0.16em] text-gray-500">Validation Sonuclari</div>
                              {task.validation.map((row) => (
                                <div key={`${task.id}-${row.command}`} className="rounded-md border border-white/5 bg-black/20 px-3 py-2 text-[11px] text-gray-300">
                                  <div className="flex items-center gap-2">
                                    {row.status === "ok" ? <CheckCircle2 size={12} className="text-emerald-300" /> : <AlertTriangle size={12} className="text-amber-300" />}
                                    <span className="font-medium text-gray-200">{row.command}</span>
                                  </div>
                                  {row.output ? <pre className="mt-2 overflow-x-auto rounded bg-black/30 px-2 py-2 text-[10px] text-gray-400">{row.output}</pre> : null}
                                </div>
                              ))}
                            </div>
                          ) : null}

                          {task.apply_result ? (
                            <div className={`rounded-lg border px-3 py-2 text-[11px] ${task.apply_result.ok ? "border-emerald-400/30 bg-emerald-400/10 text-emerald-100" : "border-rose-400/30 bg-rose-400/10 text-rose-100"}`}>
                              <div className="mb-1 font-medium">Apply Sonucu</div>
                              <div>Durum: {task.apply_result.ok ? "Basarili" : "Basarisiz"}</div>
                              {task.apply_result.changed_files?.length ? <div>Dosyalar: {task.apply_result.changed_files.join(", ")}</div> : null}
                              {task.apply_result.backup_dir ? <div>Backup: {task.apply_result.backup_dir}</div> : null}
                              {task.apply_result.error ? <div>Hata: {task.apply_result.error}</div> : null}
                            </div>
                          ) : null}

                          {task.status === "preview_ready" ? (
                            <button
                              onClick={() => handleApplyTask(task.id)}
                              disabled={busy}
                              className="inline-flex items-center gap-2 rounded-lg bg-emerald-500 px-3 py-2 text-xs font-medium text-white disabled:opacity-40"
                            >
                              <Play size={12} />
                              Bu Preview Gorevini Apply Et
                            </button>
                          ) : null}
                        </div>
                      ) : null}
                    </div>
                  ))
                )}
              </div>
            </div>
          </div>

          <div className="space-y-3">
            <div className="rounded-xl border border-surface-border bg-surface/50 p-3">
              <div className="mb-2 text-xs font-semibold uppercase tracking-[0.16em] text-gray-500">Operator Durumu</div>
              <div className="space-y-2 text-sm text-gray-300">
                <div className="flex items-center justify-between"><span>Model</span><span className="text-gray-200">{status?.model || "-"}</span></div>
                <div className="flex items-center justify-between"><span>Kuyruk</span><span className="text-gray-200">{status?.queued ?? 0}</span></div>
                <div className="flex items-center justify-between"><span>Aktif Gorev</span><span className="text-gray-200">{status?.active_task_id || "-"}</span></div>
                <div className="flex items-center justify-between"><span>Hazirlik</span><span className={`${status?.available ? "text-emerald-300" : "text-amber-300"}`}>{status?.available ? "Hazir" : "Kontrol gerekiyor"}</span></div>
              </div>
            </div>

            <div className="rounded-xl border border-surface-border bg-surface/50 p-3">
              <div className="mb-2 text-xs font-semibold uppercase tracking-[0.16em] text-gray-500">Hizli Aksiyon</div>
              <button
                onClick={handleApplyLatest}
                disabled={!latestPreviewable || busy}
                className="inline-flex w-full items-center justify-center gap-2 rounded-lg bg-emerald-500 px-3 py-2 text-sm font-medium text-white disabled:opacity-40"
              >
                <Play size={14} />
                Son Preview Gorevini Apply Et
              </button>
              <div className="mt-2 text-[11px] text-gray-500">
                Preview sonucunu once listede inceleyip sonra apply edebilirsiniz. Apply edilen degisiklikler sunucu reposunda backup alinarak yazilir.
              </div>
            </div>

            <div className="rounded-xl border border-amber-400/20 bg-amber-400/5 p-3 text-[11px] text-amber-100">
              <div className="mb-1 flex items-center gap-2 font-semibold"><Wrench size={12} /> Guvenli Calisma Sozlesmesi</div>
              <div>Operator mevcut dosyalarda kucuk patch üretir, backup alir ve validation komutu calistirir. Cok genis isteklerde preview/clarification ile durur.</div>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}