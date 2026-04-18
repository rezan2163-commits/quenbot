# PR3 Report — Phase 6 Oracle Stack · §12 Runtime Supervisor + Operations Hardening

## Scope
PR2 üstüne (base: `feat/oracle-brain`). Oracle Stack'ın üretim sertleştirmesi: runtime supervisor, heartbeat/watchdog, systemd unit, graceful shutdown, ops manual. Tüm flag'ler **default-OFF**; davranış nötr.

## Değişiklikler

### Yeni Python modülü
- `python_agents/runtime_supervisor.py` — bileşen health aggregator, heartbeat dosyası yazar, 3+ ardışık başarısızlıkta restart-callback tetikler (cap'li).

### main.py wiring (append-only)
- §12 bloğu: `Config.RUNTIME_SUPERVISOR_ENABLED` açıksa singleton + bileşen kayıtları (feature_store, confluence, decision_router, safety_net, oracle_signal_bus, factor_graph, oracle_brain + her detector).
- **Graceful shutdown**: `main()`'de SIGINT/SIGTERM için `loop.add_signal_handler` kaydı → `orchestrator.running = False`. Windows gibi platformlarda sessizce skip.
- `GET /api/runtime/status` endpoint eklendi.

### Dashboard
- `lib/intel.ts` — `useRuntimeStatus` + `RuntimeStatusResponse` tipi.
- `IntelPanel.tsx` — OracleView üstünde supervisor mini-status kartı (running/components/cycles/failures/restarts + last tick).

### Scripts
- `scripts/watchdog.sh` — external heartbeat watchdog (observe-only default; `QB_WATCHDOG_RESTART=1` aktif restart).
- `scripts/quenbot.service` — systemd unit template (opsiyonel env satırları tüm Phase 6 flag'leri için).
- `scripts/install_systemd.sh` — idempotent installer.

### Test script
- `TEST_INTEL_UPGRADE.sh` — **PHASE 6** bölümü eklendi (bus + 8 detector + factor_graph + rag + brain + supervisor testlerini smoke olarak çalıştırır).

### Ops doc
- `ORACLE_OPERATIONS_MANUAL.md` — env flag referansı, production'a açma sırası, endpoint listesi, systemd kurulumu, troubleshooting, kapatma prosedürü, doğrulama checklist.

### Testler
- `tests/test_runtime_supervisor.py` — **8 test**: singleton, tick writes status+heartbeat, restart callback, attempts cap, healthy clears failure counter, start/stop cancellation, metrics şekli, none-getter.

## Pytest
```
161 passed, 10 warnings in 3.49s
```
PR1+PR2 153 baseline + 8 yeni = **161/161 green**.

## TypeScript
`dashboard/npx tsc --noEmit` → exit 0.

## Güvenlik
- Supervisor restart-callback opsiyonel; default `None` → observe-only.
- Heartbeat dosyası opsiyonel (`WATCHDOG_ENABLED`); yazım hatası log'a düşer, crash etmez.
- Signal handler'lar platform-tolerant.
- systemd `Restart=on-failure` + `WatchdogSec=150` ile sert hata durumunda otomatik restart.

## Env flags (default OFF)
```
QUENBOT_RUNTIME_SUPERVISOR_ENABLED=0
QUENBOT_RUNTIME_HEALTH_CHECK_INTERVAL_SEC=30
QUENBOT_RUNTIME_MAX_RESTART_ATTEMPTS=3
QUENBOT_WATCHDOG_ENABLED=0
QUENBOT_WATCHDOG_TIMEOUT_SEC=120
```

## Phase 6 Stack — tamamlandı
| PR | Scope | Tests |
|---|---|---|
| #6 (merged-ready) | §1–§9 detectors + signal bus + dashboard | 128 |
| #7 | §10 factor graph + §11 brain + RAG + migration | 25 new → 153 |
| #8 (bu) | §12 supervisor + watchdog + systemd + ops | 8 new → **161** |

Tüm Oracle Stack artık üretime alınmaya hazır (flag'ler default-OFF). Operatör rehberi `ORACLE_OPERATIONS_MANUAL.md` içinde.
