# Trading Agent — System Design
**Version:** v5.13 · **Updated:** 2026-05-22

---

## 1. What It Is

An autonomous day-trading system that runs on a daily cron schedule, scans a curated universe of 600+ stocks and ETFs, selects high-probability intraday setups using Claude AI, manages risk through multiple guard layers, and executes bracket orders on Alpaca paper trading.

**Daily objective:** $500–$1,000 realized P&L via disciplined, rules-based position management.

---

## 2. Architecture

```
cron-job.org (external scheduler)
       │
       ▼
GitHub Actions (3 modes: premarket · intraday · EOD)
       │
       ▼
orchestrator.py  ──►  Scanner  ──►  ML Scorer
                 ──►  Strategy Agent (Claude)
                 ──►  Risk Agent
                 ──►  Sector Guard
                 ──►  Guardrails
                 ──►  Portfolio Agent  ──►  Alpaca Paper Trading
                                      ──►  Supabase DB
                                      ──►  Streamlit Dashboard
```

**Stack:** Python 3.11 · Claude claude-sonnet-4-6 · Alpaca Markets API · Supabase (PostgreSQL) · Streamlit Cloud · GitHub Actions

---

## 3. Daily Pipeline

### 3.1 Premarket — 10:00 AM ET

Runs once before market opens (delayed from 9:00 AM to allow spreads to stabilize).

| Step | What Happens |
|------|-------------|
| **0. Market Context** | Fetches VIX, Fear & Greed, US futures, intl markets, economic calendar. Sets `max_positions` and `quiet_day` flag. Skips trading if futures down >1.5%. |
| **1. Scan** | Scores 600+ tickers on RSI, MACD, Bollinger Bands, volume ratio, SMA20/50 trend. Returns candidates with \|score\| ≥ 3. |
| **1.5 News Filter** | Removes earnings-day tickers (blackout). Adds news sentiment context. |
| **1.75 Pre-filter** | Drops bearish candidates (score < 3). Reduces Claude input tokens by ~60%. |
| **1.76 ML Scoring** | Predicts P(stock hits +2% intraday). Re-ranks candidates by ML score. |
| **1.8 Live Prices** | Refreshes entry prices from Alpaca ask quotes (Alpaca mode only). |
| **1.85 VWAP Signals** | Enriches candidates with VWAP position, today's % change, RS vs SPY. |
| **2. Strategy (Claude)** | Selects trades, assigns confidence (HIGH/MEDIUM/LOW), sets entry/target/stop using fixed formulas. |
| **3. Risk Validation** | Enforces R:R floor, position size bounds, max loss per trade. Quiet day: R:R floor drops to 2.0. |
| **3.5 Sector Guard** | Caps exposure at 3 positions per sector. |
| **3.75 Guardrails** | Blocks duplicates, price sanity check (>5% from market = reject), daily loss limit. |
| **4. Execute** | Opens bracket orders in Alpaca. Each trade splits into two legs (partial profit design). |

### 3.2 Intraday — Every 15 min, 10:00 AM–3:45 PM ET

- **Reconcile:** Detects positions closed by Alpaca bracket (stop/target fired). Records real exit price and P&L. `_reconcile_with_alpaca()` in `agents/intraday.py` now fetches with `limit=500` and `after=today_start` to prevent UNFILLED misclassification on busy days where 40+ bracket orders previously exceeded the old 100-order API limit.
- **Refresh:** Syncs current price and unrealized P&L for open positions.
- **Lock-in logic:** Tier 1 ($716 realized) — let winners ride. Tier 2 ($1,000 total) — close everything.

### 3.3 EOD — 4:30 PM ET

- Records daily performance to `daily_performance` table.
- Runs eval against 30-day rolling window.
- Generates daily summary.

---

## 4. Trading Logic

### 4.1 Position Sizing

| Confidence | Size | Trigger |
|------------|------|---------|
| HIGH | $7,000 | Score ≥ 7, volume ratio > 1.8, 3+ signals |
| MEDIUM | $6,000 | Score 4–6 OR (score 3–4 + above VWAP + RS ≥ 1.5) |
| LOW | $5,000 | Score 3–4, weak VWAP/RS |

Confidence is assigned by Claude based on technical score, VWAP position, and relative strength vs SPY.

### 4.2 Trade Formulas (Hard Rules)

```
entry_price   = current ask price (Alpaca) or scanner close price
target_price  = round(entry * 1.02, 2)          # +2% full target
stop_loss     = round(entry * 0.9933, 2)         # -0.67% stop
partial_target = round(entry * 1.01, 2)          # +1% partial exit
```

Reward:Risk = 2% / 0.67% = **2.99 ≈ 3:1** (normal days)  
Reward:Risk floor on quiet days = **2:1** (Fear & Greed < 35)

### 4.3 Partial Profit Design

Each trade opens as **two bracket orders**:

- **Leg A** — half the shares, target = +1%. Locks in profit on smaller moves.
- **Leg B** — remaining shares, target = +2%. Rides the full move.
- Both legs share the same stop price.

**Why:** Converts all-or-nothing bracket outcomes into graduated P&L. On quiet days where 2% moves are rare, Leg A frequently hits while Leg B stops out — net positive vs. net zero under the old design.

### 4.4 Trailing Stop

Manual high-watermark trail runs every 15 min (Alpaca native trailing stops not used — not supported in bracket legs):

```
effective_stop = max(stop_loss, high_watermark × (1 - 1%))
```

After Tier 1 lock-in ($716 realized), trail tightens to 0.5% to protect gains.

---

## 5. Risk Controls

Five independent layers, applied in sequence:

| Layer | What It Blocks |
|-------|---------------|
| **Market Context** | Trading on extreme volatility days (futures < -1.5%) |
| **News Filter** | Earnings-day surprises, negative catalyst stocks |
| **Risk Agent** | R:R below floor, position size out of bounds, stop too wide |
| **Sector Guard** | > 3 positions in any single sector |
| **Guardrails** | Duplicates, price sanity (>5% from market), daily loss limit (-$500 net P&L) |

### 5.1 Quiet Day Mode

Triggered when Fear & Greed Index < 35.

- R:R floor relaxed: 3.0 → 2.0
- Confidence criteria adjusted: above-VWAP + RS ≥ 1.5 qualifies for MEDIUM even at technical score 3–4
- Max positions unchanged — quality filter still applies

**Rationale:** At 80%+ win rate (validation target), 2:1 R:R has strongly positive expected value. Quiet markets produce more near-miss trades (R:R 2.9x) that the 3.0 floor was incorrectly rejecting.

---

## 6. Key Configuration

| Parameter | Value | Purpose |
|-----------|-------|---------|
| `TOTAL_CAPITAL` | $100,000 | Simulated account size |
| `TARGET_PCT` | 2% | Profit target per trade |
| `MAX_LOSS_PER_TRADE` | 0.67% | Stop loss depth |
| `MIN_REWARD_RISK` | 3.0 | Normal day R:R floor |
| `QUIET_DAY_MIN_REWARD_RISK` | 2.0 | Quiet day R:R floor |
| `QUIET_DAY_FG_THRESHOLD` | 35 | Fear & Greed threshold for quiet day |
| `PARTIAL_PROFIT_PCT` | 1% | Partial exit target (Leg A) |
| `TRAIL_PCT` | 1% | Trailing stop from high watermark |
| `DAILY_LOCK_IN_TARGET` | $716 | Tier 1: let winners ride |
| `DAILY_BONUS_TARGET` | $1,000 | Tier 2: close everything |
| `DAILY_LOSS_LIMIT` | -$500 | Pause new entries if net P&L (realized + unrealized) drops below (1% of capital) |
| `MAX_POSITIONS` | 15 | Max concurrent positions |
| `MAX_PER_SECTOR` | 3 | Sector concentration cap |
| `SCORE_THRESHOLD` | 3 | Minimum scanner score (absolute value) |
| `STRATEGY_MIN_SCORE` | 3 | Pre-filter before Claude call (bullish only) |
| `MIN_AVG_VOLUME` | 1,000,000 | Liquidity floor |

---

## 7. Universe

**~665 tickers** (dynamic + static merged):

- **S&P 500 (~503):** Live list fetched from Wikipedia monthly via `universe_refresh`; written to `config/sp500_tickers.json` as fallback. Ensures quality floor (market cap, float, liquidity minimums).
- **Non-leveraged ETFs (41):** Sector, broad market, and thematic ETFs; no leveraged or inverse ETFs.
- **Dynamic:** Top ATR movers from the S&P 500 pool, refreshed monthly. Sorted by volatility, prepended to static list.

Merge strategy: dynamic first (highest ATR movers lead), static appended, deduplicated. Ensures broad coverage while surfacing active names.

---

## 8. Data Flow

```
Scanner (yfinance / Alpaca fallback)
  → technical scores, OHLCV, RSI, MACD, Bollinger, SMA
  → Alpaca live ask prices (refresh step)
  → Alpaca VWAP, RS vs SPY, today % change (intraday signals)
  → ML model score (P(hit +2%) — XGBoost, trained on 6 months)

Claude (claude-sonnet-4-6)
  → selects trades, sets confidence, writes reasoning
  → prompt cache: ~1,745 tokens stored, reused across calls

Alpaca Paper Trading
  → bracket orders: limit entry + limit take-profit + stop-loss
  → two orders per trade (partial profit design)
  → reconciliation: intraday agent reads filled sell orders

Supabase
  → positions, planned_trades, trade_plans, scan_results, daily_performance
  → daily_runs: one row per scan event (premarket run_number=0, intraday 1-6)
  → positions.run_id FK → daily_runs.id (links each position to its scan)
  → scan_results used as premarket run lock (duplicate prevention)

Streamlit Dashboard
  → 5 tabs: Summary · Today · Positions · Performance · Scan Log
```

---

## 9. Validation Gate

**Run on June 1, 2026:** `python3 eval.py --days 14`

| Criterion | Threshold |
|-----------|-----------|
| Win rate | ≥ 80% |
| Avg daily P&L | ≥ $500 |
| No double-sell events | Confirmed |
| No integrity flags | Clean |
| Confidence score | ≥ 7/10 |

**Do not deploy real capital until all criteria pass.**

---

## 10. Post-June-1 Roadmap

| Priority | Feature |
|----------|---------|
| P0 | P&L reconciliation: Alpaca `account.equity` as source of truth + friction breakdown |
| P1 | Intraday trade entries: second scan at 12:30 PM, momentum-only, guardrailed by open position count and daily P&L |

---

## 11. Strategy B — Overview

Strategy B is a companion live paper-trading system operating in parallel on the same Alpaca paper account. It uses a fundamentally different selection philosophy: instead of scanning 600+ tickers broadly, it maintains a curated behavioral pool of ~150 blue chip stocks and narrows to 8–10 daily elite picks using real-time signals.

**Repo:** `trading-agent-b/` (separate GitHub repository, independent deployment)

**Core idea:** Pre-qualify stocks through behavioral scoring before Claude ever sees them. Every candidate Claude receives in Strategy B has a `rolling_score` — a 7-day track record of how well that specific stock's behavior has matched this strategy's setup requirements.

### 11.1 A vs B Comparison

| Dimension | Strategy A | Strategy B |
|-----------|-----------|-----------|
| **Universe** | 600+ tickers (S&P 500 + ETFs) | ~150 blue chip large caps (Pool 1 → Pool 3) |
| **Daily candidates** | All scored tickers (20–60 after filter) | 8–10 Pool 3 daily elite picks |
| **Capital** | $100,000 | $50,000 |
| **Position sizes** | $7K / $6K / $5K (HIGH/MED/LOW) | $3.5K / $3K / $2.5K (HIGH/MED/LOW) |
| **Profit target** | +2% (premarket and intraday) | +2% premarket · +1% intraday entries |
| **Stop loss** | −0.67% | −0.67% |
| **R:R floor** | 3.0 (normal) / 2.0 (quiet day) | 2.0 (always) |
| **Max positions** | 15 | 10 |
| **Claude model** | claude-sonnet-4-6 | claude-opus-4-7 |
| **Candidate context** | RSI, MACD, BB, volume, VWAP, RS vs SPY | All of A + `pool`, `rolling_score`, `rs_vs_sector`, `atr_ratio`, `signal_type` |
| **Pool system** | None | 3-tier: Pool 1 (universe) → Pool 2 (behavioral shortlist) → Pool 3 (daily picks) |
| **Intraday scan** | Full pipeline, any scored ticker | Pool 3 movers only, SPY gate, max 6 runs, 90 min interval |
| **EOD scoring** | Daily performance summary | Pool Scorer: scores each Pool 3 stock, promotes/demotes between pools |
| **ML scorer** | XGBoost P(hit +2%), AUC 0.78 | None in Phase 1 (pool behavioral score is the equivalent) |

---

## 12. Shared Infrastructure

Both strategies share the same underlying infrastructure with clear separation by convention.

### 12.1 Supabase

Same Supabase project (same credentials, same PostgreSQL instance). Strategy A uses unprefixed tables (`positions`, `trade_plans`, `planned_trades`, `daily_performance`, `daily_runs`, `scan_results`). Strategy B uses `b_` prefixed tables (`b_positions`, `b_trade_plans`, `b_planned_trades`, `b_daily_performance`, `b_daily_runs`, `b_pools`, `b_stock_scores`).

**Dashboard** queries both schemas: the Streamlit dashboard includes tabs for both strategies and can display side-by-side P&L comparisons.

### 12.2 Alpaca

Same Alpaca paper trading account. All Strategy B orders are tagged with `strategy=b` at order placement. This enables per-strategy P&L reconciliation from Alpaca order history. When deploying to a real-money account, the tag convention allows clean separation without account duplication.

### 12.3 External Cron (cron-job.org)

Both strategies use cron-job.org to fire `workflow_dispatch` events on GitHub Actions. GitHub Actions' built-in cron scheduler can lag 5–15 minutes on busy runners, which is unacceptable for premarket and intraday timing. cron-job.org fires on time and GitHub Actions runs the actual workload.

**Current schedules (UTC):**
- Intraday: `0,15,30 14-19` (Strategy A every 15 min) / `0,30 14-20` (Strategy B every 30 min)
- EOD: `55 19` (both strategies, staggered by 5 min to avoid runner contention)

**Important:** Any change to GitHub Actions YAML schedule times must also update the corresponding cron-job.org configuration. The two are not linked — they must be kept in sync manually.

### 12.4 GitHub Actions Structure

Both repositories use the same 3-mode workflow pattern: `premarket`, `intraday`, `eod`. The `mode` input is passed as a workflow dispatch parameter and cron-job.org fires each mode independently at the correct scheduled time. The orchestrator reads `mode` and routes to the appropriate function (`orchestrator.premarket()`, `orchestrator.intraday()`, `orchestrator.eod()`).
