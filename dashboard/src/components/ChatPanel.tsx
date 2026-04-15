"use client";

import { useState, useRef, useEffect } from "react";
import { useChatMessages, sendChat, clearChatMessages, ChatActionView } from "@/lib/api";
import { MessageSquare, Send, Trash2, X } from "lucide-react";

function formatActionLabel(action: ChatActionView) {
  switch (action.type) {
    case "watchlist_add":
      return `Watchlist + ${String((action.symbols || []).join(", "))}`;
    case "watchlist_remove":
      return `Watchlist - ${String((action.symbols || []).join(", "))}`;
    case "risk_update":
      return `Risk: ${Object.entries(action.changes || {}).map(([key, value]) => `${key}=${value}`).join(", ")}`;
    case "master_directive_update":
      return `Direktif: ${String(action.text || "").slice(0, 80)}`;
    case "system_mode_update":
      return `Mod: ${String(action.mode || "?")}`;
    case "cleanup_run":
      return `Temizlik: dry_run=${String(action.dry_run)} stale=${String(action.stale_count || 0)} deleted=${String(action.deleted_count || 0)}`;
    case "system_diagnostic":
      return `Diagnostik: mode=${String(action.system_mode || "?")} llm=${String(action.llm_ok ? "ok" : "degraded")}`;
    case "symbol_analysis":
      return `Analiz: ${String(action.symbol || "?")} ${String(action.overall_signal?.direction || "neutral")} conf=${String(action.overall_signal?.confidence ?? 0)}`;
    case "code_change_request":
      return `Kod Gorevi: ${String(action.prompt || action.summary || "istek").slice(0, 80)}`;
    default:
      return String(action.type || "komut");
  }
}

export default function ChatPanel() {
  const [open, setOpen] = useState(false);
  const [input, setInput] = useState("");
  const [sending, setSending] = useState(false);
  const [localMessages, setLocalMessages] = useState<{ role: string; message: string }[]>([]);
  const [assistantLabel, setAssistantLabel] = useState("Qwen Command");
  const [commandLog, setCommandLog] = useState<Array<{ id: string; text: string }>>([]);
  const { data: messages, mutate } = useChatMessages();
  const scrollRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
    }
  }, [messages, localMessages]);

  const handleSend = async () => {
    const msg = input.trim();
    if (!msg || sending) return;
    setInput("");
    setSending(true);
    setLocalMessages((prev) => [...prev, { role: "user", message: msg }]);

    try {
      const res = await sendChat(msg);
      if (res.assistant?.name) {
        setAssistantLabel(res.assistant.name);
      }
      if (res.routed_actions?.length) {
        const nextItems = res.routed_actions.map((action, index) => ({
          id: `${Date.now()}-${index}`,
          text: formatActionLabel(action as ChatActionView),
        }));
        setCommandLog((prev) => [...nextItems, ...prev].slice(0, 8));
      }
      if (res.message) {
        setLocalMessages((prev) => [
          ...prev,
          { role: "assistant", message: res.message },
        ]);
      }
      mutate();
    } catch {
      setLocalMessages((prev) => [
        ...prev,
        { role: "assistant", message: "Yanıt alınamadı, tekrar dene." },
      ]);
    } finally {
      setSending(false);
    }
  };

  const handleClearChat = async () => {
    if (sending) return;
    try {
      await clearChatMessages();
      setLocalMessages([]);
      setCommandLog([]);
      await mutate([], { revalidate: false });
    } catch {
      setLocalMessages((prev) => [
        ...prev,
        { role: "assistant", message: "Sohbet temizlenemedi, tekrar dene." },
      ]);
    }
  };

  if (!open) {
    return (
      <button
        onClick={() => setOpen(true)}
        className="fixed bottom-20 left-3 z-40 flex items-center gap-2 rounded-xl border border-surface-border bg-surface-card px-3 py-2 text-sm font-medium text-gray-300 shadow-lg transition-colors hover:bg-surface-hover sm:bottom-4 sm:left-4 sm:px-4 sm:py-2.5"
      >
        <MessageSquare size={16} className="text-accent" />
        Chat
      </button>
    );
  }

  const allMessages = [
    ...(messages || []).map((m) => ({ role: m.role, message: m.message })),
    ...localMessages,
  ];

  return (
    <div className="fixed inset-x-3 bottom-3 z-40 flex h-[min(68svh,34rem)] flex-col overflow-hidden rounded-2xl border border-surface-border bg-surface-card shadow-2xl sm:bottom-4 sm:left-4 sm:right-auto sm:w-96 sm:h-[500px]">
      {/* Header */}
      <div className="flex items-center justify-between px-4 py-2.5 border-b border-surface-border bg-surface-card">
        <div className="flex items-center gap-2">
          <MessageSquare size={14} className="text-accent" />
          <span className="text-sm font-semibold">{assistantLabel}</span>
        </div>
        <div className="flex items-center gap-2">
          <button
            onClick={handleClearChat}
            className="inline-flex items-center gap-1 rounded-lg border border-surface-border px-2 py-1 text-xs text-gray-400 hover:text-gray-200"
          >
            <Trash2 size={12} />
            Sohbeti Temizle
          </button>
          <button
            onClick={() => setOpen(false)}
            className="text-gray-500 hover:text-gray-300"
          >
            <X size={14} />
          </button>
        </div>
      </div>

      <div className="border-b border-surface-border bg-surface/40 px-4 py-3">
        <div className="mb-2 text-[11px] font-semibold uppercase tracking-[0.18em] text-gray-500">
          Uygulanan Komutlar
        </div>
        {commandLog.length ? (
          <div className="space-y-2">
            {commandLog.map((item) => (
              <div key={item.id} className="rounded-lg border border-surface-border bg-surface/60 px-3 py-2 text-xs text-gray-300">
                {item.text}
              </div>
            ))}
          </div>
        ) : (
          <div className="rounded-lg border border-dashed border-surface-border px-3 py-2 text-xs text-gray-500">
            Henuz uygulanmis komut yok.
          </div>
        )}
      </div>

      {/* Messages */}
      <div ref={scrollRef} className="flex-1 overflow-y-auto px-4 py-3 space-y-3">
        {allMessages.map((m, i) => (
          <div
            key={i}
            className={`flex ${m.role === "user" ? "justify-end" : "justify-start"}`}
          >
            <div
              className={`max-w-[85%] px-3 py-2 rounded-xl text-sm ${
                m.role === "user"
                  ? "bg-accent/20 text-gray-200"
                  : "bg-surface/60 text-gray-300 border border-surface-border"
              }`}
            >
              {m.message}
            </div>
          </div>
        ))}
        {sending && (
          <div className="flex justify-start">
            <div className="px-3 py-2 rounded-xl bg-surface/60 border border-surface-border text-gray-500 text-sm">
              Düşünüyor...
            </div>
          </div>
        )}
      </div>

      {/* Input */}
      <div className="px-3 py-2 border-t border-surface-border flex gap-2">
        <input
          type="text"
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && handleSend()}
          placeholder="Qwen'e emir ver veya soru sor..."
          className="flex-1 px-3 py-2 rounded-lg bg-surface border border-surface-border text-sm text-gray-200 placeholder:text-gray-600 focus:outline-none focus:border-accent/50"
          disabled={sending}
        />
        <button
          onClick={handleSend}
          disabled={sending || !input.trim()}
          className="px-3 py-2 rounded-lg bg-accent text-white hover:bg-accent-dim disabled:opacity-50 transition-colors"
        >
          <Send size={14} />
        </button>
      </div>
    </div>
  );
}
