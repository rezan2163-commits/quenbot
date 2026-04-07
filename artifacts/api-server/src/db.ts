import postgres from "postgres";

export const sql = postgres(process.env.DATABASE_URL || "postgresql://user:password@localhost:5432/trade_intel", {
  max: 5,
  idle_timeout: 20,
  connect_timeout: 10,
});

export async function connectDatabase() {
  try {
    await sql`SELECT 1`;
    console.log("Connected to PostgreSQL database");
  } catch (error) {
    console.error("Database connection failed:", error);
    throw error;
  }
}

export async function createTables() {
  await sql`
    CREATE TABLE IF NOT EXISTS trades (
      id SERIAL PRIMARY KEY,
      exchange VARCHAR(50) NOT NULL,
      market_type VARCHAR(20) NOT NULL DEFAULT 'spot',
      symbol VARCHAR(20) NOT NULL,
      price NUMERIC(20, 8) NOT NULL,
      quantity NUMERIC(20, 8) NOT NULL,
      timestamp TIMESTAMP NOT NULL,
      side VARCHAR(10) NOT NULL,
      trade_id VARCHAR(100) UNIQUE,
      created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
  `;

  await sql`
    CREATE TABLE IF NOT EXISTS price_movements (
      id SERIAL PRIMARY KEY,
      exchange VARCHAR(50) NOT NULL,
      market_type VARCHAR(20) NOT NULL DEFAULT 'spot',
      symbol VARCHAR(20) NOT NULL,
      start_price NUMERIC(20, 8) NOT NULL,
      end_price NUMERIC(20, 8) NOT NULL,
      change_pct NUMERIC(10, 4) NOT NULL,
      volume NUMERIC(20, 8),
      buy_volume NUMERIC(20, 8),
      sell_volume NUMERIC(20, 8),
      direction VARCHAR(10),
      aggressiveness NUMERIC(10, 4),
      start_time TIMESTAMP NOT NULL,
      end_time TIMESTAMP NOT NULL,
      t10_data JSONB,
      created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
  `;

  await sql`
    CREATE TABLE IF NOT EXISTS signals (
      id SERIAL PRIMARY KEY,
      market_type VARCHAR(20) NOT NULL DEFAULT 'spot',
      symbol VARCHAR(20) NOT NULL,
      signal_type VARCHAR(20) NOT NULL,
      confidence NUMERIC(5, 4) NOT NULL,
      price NUMERIC(20, 8) NOT NULL,
      timestamp TIMESTAMP NOT NULL,
      status VARCHAR(20) DEFAULT 'pending',
      metadata JSONB,
      created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
  `;

  await sql`
    CREATE TABLE IF NOT EXISTS simulations (
      id SERIAL PRIMARY KEY,
      signal_id INTEGER REFERENCES signals(id),
      market_type VARCHAR(20) NOT NULL DEFAULT 'spot',
      symbol VARCHAR(20) NOT NULL,
      entry_price NUMERIC(20, 8) NOT NULL,
      exit_price NUMERIC(20, 8),
      quantity NUMERIC(20, 8) NOT NULL,
      side VARCHAR(10) NOT NULL,
      status VARCHAR(20) DEFAULT 'open',
      pnl NUMERIC(20, 8),
      pnl_pct NUMERIC(10, 4),
      entry_time TIMESTAMP NOT NULL,
      exit_time TIMESTAMP,
      stop_loss NUMERIC(20, 8),
      take_profit NUMERIC(20, 8),
      metadata JSONB,
      created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
  `;

  await sql`
    CREATE TABLE IF NOT EXISTS watchlist (
      id SERIAL PRIMARY KEY,
      symbol VARCHAR(20) NOT NULL,
      market_type VARCHAR(20) NOT NULL DEFAULT 'spot',
      description TEXT,
      created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
  `;

  await sql`
    CREATE TABLE IF NOT EXISTS blacklist_patterns (
      id SERIAL PRIMARY KEY,
      pattern_type VARCHAR(50) NOT NULL,
      pattern_data JSONB NOT NULL,
      confidence NUMERIC(5, 4) NOT NULL,
      reason TEXT,
      created_by VARCHAR(50),
      created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
  `;

  await sql`
    CREATE TABLE IF NOT EXISTS audit_reports (
      id SERIAL PRIMARY KEY,
      signal_id INTEGER REFERENCES signals(id),
      simulation_id INTEGER REFERENCES simulations(id),
      analysis JSONB NOT NULL,
      lessons_learned TEXT,
      recommendations JSONB,
      created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
  `;

  await sql`CREATE INDEX IF NOT EXISTS idx_trades_symbol_timestamp ON trades(symbol, timestamp)`;
  await sql`CREATE INDEX IF NOT EXISTS idx_price_movements_symbol_time ON price_movements(symbol, start_time)`;
  await sql`CREATE INDEX IF NOT EXISTS idx_signals_status_timestamp ON signals(status, timestamp)`;
  await sql`CREATE INDEX IF NOT EXISTS idx_simulations_status_time ON simulations(status, entry_time)`;
}
