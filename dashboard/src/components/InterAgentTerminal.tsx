"use client";

import { useMemo } from "react";
import { useSystemEvents } from "@/lib/api";
import { TerminalSquare, Activity } from "lucide-react";
import { formatTimeOnly } from "@/lib/time";

function fmtTime(ts: number) {
  return formatTimeOnly(ts * 1000);
}

function summarize(preview: Record<string, any> | undefined, type: string) {
  if (!preview) return "";
  if (type === "decision.made") {
    const decision = preview.decision as Record<string, any> | undefined;
    const command = preview.command as Record<string, any> | undefined;
    return `${decision?.decision ?? "?"} ${command?.action ?? ""} ${command?.symbol ?? ""}`.trim();
  }
  if (type === "pattern.detected") {
    return `${preview.symbol ?? "?"} sim=${Number(preview.similarity ?? 0).toFixed(2)} yon=${preview.predicted_direction ?? "?"}`;
  }
  if (type === "system.redis_message") {
    return `Redis ${preview.channel ?? "channel"} mesaji alindi`;
  }
  if (type.includes("error") || type.includes("failed")) {
    return String(preview.message ?? preview.error ?? preview.reason ?? "Hata detayi yok");
  }
  if (type.startsWith("command.")) {
    return String(preview.summary ?? preview.action ?? preview.message ?? "Komut olayi");
  }
  return String(preview.symbol ?? preview.task ?? preview.source ?? "");
}

export default function InterAgentTerminal() {
  const { data } = useSystemEvents();

  const events = useMemo(() => {
    const list = data?.recent_events || [];
    return [...list].reverse();
  }, [data]);

  return (
    <div className="h-full flex flex-col bg-surface-card/30 overflow-hidden">
      <div className="flex items-center justify-between px-3 py-2 border-b border-surface-border">
        <div className="flex items-center gap-1.5">
          <TerminalSquare size={12} className="text-accent" />
          <span className="text-xs font-semibold text-gray-300 tracking-wide">AJAN İLETİŞİM TERMINALI</span>
        </div>
        <div className="flex items-center gap-2 text-[10px] text-gray-500">
          <span className="flex items-center gap-1"><Activity size={10} />{data?.total_events ?? 0} event</span>
          <span>{data?.subscriber_count ?? 0} subscriber</span>
        </div>
      </div>

      <div className="px-3 py-1.5 border-b border-surface-border/50 text-[10px] text-gray-500">
        SuperGemma komutlari, LLM istek/yanitlari ve ajanlar arasi hareketler canli izlenir.
      </div>

      <div className="flex-1 overflow-y-auto terminal-log px-3 py-2 space-y-1">
        {events.length === 0 ? (
          <div className="text-gray-600 text-xs">Event bekleniyor...</div>
        ) : (
          events.map((e, i) => {
            const isCommand = e.type.startsWith("command.");
            const isDecision = e.type === "decision.made";
            const isRedis = e.type === "system.redis_message";
            const levelClass = isCommand
              ? "text-accent"
              : isDecision
                ? "text-emerald-300"
                : isRedis
                  ? "text-sky-300"
              : e.type.includes("rejected") || e.type.includes("failed")
                ? "text-bear"
                : "text-gray-300";

            return (
              <div key={`${e.timestamp}-${i}`} className="text-[11px] leading-5 border-b border-surface-border/20 pb-1">
                <div className="flex items-center gap-2">
                  <span className="text-gray-500 font-mono">{fmtTime(e.timestamp)}</span>
                  <span className={`font-mono ${levelClass}`}>{e.type}</span>
                  <span className="text-gray-600">[{e.source}]</span>
                </div>
                <div className="text-[10px] text-gray-400 mt-0.5">
                  {summarize(e.data_preview, e.type) || "Ozet yok"}
                </div>
                {e.data_preview && (
                  <pre className="text-[10px] text-gray-500 whitespace-pre-wrap break-words mt-0.5">
                    {JSON.stringify(e.data_preview)}
                  </pre>
                )}
              </div>
            );
          })
        )}
      </div>
    </div>
  );
}
