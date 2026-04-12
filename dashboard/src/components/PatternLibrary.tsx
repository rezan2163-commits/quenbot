"use client";

import { usePatterns, PatternRecord } from "@/lib/api";
import { Database, ChevronDown, ChevronUp } from "lucide-react";
import { useState } from "react";

function OutcomeBadge({ label, val }: { label: string; val: number | null }) {
  if (val == null) return null;
  const up = val >= 0;
  return (
    <span className={`text-[10px] font-mono ${up ? "text-bull" : "text-bear"}`}>
      {label}: {up ? "+" : ""}{val.toFixed(2)}%
    </span>
  );
}

function PatternRow({ p }: { p: PatternRecord }) {
  const [open, setOpen] = useState(false);
  const snap = typeof p.snapshot_data === "string" ? JSON.parse(p.snapshot_data) : p.snapshot_data;
  const outlook = p.outcome_1h ?? p.outcome_15m ?? null;
  const up = outlook != null && outlook >= 0;

  return (
    <div className="border-b border-surface-border/50">
      <button
        onClick={() => setOpen(!open)}
        className="w-full flex items-center justify-between px-3 py-2 hover:bg-white/[0.02] transition-colors text-left"
      >
        <div className="flex items-center gap-2 min-w-0">
          <span className="text-xs font-medium text-gray-200">{p.symbol}</span>
          <span className="text-[10px] text-gray-600 uppercase">{p.exchange} · {p.market_type}</span>
        </div>
        <div className="flex items-center gap-2">
          {outlook != null && (
            <span className={`text-[10px] font-mono ${up ? "text-bull" : "text-bear"}`}>
              {up ? "+" : ""}{outlook.toFixed(2)}%
            </span>
          )}
          {open ? <ChevronUp size={12} className="text-gray-600" /> : <ChevronDown size={12} className="text-gray-600" />}
        </div>
      </button>
      {open && (
        <div className="px-3 pb-2 space-y-1">
          <div className="flex flex-wrap gap-3">
            <OutcomeBadge label="15d" val={p.outcome_15m} />
            <OutcomeBadge label="1s" val={p.outcome_1h} />
            <OutcomeBadge label="4s" val={p.outcome_4h} />
            <OutcomeBadge label="1g" val={p.outcome_1d} />
          </div>
          <div className="text-[10px] text-gray-600">
            {new Date(p.created_at).toLocaleString("tr-TR")}
          </div>
          {snap && (
            <pre className="text-[9px] text-gray-600 bg-black/20 rounded p-1.5 max-h-24 overflow-auto">
              {JSON.stringify(snap, null, 2).slice(0, 500)}
            </pre>
          )}
        </div>
      )}
    </div>
  );
}

export default function PatternLibrary() {
  const [filter, setFilter] = useState("");
  const { data: patterns } = usePatterns(filter || undefined);

  const symbols = [...new Set(patterns?.map((p) => p.symbol) || [])].sort();

  return (
    <div className="h-full flex flex-col bg-surface-card/30 overflow-hidden">
      <div className="flex items-center justify-between px-3 py-2 border-b border-surface-border">
        <div className="flex items-center gap-1.5">
          <Database size={12} className="text-accent" />
          <span className="text-xs font-semibold text-gray-300 tracking-wide">PATERN KÜTÜPHANESİ</span>
        </div>
        <span className="text-[10px] text-gray-500">{patterns?.length ?? 0} kayıt</span>
      </div>

      {/* Filter */}
      <div className="px-3 py-1.5 border-b border-surface-border/50">
        <select
          value={filter}
          onChange={(e) => setFilter(e.target.value)}
          className="w-full bg-black/30 border border-surface-border rounded px-2 py-1 text-[10px] text-gray-300 focus:outline-none focus:border-accent"
        >
          <option value="">Tüm Coinler</option>
          {symbols.map((s) => (
            <option key={s} value={s}>{s}</option>
          ))}
        </select>
      </div>

      <div className="flex-1 overflow-y-auto custom-scrollbar">
        {!patterns || patterns.length === 0 ? (
          <div className="flex items-center justify-center h-full text-gray-600 text-xs">Patern bulunamadı</div>
        ) : (
          patterns.map((p) => <PatternRow key={p.id} p={p} />)
        )}
      </div>
    </div>
  );
}
