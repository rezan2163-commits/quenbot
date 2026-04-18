-- 002_oracle_tables.sql — Phase 6 Oracle Stack (§11)
-- Idempotent; existing-system safe. APPEND only; no alter on other tables.

CREATE TABLE IF NOT EXISTS oracle_directives (
    directive_id     TEXT PRIMARY KEY,
    ts               DOUBLE PRECISION NOT NULL,
    symbol           TEXT NOT NULL,
    action           TEXT NOT NULL,
    severity         TEXT NOT NULL,
    confidence       DOUBLE PRECISION DEFAULT 0,
    rationale        TEXT,
    params_json      TEXT,
    ttl_sec          INTEGER DEFAULT 300,
    source           TEXT DEFAULT 'qwen_oracle_brain',
    shadow           BOOLEAN DEFAULT TRUE,
    created_at       TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_oracle_directives_symbol_ts
    ON oracle_directives (symbol, ts DESC);
CREATE INDEX IF NOT EXISTS idx_oracle_directives_action
    ON oracle_directives (action);

CREATE TABLE IF NOT EXISTS oracle_reasoning_traces (
    trace_id         TEXT PRIMARY KEY,
    ts               DOUBLE PRECISION NOT NULL,
    symbol           TEXT NOT NULL,
    observation_json TEXT,
    directive_json   TEXT,
    prompt           TEXT,
    response         TEXT,
    tokens_used      INTEGER DEFAULT 0,
    latency_ms       DOUBLE PRECISION DEFAULT 0,
    rag_hits_json    TEXT,
    shadow           BOOLEAN DEFAULT TRUE,
    created_at       TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_oracle_reasoning_symbol_ts
    ON oracle_reasoning_traces (symbol, ts DESC);

CREATE TABLE IF NOT EXISTS oracle_channel_weights (
    id               SERIAL PRIMARY KEY,
    ts               DOUBLE PRECISION NOT NULL,
    weights_json     TEXT NOT NULL,
    source           TEXT DEFAULT 'qwen_oracle_brain',
    note             TEXT,
    created_at       TIMESTAMPTZ DEFAULT NOW()
);
