# PHASE 4 & 5 — Online Learning + Metrics Exporter

## Phase 4 — Online Learning Evaluator

Shadow JSONL + gerçekleşen fiyat hareketi → rolling performans metrikleri.

### Ne yapar
- `decision_router_shadow.jsonl` logunu periyodik (default 15 dk) tarar.
- Her karar satırına karar anındaki ve horizon (default 60 dk) sonraki fiyatı
  ilişkilendirir.
- Her sembol için son 2000 örnek penceresinde:
  - FastBrain directional hit rate
  - Gemma directional hit rate
  - Agreement oranı ve anlaşınca doğru bulma oranı
  - 10-bin kalibrasyon tablosu
  - Expected Calibration Error (ECE)

### Flag'ler (default OFF)
- `QUENBOT_ONLINE_LEARNING_ENABLED`
- `QUENBOT_ONLINE_LEARNING_INTERVAL_MIN=15`
- `QUENBOT_ONLINE_LEARNING_HORIZON_MIN=60`
- `QUENBOT_ONLINE_LEARNING_MIN_SAMPLES=50`

### Endpoint
```
GET /api/online-learning/stats[?symbol=BTCUSDT]
```

### Güvenlik
- Hot-path dışı; loop kaçırırsa sorun yok.
- JSONL yoksa no-op.
- Fiyat lookup'ı başarısız olursa satır atlanır.
- Log rotasyonu otomatik algılanır (`last_offset` sıfırlanır).

## Phase 5 — Metrics Exporter (Prometheus)

Tüm intel modüllerinden `metrics()` toplar, text/plain format ile yayınlar.

### Özellikler
- Ayrı port (default `9108`) — ana API trafiğini etkilemez.
- Tüm modüller otomatik registered: feature_store, ofi, multi_horizon,
  confluence, cross_asset, fast_brain, decision_router, online_learning.
- Exporter kendi scrape sayacı da döner (`quenbot_exporter_scrape_total`).
- Bir kaynak patlarsa izole edilir, diğerleri aynen yayınlanmaya devam eder.

### Flag'ler (default OFF)
- `QUENBOT_METRICS_ENABLED`
- `QUENBOT_METRICS_PORT=9108`

### Endpoint
```
GET :9108/metrics     (Prometheus text format 0.0.4)
```

## Testler

```
pytest python_agents/tests/ -q
54 passed in 0.56s
```

- +4 online_learning (ingest + maturity + rotation + shapes)
- +4 metrics_exporter (render + source isolation + sanitize + self-metrics)
- 0 regresyon (46 + 8 yeni = 54)

## Toplam Plan Durumu

| Faz | Modül | Durum | Flag |
|-----|-------|-------|------|
| 1 | feature_store, ofi, multi_horizon, confluence | ✅ deploy | ON |
| 2 | cross_asset_graph | ✅ deploy | ON |
| 3 | fast_brain, decision_router | ✅ deploy | OFF (shadow hazır) |
| 4 | online_learning | 🟡 bu PR | OFF |
| 5 | metrics_exporter | 🟡 bu PR | OFF |

Tüm flag'ler kapalı iken sistem eski davranışını aynen sürdürür.
