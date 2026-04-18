-- Intel Upgrade — Counterfactual Observations store (Phase 4 Finalization)
-- Additive-only migration; never alters existing tables.
-- Idempotent (IF NOT EXISTS) so safe to re-run on any deployment.

CREATE TABLE IF NOT EXISTS counterfactual_observations (
  id BIGSERIAL PRIMARY KEY,
  symbol VARCHAR(32) NOT NULL,
  event_ts TIMESTAMPTZ NOT NULL,
  move_magnitude_pct DOUBLE PRECISION NOT NULL,
  move_direction VARCHAR(8) NOT NULL,        -- 'up' | 'down' | 'flat'
  label VARCHAR(4) NOT NULL,                 -- 'TP' | 'FP' | 'FN' | 'TN'
  horizon_minutes INT NOT NULL,              -- 30, 60, 120, ...
  features_t_minus_30m JSONB,
  features_t_minus_1h  JSONB,
  features_t_minus_2h  JSONB,
  confluence_score_t_minus_1h DOUBLE PRECISION,
  fast_brain_p_t_minus_1h     DOUBLE PRECISION,
  conformal_lower             DOUBLE PRECISION,
  conformal_upper             DOUBLE PRECISION,
  decided BOOLEAN NOT NULL DEFAULT FALSE,
  decision_source VARCHAR(32),               -- 'fast_brain' | 'llm' | 'confluence' | 'none'
  decision_path   VARCHAR(16),               -- 'fast' | 'escalation' | 'veto' | 'shadow'
  realized_pnl_pct DOUBLE PRECISION,
  attribution JSONB,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_cf_symbol_ts   ON counterfactual_observations(symbol, event_ts DESC);
CREATE INDEX IF NOT EXISTS idx_cf_label       ON counterfactual_observations(label);
CREATE INDEX IF NOT EXISTS idx_cf_event_ts    ON counterfactual_observations(event_ts DESC);
CREATE INDEX IF NOT EXISTS idx_cf_created_at  ON counterfactual_observations(created_at DESC);
