"use client";

import { useEfomOverview, useIntegrationOverview, isConnectionHealthy } from "@/lib/api";
import { Activity, BrainCircuit, Cpu, Database, FlaskConical, HardDrive, Network, Radar, Settings2, Trophy, WifiOff, RefreshCw, AlertTriangle } from "lucide-react";
import { formatInQuenbotTimeZone, formatTimeOnly } from "@/lib/time";
import { useState, useEffect } from "react";

function toNumber(value: unknown, fallback = 0) {
  const numeric = Number(value);
  return Number.isFinite(numeric) ? numeric : fallback;
}

function formatCompact(value: unknown) {
  const numeric = toNumber(value, 0);
  if (Math.abs(numeric) >= 1_000_000) return `${(numeric / 1_000_000).toFixed(1)}M`;
  if (Math.abs(numeric) >= 1_000) return `${(numeric / 1_000).toFixed(1)}K`;
  return `${numeric.toFixed(0)}`;
}

function statusTone(status: string) {
  if (status === "running" || status === "healthy") return "text-emerald-300 border-emerald-400/25 bg-emerald-400/10";
  if (status === "stale") return "text-amber-300 border-amber-400/25 bg-amber-400/10";
  return "text-rose-300 border-rose-400/25 bg-rose-400/10";
}

function EmptyState({ message }: { message: string }) {
  return <div className="rounded-xl border border-dashed border-white/10 bg-white/[0.02] px-3 py-4 text-[11px] text-gray-500">{message}</div>;
}

function ConnectionWarning({ isLoading, onRetry }: { isLoading: boolean; onRetry: () => void }) {
  return (
    <div className="rounded-xl border border-amber-400/30 bg-amber-400/5 px-4 py-3 flex items-center justify-between gap-3">
      <div className="flex items-center gap-2">
        <AlertTriangle size={14} className="text-amber-400" />
        <div>
          <div className="text-[11px] text-amber-300 font-medium">Bağlantı Bekleniyor</div>
          <div className="text-[10px] text-gray-500">Python ajanları ile iletişim kuruluyor...</div>
        </div>
      </div>
      <button 
        onClick={onRetry}
        disabled={isLoading}
        className="px-2 py-1 rounded-lg bg-amber-400/20 text-amber-300 text-[10px] hover:bg-amber-400/30 disabled:opacity-50 flex items-center gap-1"
      >
        <RefreshCw size={10} className={isLoading ? "animate-spin" : ""} />
        {isLoading ? "Bağlanıyor" : "Tekrar Dene"}
      </button>
    </div>
  );
}

export default function IntegrationPanel() {
  const { data, error, isLoading, mutate } = useIntegrationOverview();
  const { data: efomData } = useEfomOverview();
  const [retryCount, setRetryCount] = useState(0);

  // Check for initial load with no data
  const hasNoData = !data && !isLoading;
  const hasPartialData = data && (!data.agents?.length && !data.brain_control);

  const handleRetry = () => {
    setRetryCount(c => c + 1);
    mutate();
  };

  const agents = data?.agents || [];
  const models = data?.models || [];
  const exchanges = data?.exchanges || [];
  const performance = data?.signals?.performance || [];
  const history = data?.brain?.history || [];
  const brainControl = data?.brain_control;
  const topPerformance = performance.slice(0, 5);
  const maxTrades = Math.max(...history.map((item) => toNumber(item.total_trades, 0)), 1);
  const efomTrials = efomData?.optuna?.trials || [];
  const efomPostMortem = efomData?.post_mortem;
  const efomBest = efomData?.optuna?.best_trial;
  const trialBars = efomTrials.slice(-12);
  const maxTrialValue = Math.max(...trialBars.map((item) => toNumber(item.value, 0)), 1);

  return (
    <div className="h-full flex flex-col overflow-hidden bg-[radial-gradient(circle_at_top_left,_rgba(56,189,248,0.12),_transparent_35%),radial-gradient(circle_at_bottom_right,_rgba(251,191,36,0.09),_transparent_30%),linear-gradient(180deg,rgba(15,23,42,0.96),rgba(3,7,18,0.98))]">
      <div className="px-4 py-3 border-b border-surface-border/70 flex items-center justify-between">
        <div>
          <div className="text-xs font-semibold tracking-[0.18em] text-gray-200">ENTEGRASYON MERKEZİ</div>
          <div className="text-[10px] text-gray-500 mt-1">Ajanlar, modeller, borsalar ve beynin canlı çalışma ritmi</div>
        </div>
        <div className="flex items-center gap-2">
          {error && <WifiOff size={12} className="text-amber-400" />}
          <div className="text-[10px] text-cyan-300">{data ? formatTimeOnly(data.generated_at) : isLoading ? "yükleniyor..." : "bağlantı bekleniyor"}</div>
        </div>
      </div>

      <div className="flex-1 overflow-y-auto custom-scrollbar p-3 space-y-3">
        {/* Connection warning banner */}
        {(hasNoData || hasPartialData || error) && (
          <ConnectionWarning isLoading={isLoading} onRetry={handleRetry} />
        )}

        <section className="rounded-2xl border border-white/8 bg-black/20 p-3">
          <div className="flex items-center gap-2 text-[11px] text-gray-300 font-semibold mb-3"><Settings2 size={13} className="text-sky-300" /> Beyin Yonetimi</div>
          <div className="grid grid-cols-2 gap-2 text-[11px] mb-3">
            <div className="rounded-xl border border-white/6 bg-white/[0.03] px-3 py-2">
              <div className="text-gray-500">Karar modeli</div>
              <div className="text-white font-semibold mt-1">{brainControl?.decision_core.model || "bilinmiyor"}</div>
              <div className="text-[10px] text-gray-500 mt-1">Durum {brainControl?.health || "unknown"} • mod {brainControl?.mode || "unknown"}</div>
            </div>
            <div className="rounded-xl border border-white/6 bg-white/[0.03] px-3 py-2">
              <div className="text-gray-500">SuperGemma karar akisi</div>
              <div className="text-emerald-300 font-semibold mt-1">%{toNumber(brainControl?.decision_core.approval_rate).toFixed(1)} onay</div>
              <div className="text-[10px] text-gray-500 mt-1">{toNumber(brainControl?.decision_core.total_requests)} istek • {toNumber(brainControl?.decision_core.avg_latency_ms).toFixed(0)} ms</div>
            </div>
            <div className="rounded-xl border border-white/6 bg-white/[0.03] px-3 py-2 col-span-2">
              <div className="text-gray-500">Master directive</div>
              <div className="text-[11px] text-white mt-1 leading-5">{brainControl?.directive_preview || "Merkezi directive kaydi su an erisilemiyor."}</div>
              <div className="text-[10px] text-gray-500 mt-1">Guncelleme {brainControl?.directive_updated_at ? formatInQuenbotTimeZone(brainControl.directive_updated_at) : "bilinmiyor"}</div>
            </div>
          </div>
          <div className="grid grid-cols-2 gap-2 text-[10px] text-gray-400">
            <div className="rounded-xl border border-white/6 bg-white/[0.03] px-3 py-2">Similarity agirligi <span className="text-cyan-200 ml-1">{toNumber(brainControl?.learning_weights.similarity).toFixed(2)}</span></div>
            <div className="rounded-xl border border-white/6 bg-white/[0.03] px-3 py-2">Volume agirligi <span className="text-cyan-200 ml-1">{toNumber(brainControl?.learning_weights.volume_match).toFixed(2)}</span></div>
            <div className="rounded-xl border border-white/6 bg-white/[0.03] px-3 py-2">Direction agirligi <span className="text-cyan-200 ml-1">{toNumber(brainControl?.learning_weights.direction_match).toFixed(2)}</span></div>
            <div className="rounded-xl border border-white/6 bg-white/[0.03] px-3 py-2">History agirligi <span className="text-cyan-200 ml-1">{toNumber(brainControl?.learning_weights.confidence_history).toFixed(2)}</span></div>
          </div>
        </section>

        <section className="rounded-2xl border border-cyan-400/15 bg-[linear-gradient(135deg,rgba(8,145,178,0.12),rgba(15,23,42,0.42))] p-3">
          <div className="flex items-center gap-2 text-[11px] text-gray-100 font-semibold mb-3"><FlaskConical size={13} className="text-cyan-300" /> EFOM Kartı</div>
          <div className="grid grid-cols-3 gap-2 mb-3 text-[11px]">
            <div className="rounded-xl border border-white/8 bg-black/20 px-3 py-2">
              <div className="text-gray-500">Loglanan trade</div>
              <div className="text-white text-lg font-semibold mt-1">{toNumber(brainControl?.efom.logged_trades)}</div>
            </div>
            <div className="rounded-xl border border-white/8 bg-black/20 px-3 py-2">
              <div className="text-gray-500">Optuna denemesi</div>
              <div className="text-white text-lg font-semibold mt-1">{toNumber(efomData?.optuna?.total_trials || brainControl?.efom.optuna_total_trials)}</div>
            </div>
            <div className="rounded-xl border border-white/8 bg-black/20 px-3 py-2">
              <div className="text-gray-500">En iyi skor</div>
              <div className="text-emerald-300 text-lg font-semibold mt-1">{toNumber(efomBest?.value || brainControl?.efom.optuna_best_value).toFixed(2)}</div>
            </div>
          </div>

          <div className="rounded-xl border border-white/8 bg-black/20 p-3 mb-3">
            <div className="text-[10px] text-gray-500 mb-1">Post-Mortem Özeti</div>
            <div className="text-[11px] text-white leading-5">{efomPostMortem?.summary || brainControl?.efom.latest_report_summary || "EFOM henüz yeterli veri biriktirmedi."}</div>
            <div className="mt-2 text-[10px] text-gray-400">Örneklem: {toNumber(efomPostMortem?.sample_size || brainControl?.efom.latest_report_sample_size)}</div>
            <div className="mt-2 space-y-2">
              {(efomPostMortem?.failure_patterns || brainControl?.efom.failure_patterns || []).slice(0, 3).map((pattern) => (
                <div key={pattern.condition} className="rounded-lg border border-white/6 bg-white/[0.03] px-2.5 py-2">
                  <div className="text-[10px] text-amber-300">{pattern.condition}</div>
                  <div className="text-[10px] text-gray-300 mt-1 leading-4">{pattern.impact}</div>
                </div>
              ))}
            </div>
          </div>

          <div className="rounded-xl border border-white/8 bg-black/20 p-3">
            <div className="flex items-center justify-between text-[10px] text-gray-500 mb-2">
              <span>Optuna Trial İlerlemesi</span>
              <span>{trialBars.length} görünür deneme</span>
            </div>
            {trialBars.length === 0 ? (
              <div className="text-[11px] text-gray-500">Henüz Optuna trial verisi oluşmadı.</div>
            ) : (
              <>
                <div className="flex items-end gap-1.5 h-24">
                  {trialBars.map((trial) => {
                    const height = Math.max(8, Math.round((toNumber(trial.value) / maxTrialValue) * 96));
                    const positive = toNumber(trial.value) >= 0;
                    return (
                      <div key={trial.number} className="flex-1 flex flex-col items-center justify-end gap-1">
                        <div className={`w-full rounded-t-md ${positive ? "bg-[linear-gradient(180deg,#22d3ee,#0ea5e9)]" : "bg-[linear-gradient(180deg,#fda4af,#e11d48)]"}`} style={{ height }} />
                        <div className="text-[9px] text-gray-500">#{trial.number}</div>
                      </div>
                    );
                  })}
                </div>
                <div className="mt-3 grid grid-cols-3 gap-2 text-[10px] text-gray-400">
                  <div className="rounded-lg border border-white/6 bg-white/[0.03] px-2 py-2">Coverage <span className="text-cyan-200 ml-1">{toNumber(efomBest?.coverage).toFixed(2)}</span></div>
                  <div className="rounded-lg border border-white/6 bg-white/[0.03] px-2 py-2">Sharpe <span className="text-cyan-200 ml-1">{toNumber(efomBest?.sharpe).toFixed(2)}</span></div>
                  <div className="rounded-lg border border-white/6 bg-white/[0.03] px-2 py-2">Sortino <span className="text-cyan-200 ml-1">{toNumber(efomBest?.sortino).toFixed(2)}</span></div>
                </div>
              </>
            )}
          </div>
        </section>

        <section className="rounded-2xl border border-white/8 bg-black/20 p-3">
          <div className="flex items-center gap-2 text-[11px] text-gray-300 font-semibold mb-3"><Activity size={13} className="text-cyan-300" /> Ajan Aktivitesi</div>
          <div className="space-y-2">
            {agents.length === 0 ? <EmptyState message="Canlı ajan heartbeat verisi bekleniyor." /> : agents.map((agent) => (
              <div key={agent.name} className="rounded-xl border border-white/6 bg-white/[0.03] px-3 py-2">
                <div className="flex items-center justify-between gap-3">
                  <div>
                    <div className="text-[12px] text-white font-medium">{agent.name.replaceAll("_", " ")}</div>
                    <div className="text-[10px] text-gray-500 mt-0.5">Son heartbeat {toNumber(agent.age_seconds)} sn önce</div>
                  </div>
                  <div className={`text-[10px] px-2 py-1 rounded-full border ${statusTone(agent.status)}`}>{agent.status}</div>
                </div>
                <div className="mt-2 grid grid-cols-2 gap-2 text-[10px] text-gray-400">
                  <div>Aktivite skoru <span className="text-white ml-1">{formatCompact(agent.activity_score)}</span></div>
                  <div>Model <span className="text-cyan-200 ml-1">{agent.metadata?.active_model || agent.metadata?.model || agent.metadata?.llm_model || "-"}</span></div>
                </div>
              </div>
            ))}
          </div>
        </section>

        <section className="rounded-2xl border border-white/8 bg-black/20 p-3">
          <div className="flex items-center gap-2 text-[11px] text-gray-300 font-semibold mb-3"><Cpu size={13} className="text-amber-300" /> Kaynak Kullanımı</div>
          <div className="grid grid-cols-2 gap-2 text-[11px]">
            <div className="rounded-xl bg-white/[0.03] border border-white/6 px-3 py-2">
              <div className="text-gray-500 flex items-center gap-1"><Cpu size={11} /> CPU</div>
              <div className="text-white text-lg font-semibold mt-1">%{toNumber(data?.resources.cpu_percent).toFixed(1)}</div>
            </div>
            <div className="rounded-xl bg-white/[0.03] border border-white/6 px-3 py-2">
              <div className="text-gray-500 flex items-center gap-1"><Database size={11} /> RAM</div>
              <div className="text-white text-lg font-semibold mt-1">%{toNumber(data?.resources.ram_percent).toFixed(1)}</div>
              <div className="text-[10px] text-gray-500 mt-0.5">{toNumber(data?.resources.ram_used_mb).toFixed(0)} MB</div>
            </div>
            <div className="rounded-xl bg-white/[0.03] border border-white/6 px-3 py-2 col-span-2">
              <div className="text-gray-500 flex items-center gap-1"><HardDrive size={11} /> Process RSS / Disk / Load</div>
              <div className="mt-1 text-white">{toNumber(data?.resources.process_rss_mb).toFixed(0)} MB • Disk %{toNumber(data?.resources.disk_percent).toFixed(1)} • Load {data?.resources.load_avg?.map((value) => toNumber(value).toFixed(2)).join(" / ")}</div>
            </div>
          </div>
        </section>

        <section className="rounded-2xl border border-white/8 bg-black/20 p-3">
          <div className="flex items-center gap-2 text-[11px] text-gray-300 font-semibold mb-3"><Network size={13} className="text-violet-300" /> Borsa Beslemeleri</div>
          <div className="grid grid-cols-1 gap-2">
            {exchanges.length === 0 ? <EmptyState message="Borsa besleme heartbeat verisi bekleniyor." /> : exchanges.map((feed) => (
              <div key={`${feed.exchange}-${feed.market_type}`} className="rounded-xl border border-white/6 bg-white/[0.03] px-3 py-2 flex items-center justify-between gap-3">
                <div>
                  <div className="text-white text-[12px] uppercase">{feed.exchange} {feed.market_type}</div>
                  <div className="text-[10px] text-gray-500 mt-0.5">5dk: {toNumber(feed.trades_5m)} trade • 1sa: {toNumber(feed.trades_1h)} trade</div>
                </div>
                <div className={`text-[10px] px-2 py-1 rounded-full border ${toNumber(feed.age_seconds) <= 120 ? "text-emerald-300 border-emerald-400/25 bg-emerald-400/10" : "text-amber-300 border-amber-400/25 bg-amber-400/10"}`}>
                  {toNumber(feed.age_seconds)} sn gecikme
                </div>
              </div>
            ))}
          </div>
        </section>

        <section className="rounded-2xl border border-white/8 bg-black/20 p-3">
          <div className="flex items-center gap-2 text-[11px] text-gray-300 font-semibold mb-3"><Trophy size={13} className="text-emerald-300" /> Sinyal Kalitesi</div>
          <div className="space-y-2">
            {topPerformance.length === 0 ? <EmptyState message="Strateji onaylı sinyal performansı oluştuğunda burada gösterilecek." /> : topPerformance.map((item) => (
              <div key={`${item.source}-${item.source_model}`} className="rounded-xl border border-white/6 bg-white/[0.03] px-3 py-2">
                <div className="flex items-center justify-between gap-3">
                  <div>
                    <div className="text-[12px] text-white">{item.source.replaceAll("_", " ")}</div>
                    <div className="text-[10px] text-gray-500 mt-0.5">{item.source_model}</div>
                  </div>
                  <div className="text-right">
                    <div className="text-[11px] text-emerald-300">%{toNumber(item.win_rate).toFixed(1)} başarı</div>
                    <div className="text-[10px] text-gray-500">{toNumber(item.total_signals)} sinyal</div>
                  </div>
                </div>
                <div className="mt-2 text-[10px] text-gray-400">Aktif {toNumber(item.active_signals)} • Kapalı sim {toNumber(item.closed_simulations)} • Ort PnL {toNumber(item.avg_pnl_pct).toFixed(2)}%</div>
              </div>
            ))}
          </div>
        </section>

        <section className="rounded-2xl border border-white/8 bg-black/20 p-3">
          <div className="flex items-center gap-2 text-[11px] text-gray-300 font-semibold mb-3"><BrainCircuit size={13} className="text-fuchsia-300" /> Beyin Gelişimi Simülasyonu</div>
          <div className="grid grid-cols-3 gap-2 mb-3 text-[11px]">
            <div className="rounded-xl border border-white/6 bg-white/[0.03] px-3 py-2">
              <div className="text-gray-500">Toplam Öğrenme</div>
              <div className="text-white text-lg font-semibold mt-1">{toNumber(data?.brain.total)}</div>
            </div>
            <div className="rounded-xl border border-white/6 bg-white/[0.03] px-3 py-2">
              <div className="text-gray-500">Doğruluk</div>
              <div className="text-emerald-300 text-lg font-semibold mt-1">%{toNumber(data?.brain.accuracy).toFixed(1)}</div>
            </div>
            <div className="rounded-xl border border-white/6 bg-white/[0.03] px-3 py-2">
              <div className="text-gray-500">Ort PnL</div>
              <div className={`text-lg font-semibold mt-1 ${toNumber(data?.brain.avg_pnl) >= 0 ? "text-emerald-300" : "text-rose-300"}`}>{toNumber(data?.brain.avg_pnl).toFixed(2)}%</div>
            </div>
          </div>

          <div className="grid grid-cols-2 gap-2 mb-3 text-[10px] text-gray-400">
            <div className="rounded-xl border border-white/6 bg-white/[0.03] px-3 py-2">EFOM trade log <span className="text-white ml-1">{toNumber(brainControl?.efom.logged_trades)}</span></div>
            <div className="rounded-xl border border-white/6 bg-white/[0.03] px-3 py-2">EFOM optimization <span className="text-white ml-1">{toNumber(brainControl?.efom.optimizations_run)}</span></div>
          </div>

          <div className="rounded-xl border border-white/6 bg-[linear-gradient(180deg,rgba(8,47,73,0.2),rgba(17,24,39,0.3))] p-3">
            {history.length === 0 ? <EmptyState message="Beyin durum geçmişi henüz dolmadı." /> : (
              <>
                <div className="flex items-end gap-1.5 h-24">
                  {history.slice(-24).map((point) => {
                    const height = Math.max(8, Math.round((toNumber(point.total_trades) / maxTrades) * 96));
                    const pnlPositive = toNumber(point.daily_pnl) >= 0;
                    return (
                      <div key={point.timestamp} className="flex-1 flex flex-col items-center justify-end gap-1">
                        <div
                          className={`w-full rounded-t-md ${pnlPositive ? "bg-[linear-gradient(180deg,#34d399,#059669)]" : "bg-[linear-gradient(180deg,#fb7185,#e11d48)]"}`}
                          style={{ height }}
                        />
                      </div>
                    );
                  })}
                </div>
                <div className="mt-2 flex items-center justify-between text-[10px] text-gray-500">
                  <span>Son 24 durum örneği</span>
                  <span>İşlem yoğunluğu + günlük PnL eğilimi</span>
                </div>
              </>
            )}
          </div>
        </section>

        <section className="rounded-2xl border border-white/8 bg-black/20 p-3">
          <div className="flex items-center gap-2 text-[11px] text-gray-300 font-semibold mb-3"><Radar size={13} className="text-sky-300" /> Model Dağılımı</div>
          <div className="space-y-2">
            {models.length === 0 ? <EmptyState message="Model aktivitesi verisi bekleniyor." /> : models.slice(0, 6).map((model) => (
              <div key={model.name} className="rounded-xl border border-white/6 bg-white/[0.03] px-3 py-2 flex items-center justify-between gap-3">
                <div>
                  <div className="text-[12px] text-white">{model.name}</div>
                  <div className="text-[10px] text-gray-500 mt-0.5">Sahip {model.owner}</div>
                </div>
                  <div className="text-[11px] text-cyan-300">{formatCompact(model.activity)} aktivite</div>
              </div>
            ))}
          </div>
        </section>
      </div>
    </div>
  );
}