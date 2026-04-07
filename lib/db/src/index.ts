import { drizzle } from "drizzle-orm/postgres-js";
import postgres from "postgres";
import * as schema from "./schema";

// Database connection
const connectionString = process.env.DATABASE_URL || "postgresql://user:password@localhost:5432/trade_intel";
const client = postgres(connectionString);
export const db = drizzle(client, { schema });

// Export schema for use in other modules
export * from "./schema";