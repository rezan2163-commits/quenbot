-- 004_directive_impact.sql — Aşama 2 Impact Feedback Loop
-- Additive-only; idempotent; safe to re-run.

ALTER TABLE oracle_directives
  ADD COLUMN IF NOT EXISTS impact_score DOUBLE PRECISION,
  ADD COLUMN IF NOT EXISTS impact_measured_at TIMESTAMPTZ,
  ADD COLUMN IF NOT EXISTS synthetic BOOLEAN NOT NULL DEFAULT FALSE,
  ADD COLUMN IF NOT EXISTS source_tag VARCHAR(64);

CREATE INDEX IF NOT EXISTS idx_od_impact_measured
  ON oracle_directives(impact_measured_at DESC);

CREATE INDEX IF NOT EXISTS idx_od_synthetic
  ON oracle_directives(synthetic);

CREATE INDEX IF NOT EXISTS idx_od_source_tag
  ON oracle_directives(source_tag);
