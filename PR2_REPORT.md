# PR2 Report — Phase 6 Oracle Stack · §10 Factor Graph + §11 Oracle Brain

## Scope
Bu PR PR1 üstüne eklenir (base: `feat/oracle-detectors`). Tüm eklentiler **default-OFF** ve **shadow-mode**; mevcut karar ağına (strategist / risk_manager / router) dokunulmaz.

## Değişiklikler

### Yeni Python modülleri (5)
- `python_agents/qwen_oracle_schemas.py` — `OracleObservation`, `OracleDirective`, `ReasoningTrace` dataclass'ları (pydantic-free).
- `python_agents/factor_graph_fusion.py` — §10: 12 kanal üzerinde loopy belief propagation tarzı damping'li log-odds füzyonu. Çıktı: **IFI ∈ [0,1]** + direction ∈ [−1,+1] + per-channel marginals. Oracle kanal adı: `invisible_footprint_index`.
- `python_agents/qwen_oracle_rag.py` — §11 reasoning trace RAG katmanı. ChromaDB varsa `oracle_reasoning` koleksiyonunu kullanır; yoksa in-memory ring fallback.
- `python_agents/qwen_oracle_brain.py` — §11 merkezi orkestrasyon beyni (shadow default). 5-kuralı heuristic cascade + opsiyonel LLM teach çağrısı + learn/teach/daily döngüleri.
- `python_agents/scripts/self_play_scaffold.py` — PR3 için placeholder.

### Yeni SQL migration
- `python_agents/migrations/002_oracle_tables.sql` — `oracle_directives`, `oracle_reasoning_traces`, `oracle_channel_weights` tabloları (`IF NOT EXISTS`, idempotent).

### main.py wiring (APPEND-only)
- §10 bloğu: `Config.FACTOR_GRAPH_ENABLED` açıksa singleton oluşturulur, signal_bus'a kanal kaydı yapılır, watchlist için periyodik publisher loop başlatılır.
- §11 bloğu: `Config.ORACLE_BRAIN_ENABLED` açıksa brain + RAG oluşturulur, `safety_net` bağlanır, `brain.start()` çağrılır.
- API endpoints:
  - `GET /api/oracle/factor-graph` (tüm semboller) · `GET /api/oracle/factor-graph/{symbol}`
  - `GET /api/oracle/brain/directives`
  - `GET /api/oracle/brain/traces?limit=N`
  - `GET /api/oracle/brain/health`
- `/api/oracle/summary` ve `/api/intel/summary` cevabı `factor_graph` + `brain` bloklarıyla genişletildi.

### Dashboard (Intel Panel · Oracle tab)
- `dashboard/src/lib/intel.ts` — yeni hook'lar: `useOracleFactorGraph`, `useOracleBrainDirectives`, `useOracleBrainTraces`, `useOracleBrainHealth` + tip tanımları.
- `dashboard/src/components/IntelPanel.tsx` — OracleView'e şu kartlar eklendi:
  - **Factor Graph — IFI gauge**: IFI, direction, kanal sayısı + renkli bar.
  - **Oracle Brain Direktifleri**: sembol bazında son direktif (action + severity rozeti + rationale + confidence + ttl).
  - **Son Reasoning Trace'ler**: son 6 trace (shadow marker ile).

### Yeni testler (3 dosya, 25 test)
- `tests/test_factor_graph.py` (8 test) — singleton, boş/ dolu fuse, bullish/bearish direction, publish throttle + signal_bus round-trip, update_weights, metrics şekli.
- `tests/test_oracle_rag.py` (5 test) — singleton, add_trace stats, symbol filter, top_k limit, stats şekli.
- `tests/test_oracle_brain.py` (12 test) — singleton/shadow default, 5 heuristic kural (critical/high/medium×2/low/monitor), tick emits direktif+trace, safety_tripped no-op, set_symbols, health/metrics, start/stop cancellation.

## Pytest
```
153 passed, 10 warnings in 2.76s
```
PR1 128 baseline + 25 yeni = **153/153 green**.

## TypeScript
Dashboard `npx tsc --noEmit` temiz çıkış kodu 0.

## Matematik notu — §10 fusion

Her kanal $c$'nin gözlemi $v_c \in [-1,+1]$, ağırlığı $w_c$, polaritesi $\rho_c \in \{-1,0,+1\}$.

**Intensity log-odds**:
$$ \ell_c = \mathrm{logit}\bigl(\max(\epsilon, \min(1-\epsilon, |v_c|))\bigr) $$

İteratif damping güncellemesi ($\gamma$ = `FG_DAMPING`):
$$
b^{(t+1)} = \gamma\, b^{(t)} + (1-\gamma)\, \frac{\sum_c w_c \ell_c}{\sum_c w_c}
$$
$$
d^{(t+1)} = \gamma\, d^{(t)} + (1-\gamma)\, \frac{\sum_c w_c \rho_c v_c}{\sum_c w_c}
$$

$$ \mathrm{IFI} = \sigma(b^{(T)}) \in [0,1],\qquad \mathrm{direction} = \mathrm{clip}(d^{(T)}, -1, +1) $$

## Heuristic cascade (§11)
Öncelik sırası:
1. **Critical** — `topology ≥ 0.8` ∧ `mirror_flow ≥ 0.8` → `HOLD_OFF`
2. **High** — `IFI ≥ 0.75` ∧ `|direction| ≥ 0.5` → `BIAS_DIRECTION` (long/short)
3. **Medium** — `entropy_cooling ≥ 0.7` → `TIGHTEN_STOPS` (scale 0.7)
4. **Medium** — `|wasserstein_z| ≥ 0.7` → `ADJUST_RISK` (kelly_scale 0.5)
5. **Low** — `IFI ≥ 0.5` ∧ `|direction| ≥ 0.2` → mild `BIAS_DIRECTION`
6. **Default** — `MONITOR`

`confidence = clip(IFI × (0.6 + 0.4·|direction|), 0, 1)`.

## Güvenlik
- `safety_net.tripped` ise `_tick_symbol` no-op döner.
- `shadow=True` (default): LLM çağrıları try/except; direktifler sadece log + event (`ORACLE_DIRECTIVE_ISSUED`), karar ağına uygulanmaz.
- Tüm flag'ler env-based ve varsayılan `0`.

## Env flags (default OFF)
```
QUENBOT_FACTOR_GRAPH_ENABLED=0
QUENBOT_FG_BP_ITER=100
QUENBOT_FG_DAMPING=0.5
QUENBOT_FG_PUBLISH_HZ=0.5
QUENBOT_ORACLE_BRAIN_ENABLED=0
QUENBOT_ORACLE_BRAIN_SHADOW=1
QUENBOT_ORACLE_BRAIN_LEARN_INTERVAL_MIN=10
QUENBOT_ORACLE_BRAIN_TEACH_INTERVAL_MIN=60
QUENBOT_ORACLE_BRAIN_DAILY_REPORT_HOUR=3
QUENBOT_ORACLE_BRAIN_RAG_TOP_K=5
```

## Devam planı (PR3)
- `§12` runtime_supervisor + watchdog + systemd unit
- Graceful shutdown + signal handlers
- `TEST_INTEL_UPGRADE.sh` Phase 6 section
- `ORACLE_OPERATIONS_MANUAL.md`
