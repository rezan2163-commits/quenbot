"use client";

import { useSignatureMatches, SignatureMatchRecord } from "@/lib/api";
import { Radar, TrendingUp, TrendingDown } from "lucide-react";
import { useState } from "react";
import { formatInQuenbotTimeZone } from "@/lib/time";

function SimilarityBar({ value }: { value: number }) {
  const pct = Math.min(100, Math.max(0, value * 100));
  const color =
    pct >= 80 ? "bg-emerald-400" : pct >= 70 ? "bg-cyan-400" : "bg-amber-400";
  return (
    <div className="flex items-center gap-1.5 min-w-[80px]">
      <div className="flex-1 h-1 bg-gray-800 rounded-full overflow-hidden">
        <div className={`h-full ${color} rounded-full`} style={{ width: `${pct}%` }} />
      </div>
      <span className={`text-[10px] font-mono ${pct >= 80 ? "text-emerald-300" : pct >= 70 ? "text-cyan-300" : "text-amber-300"}`}>
        %{pct.toFixed(1)}
      </span>
    </div>
  );
}

function ScoreChip({ label, value }: { label: string; value: number }) {
  if (!value) return null;
  return (
    <span className="text-[9px] font-mono px-1.5 py-0.5 rounded bg-gray-800/80 text-gray-400">
      {label}: {(value * 100).toFixed(0)}%
    </span>
  );
}

function MatchCard({ m }: { m: SignatureMatchRecord }) {
  const isLong = m.direction === "long" || m.direction === "up";

  return (
    <div className="border-b border-surface-border/50 px-3 py-2.5 hover:bg-white/[0.02] transition-colors">
      {/* Header row */}
      <div className="flex items-center justify-between mb-1">
        <div className="flex items-center gap-2">
          {isLong ? (
            <TrendingUp size={11} className="text-bull" />
          ) : (
            <TrendingDown size={11} className="text-bear" />
          )}
          <span className="text-xs font-medium text-gray-200">{m.symbol}</span>
          <span className="text-[10px] text-gray-600 uppercase">{m.timeframe}</span>
        </div>
        <SimilarityBar value={m.similarity} />
      </div>

      {/* Score breakdown */}
      <div className="flex flex-wrap gap-1 mb-1">
        <ScoreChip label="DTW" value={m.dtw_score} />
        <ScoreChip label="FFT" value={m.fft_score} />
        <ScoreChip label="COS" value={m.cosine_score} />
        <ScoreChip label="POLY" value={m.poly_score} />
      </div>

      {/* Context */}
      {m.context_string && (
        <p className="text-[10px] text-gray-500 leading-relaxed line-clamp-2">
          {m.context_string}
        </p>
      )}

      {/* Footer */}
      <div className="flex items-center justify-between mt-1">
        <span className="text-[9px] text-gray-600">
          ${Number(m.current_price).toLocaleString(undefined, { maximumFractionDigits: 2 })}
        </span>
        <span className="text-[9px] text-gray-600">
          {formatInQuenbotTimeZone(m.created_at)}
        </span>
      </div>
    </div>
  );
}

export default function SimilarityPanel() {
  const [filter, setFilter] = useState("");
  const { data: matches, isLoading } = useSignatureMatches(filter || undefined);

  return (
    <div className="h-full flex flex-col bg-surface-card/30 overflow-hidden">
      {/* Header */}
      <div className="flex items-center justify-between px-3 py-2 border-b border-surface-border">
        <div className="flex items-center gap-1.5">
          <Radar size={12} className="text-accent" />
          <span className="text-xs font-semibold text-gray-300 tracking-wide">
            SIGNATURE MATCHES
          </span>
        </div>
        <span className="text-[10px] text-gray-500">
          {matches?.length ?? 0} eşleşme
        </span>
      </div>

      {/* Filter */}
      <div className="px-3 py-1.5 border-b border-surface-border/50">
        <input
          type="text"
          placeholder="Sembol filtrele..."
          value={filter}
          onChange={(e) => setFilter(e.target.value.toUpperCase())}
          className="w-full bg-transparent text-[11px] text-gray-300 placeholder-gray-600 outline-none"
        />
      </div>

      {/* List */}
      <div className="flex-1 overflow-y-auto custom-scrollbar">
        {isLoading && (
          <div className="px-3 py-4 text-center text-[10px] text-gray-600">
            Yükleniyor...
          </div>
        )}
        {!isLoading && (!matches || matches.length === 0) && (
          <div className="px-3 py-4 text-center text-[10px] text-gray-600">
            Henüz imza eşleşmesi bulunamadı.
          </div>
        )}
        {(matches || []).map((m) => (
          <MatchCard key={m.id} m={m} />
        ))}
      </div>
    </div>
  );
}
