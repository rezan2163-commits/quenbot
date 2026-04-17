# PHASE 3 — Fast Brain + Decision Router (Shadow Mode)

Tarih: kayıt anı itibariyle lokal implementasyon tamamlandı, deploy onayı bekleniyor.

## 1. Özet

Phase 3, Quenbot'un karar hattına iki yeni katman ekler:

1. **FastBrain** — LightGBM tabanlı, kalibre edilmiş hızlı olasılık tahmincisi
   (<5 ms). Canlı singleton'lardan (microstructure / OFI / multi-horizon /
   cross-asset / confluence) feature vektörü toplar, yön olasılığı üretir.
2. **Decision Router** — FastBrain + Gemma kararlarını birleştirir.
   **Varsayılan shadow modunda**: her iki tarafı da yürütür, anlaşmazlıkları
   JSONL'e loglar, ama asla Gemma'yı override etmez. Yani risk **sıfır**.

Her iki modül de **flag-kapalı** (default) ve **graceful degradation** ile
gelir: lightgbm yüklü değilse, model dosyası yoksa, ya da yeterli feature
toplanamıyorsa modüller sessiz kalır, sistem eski davranışını aynen sürdürür.

## 2. Değişen / eklenen dosyalar

### Yeni
- `python_agents/fast_brain.py` — `FastBrainEngine`, `FastBrainPrediction`,
  Platt/isotonic kalibrasyon, canlı feature collector, singleton.
- `python_agents/decision_router.py` — `DecisionRouter`, shadow/active
  routing, JSONL log + rotasyon.
- `python_agents/scripts/train_fast_brain.py` — offline trainer (feature_store
  parquet → LightGBM + Platt kalibrasyon).
- `python_agents/tests/test_fast_brain.py` — 7 test.
- `python_agents/tests/test_decision_router.py` — 8 test.

### Değişen (additive)
- `python_agents/config.py` — Phase 3 flag'leri:
  - `FAST_BRAIN_ENABLED` (default OFF)
  - `FAST_BRAIN_MODEL_PATH` = `python_agents/.models/fast_brain_latest.lgb`
  - `FAST_BRAIN_CALIBRATION_PATH` = `...fast_brain_latest.calib.json`
  - `FAST_BRAIN_T_HIGH` = 0.65, `FAST_BRAIN_T_LOW` = 0.45
  - `FAST_BRAIN_MIN_FEATURES` = 4
  - `DECISION_ROUTER_ENABLED` (default OFF)
  - `DECISION_ROUTER_SHADOW` (default ON — yani override etmez)
  - `DECISION_ROUTER_LOG_PATH`, `DECISION_ROUTER_MAX_LOG_ROWS` = 50000
- `python_agents/main.py` — Phase 3 bootstrap bloğu + endpoints:
  - `GET /api/fast-brain/{symbol}`
  - `GET /api/decision-router/status`
  - `/api/intel/summary` yanıtına `fast_brain` ve `decision_router` alanları
- `python_agents/gemma_decision_core.py` — Her karar sonrası
  `_route_through_fast_brain()` hook'u (Event `DECISION_SHADOW` publish
  eder). Hook hataları yutulur, hot-path'i bozmaz.

## 3. Test sonuçları

```
pytest python_agents/tests/ -v
46 passed in 0.58s
```

- Önceki: 31/31 (Phase 1: 23 + Phase 2: 8)
- Yeni: 46/46 (+15 Phase 3 testi)
- Regresyon yok.

Phase 3 testleri:
- FastBrain dormant (model yok) → `predict() → None`
- Sigmoid / Platt / isotonic interpolation matematiği
- Feature collector eksik feature raporlama
- Stub booster ile tam predict path (threshold → direction)
- Router shadow modunda **asla** override etmez
- Router active modunda agreement + yüksek olasılık → fast override
- Disagreement / neutral → gemma geçer
- JSONL log append + rotasyon (`.jsonl.1` oluşur)
- `metrics()` ve `health_check()` çıktıları

## 4. Güvenlik & kill-switch

- **Flag default OFF**: hiçbir şey değişmez, hiçbir yeni log üretilmez.
- **Flag ON + model yok**: FastBrain dormant, Router gemma'yı değiştirmez.
- **Flag ON + model var + shadow ON** (önerilen ilk canlı mod): Tüm veriler
  loglanır, kararlar **değişmez**. Konsey veriyi inceler.
- **Flag ON + shadow OFF** (ileriki onay adımı): Router ancak
  (a) FastBrain yönü Gemma'nın yönüyle aynıysa, ve (b) olasılık eşikleri
  aşıyorsa confidence'ı güncelleyebilir. Gemma'nın HOLD/REJECT'ini **asla**
  BUY/SELL'e çeviremez (conservative contract).
- Hot-path hata yutulur (`logger.debug`), hiç exception propagate etmez.

## 5. Yeni endpoint'ler

```
GET /api/intel/summary              (güncellendi, +fast_brain, +decision_router)
GET /api/fast-brain/{symbol}        (canlı tahmin)
GET /api/decision-router/status     (routed_total, agree/disagree, son kararlar)
```

## 6. Model eğitimi (opsiyonel, deploy sonrası)

Model olmadan sistem sorunsuz çalışır — FastBrain dormant kalır. Eğitim için:

```bash
# Sunucuda:
cd /root/quenbot
pip install --break-system-packages lightgbm
python python_agents/scripts/train_fast_brain.py \
    --days 30 --horizon-min 60 --threshold-bps 50 \
    --output python_agents/.models/fast_brain_latest
# Sonra: flag'i .env'e ekle:
# QUENBOT_FAST_BRAIN_ENABLED=true
# QUENBOT_DECISION_ROUTER_ENABLED=true  (shadow zaten default ON)
pm2 restart all
```

## 7. Deploy adımları (onay sonrası)

1. `git push` (main)
2. Sunucu: `git pull`
3. `pip install --break-system-packages lightgbm` (opsiyonel — flag'ler OFF
   ise gerekli değil)
4. `pm2 restart all`
5. Doğrulama:
   ```bash
   curl -s localhost:3002/api/intel/summary | jq '.fast_brain, .decision_router'
   # Her ikisi de {"enabled": false} dönmeli (flag OFF).
   ```
6. Regresyon: Phase 1/2 endpoint'leri aynı değerleri vermeli.

## 8. Phase 4 hazırlık notu

Phase 3 aktive edildiğinde (shadow) toplanacak JSONL logu,
Phase 4'te **Meta-labeler + Online güncelleme** katmanının temel veri
kaynağı olacak: disagreement örnekleri otomatik re-labeling havuzuna
beslenecek.
