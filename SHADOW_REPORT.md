# Shadow Report — Intel Upgrade Safety Gating

Shadow mode, yeni karar/tahmin katmanlarını **canlı emir vermeden** izleme
protokolüdür. Amaç: baseline ile yeni sistem arasında delta'yı ölçüp
promotion kararını veriyle almak.

## Shadow Paths

| Katman | Shadow Flag | Canlı Flag | Shadow'da Ne Yapar? |
| --- | --- | --- | --- |
| FastBrain | — (her zaman shadow) | `FAST_BRAIN_ENABLED` | Tahmin yayınlar; DECISION'a etki yok. |
| Confluence | — | `CONFLUENCE_ENABLED` | Skor yayınlar; routing kullanmaz. |
| Online Learning | `ONLINE_LEARNING_ENABLED` | aynı + `ONLINE_LEARNING_PERSIST_DB` | Gözlemleri DB'ye persist eder; ağırlık rotasyonu `ONLINE_LEARNING_APPLY_WEIGHTS` ile gate. |
| Decision Router | `DECISION_ROUTER_SHADOW` | `DECISION_ROUTER_ENABLED` | Kararı log'lar, emit etmez. |

## Shadow Metrikleri (günlük izlenecek)

- **FastBrain**: `safety_net_brier_24h`, `safety_net_hitrate_24h`,
  `fast_brain_predictions_total`.
- **Confluence**: `/api/intel/counterfactuals?window=24` → precision, recall, F1.
- **Decision Router**: shadow log satırlarının oranı → canlı yolla uyum %'si.

## Geçiş Kriterleri (Shadow → Live)

1. 72 saat shadow boyunca `safety_net_tripped == 0`.
2. Brier(24h) ≤ 0.92 × baseline_brier (iyileşme).
3. Hitrate(24h) ≥ 1.05 × baseline_hitrate.
4. counterfactual_metrics precision ≥ 0.55, recall ≥ 0.50 (window=72h).
5. `promote_confluence_weights.py` çalıştırıldığında logloss gain ≥ %5 &
   Sharpe CI lower > 0 & ≥%80 sembol non-regressed.

Hepsi yeşilse ilgili `*_ENABLED` flag'i 1'e çekilir.

## Rollback

Tüm shadow flag'leri 0 → sistem Phase 2 davranışına döner. Hiçbir tablo
silinmez, veriler korunur. Rollback komutu FINALIZATION_REPORT.md'de.
