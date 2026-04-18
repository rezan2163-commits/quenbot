# PR1 — Phase 6 Oracle Stack Detectors

## Kapsam

PR1, Faz 6 Oracle Stack'in **yalnızca dedektör ve sinyal veriyolu katmanını** ekler. Karar mekanizmalarına hiçbir dokunuş yoktur; tüm yeni modüller default **OFF** flag'leri arkasında pasif biçimde bekler. Mevcut davranış %100 korunur.

## Değişen / Eklenen Modüller

### Yeni modüller (round-1)
- `oracle_signal_bus.py` — §9 Kayıt + publish/read/metadata API'si
- `bocpd_detector.py` — §1 Adams-MacKay 2007 multi-stream BOCPD (7 akım, Student-t conjugate)

### Yeni modüller (round-2)
- `wasserstein_drift.py` — §4 W2 (quantile L2) dağılım kayması, z-score kanalı
- `lob_thermodynamics.py` — §3 Shannon entropy + entropy production rate + JS divergence
- `hawkes_kernel_fitter.py` — §2 Multivariate exp-kernel Hawkes EM (5 mark tipi)
- `path_signature_engine.py` — §5 Lyons rough-path signatures (iisignature ops., depth-2 fallback) + Chroma similarity
- `mirror_flow_analyzer.py` — §6 Pure-python Sakoe-Chiba DTW, Binance↔Bybit senkron akış
- `topological_lob_analyzer.py` — §7 Persistent homology (ripser/gudhi ops., union-find fallback)
- `onchain_client.py` — §8 aiohttp on-chain poller (backoff, key yoksa disabled)
- `causal_onchain_bridge.py` — §8 CCM (E=3, τ=1) nedensel yönlendirme skoru
- `scripts/build_signature_library.py` — §5 scaffold (PR2'de doldurulacak)

### Dokunulan modüller
- `event_bus.py` — 11 yeni EventType **APPEND-only**
- `config.py` — 60+ yeni Phase 6 flag (ORACLE_BUS_ENABLED dışı tamamı OFF)
- `main.py::_bootstrap_intel_upgrade` — Faz 6 bloğu + §1-§8 koşullu init (try/except ile izole)
- `.gitignore` — Phase 6 artifact yolları

## Dedektör sözleşmesi (tümü için ortak)

Her dedektör şu skeleton'u uygular:
- `async initialize()` — idempotent, bağımlılık yüklemeyi try/except ile sarar
- `observe(...)` — non-blocking, NaN/boş input sessiz reddedilir
- `maybe_publish(symbol, ts?)` — throttle'lı sonuç yayını; signal_bus + event_bus + feature_store best-effort
- `snapshot(symbol) / all_snapshots() / oracle_channel_value(symbol)` — okuma API'si
- `async health_check() / metrics()` — ops görünürlüğü
- `get_<name>(...)` — DI-friendly singleton; `_reset_for_tests()` test izolasyonu için

## Oracle kanalları

| # | Kanal adı | Kaynak | Aralık |
|---|-----------|--------|--------|
| 1 | `bocpd_consensus` | bocpd_detector | [0,1] |
| 2 | `hawkes_branching_ratio` | hawkes_kernel_fitter | [-1,+1] |
| 3 | `entropy_cooling` | lob_thermodynamics | [0,1] |
| 4 | `wasserstein_drift_zscore` | wasserstein_drift | [-1,+1] |
| 5 | `path_signature_similarity` | path_signature_engine | [0,1] |
| 6 | `mirror_execution_strength` | mirror_flow_analyzer | [0,1] |
| 7 | `topological_whale_birth` | topological_lob_analyzer | [0,1] |
| 8 | `onchain_lead_strength` | causal_onchain_bridge | [-1,+1] |

## Test sonuçları

```
128 passed, 10 warnings in ~2.0s
```

Yeni testler (round-2, 38 adet):
- `test_wasserstein.py` (5)
- `test_lob_thermodynamics.py` (5)
- `test_hawkes.py` (6)
- `test_path_signatures.py` (5)
- `test_mirror_flow.py` (6)
- `test_topology.py` (5)
- `test_ccm.py` (6)

Round-1'den taşınan (16 adet):
- `test_oracle_signal_bus.py` (9)
- `test_bocpd.py` (7)

Toplam regresyon: **128/128 yeşil** (baseline 90 + 38 yeni round-2). Mevcut 90 testin hiçbiri değişmedi.

## Hard constraints doğrulaması

- ✅ EventType enum **APPEND-only**; mevcut değerler korundu
- ✅ Tüm yeni flag'ler default OFF (yalnız `ORACLE_BUS_ENABLED=1`)
- ✅ Opsiyonel deps (`numpy`, `iisignature`, `gudhi`, `ripser`, `chromadb`, `aiohttp`) try/except + fallback
- ✅ Modül dosyaları <600 satır
- ✅ Türkçe docstring başlığı her modülde mevcut
- ✅ Conventional commits: `feat(oracle):`, `test(oracle):`
- ✅ Mevcut karar yollarına (strategy, risk_manager, code_operator) dokunulmadı

## Açılma prosedürü (ops)

Dedektörleri canlıya almak için ilgili env var'ı `1` yap:
```
export QUENBOT_BOCPD_ENABLED=1
export QUENBOT_HAWKES_ENABLED=1
export QUENBOT_LOB_THERMO_ENABLED=1
export QUENBOT_WASSERSTEIN_ENABLED=1
export QUENBOT_PATH_SIGNATURE_ENABLED=1
export QUENBOT_MIRROR_FLOW_ENABLED=1
export QUENBOT_TDA_ENABLED=1
export QUENBOT_ONCHAIN_ENABLED=1
```

Hepsi bağımsızdır; biri bootstrap'ta fail ederse diğerleri etkilenmez (try/except ile izole).

## Devamı

- **PR2** — §10 Factor graph fusion + §11 Qwen Oracle brain (karar katmanı; hâlâ shadow-mode default).
- **PR3** — §12-§15 Supervisor/watchdog + systemd + dashboard + API + ops manual.
