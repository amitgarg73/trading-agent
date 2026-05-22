-- Migration 002: add scanner signal columns to planned_trades
-- Run once in the Supabase SQL editor

ALTER TABLE planned_trades
  ADD COLUMN IF NOT EXISTS technical_score integer,
  ADD COLUMN IF NOT EXISTS rsi             numeric,
  ADD COLUMN IF NOT EXISTS volume_ratio    numeric,
  ADD COLUMN IF NOT EXISTS scanner_signals jsonb;

COMMENT ON COLUMN planned_trades.technical_score IS 'Scanner score at time of selection (-10 to +10)';
COMMENT ON COLUMN planned_trades.rsi              IS 'RSI at time of selection';
COMMENT ON COLUMN planned_trades.volume_ratio     IS 'Volume ratio vs 20-day avg at time of selection';
COMMENT ON COLUMN planned_trades.scanner_signals  IS 'Full signals list from scanner (JSON array)';
