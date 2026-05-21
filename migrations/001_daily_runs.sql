-- Migration 001: daily_runs table + run_id on positions
-- Run once in the Supabase SQL editor, then run validate_daily_runs.py

-- 1. Per-scan run ledger (one row per scan event that opens positions)
create table if not exists daily_runs (
  id              uuid primary key default gen_random_uuid(),
  date            date        not null,
  run_type        text        not null check (run_type in ('premarket', 'intraday')),
  run_number      integer     not null default 0,  -- 0=premarket, 1-6=intraday
  started_at      timestamptz not null default now(),
  positions_opened integer    not null default 0,
  loss_guard_active boolean   not null default false,
  created_at      timestamptz not null default now(),
  unique (date, run_number)
);

create index if not exists idx_daily_runs_date on daily_runs(date);

-- 2. Link positions → run that opened them (nullable — NULL for pre-migration rows)
alter table positions add column if not exists run_id uuid references daily_runs(id);
create index if not exists idx_positions_run_id on positions(run_id);
