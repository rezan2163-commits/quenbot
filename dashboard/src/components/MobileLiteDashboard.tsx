"use client";

import { useMemo, useState } from "react";
import { Activity, Bell, Eye, Layers3, MessageSquareMore, TrendingDown, TrendingUp } from "lucide-react";
import { useLivePrices, useSignals, useSimulations, useSystemSummary, useWatchlist } from "@/lib/api";

type MobileTab = "overview" | "signals" | "watchlist" | "positions";

function toNumber(value: unknown, fallback = 0) {
  const numeric = Number(value);
  return Number.isFinite(numeric) ? numeric : fallback;
}

function formatPrice(value: unknown) {
  return `$${toNumber(value).toLocaleString("en-US", { maximumFractionDigits: 6 })}`;
}

function normalizeTargetPct(value: unknown) {
  const numeric = toNumber(value, 0);
  if (numeric <= 0) return 0;
  return numeric > 0.5 ? numeric / 100 : numeric;
}

function signalTargetPct(signal: any) {
  const direct = normalizeTargetPct(signal.target_pct ?? signal.metadata?.target_pct ?? signal.metadata?.predicted_magnitude);
  if (direct > 0) return direct;
  const entry = toNumber(signal.entry_price ?? signal.price, 0);
  const target = toNumber(signal.target_price, 0);
  if (entry > 0 && target > 0) return Math.abs((target - entry) / entry);
  return 0;
}

export default function MobileLiteDashboard() {
  const [tab, setTab] = useState<MobileTab>("overview");
  const { data: system } = useSystemSummary();
  const { data: signals } = useSignals();
  const { data: simulations } = useSimulations();
  const { data: watchlist } = useWatchlist();
  const { data: prices } = useLivePrices();

  const latestPrices = useMemo(() => {
    const map = new Map<string, { symbol: string; price: number; exchange: string; market_type: string; timestamp: string }>();
    for (const item of prices || []) {
      const existing = map.get(item.symbol);
      if (!existing || new Date(item.timestamp) > new Date(existing.timestamp)) {
        map.set(item.symbol, {
          symbol: item.symbol,
          price: toNumber(item.price),
          exchange: item.exchange,
          market_type: item.market_type,
          timestamp: item.timestamp,
        });
      }
    }
    return Array.from(map.values()).sort((left, right) => left.symbol.localeCompare(right.symbol));
  }, [prices]);

  const openSimulations = (simulations || []).filter((item) => item.status === "open");
  const visibleSignals = (signals || []).filter((item) => !["dismissed", "closed", "failed", "expired"].includes(item.status));
  const movementList = [...visibleSignals]
    .sort((a, b) => (signalTargetPct(b) * 100 + toNumber(b.confidence) * 10) - (signalTargetPct(a) * 100 + toNumber(a.confidence) * 10))
    .slice(0, 8);

  const tabs: Array<{ key: MobileTab; label: string; icon: React.ElementType }> = [
    { key: "overview", label: "Ozet", icon: Activity },
    { key: "signals", label: "Sinyal", icon: Bell },
    { key: "watchlist", label: "Izleme", icon: Eye },
    { key: "positions", label: "Pozisyon", icon: Layers3 },
  ];

  return (
    <div className="flex h-full min-h-0 flex-col bg-surface lg:hidden">
      <div className="border-b border-surface-border px-3 py-3">
        <div className="flex items-start justify-between gap-3">
          <div>
            <div className="text-xs font-semibold uppercase tracking-[0.18em] text-gray-400">Lite Dashboard</div>
            <div className="mt-1 text-lg font-semibold text-gray-100">QuenBot Mobile</div>
            <div className="mt-1 text-[11px] text-gray-500">
              {system?.mode || "-"} • {system?.health || "-"} • {system?.llm?.model || "model yok"}
            </div>
          </div>
          <div className="rounded-xl border border-surface-border bg-surface-card px-3 py-2 text-right">
            <div className="text-[10px] uppercase tracking-[0.16em] text-gray-500">RAM</div>
            <div className="mt-1 text-sm font-semibold text-gray-100">{system?.resources?.ram_mb || "-"}</div>
          </div>
        </div>

        <div className="mt-3 grid grid-cols-2 gap-2">
          <StatCard label="Aktif Sinyal" value={`${visibleSignals.length}`} tone="text-cyan-300" />
          <StatCard label="Acik Pozisyon" value={`${openSimulations.length}`} tone="text-amber-300" />
          <StatCard label="Islem Modu" value={system?.state?.mode || "-"} tone="text-emerald-300" />
          <StatCard label="Toplam PnL" value={`${toNumber(system?.state?.pnl).toFixed(2)}%`} tone={toNumber(system?.state?.pnl) >= 0 ? "text-emerald-300" : "text-rose-300"} />
        </div>
      </div>

      <div className="grid grid-cols-4 border-b border-surface-border bg-surface-card/40">
        {tabs.map((item) => (
          <button
            key={item.key}
            onClick={() => setTab(item.key)}
            className={`flex flex-col items-center gap-1 px-2 py-3 text-[10px] font-medium transition-colors ${
              tab === item.key ? "text-accent" : "text-gray-500"
            }`}
          >
            <item.icon size={14} />
            {item.label}
          </button>
        ))}
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto px-3 py-3 pb-28">
        {tab === "overview" && (
          <div className="space-y-3">
            <Section title="Sistem Durumu">
              <InfoRow label="Health" value={system?.health || "-"} />
              <InfoRow label="CPU" value={`%${toNumber(system?.resources?.cpu).toFixed(1)}`} />
              <InfoRow label="Disk" value={`%${toNumber(system?.resources?.disk).toFixed(1)}`} />
              <InfoRow label="Trade Sayisi" value={`${toNumber(system?.state?.trades)}`} />
            </Section>

            <Section title="En Kritik Sinyaller">
              <div className="space-y-2">
                {visibleSignals.slice(0, 4).map((signal) => (
                  <SignalRow key={signal.id} signal={signal} />
                ))}
                {visibleSignals.length === 0 && <EmptyState text="Aktif sinyal bekleniyor." />}
              </div>
            </Section>
          </div>
        )}

        {tab === "signals" && (
          <div className="space-y-3">
            <Section title="Hareket Beklenen Coinler">
              <div className="flex gap-1.5 overflow-x-auto pb-1 custom-scrollbar">
                {movementList.length === 0 ? (
                  <EmptyState text="Liste bos." />
                ) : (
                  movementList.map((signal) => {
                    const targetPct = signalTargetPct(signal) * 100;
                    const bullish = String(signal.direction || "long").toLowerCase() !== "short";
                    return (
                      <div
                        key={`mobile-move-${signal.id}`}
                        className={`min-w-fit rounded-full border px-2.5 py-1 text-[10px] font-medium whitespace-nowrap ${bullish ? "border-emerald-400/25 bg-emerald-400/10 text-emerald-200" : "border-rose-400/25 bg-rose-400/10 text-rose-200"}`}
                      >
                        {signal.symbol} • %{targetPct.toFixed(1)}
                      </div>
                    );
                  })
                )}
              </div>
            </Section>

            <div className="space-y-2">
            {visibleSignals.slice(0, 12).map((signal) => (
              <SignalRow key={signal.id} signal={signal} compact={false} />
            ))}
            {visibleSignals.length === 0 && <EmptyState text="Gosterilecek aktif sinyal yok." />}
            </div>
          </div>
        )}

        {tab === "watchlist" && (
          <div className="space-y-2">
            {(watchlist || []).slice(0, 16).map((item) => {
              const latest = latestPrices.find((price) => price.symbol === item.symbol);
              return (
                <div key={`${item.id}-${item.symbol}`} className="rounded-2xl border border-surface-border bg-surface-card/50 px-3 py-3">
                  <div className="flex items-center justify-between gap-3">
                    <div>
                      <div className="text-sm font-semibold text-gray-100">{item.symbol}</div>
                      <div className="text-[11px] text-gray-500">{item.exchange} • {item.market_type}</div>
                    </div>
                    <div className="text-right">
                      <div className="text-sm font-semibold text-gray-100">{latest ? formatPrice(latest.price) : "-"}</div>
                      <div className="text-[11px] text-gray-500">{latest?.exchange || "veri yok"}</div>
                    </div>
                  </div>
                </div>
              );
            })}
            {(!watchlist || watchlist.length === 0) && <EmptyState text="Izleme listesi bos." />}
          </div>
        )}

        {tab === "positions" && (
          <div className="space-y-2">
            {openSimulations.slice(0, 12).map((simulation) => {
              const pnlPct = toNumber(simulation.pnl_pct);
              const positive = pnlPct >= 0;
              return (
                <div key={simulation.id} className="rounded-2xl border border-surface-border bg-surface-card/50 px-3 py-3">
                  <div className="flex items-center justify-between gap-3">
                    <div>
                      <div className="text-sm font-semibold text-gray-100">{simulation.symbol}</div>
                      <div className="text-[11px] text-gray-500">{simulation.side} • {simulation.status}</div>
                    </div>
                    <div className={`flex items-center gap-1 text-sm font-semibold ${positive ? "text-emerald-300" : "text-rose-300"}`}>
                      {positive ? <TrendingUp size={14} /> : <TrendingDown size={14} />}
                      {pnlPct.toFixed(2)}%
                    </div>
                  </div>
                  <div className="mt-2 text-[11px] text-gray-400">Giris {formatPrice(simulation.entry_price)}</div>
                </div>
              );
            })}
            {openSimulations.length === 0 && <EmptyState text="Acik pozisyon yok." />}
          </div>
        )}
      </div>

      <div className="fixed inset-x-0 bottom-0 z-30 border-t border-surface-border bg-surface/95 px-3 py-2 backdrop-blur lg:hidden">
        <div className="flex items-center justify-between rounded-2xl border border-surface-border bg-surface-card px-3 py-2">
          <div>
            <div className="text-[10px] uppercase tracking-[0.16em] text-gray-500">Tek Elle Hizli Erisim</div>
            <div className="mt-1 text-xs text-gray-300">Chat ve strateji butonlari alt koselerde aktif.</div>
          </div>
          <MessageSquareMore size={16} className="text-accent" />
        </div>
      </div>
    </div>
  );
}

function StatCard({ label, value, tone }: { label: string; value: string; tone: string }) {
  return (
    <div className="rounded-2xl border border-surface-border bg-surface-card/50 px-3 py-3">
      <div className="text-[10px] uppercase tracking-[0.16em] text-gray-500">{label}</div>
      <div className={`mt-2 text-base font-semibold ${tone}`}>{value}</div>
    </div>
  );
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="rounded-2xl border border-surface-border bg-surface-card/50 px-3 py-3">
      <div className="mb-3 text-[11px] font-semibold uppercase tracking-[0.18em] text-gray-400">{title}</div>
      {children}
    </section>
  );
}

function InfoRow({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-center justify-between gap-3 py-1 text-sm">
      <span className="text-gray-500">{label}</span>
      <span className="font-medium text-gray-100">{value}</span>
    </div>
  );
}

function SignalRow({ signal, compact = true }: { signal: any; compact?: boolean }) {
  const confidence = toNumber(signal.confidence) * 100;
  const bullish = String(signal.direction || "long").toLowerCase() !== "short";
  const targetPct = signalTargetPct(signal) * 100;
  return (
    <div className="rounded-2xl border border-surface-border bg-surface/70 px-2.5 py-2.5">
      <div className="flex items-start justify-between gap-3">
        <div>
          <div className="text-sm font-semibold text-gray-100">{signal.symbol}</div>
          <div className="text-[11px] text-gray-500">{signal.signal_type} • {signal.status}</div>
        </div>
        <div className={`flex items-center gap-1 text-sm font-semibold ${bullish ? "text-emerald-300" : "text-rose-300"}`}>
          {bullish ? <TrendingUp size={14} /> : <TrendingDown size={14} />}
          {signal.direction}
        </div>
      </div>
      <div className={`mt-2 ${compact ? "grid grid-cols-3 gap-1.5" : "grid grid-cols-3 gap-1.5"}`}>
        <MiniMetric label="Güven" value={`%${confidence.toFixed(0)}`} />
        <MiniMetric label="Fiyat" value={formatPrice(signal.price)} />
        <MiniMetric label="Hedef" value={signal.target_price ? formatPrice(signal.target_price) : ` %${targetPct.toFixed(1)}`} />
      </div>
    </div>
  );
}

function MiniMetric({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-xl border border-surface-border bg-surface-card/60 px-3 py-2">
      <div className="text-[10px] uppercase tracking-[0.16em] text-gray-500">{label}</div>
      <div className="mt-1 text-sm font-medium text-gray-100">{value}</div>
    </div>
  );
}

function EmptyState({ text }: { text: string }) {
  return <div className="rounded-2xl border border-dashed border-surface-border px-3 py-6 text-center text-xs text-gray-500">{text}</div>;
}