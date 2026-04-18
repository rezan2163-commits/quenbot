# Intel Upgrade — Phase 5 Report (Safety Net + Decision Router Shadow)

## Overview
Phase 5, Intel upgrade'in devre kesicisidir. SafetyNet 24 saatlik Brier/hitrate
sapmasını, confluence drift'ini ve feature-store sağlığını izler. Eşikler
aşılırsa otomatik `trip()` çalışır: `FAST_BRAIN_ENABLED=False`, sentinel dosyası
yazılır (restart'ta korunur), online_learning ağırlık rotasyonu donar.

Decision Router Shadow: `DECISION_ROUTER_SHADOW=1` ile çalışır; karar emitlemez,
sadece gölge metriklerini loglar. `DECISION_ROUTER_ENABLED` yalnız shadow +
safety_net yeşil iken 1'e çekilmelidir.

## Deliverables
| Module | File | Flag |
| --- | --- | --- |
| Safety Net | `python_agents/safety_net.py` | `SAFETY_NET_ENABLED` |
| API — safety_net status | `GET /api/intel/safety_net` | (route) |
| API — counterfactual metrics | `GET /api/intel/counterfactuals` | (route) |
| intel/summary extension | `GET /api/intel/summary` | `safety_net` key |

## Event Types Added
- `SAFETY_NET_TRIPPED`
- `SAFETY_NET_RESET`
- `SAFETY_NET_DRIFT_ALERT`
- `SAFETY_NET_FS_DEGRADED`

## Config Flags
| Flag | Default | Purpose |
| --- | --- | --- |
| `SAFETY_NET_ENABLED` | `False` | Master switch |
| `SAFETY_NET_BRIER_TOL` | `1.25` | Brier baseline × tol |
| `SAFETY_NET_HITRATE_TOL` | `0.80` | Hitrate baseline × tol |
| `SAFETY_NET_DEGRADATION_WINDOW_MIN` | `120` | Sustained degradation duration |
| `SAFETY_NET_CONFLUENCE_DRIFT_SIGMA` | `3.0` | Per-symbol z-score threshold |
| `SAFETY_NET_FS_FAILURE_TOL` | `0.05` | Feature-store failure ratio |
| `SAFETY_NET_BASELINE_PATH` | `.safety_net_baseline.json` | Bootstrap cache |
| `SAFETY_NET_TRIP_SENTINEL` | `.safety_net_trip.json` | Restart-persistent trip flag |
| `SAFETY_NET_BG_INTERVAL_SEC` | `30` | Watchdog tick interval |

## Trip Scenarios (unit tested)
1. **Accuracy degradation** — Brier ≥ baseline × 1.25 **veya** hitrate < baseline × 0.80 sustained ≥ `DEGRADATION_WINDOW_MIN` → `trip("accuracy_degraded")`.
2. **Confluence drift** — ≥50% tracked symbols at |z| ≥ 3.0 for ≥30 min → `SAFETY_NET_DRIFT_ALERT` + `trip("confluence_drift")`.
3. **Feature-store degraded** — failure_ratio > 0.05 veya queue > 80% sustained 10 dk → `SAFETY_NET_FS_DEGRADED` (warn only, no trip).

## Sentinel Behavior
- `trip()` → `.safety_net_trip.json` yazılır (reason + trip_ts + baseline).
- Reboot → `_load_sentinel()` okur → `Config.FAST_BRAIN_ENABLED=False` (otomatik).
- `reset(operator, note)` → sentinel silinir, state temizlenir.

## Testing
`tests/test_safety_net.py` — 6 test: trip/reset/sentinel, rehydration disables
FastBrain, accuracy trip, drift detection, rolling Brier/hitrate, singleton.

## Status
✅ Kod + API + testler hazır. `SAFETY_NET_ENABLED=False` (default). Rampa için FINALIZATION_REPORT.md'ye bakınız.
