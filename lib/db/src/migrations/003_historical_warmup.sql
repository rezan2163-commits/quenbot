-- 003_historical_warmup.sql — Aşama 1 Historical Warmup
-- Additive-only; touches no existing columns, never alters FKs.
-- Idempotent (IF NOT EXISTS) so safe to re-run on any deployment.

ALTER TABLE counterfactual_observations
  ADD COLUMN IF NOT EXISTS historical_impact_simulation JSONB,
  ADD COLUMN IF NOT EXISTS warmup_generated BOOLEAN NOT NULL DEFAULT FALSE;

CREATE INDEX IF NOT EXISTS idx_cf_warmup
  ON counterfactual_observations(warmup_generated);

-- Tag-friendly index for live-vs-warmup filtering
CREATE INDEX IF NOT EXISTS idx_cf_warmup_symbol_ts
  ON counterfactual_observations(symbol, event_ts DESC)
  WHERE warmup_generated = FALSE;
