# Intel Upgrade — Finalization Report

**Tek PR başlığı**: `feat(intel): Finalization — safety net + counterfactual DB + historical backfill + E2E tests`

**Kontrat**: Tüm değişiklikler **additive** ve **feature-flagged**. Default OFF.
Mevcut hot-path davranışı değişmez; rollback = tüm yeni flag'leri 0'a çek.

---

## 1. Gap Closure Özeti

| GAP | Durum | Delivery |
| --- | --- | --- |
| 1 — Safety Net | ✅ | `python_agents/safety_net.py`, 4 event, `/api/intel/safety_net`, sentinel rehydration. |
| 2 — Counterfactual DB | ✅ | SQL migration + Drizzle table + 4 DB method + `online_learning.persist_to_db`. |
| 3 — Historical backfill | ✅ | `backfill_features_from_trades.py`, `scripts/backfill_counterfactuals.py`, `scripts/promote_confluence_weights.py`. |
| 4 — E2E test harness | ✅ | `TEST_INTEL_UPGRADE.sh` (3 faz), `main.py --dry-run / --exit-after-seconds`. |
| 5 — Tests ≥45 | ✅ | **70 passed, 1 skipped** (`python -m pytest python_agents/tests/ -q`). |
| 6 — Reports | ✅ | PHASE_3/4/5, SHADOW, FINALIZATION. |

---

## 2. Yeni Event Types (additive)

```
FAST_BRAIN_PREDICTION          (Phase 3)
CONFLUENCE_SCORE               (Phase 3)
MULTI_HORIZON_SIGNATURE        (Phase 3)
COUNTERFACTUAL_UPDATE          (Phase 4)
CONFLUENCE_WEIGHTS_ROTATED     (Phase 4)
SAFETY_NET_TRIPPED             (Phase 5)
SAFETY_NET_RESET               (Phase 5)
SAFETY_NET_DRIFT_ALERT         (Phase 5)
SAFETY_NET_FS_DEGRADED         (Phase 5)
```
Hiçbir mevcut üye yeniden adlandırılmadı veya sıralanmadı.

---

## 3. Yeni DB Migration

`lib/db/src/migrations/001_counterfactual_observations.sql`
- Idempotent (`CREATE TABLE IF NOT EXISTS`).
- 4 indeks (symbol+ts, label+ts, horizon, decided).
- Drizzle ORM karşılığı `lib/db/src/schema.ts` içinde `counterfactualObservations`.

Uygulama: `Database().create_counterfactual_table()` — `main.py` bootstrap'ta
otomatik çağrılır (flag: `ONLINE_LEARNING_PERSIST_DB=1`).

---

## 4. Ramp Plan (Production)

Sırayla çıkar, her adımda 24 saat gözlemle:

1. **SAFETY_NET_ENABLED=1** — watchdog aktif, hiçbir karar değişmez; sentinel'i gör.
2. **ONLINE_LEARNING_ENABLED=1 + ONLINE_LEARNING_PERSIST_DB=1** — counterfactual tablosu dolmaya başlar.
3. **Backfill**: `python python_agents/scripts/backfill_counterfactuals.py --days 30 --dry-run` → stats incele → `--days 30` (write).
4. **FAST_BRAIN_ENABLED=1** — yalnız shadow.
5. **CONFLUENCE_ENABLED=1** — skor yayını.
6. **DECISION_ROUTER_SHADOW=1** — gölge karar; 72 saat log gözlemle.
7. **promote_confluence_weights.py** → promote onayı varsa `DECISION_ROUTER_ENABLED=1`.

Her adımda `/api/intel/safety_net` ve `/api/intel/summary` yeşil olmalı.

---

## 5. Rollback

```
# Tüm intel katmanlarını kapat (geri dönüş = Phase 2 davranışı):
unset QUENBOT_FAST_BRAIN_ENABLED
unset QUENBOT_CONFLUENCE_ENABLED
unset QUENBOT_ONLINE_LEARNING_ENABLED
unset QUENBOT_DECISION_ROUTER_ENABLED
unset QUENBOT_DECISION_ROUTER_SHADOW
unset QUENBOT_SAFETY_NET_ENABLED
# Veya spesifik tek flag'i kapat: QUENBOT_FAST_BRAIN_ENABLED=0 gibi.

# Manuel acil durum:
# safety_net.trip("manual_rollback")  → sentinel yazılır, restart'ta FAST_BRAIN OFF kalır.
```

Tablolar silinmez; sadece yeni yazımlar durur.

---

## 6. Test Kanıtları

```
$ python -m pytest python_agents/tests/ -q
70 passed, 1 skipped in 3.15s

$ bash TEST_INTEL_UPGRADE.sh
✅ PHASE 1 OK (ALL_OFF)
✅ PHASE 2 OK (ALL_ON dry-run)
✅ PHASE 3 OK (TRIP_SIMULATION)
🟢 INTEL UPGRADE FINALIZATION: TUM FAZLAR YESIL
```

---

## 7. Dosyalar

**Yeni**:
- `python_agents/safety_net.py`
- `python_agents/backfill_features_from_trades.py`
- `python_agents/scripts/backfill_counterfactuals.py`
- `python_agents/scripts/promote_confluence_weights.py`
- `python_agents/tests/test_safety_net.py`
- `python_agents/tests/test_multi_horizon_signatures.py`
- `python_agents/tests/test_counterfactual_backfill.py`
- `lib/db/src/migrations/001_counterfactual_observations.sql`
- `TEST_INTEL_UPGRADE.sh`
- `PHASE_4_REPORT.md`, `PHASE_5_REPORT.md`, `SHADOW_REPORT.md`, `FINALIZATION_REPORT.md`

**Değiştirildi (additive)**:
- `lib/db/src/schema.ts` — `counterfactualObservations` tablosu eklendi.
- `python_agents/database.py` — 4 yeni metod sona append edildi.
- `python_agents/config.py` — 10 yeni flag (tümü default OFF, `ONLINE_LEARNING_PERSIST_DB` default 1).
- `python_agents/event_bus.py` — 4 yeni EventType üyesi.
- `python_agents/online_learning.py` — DB persist + `weights_frozen` + `recompute_from_db`.
- `python_agents/main.py` — bootstrap wiring, 2 yeni aiohttp route, intel/summary genişletmesi, `--dry-run`/`--exit-after-seconds` CLI.
- `python_agents/tests/test_backward_compat.py` — 3 yeni additive test.

---

**Durum**: ✅ PR'a hazır.
