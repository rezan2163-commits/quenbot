# Phase 2 — Cross-Asset Graph (Lead/Lag + Spillover) | Tamamlanma Raporu

**Durum**: ✅ TAMAMLANDI — flag ile default **OFF**, manuel etkinleştirme gerekir.  
**Bağımlılık eklemesi**: YOK (pure stdlib + mevcut numpy)  
**Prensip**: Additive, flag-gated, kill-switch güvenli.

---

## 1. Teslim Edilen

| Dosya | Satır | Test | Amaç |
|---|---|---|---|
| `python_agents/cross_asset_graph.py` | ~370 | 8 ✓ | Cross-correlation lead/lag tespiti + leader alert yayını |
| `python_agents/tests/test_cross_asset.py` | ~130 | 8/8 ✓ | Unit tests |

Entegrasyonlar:
- `config.py`: 8 yeni `CROSS_ASSET_*` flag (hepsi env-override'lı, default **OFF**)
- `confluence_engine.py`: `cross_asset_spillover` yeni sinyal + `DEFAULT_WEIGHTS` içinde (0.55). Eski ağırlık dosyası otomatik olarak bu alanı default'tan alır.
- `main.py`:
  - `_bootstrap_intel_upgrade` sonuna Phase 2 bloğu; `SCOUT_PRICE_UPDATE` → `on_price_update` subscribe
  - `rebuild_loop` background task (varsayılan 15 dk)
  - Yeni endpoint'ler:
    - `GET /api/cross-asset/graph` — tüm grafik
    - `GET /api/cross-asset/{symbol}` — leaders/followers/spillover
  - `/api/intel/summary`'a `cross_asset` eklendi
- `gemma_decision_core.py`: Confluence prompt bloğuna opsiyonel `Cross-Asset Leaders` satırı (flag kapalı → bit bit aynı çıktı).

---

## 2. Algoritma

1. **Binleme**: Her sembolün `SCOUT_PRICE_UPDATE` akışından 15 sn bin'lerde log-return.
   Son 2 saatlik pencere (480 bin) RAM'de tutulur.
2. **Rebuild** (her 15 dk):
   - Her (A, B) çifti için `argmax_{l ∈ [-max_lag, max_lag]} ρ(A_t, B_{t+l})` hesapla.
   - `|ρ| < CROSS_ASSET_MIN_EDGE_STRENGTH` → kenar atılır.
   - `lag > 0` → A lider (A → B kenarı, lag_bins = lag).
   - numpy varsa vektörize, yoksa pure-Python fallback.
3. **Leader alert**: Canlı tick'lerde bir sembolde `|return| ≥ LEADER_MIN_MOVE_BPS` (default 15 bps ≈ 0.15%) görüldüğünde o sembolün follower'larına spillover sinyali enjekte edilir (`LEAD_LAG_ALERT` event + confluence için active spillover z-skoru, expiry = 2× expected_lag).
4. **Cooldown**: Aynı leader için `CROSS_ASSET_ALERT_COOLDOWN_SEC` (60 sn) içinde 2. alert üretilmez.

---

## 3. Test Sonuçları

```
python -m pytest python_agents/tests/ -q
............................... [100%]
31 passed in 0.55s    (23 Phase 1 + 8 Phase 2)
```

Phase 2 test kapsamı:
- `_crosscorr`: zero-lag perfect match, pozitif lag tespiti, yetersiz veri, sabit seri (std=0)
- Engine ingest → rebuild → kenar üretimi (AAA 15sn önden giden, BBB takip eden)
- Leader alert'in cooldown'a uyması
- Spillover sinyalinin expiry sonrası sıfırlanması
- `health_check` + `metrics` yapısı

---

## 4. Flag Referansı

| Flag | Default | Not |
|---|---|---|
| `CROSS_ASSET_ENABLED` | `False` | Motor ve rebuild task etkinleştirilir |
| `CROSS_ASSET_LAG_STEP_SEC` | `15` | Bin genişliği (min engine floor: 5s) |
| `CROSS_ASSET_HISTORY_SEC` | `7200` | Rolling pencere (2 saat) |
| `CROSS_ASSET_MAX_LAG_SEC` | `300` | ±5 dk tarama aralığı |
| `CROSS_ASSET_MIN_SAMPLES` | `60` | Kenar hesaplama için min tick sayısı |
| `CROSS_ASSET_MIN_EDGE_STRENGTH` | `0.08` | \|ρ\| eşiği |
| `CROSS_ASSET_REBUILD_INTERVAL_MIN` | `15` | Rebuild periyodu |
| `CROSS_ASSET_ALERT_COOLDOWN_SEC` | `60` | Leader başına min aralık |
| `CROSS_ASSET_LEADER_MIN_MOVE_BPS` | `15` | Leader tetikleme eşiği |
| `CROSS_ASSET_GRAPH_PATH` | `python_agents/.cross_asset/latest_graph.json` | Snapshot |

---

## 5. Yeni HTTP Endpoint'leri

```http
GET /api/cross-asset/graph
→ 503 {"error":"cross_asset disabled"}                 # CROSS_ASSET_ENABLED=False
→ 200 {"ts": ..., "nodes":[...], "edges":[{"src":"BTCUSDT","dst":"ETHUSDT","lag_sec":15,"rho":0.41}, ...]}

GET /api/cross-asset/{SYMBOL}
→ 200 {"symbol":"ETHUSDT", "leaders":[{"symbol":"BTCUSDT","lag_sec":15,"rho":0.41}], "followers":[...], "active_spillover":0.0}
```

---

## 6. Geri Dönüş / Kill-Switch

```bash
# Motoru anında kapat (default zaten OFF)
export QUENBOT_CROSS_ASSET_ENABLED=false
pm2 restart quenbot-agents

# Sadece LLM'e bilgiyi gösterme (confluence_block'tan Cross-Asset satırı düşer)
# → flag kapalı olunca otomatik; ayrı flag gerekmez
```

Engine offline olduğunda:
- `confluence_engine._collect_signals` `cross_asset_spillover` anahtarını hiç eklemez.
- `gemma_decision_core._get_confluence_block` cross_asset satırını boş bırakır.
- Hot path (karar akışı) etkilenmez.

---

## 7. Geriye Dönük Uyumluluk

- Mevcut `DEFAULT_WEIGHTS` anahtarları dokunulmadı — sadece `cross_asset_spillover` eklendi.
- Eski `.confluence_weights.json` dosyaları `load_weights` içinde `dict(DEFAULT_WEIGHTS)` ile merge edilir → yeni anahtar otomatik olarak 0.55 varsayılanı alır.
- Phase 1 ve mevcut sistem davranışları **değişmedi**: 23/23 Phase 1 testi hâlâ yeşil.

---

## 8. Aktivasyon Planı (server)

```bash
# 1) Kod
ssh root@178.104.159.101 'cd /root/quenbot && git pull --ff-only'

# 2) Kademeli etkinleştirme (önce bir gün gözlemle)
ssh root@178.104.159.101 'pm2 restart quenbot-agents --update-env --silent \
    --env QUENBOT_CROSS_ASSET_ENABLED=true'

# 3) Doğrula
ssh root@178.104.159.101 'curl -sS http://127.0.0.1:3002/api/intel/summary | jq .cross_asset'
ssh root@178.104.159.101 'curl -sS http://127.0.0.1:3002/api/cross-asset/graph | jq ".edges | length"'
```

Not: İlk rebuild CROSS_ASSET_REBUILD_INTERVAL_MIN (15 dk) sonra çıkar. İlk 15 dk `edges=[]` beklenir — `min_samples=60` doldurmak için bin başına en az ~1 tick/5s gerekir.

---

## 9. Phase 3 İçin Hazırlık

Phase 3 kapsamı: **Fast Brain (LightGBM calibrated prediction) + Decision Router**.
Phase 2'nin sağladığı girdiler:
- `cross_asset_spillover` z-skoru → fast-brain feature
- Lead/lag graf, Phase 3'te regime conditioning için kullanılabilir
- feature_store'da `cross_asset.*` bin'leri (ileride eklenebilir)

Gerekli ek dep: `lightgbm` (requirements.txt'de var, server'a kurulmadı henüz).

**PHASE 2 COMPLETE — READY FOR REVIEW.**  
Phase 3'e geçmek için onayınızı bekliyorum.
