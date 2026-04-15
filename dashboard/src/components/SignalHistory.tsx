"use client";

import { useSignalHistory } from "@/lib/api";
import { History, ArrowUp, ArrowDown, Filter } from "lucide-react";
import { useState } from "react";
import { formatInQuenbotTimeZone } from "@/lib/time";

export default function SignalHistory() {
  const [symbolFilter, setSymbolFilter] = useState("");
  const [statusFilter, setStatusFilter] = useState("");
  const { data: signals } = useSignalHistory(symbolFilter || undefined, statusFilter || undefined);
  const toNumber = (value: unknown, fallback = 0) => {
    const n = Number(value);
    return Number.isFinite(n) ? n : fallback;
  };

  const symbols = [...new Set(signals?.map((s) => s.symbol) || [])].sort();

  return (
    <div className="h-full flex flex-col bg-surface-card/30 overflow-hidden">
      <div className="flex items-center justify-between px-3 py-2 border-b border-surface-border">
        <div className="flex items-center gap-1.5">
          <History size={12} className="text-accent" />
          <span className="text-xs font-semibold text-gray-300 tracking-wide">SİNYAL GEÇMİŞİ</span>
        </div>
        <span className="text-[10px] text-gray-500">{signals?.length ?? 0} kayıt</span>
      </div>

      {/* Filters */}
      <div className="flex gap-1.5 px-3 py-1.5 border-b border-surface-border/50">
        <select
          value={symbolFilter}
          onChange={(e) => setSymbolFilter(e.target.value)}
          className="flex-1 bg-black/30 border border-surface-border rounded px-2 py-1 text-[10px] text-gray-300 focus:outline-none focus:border-accent"
        >
          <option value="">Tüm Coinler</option>
          {symbols.map((s) => (
            <option key={s} value={s}>{s}</option>
          ))}
        </select>
        <select
          value={statusFilter}
          onChange={(e) => setStatusFilter(e.target.value)}
          className="flex-1 bg-black/30 border border-surface-border rounded px-2 py-1 text-[10px] text-gray-300 focus:outline-none focus:border-accent"
        >
          <option value="">Tüm Durumlar</option>
          <option value="active">Active</option>
          <option value="closed">Closed</option>
          <option value="expired">Expired</option>
        </select>
      </div>

      {/* Table */}
      <div className="flex-1 overflow-y-auto custom-scrollbar">
        {!signals || signals.length === 0 ? (
          <div className="flex items-center justify-center h-full text-gray-600 text-xs">Sinyal kaydı yok</div>
        ) : (
          <table className="w-full text-[10px]">
            <thead className="sticky top-0 bg-surface-card/80 backdrop-blur">
              <tr className="text-gray-500 border-b border-surface-border">
                <th className="text-left px-2 py-1.5 font-medium">Coin</th>
                <th className="text-left px-2 py-1.5 font-medium">Yön</th>
                <th className="text-right px-2 py-1.5 font-medium">Fiyat</th>
                <th className="text-right px-2 py-1.5 font-medium">Güven</th>
                <th className="text-center px-2 py-1.5 font-medium">Durum</th>
                <th className="text-right px-2 py-1.5 font-medium">Zaman</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-surface-border/30">
              {signals.map((s) => {
                const isLong = (s.direction || "").toLowerCase() === "long" || (s.direction || "").toLowerCase() === "buy";
                return (
                  <tr key={s.id} className="hover:bg-white/[0.02] transition-colors">
                    <td className="px-2 py-1.5 text-gray-200 font-medium">{s.symbol}</td>
                    <td className="px-2 py-1.5">
                      <span className={`flex items-center gap-0.5 ${isLong ? "text-bull" : "text-bear"}`}>
                        {isLong ? <ArrowUp size={10} /> : <ArrowDown size={10} />}
                        {(s.direction || "?").toUpperCase()}
                      </span>
                    </td>
                    <td className="px-2 py-1.5 text-right font-mono text-gray-300">
                      ${toNumber(s.price) < 1 ? toNumber(s.price).toFixed(6) : toNumber(s.price).toLocaleString()}
                    </td>
                    <td className="px-2 py-1.5 text-right text-accent">{(toNumber(s.confidence) * 100).toFixed(0)}%</td>
                    <td className="px-2 py-1.5 text-center">
                      <span className={`inline-block px-1.5 py-0.5 rounded text-[9px] font-medium ${
                        s.status === "active" ? "bg-bull/10 text-bull" :
                        s.status === "closed" ? "bg-gray-700/50 text-gray-400" :
                        "bg-warn/10 text-warn"
                      }`}>
                        {s.status}
                      </span>
                    </td>
                    <td className="px-2 py-1.5 text-right text-gray-500">
                      {formatInQuenbotTimeZone(s.signal_time || s.timestamp, { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" })}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}
