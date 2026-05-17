-- Run this in your Supabase SQL editor to set up the schema

-- Daily trade plans (generated premarket)
create table trade_plans (
    id          uuid primary key default gen_random_uuid(),
    date        date not null unique,
    market_context text,
    total_estimated_profit numeric,
    risk_note   text,
    status      text default 'ACTIVE',  -- ACTIVE, COMPLETED
    created_at  timestamptz default now()
);

-- Individual planned trades within a plan
create table planned_trades (
    id              uuid primary key default gen_random_uuid(),
    plan_id         uuid references trade_plans(id),
    ticker          text not null,
    action          text not null,   -- BUY, SELL_SHORT
    entry_price     numeric,
    target_price    numeric,
    stop_loss       numeric,
    position_size   numeric,
    shares          integer,
    estimated_profit numeric,
    confidence      text,            -- HIGH, MEDIUM, LOW
    reasoning       text,
    status          text default 'PLANNED',  -- PLANNED, OPEN, CLOSED, CANCELLED
    created_at      timestamptz default now()
);

-- Simulated open/closed positions
create table positions (
    id                  uuid primary key default gen_random_uuid(),
    planned_trade_id    uuid references planned_trades(id),
    ticker              text not null,
    action              text not null,
    entry_price         numeric not null,
    current_price       numeric,
    target_price        numeric,
    stop_loss           numeric,
    shares              integer,
    position_size       numeric,
    unrealized_pnl      numeric default 0,
    status              text default 'OPEN',  -- OPEN, CLOSED
    opened_at           timestamptz default now(),
    closed_at           timestamptz,
    close_price         numeric,
    realized_pnl        numeric,
    close_reason        text   -- TARGET, STOP, EOD, MANUAL
);

-- Daily P&L performance
create table daily_performance (
    id                  uuid primary key default gen_random_uuid(),
    date                date not null unique,
    starting_capital    numeric default 100000,
    ending_capital      numeric,
    total_pnl           numeric,
    win_count           integer default 0,
    loss_count          integer default 0,
    total_trades        integer default 0,
    win_rate            numeric,
    best_trade_ticker   text,
    best_trade_pnl      numeric,
    worst_trade_ticker  text,
    worst_trade_pnl     numeric,
    notes               text,
    created_at          timestamptz default now()
);

-- Raw scan results (audit trail)
create table scan_results (
    id          uuid primary key default gen_random_uuid(),
    date        date not null,
    scan_type   text,    -- premarket, intraday
    results     jsonb,
    created_at  timestamptz default now()
);

-- Indexes
create index on planned_trades(plan_id);
create index on positions(status);
create index on positions(ticker);
create index on daily_performance(date desc);
create index on scan_results(date desc);
