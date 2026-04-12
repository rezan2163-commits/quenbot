"use client";

import { useState, useRef, useEffect } from "react";
import { useChatMessages, sendChat, ChatMessage } from "@/lib/api";
import { MessageSquare, Send, X } from "lucide-react";

export default function ChatPanel() {
  const [open, setOpen] = useState(false);
  const [input, setInput] = useState("");
  const [sending, setSending] = useState(false);
  const [localMessages, setLocalMessages] = useState<{ role: string; message: string }[]>([]);
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

  if (!open) {
    return (
      <button
        onClick={() => setOpen(true)}
        className="fixed bottom-4 left-4 z-40 flex items-center gap-2 px-4 py-2.5 rounded-xl bg-surface-card border border-surface-border text-gray-300 text-sm font-medium shadow-lg hover:bg-surface-hover transition-colors"
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
    <div className="fixed bottom-4 left-4 z-40 w-96 h-[500px] flex flex-col bg-surface-card border border-surface-border rounded-2xl shadow-2xl overflow-hidden">
      {/* Header */}
      <div className="flex items-center justify-between px-4 py-2.5 border-b border-surface-border bg-surface-card">
        <div className="flex items-center gap-2">
          <MessageSquare size={14} className="text-accent" />
          <span className="text-sm font-semibold">QuenBot Chat</span>
        </div>
        <button
          onClick={() => setOpen(false)}
          className="text-gray-500 hover:text-gray-300"
        >
          <X size={14} />
        </button>
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
          placeholder="QuenBot'a sor..."
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
