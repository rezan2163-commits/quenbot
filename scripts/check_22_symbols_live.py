import asyncio
import asyncpg
import os
from datetime import datetime, timezone

DB = os.getenv('DATABASE_URL', 'postgresql://user:password@localhost:5432/trade_intel')


async def main() -> None:
    print('connect_start')
    conn = await asyncpg.connect(DB, timeout=8)
    await conn.execute("SET statement_timeout = '7000ms'")
    print('connect_ok')
    try:
        watch = await conn.fetch(
            """
            SELECT DISTINCT UPPER(symbol) AS symbol
            FROM user_watchlist
            WHERE active = TRUE
            ORDER BY 1
            """
        )
        symbols = [r['symbol'] for r in watch]

        by_symbol = {}
        for symbol in symbols:
            c10 = await conn.fetchval(
                """
                SELECT COUNT(*)::int
                FROM trades
                WHERE symbol = $1
                  AND timestamp >= NOW() - INTERVAL '10 minutes'
                """,
                symbol,
            )
            c2 = await conn.fetchval(
                """
                SELECT COUNT(*)::int
                FROM trades
                WHERE symbol = $1
                  AND timestamp >= NOW() - INTERVAL '2 minutes'
                """,
                symbol,
            )
            last_ts = await conn.fetchval(
                """
                SELECT timestamp
                FROM trades
                WHERE symbol = $1
                ORDER BY timestamp DESC
                LIMIT 1
                """,
                symbol,
            )
            by_symbol[symbol] = {
                'c10': int(c10 or 0),
                'c2': int(c2 or 0),
                'last_ts': last_ts,
            }
        now = datetime.now(timezone.utc).replace(tzinfo=None)

        missing_10 = []
        stale_120 = []
        ok = []

        for symbol in symbols:
            row = by_symbol.get(symbol)
            if not row or row['c10'] == 0 or row['last_ts'] is None:
                missing_10.append(symbol)
                continue

            age = int((now - row['last_ts']).total_seconds())
            item = (symbol, age, row['c10'], row['c2'])
            if age > 120:
                stale_120.append(item)
            else:
                ok.append(item)

        print(f'watchlist_count={len(symbols)}')
        print(f'ok_fresh<=120s={len(ok)}')
        print(f'missing_last10m={len(missing_10)}')
        print(f'stale_over120s={len(stale_120)}')

        if missing_10:
            print('missing_symbols=' + ','.join(missing_10))
        if stale_120:
            print(
                'stale_symbols='
                + ';'.join(
                    [f"{s}:{age}s(c10={c10},c2={c2})" for s, age, c10, c2 in stale_120]
                )
            )

        top = sorted(ok + stale_120, key=lambda x: x[1])[:22]
        print(
            'per_symbol=' + ';'.join([f"{s}:{age}s(c10={c10},c2={c2})" for s, age, c10, c2 in top])
        )
    finally:
        await conn.close()


if __name__ == '__main__':
    asyncio.run(main())
