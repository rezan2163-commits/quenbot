"use client";

import { useState } from "react";
import { setDirective, getDirectives } from "@/lib/api";
import { Settings, Send, AlertTriangle, RefreshCw } from "lucide-react";

const PRESET_STRATEGIES = [
  {
    name: "Agresif Mod",
    desc: "Yüksek risk, yüksek ödül. Daha düşük güven eşiği ile daha çok sinyal.",
    directive:
      "Agresif strateji: confidence >= 0.45 olan tüm sinyalleri kabul et. Risk limitlerini 2x genişlet. Daha fazla pozisyon aç.",
  },
  {
    name: "Konservatif Mod",
    desc: "Düşük risk, yavaş ama istikrarlı. Sadece yüksek güvenli sinyaller.",
    directive:
      "Konservatif strateji: sadece confidence >= 0.80 olan sinyalleri kabul et. Günlük max 5 pozisyon. Stop-loss sıkı tut.",
  },
  {
    name: "Sadece BTC/ETH",
    desc: "Yalnızca büyük coinlere odaklan.",
    directive:
      "Sadece BTCUSDT ve ETHUSDT sembollerini işle. Diğer tüm sembolleri yok say. Derin analiz yap.",
  },
  {
    name: "Tarama Modu",
    desc: "Pozisyon açma, sadece izle ve raporla.",
    directive:
      "Gözlem modu: hiçbir pozisyon açma, sinyal üret ama execute etme. Tüm anomalileri raporla.",
  },
];

export default function StrategyControl() {
  const [isOpen, setIsOpen] = useState(false);
  const [customDirective, setCustomDirective] = useState("");
  const [sending, setSending] = useState(false);
  const [feedback, setFeedback] = useState<{ type: "ok" | "err"; msg: string } | null>(null);

  const handleApply = async (directive: string) => {
    setSending(true);
    setFeedback(null);
    try {
      await setDirective(directive);
      setFeedback({ type: "ok", msg: "Strateji tüm ajanlara gönderildi." });
      setTimeout(() => setFeedback(null), 4000);
    } catch {
      setFeedback({ type: "err", msg: "Strateji gönderilemedi." });
    } finally {
      setSending(false);
    }
  };

  if (!isOpen) {
    return (
      <button
        onClick={() => setIsOpen(true)}
        className="fixed bottom-4 right-4 z-50 flex items-center gap-2 px-4 py-2.5 rounded-xl bg-accent text-white text-sm font-medium shadow-lg shadow-accent/25 hover:bg-accent-dim transition-colors"
      >
        <Settings size={16} />
        Strateji Kontrol
      </button>
    );
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm">
      <div className="w-full max-w-lg mx-4 bg-surface-card rounded-2xl border border-surface-border shadow-2xl">
        {/* Header */}
        <div className="flex items-center justify-between p-4 border-b border-surface-border">
          <div className="flex items-center gap-2">
            <Settings size={18} className="text-accent" />
            <h2 className="text-sm font-bold">Global Strateji Kontrol Merkezi</h2>
          </div>
          <button
            onClick={() => setIsOpen(false)}
            className="text-gray-500 hover:text-gray-300 text-lg leading-none"
          >
            ✕
          </button>
        </div>

        {/* Presets */}
        <div className="p-4 space-y-2">
          <p className="text-[11px] text-gray-500 uppercase tracking-wider font-semibold mb-3">
            Hazır Stratejiler
          </p>
          <div className="grid grid-cols-2 gap-2">
            {PRESET_STRATEGIES.map((preset) => (
              <button
                key={preset.name}
                onClick={() => handleApply(preset.directive)}
                disabled={sending}
                className="text-left p-3 rounded-lg bg-surface/60 hover:bg-surface-hover border border-surface-border transition-colors disabled:opacity-50"
              >
                <p className="text-xs font-medium text-gray-200">{preset.name}</p>
                <p className="text-[10px] text-gray-500 mt-0.5">{preset.desc}</p>
              </button>
            ))}
          </div>
        </div>

        {/* Custom directive */}
        <div className="px-4 pb-4 space-y-2">
          <p className="text-[11px] text-gray-500 uppercase tracking-wider font-semibold">
            Özel Direktif
          </p>
          <div className="flex gap-2">
            <input
              type="text"
              value={customDirective}
              onChange={(e) => setCustomDirective(e.target.value)}
              placeholder="Tüm ajanlara gönderilecek strateji emri..."
              className="flex-1 px-3 py-2 rounded-lg bg-surface border border-surface-border text-sm text-gray-200 placeholder:text-gray-600 focus:outline-none focus:border-accent/50"
              onKeyDown={(e) => {
                if (e.key === "Enter" && customDirective.trim()) {
                  handleApply(customDirective);
                }
              }}
            />
            <button
              onClick={() => customDirective.trim() && handleApply(customDirective)}
              disabled={sending || !customDirective.trim()}
              className="px-3 py-2 rounded-lg bg-accent text-white text-sm font-medium hover:bg-accent-dim disabled:opacity-50 transition-colors"
            >
              <Send size={14} />
            </button>
          </div>
        </div>

        {/* Feedback */}
        {feedback && (
          <div
            className={`mx-4 mb-4 px-3 py-2 rounded-lg text-xs flex items-center gap-2 ${
              feedback.type === "ok"
                ? "bg-bull/10 text-bull"
                : "bg-bear/10 text-bear"
            }`}
          >
            {feedback.type === "ok" ? (
              <RefreshCw size={12} />
            ) : (
              <AlertTriangle size={12} />
            )}
            {feedback.msg}
          </div>
        )}

        {/* Warning */}
        <div className="px-4 pb-4">
          <div className="flex items-center gap-2 px-3 py-2 rounded-lg bg-warn/5 border border-warn/20">
            <AlertTriangle size={12} className="text-warn flex-shrink-0" />
            <p className="text-[10px] text-warn/80">
              Direktif tüm ajanlara anlık olarak uygulanır. Sistem paper-trade modundadır.
            </p>
          </div>
        </div>
      </div>
    </div>
  );
}
