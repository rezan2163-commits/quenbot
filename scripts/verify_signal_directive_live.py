import asyncio
import asyncpg

DB = "postgresql://user:password@localhost:5432/trade_intel"


async def main() -> None:
    conn = await asyncpg.connect(DB, timeout=8)
    try:
        await conn.execute("SET statement_timeout = '9000ms'")
        row = await conn.fetchrow(
            """
            SELECT
              COUNT(*) FILTER (
                WHERE COALESCE((metadata->>'estimated_duration_to_target_minutes')::int, 60) < 60
                   OR COALESCE((metadata->>'estimated_duration_to_target_minutes')::int, 60) > 1440
              )::int AS bad_eta,
              COUNT(*) FILTER (
                WHERE COALESCE((metadata->>'target_pct')::double precision, 0) < 0.02
              )::int AS bad_target,
              COUNT(*)::int AS total
            FROM signals
            WHERE timestamp >= NOW() - INTERVAL '20 minutes'
              AND LOWER(COALESCE(metadata->>'source', source, '')) IN ('strategist','pattern_matcher')
            """
        )
        print(f"recent_total={row['total']} bad_eta={row['bad_eta']} bad_target={row['bad_target']}")

        rows = await conn.fetch(
            """
            SELECT symbol, signal_type,
                   metadata->>'estimated_duration_to_target_minutes' AS eta,
                   metadata->>'target_pct' AS target_pct
            FROM signals
            WHERE timestamp >= NOW() - INTERVAL '20 minutes'
              AND LOWER(COALESCE(metadata->>'source', source, '')) IN ('strategist','pattern_matcher')
            ORDER BY timestamp DESC
            LIMIT 10
            """
        )
        for r in rows:
            print(f"{r['symbol']} {r['signal_type']} eta={r['eta']} tp={r['target_pct']}")
    finally:
        await conn.close()


if __name__ == '__main__':
    asyncio.run(main())
