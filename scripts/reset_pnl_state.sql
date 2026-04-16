-- =====================================================================
-- Quenbot: Kümülatif simüle PnL + state sıfırlama
-- ---------------------------------------------------------------------
-- Silinenler:
--   - simulations (tüm simüle edilmiş işlemler)
--   - state_history (PnL snapshot zaman serisi)
--   - bot_state key='main' içindeki kümülatif alanlar sıfırlanır
-- Korunanlar:
--   - signals (sinyal geçmişi ve kartlar)
--   - brain_learning_log (öğrenme kayıtları)
--   - pattern_match_results, trades (piyasa ham verisi)
-- =====================================================================

BEGIN;

-- Önce özet göster
\echo '--- Reset öncesi durum ---'
SELECT
    (SELECT COUNT(*) FROM simulations)                       AS simulations,
    (SELECT COUNT(*) FROM state_history)                     AS state_history_rows,
    (SELECT state_value::jsonb->>'cumulative_pnl'
       FROM bot_state WHERE state_key='main')                AS current_cumulative_pnl;

-- Simülasyonları temizle (ghost simulator in-memory cache restart'ta yenilenir)
TRUNCATE TABLE simulations RESTART IDENTITY CASCADE;

-- State history (dashboard zaman serisi) temizle
TRUNCATE TABLE state_history RESTART IDENTITY;

-- bot_state içindeki kümülatif alanları sıfırla (mode ve forced_mode korunur)
UPDATE bot_state
SET state_value = (
        (state_value::jsonb)
        || jsonb_build_object(
            'cumulative_pnl',      0.0,
            'peak_pnl',            0.0,
            'current_drawdown',    0.0,
            'consecutive_losses',  0,
            'consecutive_wins',    0,
            'daily_pnl',           0.0,
            'daily_trade_count',   0,
            'total_trades',        0,
            'total_wins',          0,
            'signal_type_stats',   '{}'::jsonb,
            'active_symbols',      '[]'::jsonb,
            'last_trade_time',     NULL
        )
    )::text,
    updated_at = CURRENT_TIMESTAMP
WHERE state_key = 'main';

-- Doğrulama
\echo '--- Reset sonrası durum ---'
SELECT
    (SELECT COUNT(*) FROM simulations)                       AS simulations,
    (SELECT COUNT(*) FROM state_history)                     AS state_history_rows,
    (SELECT state_value::jsonb->>'cumulative_pnl'
       FROM bot_state WHERE state_key='main')                AS new_cumulative_pnl,
    (SELECT state_value::jsonb->>'total_trades'
       FROM bot_state WHERE state_key='main')                AS total_trades;

COMMIT;
