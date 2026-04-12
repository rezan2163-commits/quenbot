"use client";

import { useEffect, useRef, useState, useCallback } from "react";
import { useLivePrices, useSignals, useSimulations } from "@/lib/api";
import { Terminal as TerminalIcon, Pause, Play, ArrowDown } from "lucide-react";

interface LogEntry {
  id: number;
  time: string;
  level: "info" | "warn" | "error" | "bull" | "bear";
  text: string;
}

let logCounter = 0;

export default function BottomTerminal() {
  const [logs, setLogs] = useState<LogEntry[]>([]);
  const [paused, setPaused] = useState(false);
  const [autoScroll, setAutoScroll] = useState(true);
  const scrollRef = useRef<HTMLDivElement>(null);

  const { data: prices } = useLivePrices();
  const { data: signals } = useSignals();
  const { data: sims } = useSimulations();

  const prevPricesRef = useRef<string>("");
  const prevSignalsRef = useRef<string>("");
  const prevSimsRef = useRef<string>("");

  const addLog = useCallback(
    (level: LogEntry["level"], text: string) => {
      if (paused) return;
      const entry: LogEntry = {
        id: ++logCounter,
        time: new Date().toLocaleTimeString("tr-TR"),
        level,
        text,
      };
      setLogs((prev) => {
        const next = [...prev, entry];
        return next.length > 300 ? next.slice(-200) : next;
      });
    },
    [paused]
  );

  // Price feed
  useEffect(() => {
    if (!prices?.length) return;
    const key = prices.map((p) => `${p.symbol}:${p.price}`).join(",");
    if (key === prevPricesRef.current) return;
    prevPricesRef.current = key;

    for (const p of prices.slice(0, 6)) {
      addLog("info", `${p.symbol} $${p.price.toLocaleString()} [${p.exchange}]`);
    }
  }, [prices, addLog]);

  // Signal feed
  useEffect(() => {
    if (!signals?.length) return;
    const key = signals
      .slice(0, 3)
      .map((s) => s.id)
      .join(",");
    if (key === prevSignalsRef.current) return;
    prevSignalsRef.current = key;

    for (const s of signals.slice(0, 3)) {
      const level = s.direction === "long" ? "bull" : "bear";
      addLog(
        level,
        `SINYAL ${s.direction.toUpperCase()} ${s.symbol} %${(
          s.confidence * 100
        ).toFixed(0)} conf @$${s.price.toLocaleString()} [${s.status}]`
      );
    }
  }, [signals, addLog]);

  // Simulation feed
  useEffect(() => {
    if (!sims?.length) return;
    const key = sims
      .slice(0, 3)
      .map((s) => `${s.id}:${s.status}`)
      .join(",");
    if (key === prevSimsRef.current) return;
    prevSimsRef.current = key;

    for (const sim of sims.slice(0, 3)) {
      if (sim.status === "open") {
        addLog("info", `SIM AÇIK ${sim.symbol} ${sim.side} @$${sim.entry_price.toLocaleString()}`);
      } else if (sim.status === "closed" && sim.pnl_pct != null) {
        const level = sim.pnl_pct >= 0 ? "bull" : "bear";
        addLog(level, `SIM KAPANDI ${sim.symbol} PnL: ${sim.pnl_pct.toFixed(2)}%`);
      }
    }
  }, [sims, addLog]);

  // Auto-scroll
  useEffect(() => {
    if (autoScroll && scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
    }
  }, [logs, autoScroll]);

  return (
    <div className="flex flex-col h-full border-t border-surface-border bg-surface">
      {/* Toolbar */}
      <div className="flex items-center justify-between px-3 py-1.5 bg-surface-card/40 border-b border-surface-border">
        <div className="flex items-center gap-2">
          <TerminalIcon size={13} className="text-gray-500" />
          <span className="text-[11px] font-semibold text-gray-400 uppercase tracking-wider">
            Canlı Veri Akışı
          </span>
          <span className="text-[10px] text-gray-600 font-mono">{logs.length} satır</span>
        </div>
        <div className="flex items-center gap-1">
          <button
            onClick={() => setPaused((p) => !p)}
            className="p-1 rounded hover:bg-surface-hover text-gray-500 hover:text-gray-300 transition-colors"
            title={paused ? "Devam" : "Duraklat"}
          >
            {paused ? <Play size={13} /> : <Pause size={13} />}
          </button>
          <button
            onClick={() => setAutoScroll((a) => !a)}
            className={`p-1 rounded hover:bg-surface-hover transition-colors ${
              autoScroll ? "text-accent" : "text-gray-500 hover:text-gray-300"
            }`}
            title="Otomatik kaydır"
          >
            <ArrowDown size={13} />
          </button>
          <button
            onClick={() => setLogs([])}
            className="text-[10px] text-gray-500 hover:text-gray-300 px-2 py-0.5 rounded hover:bg-surface-hover transition-colors"
          >
            Temizle
          </button>
        </div>
      </div>

      {/* Log content */}
      <div ref={scrollRef} className="flex-1 overflow-y-auto px-3 py-1 terminal-log">
        {logs.map((entry) => (
          <div key={entry.id} className="flex gap-2 py-px">
            <span className="log-time flex-shrink-0">{entry.time}</span>
            <span className={`log-${entry.level}`}>{entry.text}</span>
          </div>
        ))}
        {logs.length === 0 && (
          <div className="text-gray-600 text-center py-4">Veri bekleniyor...</div>
        )}
      </div>
    </div>
  );
}
