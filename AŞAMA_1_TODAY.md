# AŞAMA 1 — Low-Dose Active Mode: Operator Activation Checklist

Bu kontrol listesi QuenBot Oracle'ı Shadow Mode'dan **Low-Dose Active Mode**'a
geçirmek için izlenmesi gereken adımları sıralar. Tüm değişiklikler feature
flag arkasındadır; flag'ler `False` iken davranış Shadow Mode ile **byte-identical**'dir.

---

## 0. Ön koşul

- Branch merge edildi, Aşama 1 PR yeşil (184 test geçiyor, 0 regression).
- `python_agents/`, `artifacts/api-server/`, `lib/db/` güncel.
- Postgres 14+ erişilebilir (`DATABASE_URL` set).

---

## 1. DB Migration (additive, geri alınabilir)

```bash
cd /workspaces/quenbot/lib/db
psql "$DATABASE_URL" -f src/migrations/003_historical_warmup.sql
```

Doğrulama:

```sql
\d+ counterfactual_observations
-- historical_impact_simulation JSONB ve warmup_generated BOOLEAN görünmeli.
```

Rollback gerekirse:

```sql
ALTER TABLE counterfactual_observations
  DROP COLUMN IF EXISTS historical_impact_simulation,
  DROP COLUMN IF EXISTS warmup_generated;
```

---

## 2. Historical Warmup (dry-run → live)

### 2a. Dry-run (yazma yok)

```bash
cd /workspaces/quenbot/python_agents
python scripts/warmup_from_history.py \
  --days 30 \
  --symbols BTCUSDT,ETHUSDT,SOLUSDT \
  --rag-limit 1000 \
  --dry-run
```

Çıktı: `.warmup_reports/warmup_report_<stamp>.md`. Rapor'da şunlar bulunmalı:

- Kanal başına trust-score (Dirichlet α_TP / α_FP / α_FN / α_TN).
- Brier baseline (30. persentil, ≥100 numune) + hitrate.
- Seeded RAG girişi sayısı (`source=warmup_synthetic`).
- `warmup_generated=TRUE` olarak işaretlenecek satır sayısı.

### 2b. Live run

```bash
python scripts/warmup_from_history.py \
  --days 30 \
  --symbols BTCUSDT,ETHUSDT,SOLUSDT \
  --rag-limit 1000
```

Doğrulama:

- `python_agents/.brain_safety_baseline.json` güncel; `brier` ve `hitrate` alanları dolu.
- `python_agents/.warmup_checkpoint.json` oluştu; `finished_at` set.
- RAG koleksiyonunda `metadata.source == "warmup_synthetic"` olan kayıtlar var.
- Event bus'ta `oracle.warmup_completed` event'i görüldü.

---

## 3. DirectiveGatekeeper aktivasyonu

`python_agents/config.py` (veya env ile override):

```env
DIRECTIVE_GATEKEEPER_ENABLED=true
ORACLE_BRAIN_DIRECTIVE_CONFIDENCE_MIN=0.80
ORACLE_BRAIN_MAX_DIRECTIVES_PER_HOUR=3
ORACLE_BRAIN_DIRECTIVE_ALLOWLIST=ADJUST_CONFIDENCE_THRESHOLD,ADJUST_POSITION_SIZE_MULT,PAUSE_SYMBOL
```

Hard blocklist (değiştirilemez): `CHANGE_STRATEGY`, `OVERRIDE_VETO`, `FORCE_TRADE`.

Doğrulama:

```bash
curl -s http://localhost:8787/api/oracle/gatekeeper/stats | jq
```

Beklenen: `accepted_total`, `rejected_total`, filtre başına counter ve
`rejection_rate_1h` dönmeli.

---

## 4. AutoRollbackMonitor aktivasyonu

```env
AUTO_ROLLBACK_ENABLED=true
AUTO_ROLLBACK_REJECTION_RATE_MAX=0.60
AUTO_ROLLBACK_SHADOW_ACCURACY_MIN=0.45
AUTO_ROLLBACK_META_CONF_STREAK=10
```

Status:

```bash
curl -s http://localhost:8787/api/oracle/autorollback/status | jq
```

Beklenen alanlar: `shadow_forced`, `last_trigger`, `last_fired_at`, per-trigger counter.

Tetik zorlama (drill):

```bash
curl -sX POST http://localhost:8787/api/oracle/autorollback/force \
  -H 'content-type: application/json' \
  -d '{"reason":"operator_drill"}'
```

Doğrulama:

- `.oracle_shadow_forced.json` oluştu (restart-persistent).
- `.auto_rollback_<ts>.json` forensic bundle yazıldı.
- Event bus'ta `oracle.auto_rollback` event'i çıktı.
- `Config.ORACLE_BRAIN_SHADOW = True` durumuna geçti.

Temizlik:

```python
from auto_rollback_monitor import get_auto_rollback_monitor
get_auto_rollback_monitor().reset(operator="ops@quenbot")
```

---

## 5. Low-Dose Active Mode'a geçiş

Warmup + gatekeeper + rollback monitor hepsi yeşil olduktan sonra:

```env
ORACLE_BRAIN_SHADOW=false
```

Servisi restart et:

```bash
pm2 restart quenbot-brain
```

---

## 6. İlk 24 saat izleme

Saatlik bakılacak metrikler:

| Metrik | Eşik | Endpoint |
|---|---|---|
| Gatekeeper reject rate (1h) | < 0.60 | `/api/oracle/gatekeeper/stats` |
| Shadow accuracy (son 50) | > 0.45 | `/api/oracle/brain/stats` |
| Meta-confidence streak | < 10 ardışık <0.40 | `/api/oracle/brain/stats` |
| AutoRollback `shadow_forced` | `false` | `/api/oracle/autorollback/status` |
| Runtime health | healthy | `/api/health` |

---

## 7. Acil rollback (manuel)

```bash
curl -sX POST http://localhost:8787/api/oracle/autorollback/force \
  -H 'content-type: application/json' \
  -d '{"reason":"manual_incident"}'
```

---

## 8. Warmup raporu son hali

```bash
curl -s http://localhost:8787/api/oracle/warmup/report | jq
```

Rapor URL'si, baseline değerleri, trust-score tablosu döner.

---

## 9. Gatekeeper log'u

```bash
tail -f python_agents/.directive_rejected.jsonl | jq
```

---

## 10. Flag'leri kapat (Shadow Mode'a dönüş)

```env
DIRECTIVE_GATEKEEPER_ENABLED=false
AUTO_ROLLBACK_ENABLED=false
ORACLE_BRAIN_SHADOW=true
```

Restart → davranış PR öncesi ile **byte-identical**.

---

## Onay

- [ ] Migration uygulandı.
- [ ] Dry-run raporu incelendi.
- [ ] Live warmup tamamlandı; baseline dosyası set.
- [ ] Gatekeeper endpoint sağlıklı cevap veriyor.
- [ ] Rollback drill başarılı + reset yapıldı.
- [ ] Shadow=false geçişi yapıldı.
- [ ] 24h izleme penceresi planlandı.

Operator: __________________  Tarih: __________________
