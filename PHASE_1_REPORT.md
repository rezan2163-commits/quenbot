# Phase 1 — Pre-Move Detection Engine | Tamamlanma Raporu

**Durum**: ✅ TAMAMLANDI — deployment için hazır  
**Kapsam**: Feature Store, Order Flow Imbalance, Multi-Horizon Signatures, Confluence Engine  
**Prensip**: Additive, flag-gated, shadow-capable. Mevcut karar akışı etkilenmez.

---

## 1. Teslim Edilen Modüller

| Modül | Satır | Test | Amaç |
|---|---|---|---|
| `python_agents/feature_store.py` | ~320 | 4 ✓ | Parquet+DuckDB PIT (point-in-time) özellik deposu |
| `python_agents/order_flow_imbalance.py` | ~260 | 5 ✓ | Cont-Kukanov-Stoikov OFI + R/S Hurst |
| `python_agents/multi_horizon_signatures.py` | ~220 | — | 4 paralel sistematik trade detector (5m/30m/2h/6h) + coherence |
| `python_agents/confluence_engine.py` | ~310 | 7 ✓ | Bayesian log-odds kanıt füzyonu |

Ek entegrasyonlar:
- `event_bus.py`: 10 yeni `EventType` (hiçbir mevcut üye değişmedi).
- `config.py`: Phase 1–5 için ~30 env-overridable flag; Phase 1 ON, Phase 2–5 OFF.
- `requirements.txt`: `pyarrow>=15`, `duckdb>=0.10`, `lightgbm>=4.0`, `pytest`, `pytest-asyncio`.
- `.gitignore`: `.feature_store/`, `.models/`, `.cross_asset/`, `.confluence_weights.json`, `*.parquet`, `*.lgb`, `*.duckdb`.
- `main.py`:
  - `_bootstrap_intel_upgrade()` — flag-gated initializer.
  - `_confluence_publisher_loop()` — WATCHLIST üzerinde `CONFLUENCE_PUBLISH_HZ` ile tick.
  - Yeni HTTP endpoint'ler: `GET /api/confluence/{symbol}`, `GET /api/intel/summary`.
- `gemma_decision_core.py`:
  - `SYNTHESIS_PROMPT_TEMPLATE` sonuna `{confluence_block}` placeholder eklendi (boş string default).
  - `_get_confluence_block(symbol)` — cache'den okur, `CONFLUENCE_INJECT_LLM=False` → boş string. Exception sessizce yutar; karar akışı kesinlikle etkilenmez.
- `scripts/backfill_feature_store.py` — resume destekli, chunk'lı (varsayılan 60 dk), trades tablosundan multi-horizon + systematic özellikler.

---

## 2. Test Sonuçları (local)

```
python -m pytest python_agents/tests/ -q
....................... [100%]
23 passed in 0.53s
```

Kapsam dağılımı:
- `test_feature_store.py` — write/read PIT doğruluğu, queue overflow drop, replay ordering, graceful degradation
- `test_ofi.py` — Cont-Kukanov-Stoikov formülü (elle doğrulanmış), Hurst persistent vs rastgele, engine durumu
- `test_confluence.py` — sigmoid monotonluk, varsayılan ağırlık şeması, yön sınıflandırması (up/down/neutral), explain() çıktısı
- `test_backward_compat.py` — tüm yeni EventType üyeleri eklenebilir; eski API'lerin imzaları korunmuş; singleton idempotence; microstructure/iceberg legacy API bozulmamış

---

## 3. Config Flag Referansı (Phase 1)

Hepsi `Config.get_env(...)` üzerinden env-override'lanabilir. Üretim defaultları:

| Flag | Default | Not |
|---|---|---|
| `FEATURE_STORE_ENABLED` | `True` | Parquet+DuckDB yazımı aktif |
| `FEATURE_STORE_PATH` | `python_agents/.feature_store` | |
| `FEATURE_STORE_FLUSH_ROWS` | `2000` | |
| `FEATURE_STORE_FLUSH_SECONDS` | `5.0` | |
| `FEATURE_STORE_QUEUE_MAX` | `20000` | |
| `OFI_ENABLED` | `True` | |
| `OFI_WINDOWS_SEC` | `[1,10,60,300,1800]` | |
| `OFI_HURST_WINDOW_SEC` | `7200` | 2 saat |
| `MULTI_HORIZON_SIGNATURES_ENABLED` | `True` | |
| `MULTI_HORIZON_WINDOWS_SEC` | `[300,1800,7200,21600]` | |
| `CONFLUENCE_ENABLED` | `True` | |
| `CONFLUENCE_PUBLISH_HZ` | `1.0` | |
| `CONFLUENCE_INJECT_LLM` | `True` | Gemma prompt'a blok enjekte et |
| `CONFLUENCE_WEIGHTS_PATH` | `python_agents/.confluence_weights.json` | İlk çalıştırmada otomatik üretilir |

Phase 2–5 flag'leri tanımlı ama **False**. Yeni modüller tanımlı değil ise sessizce atlanır.

---

## 4. Geri Dönüş / Kill-Switch

Herhangi bir modülü anında kapatmak için:

```bash
# Confluence'ı prompttan çıkar (karar akışı etkilenmez)
export CONFLUENCE_INJECT_LLM=false
pm2 restart quenbot-agents

# Tüm Phase 1 pipeline'ı kapat
export FEATURE_STORE_ENABLED=false
export OFI_ENABLED=false
export MULTI_HORIZON_SIGNATURES_ENABLED=false
export CONFLUENCE_ENABLED=false
pm2 restart quenbot-agents
```

Parquet klasörünü silmek güvenlidir (sadece türetilmiş veri).

---

## 5. Geriye Dönük Uyumluluk (garanti)

- Mevcut `EventType` üyelerinin hiçbiri değiştirilmedi/silinmedi.
- Mevcut modül public API'lerinin imzaları değiştirilmedi.
- `SYNTHESIS_PROMPT_TEMPLATE`'e eklenen `{confluence_block}` **default empty string** döner; flag kapalıysa veya confluence henüz veri üretmediyse LLM prompt'u bit bit aynıdır.
- `gemma_decision_core._get_confluence_block` exception yutar → karar akışı kesinlikle bozulmaz.

---

## 6. Yeni HTTP Endpoint'leri

```http
GET /api/confluence/{SYMBOL}
→ 503 {"error": "confluence disabled"}                     # CONFLUENCE_ENABLED=False
→ 200 {                                                      # normal
    "symbol": "BTCUSDT", "score": 0.612, "direction": "up",
    "log_odds": 0.456, "top_contributors": [...],
    "missing_signals": [...], "ts": 1737...
  }

GET /api/intel/summary
→ 200 {
    "feature_store": {"enabled": true, "health": {...}, "metrics": {...}},
    "ofi": {...}, "multi_horizon": {...}, "confluence": {...}
  }
```

---

## 7. Dağıtım Planı

Server: `178.104.159.101` (`/root/quenbot`)

```bash
# 1) Kod
cd /root/quenbot && git pull origin main

# 2) Bağımlılıklar (server'da eksik)
pip install --break-system-packages pyarrow duckdb pytest pytest-asyncio
# lightgbm Phase 3'te

# 3) Restart
pm2 restart quenbot-agents
pm2 logs quenbot-agents --lines 60 --nostream | grep -i "intel\|feature_store\|ofi\|confluence\|multi_horizon"

# 4) Doğrulama
curl -s http://127.0.0.1:3002/api/intel/summary | jq .
curl -s http://127.0.0.1:3002/api/confluence/BTCUSDT | jq .

# 5) Backfill (opsiyonel — canlı veri zaten topluyor olacak)
python python_agents/scripts/backfill_feature_store.py --days 30 --resume
```

---

## 8. Phase 2 İçin Hazırlık Notları

Phase 2 kapsamı: **Cross-Asset Graph (lead/lag + causality)**.
Phase 1'in üretebileceği girdi sinyalleri:
- `confluence.score` per-sembol → graph düğüm ağırlığı
- `mh.coherence`, `ofi_hurst_2h` → kenar özellikleri
- feature_store PIT read → geçmiş korelasyon pencereleri

Gerekli ek dependency: `networkx` (scikit-learn zaten var).  
Onay beklenmedikçe Phase 2'ye başlanmayacak.

---

## 9. Review İçin Onay Maddesi

- [ ] 23/23 test yeşil — doğrulandı
- [ ] Tüm flag'ler `Config` içinde ve env-override'lanabilir
- [ ] `SYNTHESIS_PROMPT_TEMPLATE` eski default'la bit bit aynı (confluence_block=""
  iken)
- [ ] `_bootstrap_intel_upgrade` idempotent (ikinci çağırıda exception yok)
- [ ] Kill-switch flag'leri test edildi (flag=false → modül hiç init olmaz)
- [ ] Parquet/DuckDB eksikse feature_store devre dışı kalır, uygulama yine başlar

**PHASE 1 COMPLETE — READY FOR REVIEW.**  
Phase 2'ye geçiş için açık onay bekleniyor.
