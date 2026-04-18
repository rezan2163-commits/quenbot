# AŞAMA 2 — Impact Feedback Loop: Operator Activation Checklist

QuenBot Oracle'ı Aşama 1 Low-Dose Active Mode'dan **Normal Active Mode**'a
(daha geniş allowlist + daha sıkı rollback) geçirme adımları.

---

## 0. Ön koşul — Aşama 1 sağlık kontrolü

Aşama 1 son 1 saatte sağlıklı olmalı:

```bash
curl -s http://localhost:8787/api/oracle/gatekeeper/stats | jq '.rejection_rate_1h, .accepted_total, .rejected_total'
curl -s http://localhost:8787/api/oracle/autorollback/status | jq '.light, .state.rolled_back'
curl -s http://localhost:8787/api/oracle/brain/stats | jq '.directives_emitted_last_1h // .directives_emitted'
```

- [ ] Rejection rate < 0.60
- [ ] Autorollback `light == "armed"`, `rolled_back == false`
- [ ] Qwen son 1 saatte ≥ 5 direktif yayınladı

Bu koşullardan herhangi biri sağlanmıyorsa Aşama 2'ye geçmeyin.

---

## 1. Kod + test

```bash
cd /workspaces/quenbot
git pull
cd python_agents
pytest -q --ignore=tests/test_counterfactual_backfill.py
# Beklenen: 212 passed, 1 skipped
```

---

## 2. Migration

```bash
psql "$DATABASE_URL" -f lib/db/src/migrations/004_directive_impact.sql
```

Doğrulama:

```sql
\d+ oracle_directives
-- Yeni kolonlar: impact_score double precision,
--                impact_measured_at timestamptz,
--                synthetic boolean NOT NULL DEFAULT false,
--                source_tag varchar(64)
```

Rollback (gerekirse):

```sql
ALTER TABLE oracle_directives
  DROP COLUMN IF EXISTS impact_score,
  DROP COLUMN IF EXISTS impact_measured_at,
  DROP COLUMN IF EXISTS synthetic,
  DROP COLUMN IF EXISTS source_tag;
```

---

## 3. Historical impact backfill (dry-run → live)

### 3a. Dry-run

```bash
cd /workspaces/quenbot
python python_agents/scripts/backfill_directive_impact.py \
  --days 90 \
  --symbols BTCUSDT,ETHUSDT,SOLUSDT,BNBUSDT,XRPUSDT \
  --dry-run
```

Çıktı JSON'da:
- `rows_fetched` > 0
- `rows_inserted` (dry-run'da simüle)
- `impact_mean`, `impact_std`
- `by_type` — 6 Aşama 2 direktif tipine dağılım
- `dry_run: true`

### 3b. Live

```bash
python python_agents/scripts/backfill_directive_impact.py \
  --days 90 \
  --symbols BTCUSDT,ETHUSDT,SOLUSDT,BNBUSDT,XRPUSDT
```

Hedef: 3000–8000 arası satır; her satır `synthetic=TRUE`, `source_tag='aşama2_backfill'`.

Doğrulama:

```sql
SELECT synthetic, source_tag, COUNT(*), AVG(impact_score), STDDEV(impact_score)
FROM oracle_directives GROUP BY 1,2;
```

---

## 4. Gatekeeper loosening + rollback tightening (.env)

```env
# Loosen gatekeeper (Aşama 2 defaults already match; env ancak override için)
QUENBOT_ORACLE_BRAIN_DIRECTIVE_CONFIDENCE_MIN=0.65
QUENBOT_ORACLE_BRAIN_MAX_DIRECTIVES_PER_HOUR=10
QUENBOT_ORACLE_BRAIN_DIRECTIVE_ALLOWLIST=ADJUST_CONFIDENCE_THRESHOLD,ADJUST_POSITION_SIZE_MULT,PAUSE_SYMBOL,RESUME_SYMBOL,CHANGE_STRATEGY_WEIGHT,ADJUST_TP_SL_RATIO

# Tighten auto-rollback
QUENBOT_AUTO_ROLLBACK_REJECTION_RATE=0.50
QUENBOT_AUTO_ROLLBACK_ACCURACY_MIN=0.50
QUENBOT_AUTO_ROLLBACK_ACCURACY_WINDOW=100
QUENBOT_AUTO_ROLLBACK_CASCADE_DETECTION=1
QUENBOT_AUTO_ROLLBACK_MAX_AGENT_OVERRIDE_PCT=0.30
QUENBOT_AUTO_ROLLBACK_IMPACT_MEAN_MIN=-0.15

# Impact tracker
QUENBOT_DIRECTIVE_IMPACT_TRACKER_ENABLED=1
QUENBOT_DIRECTIVE_IMPACT_BASELINE_WINDOW_SEC=3600
QUENBOT_DIRECTIVE_IMPACT_MEASURE_WINDOW_SEC=14400

# Safety net impact regression
QUENBOT_SAFETY_NET_IMPACT_REGRESSION_ENABLED=1
QUENBOT_SAFETY_NET_IMPACT_REGRESSION_SIGMA=2.0
QUENBOT_SAFETY_NET_IMPACT_REGRESSION_DURATION_SEC=10800
```

---

## 5. Restart

```bash
sudo systemctl restart quenbot
# veya
pm2 restart quenbot-brain
```

---

## 6. İlk 90 dakika izleme

```bash
curl -s http://localhost:8787/api/oracle/impact/recent?limit=20 | jq '.items[] | {type:.directive_type, impact:.impact_score, synthetic}'
curl -s http://localhost:8787/api/oracle/impact/by-type | jq
curl -s http://localhost:8787/api/oracle/impact/synthetic-vs-live | jq '.rows'
curl -s http://localhost:8787/api/oracle/brain/directives | jq '.[] | .action' | sort | uniq -c
```

Beklenenler:
- Qwen yeni direktif tiplerini (RESUME_SYMBOL / CHANGE_STRATEGY_WEIGHT / ADJUST_TP_SL_RATIO) kullanmaya başlıyor.
- `impact/synthetic-vs-live` kalibrasyon tablosu görünüyor (ilk 4 saat: çoğu canlı direktif hâlâ `pending` — ilk impact ölçümleri 4h gecikmeli).
- Auto-rollback `light == "armed"` kalıyor.

---

## 7. İlk 24 saat izleme

| Metrik | Eşik | Endpoint |
|---|---|---|
| Canlı impact 24h ortalaması | > −0.15 | `/api/oracle/impact/synthetic-vs-live` |
| Canlı - synthetic sapma | < 2σ | `/api/oracle/impact/synthetic-vs-live` |
| Rejection rate 1h | < 0.50 | `/api/oracle/gatekeeper/stats` |
| Shadow accuracy 100 | > 0.50 | `/api/oracle/brain/stats` |
| Authority override 1h | < 0.30 | `/api/oracle/brain/stats` (authority_override_pct_1h) |
| Auto-rollback state | `armed` | `/api/oracle/autorollback/status` |

---

## 8. Aşama 1'e tek-env rollback

Herhangi bir anormallik görürseniz:

```env
QUENBOT_ORACLE_BRAIN_DIRECTIVE_CONFIDENCE_MIN=0.80
QUENBOT_ORACLE_BRAIN_MAX_DIRECTIVES_PER_HOUR=3
QUENBOT_ORACLE_BRAIN_DIRECTIVE_ALLOWLIST=ADJUST_CONFIDENCE_THRESHOLD,ADJUST_POSITION_SIZE_MULT,PAUSE_SYMBOL
QUENBOT_AUTO_ROLLBACK_REJECTION_RATE=0.60
QUENBOT_AUTO_ROLLBACK_ACCURACY_MIN=0.45
QUENBOT_AUTO_ROLLBACK_ACCURACY_WINDOW=50
QUENBOT_AUTO_ROLLBACK_CASCADE_DETECTION=0
```

+ restart. Davranış Aşama 1 ile eşleşir.

---

## 9. Acil durum — Shadow'a tam rollback

```bash
curl -sX POST http://localhost:8787/api/oracle/autorollback/force \
  -H 'content-type: application/json' \
  -d '{"reason":"operator_aşama2_incident"}'
```

---

## Onay

- [ ] Aşama 1 son 1 saatte sağlıklı.
- [ ] Migration 004 uygulandı.
- [ ] Backfill ≥ 3000 synthetic satır üretti.
- [ ] .env güncellendi, servis restart edildi.
- [ ] İlk 90 dk gözetim temiz.
- [ ] 24 saat eşik matrisi yeşil.

Operator: __________________  Tarih: __________________
