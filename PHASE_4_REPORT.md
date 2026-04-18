# Intel Upgrade — Phase 4 Report (Online Learning + Counterfactuals)

## Overview
Phase 4, Phase 3'ün statik ağırlıklarını online öğrenmeye çeviren katmandır.
Counterfactual gözlemler (TP/FP/FN/TN) DB tablosunda kalıcı hale geldi,
evaluator bu tablodan warm-start ağırlık üretebilir. Tüm yollar flag arkasında.

## Deliverables
| Module | File | Flag |
| --- | --- | --- |
| Online Learning Evaluator | `python_agents/online_learning.py` | `ONLINE_LEARNING_ENABLED` |
| Counterfactual DB Table | `lib/db/src/migrations/001_counterfactual_observations.sql` | `ONLINE_LEARNING_PERSIST_DB` |
| Backfill — features from trades | `python_agents/backfill_features_from_trades.py` | (script) |
| Backfill — counterfactuals | `python_agents/scripts/backfill_counterfactuals.py` | (script, `--dry-run`/`--mock`) |
| Weight promotion | `python_agents/scripts/promote_confluence_weights.py` | (script) |

## Event Types Added (additive)
- `COUNTERFACTUAL_UPDATE`
- `CONFLUENCE_WEIGHTS_ROTATED`

## DB Schema
`counterfactual_observations` — `bigserial` PK, 20 kolon, 4 indeks (symbol+ts,
label+ts, horizon, decided). Idempotent (`CREATE TABLE IF NOT EXISTS`).
Mevcut hiçbir tabloya dokunulmadı; sadece yeni tablo eklendi.

## Persistence Path
- `OnlineLearningEvaluator` JSONL'dan `.online_learning_db_offset.json`
  checkpoint'i ile tail eder → counterfactual_observations'a batch insert.
- `recompute_from_db(limit)` son 30 gün + limit ile naive logistic SGD
  → `.confluence_weights_candidate.json` üretir (aday, canlı değil).
- `SAFETY_NET_TRIPPED` event'i → `weights_frozen=True` (rotasyon durur).

## Backfill CLI
```
python python_agents/scripts/backfill_counterfactuals.py --days 90 --dry-run
python python_agents/scripts/backfill_counterfactuals.py --days 7  # writes
python python_agents/scripts/backfill_counterfactuals.py --mock    # offline
```

## Testing
- `tests/test_counterfactual_backfill.py` — 5 test (mock DB, dry-run, wet-run, labels, checkpoint).
- `tests/test_backward_compat.py` — `test_database_has_counterfactual_table_api`.

## Status
✅ DB migration + DAO + backfill + evaluator + tests hazır. Tüm flag'ler OFF.
