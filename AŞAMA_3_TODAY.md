# AŞAMA 3 — Bugünkü Operatör Adımları

> Aşama 2'den **Free Roam Mode (Aşama 3)**'a geçiş. Aşama 2 son 90 dk stabil olmalı.

---

## 1. Preflight — Aşama 2 sağlık kapısı

```bash
# Son 90 dk auto-rollback yok mu?
curl -s http://127.0.0.1:3002/api/oracle/autorollback/status | jq '.shadow_forced, .last_trigger'
# Aktif (publish eden) direktif sayısı ≥ 5?
curl -s http://127.0.0.1:3002/api/oracle/brain/directives | jq '. | length'
# Impact tracker hayatta mı? (canlı veri sparse olabilir — synthetic dolu olmalı)
curl -s http://127.0.0.1:3002/api/oracle/impact/synthetic-vs-live | jq '.summary'
```

Hepsi yeşilse devam.

## 2. Pull + test (≥ 205 yeşil)

```bash
git pull
cd python_agents && pytest -q
# Beklenen: 205+ passed
```

## 3. Cron extension kur

```bash
bash scripts/cron_daily_report.sh
crontab -l | grep QUENBOT_ASAMA3
```

Üç entry görmelisin: weekly review (Pazar 15:00 UTC), monthly self-audit (1'i 03:00 UTC), hourly ack watchdog.

## 4. **Emergency token kur (ZORUNLU)**

```bash
openssl rand -hex 32
# Çıktıyı kopyala:
echo "EMERGENCY_TOKEN=<paste>" >> .env.secrets
chmod 600 .env.secrets
```

Token olmadan `/api/oracle/emergency-lockdown` 503 döner. Sentinel ve CLI yine çalışır.

## 5. .env güncellemeleri

```bash
# Aşama 3 — natural pace
QUENBOT_ORACLE_BRAIN_MAX_DIRECTIVES_PER_HOUR=30
QUENBOT_ORACLE_BRAIN_DIRECTIVE_ALLOWLIST=ADJUST_CONFIDENCE_THRESHOLD,ADJUST_POSITION_SIZE_MULT,PAUSE_SYMBOL,RESUME_SYMBOL,CHANGE_STRATEGY_WEIGHT,ADJUST_TP_SL_RATIO,CHANGE_STRATEGY
```

> ⚠️ `OVERRIDE_VETO`, `FORCE_TRADE`, `DISABLE_SAFETY_NET` HARD blocklist'te kalır. Allowlist'e eklenseler bile kabul edilmezler.

## 6. Restart

```bash
sudo systemctl restart quenbot
# veya
pm2 restart quenbot
```

## 7. Final smoke

```bash
# Dashboard'a bak — Aşama 3 paneli render olmalı.
# Dry-run weekly review (boş haftada bile geçerli markdown):
python python_agents/scripts/weekly_strategic_review.py --dry-run
# JSON çıktıda dry_run=true ve output_path beklenir.
```

## 8. Takvim hatırlatıcıları

- Her **Pazartesi 20:00**: weekly ack komutu çalıştır
  ```bash
  python python_agents/scripts/ack_weekly.py --week $(date +%G-%V) --note "..."
  ```
- Her ayın **1'i 10:00**: self-audit raporunu oku ve disagreement_rate'i kontrol et.

## 9. Geri dönüş (Aşama 2'ye)

`.env`:
```bash
QUENBOT_ORACLE_BRAIN_MAX_DIRECTIVES_PER_HOUR=10
QUENBOT_ORACLE_BRAIN_DIRECTIVE_ALLOWLIST=ADJUST_CONFIDENCE_THRESHOLD,ADJUST_POSITION_SIZE_MULT,PAUSE_SYMBOL,RESUME_SYMBOL,CHANGE_STRATEGY_WEIGHT,ADJUST_TP_SL_RATIO
```
+ restart. Davranış Aşama 2'ye eşit.

## 10. SİSTEM TAM AUTONOMİDE

Bugünden itibaren senin işin **stratejik**, taktiksel değil:

- Haftalık ack — 7 gün maksimum, yoksa otomatik düşer.
- Aylık disagreement review — > %40 ise prompt + RAG bakım.
- Çeyreklik retraining + mimari değerlendirme.
- Acil durumlarda `touch /tmp/quenbot_emergency`.

Ayrıntılı kılavuz: [`ORACLE_STEADY_STATE_OPERATIONS.md`](./ORACLE_STEADY_STATE_OPERATIONS.md).
