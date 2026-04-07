import { pgTable, serial, varchar, decimal, timestamp, jsonb, integer, text } from "drizzle-orm/pg-core";

// Trades table
export const trades = pgTable("trades", {
  id: serial("id").primaryKey(),
  exchange: varchar("exchange", { length: 50 }).notNull(),
  symbol: varchar("symbol", { length: 20 }).notNull(),
  price: decimal("price", { precision: 20, scale: 8 }).notNull(),
  quantity: decimal("quantity", { precision: 20, scale: 8 }).notNull(),
  timestamp: timestamp("timestamp").notNull(),
  side: varchar("side", { length: 10 }).notNull(),
  tradeId: varchar("trade_id", { length: 100 }),
  createdAt: timestamp("created_at").defaultNow(),
});

// Price movements table
export const priceMovements = pgTable("price_movements", {
  id: serial("id").primaryKey(),
  symbol: varchar("symbol", { length: 20 }).notNull(),
  startPrice: decimal("start_price", { precision: 20, scale: 8 }).notNull(),
  endPrice: decimal("end_price", { precision: 20, scale: 8 }).notNull(),
  changePct: decimal("change_pct", { precision: 10, scale: 4 }).notNull(),
  volume: decimal("volume", { precision: 20, scale: 8 }),
  startTime: timestamp("start_time").notNull(),
  endTime: timestamp("end_time").notNull(),
  exchange: varchar("exchange", { length: 50 }).notNull(),
  t10Data: jsonb("t10_data"),
  createdAt: timestamp("created_at").defaultNow(),
});

// Signals table
export const signals = pgTable("signals", {
  id: serial("id").primaryKey(),
  symbol: varchar("symbol", { length: 20 }).notNull(),
  signalType: varchar("signal_type", { length: 20 }).notNull(),
  confidence: decimal("confidence", { precision: 5, scale: 4 }).notNull(),
  price: decimal("price", { precision: 20, scale: 8 }).notNull(),
  timestamp: timestamp("timestamp").notNull(),
  status: varchar("status", { length: 20 }).default("pending"),
  metadata: jsonb("metadata"),
  createdAt: timestamp("created_at").defaultNow(),
});

// Simulations table
export const simulations = pgTable("simulations", {
  id: serial("id").primaryKey(),
  signalId: integer("signal_id").references(() => signals.id),
  symbol: varchar("symbol", { length: 20 }).notNull(),
  entryPrice: decimal("entry_price", { precision: 20, scale: 8 }).notNull(),
  exitPrice: decimal("exit_price", { precision: 20, scale: 8 }),
  quantity: decimal("quantity", { precision: 20, scale: 8 }).notNull(),
  side: varchar("side", { length: 10 }).notNull(),
  status: varchar("status", { length: 20 }).default("open"),
  pnl: decimal("pnl", { precision: 20, scale: 8 }),
  pnlPct: decimal("pnl_pct", { precision: 10, scale: 4 }),
  entryTime: timestamp("entry_time").notNull(),
  exitTime: timestamp("exit_time"),
  stopLoss: decimal("stop_loss", { precision: 20, scale: 8 }),
  takeProfit: decimal("take_profit", { precision: 20, scale: 8 }),
  metadata: jsonb("metadata"),
  createdAt: timestamp("created_at").defaultNow(),
});

// Blacklist patterns table
export const blacklistPatterns = pgTable("blacklist_patterns", {
  id: serial("id").primaryKey(),
  patternType: varchar("pattern_type", { length: 50 }).notNull(),
  patternData: jsonb("pattern_data").notNull(),
  confidence: decimal("confidence", { precision: 5, scale: 4 }).notNull(),
  reason: text("reason"),
  createdBy: varchar("created_by", { length: 50 }),
  createdAt: timestamp("created_at").defaultNow(),
});

// Audit reports table
export const auditReports = pgTable("audit_reports", {
  id: serial("id").primaryKey(),
  signalId: integer("signal_id").references(() => signals.id),
  simulationId: integer("simulation_id").references(() => simulations.id),
  analysis: jsonb("analysis").notNull(),
  lessonsLearned: text("lessons_learned"),
  recommendations: jsonb("recommendations"),
  createdAt: timestamp("created_at").defaultNow(),
});

// Agent config table
export const agentConfig = pgTable("agent_config", {
  id: serial("id").primaryKey(),
  agentName: varchar("agent_name", { length: 50 }).notNull(),
  configKey: varchar("config_key", { length: 100 }).notNull(),
  configValue: jsonb("config_value").notNull(),
  updatedAt: timestamp("updated_at").defaultNow(),
});