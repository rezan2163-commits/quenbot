"use client";

import { useMamisStatus } from "@/lib/api";

function metricTone(value: number, positiveThreshold: number, negativeThreshold: number) {
  if (value >= positiveThreshold) return "text-emerald-400";
  if (value <= negativeThreshold) return "text-rose-400";
  return "text-gray-300";
}

function formatCompact(value: number, digits = 1) {
  if (!Number.isFinite(value)) return "-";
  const absValue = Math.abs(value);
  if (absValue >= 1_000_000_000) return `${(value / 1_000_000_000).toFixed(digits)}B`;
  if (absValue >= 1_000_000) return `${(value / 1_000_000).toFixed(digits)}M`;
  if (absValue >= 1_000) return `${(value / 1_000).toFixed(digits)}K`;
  return value.toFixed(digits);
}

export default function MamisPanel() {
  const { data } = useMamisStatus();
  const latestBar = data?.bars?.[0];
  const latestClassification = data?.classifications?.[0];
  const latestSignal = data?.signals?.[0];

  return (
    <div className="h-full overflow-y-auto bg-surface-card px-4 py-4 text-gray-200 custom-scrollbar">
      <div className="mb-4 flex items-center justify-between">
        <div>
          <div className="text-xs uppercase tracking-[0.24em] text-gray-500">MAMIS</div>
          <div className="text-lg font-semibold">Microstructure Intelligence</div>
        </div>
        <div className={`rounded-full px-2 py-1 text-[10px] font-semibold ${data?.health?.healthy ? "bg-emerald-500/15 text-emerald-300" : "bg-rose-500/15 text-rose-300"}`}>
          {data?.health?.healthy ? "AKTIF" : "PASIF"}
        </div>
      </div>

      <div className="grid grid-cols-2 gap-3 mb-4">
        <div className="rounded-xl border border-surface-border bg-surface/50 p-3">
          <div className="text-[10px] uppercase tracking-[0.18em] text-gray-500">OFI</div>
          <div className={`mt-1 text-xl font-semibold ${metricTone(latestBar?.ofi_normalized ?? 0, 0.12, -0.12)}`}>
            {latestBar ? latestBar.ofi_normalized.toFixed(3) : "-"}
          </div>
        </div>
        <div className="rounded-xl border border-surface-border bg-surface/50 p-3">
          <div className="text-[10px] uppercase tracking-[0.18em] text-gray-500">VPIN</div>
          <div className={`mt-1 text-xl font-semibold ${metricTone(latestBar?.vpin ?? 0, 0.62, 0.25)}`}>
            {latestBar ? latestBar.vpin.toFixed(3) : "-"}
          </div>
        </div>
        <div className="rounded-xl border border-surface-border bg-surface/50 p-3">
          <div className="text-[10px] uppercase tracking-[0.18em] text-gray-500">CVD</div>
          <div className={`mt-1 text-xl font-semibold ${metricTone(latestBar?.cumulative_volume_delta ?? 0, 1, -1)}`}>
            {latestBar ? formatCompact(latestBar.cumulative_volume_delta, 1) : "-"}
          </div>
        </div>
        <div className="rounded-xl border border-surface-border bg-surface/50 p-3">
          <div className="text-[10px] uppercase tracking-[0.18em] text-gray-500">Pattern</div>
          <div className="mt-1 text-sm font-semibold text-amber-300">
            {latestClassification?.pattern_type || "-"}
          </div>
        </div>
      </div>

      <div className="space-y-4">
        <section className="rounded-2xl border border-surface-border bg-surface/40 p-4">
          <div className="mb-2 text-xs uppercase tracking-[0.18em] text-gray-500">Son Sinyal</div>
          {latestSignal ? (
            <div className="space-y-2 text-sm">
              <div className="flex items-center justify-between">
                <span className="font-semibold">{latestSignal.symbol}</span>
                <span className={latestSignal.signal_direction === "long" ? "text-emerald-400" : latestSignal.signal_direction === "short" ? "text-rose-400" : "text-gray-300"}>
                  {latestSignal.signal_direction}
                </span>
              </div>
              <div>Confidence: %{(latestSignal.confidence_score * 100).toFixed(1)}</div>
              <div>Volatility: %{(latestSignal.estimated_volatility * 100).toFixed(3)}</div>
              <div>Position Size: ${latestSignal.position_size.toFixed(0)}</div>
              <div className="text-gray-400">{latestSignal.detected_pattern_type}</div>
            </div>
          ) : (
            <div className="text-sm text-gray-500">Henüz MAMIS sinyali yok.</div>
          )}
        </section>

        <section className="rounded-2xl border border-surface-border bg-surface/40 p-4">
          <div className="mb-3 text-xs uppercase tracking-[0.18em] text-gray-500">Son Sınıflandırmalar</div>
          <div className="space-y-2">
            {(data?.classifications || []).slice(0, 5).map((item, index) => (
              <div key={`${item.symbol}-${index}`} className="rounded-xl border border-surface-border bg-surface/60 p-3 text-sm">
                <div className="flex items-center justify-between">
                  <span className="font-semibold">{item.symbol}</span>
                  <span className="text-xs text-gray-400">%{(item.confidence * 100).toFixed(0)}</span>
                </div>
                <div className="mt-1 text-amber-300">{item.pattern_type}</div>
                <div className="mt-1 text-gray-400">{item.reason}</div>
              </div>
            ))}
            {!data?.classifications?.length && <div className="text-sm text-gray-500">Sınıflandırma akışı bekleniyor.</div>}
          </div>
        </section>

        <section className="rounded-2xl border border-surface-border bg-surface/40 p-4">
          <div className="mb-3 text-xs uppercase tracking-[0.18em] text-gray-500">Bar Akışı</div>
          <div className="space-y-2">
            {(data?.bars || []).slice(0, 6).map((bar, index) => (
              <div key={`${bar.symbol}-${bar.bar_index}-${index}`} className="grid grid-cols-2 gap-2 rounded-xl border border-surface-border bg-surface/60 p-3 text-xs">
                <div className="font-semibold text-gray-200">{bar.symbol}</div>
                <div className="text-right text-gray-400">#{bar.bar_index}</div>
                <div>OFI: <span className={metricTone(bar.ofi_normalized, 0.12, -0.12)}>{bar.ofi_normalized.toFixed(3)}</span></div>
                <div>VPIN: <span className={metricTone(bar.vpin, 0.62, 0.25)}>{bar.vpin.toFixed(3)}</span></div>
                <div>CVD: {formatCompact(bar.cumulative_volume_delta, 1)}</div>
                <div>CTR: {bar.cancel_to_trade_ratio.toFixed(2)}</div>
              </div>
            ))}
            {!data?.bars?.length && <div className="text-sm text-gray-500">Event bar bekleniyor.</div>}
          </div>
        </section>
      </div>
    </div>
  );
}