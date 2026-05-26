# Trading Agent — System Design
**Version:** v5.22 · **Updated:** 2026-05-26

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
| **0. Market Context** | Fetches VIX, Fear & Greed, US futures, intl markets, economic calendar, and sector rotation (11 sector ETFs ranked by today's return). Sets `max_positions` and `quiet_day` flag. Skips trading if futures down >1.5%. |
| **1. Scan** | Scores ~450 tickers on RSI, MACD, Bollinger Bands, volume ratio, SMA20/50 trend, breakout freshness. Applies bid-ask spread filter (>0.5% → skip), pre-market gap filter (abs(gap) >8% → skip), intraday range filter (avg H-L/O >5% → skip), and ATR quality gate (ATR% >5% → skip — ATR sizer would produce R:R<1 for these). Enriches passing candidates with ORB and intraday VWAP signals from 5-min bars. Returns candidates with \|score\| ≥ 4. |
| **1.5 News Filter** | Removes earnings-day tickers (today or tomorrow blackout). Adds news sentiment context. |
| **1.75 Pre-filter** | Drops bearish candidates (score < `PREMARKET_MIN_SCORE` = 5). Higher bar than intraday (4) because premarket candidates haven't proved today's move yet. Reduces Claude input tokens by ~60%. |
| **1.76 ML Scoring** | Predicts P(stock hits +2% intraday). Re-ranks candidates by ML score. |
| **1.8 Live Prices** | Refreshes entry prices from Alpaca ask quotes (Alpaca mode only). |
| **1.85 VWAP Signals** | Enriches candidates with VWAP position, today's % change, RS vs SPY. |
| **2. Strategy (Claude)** | Selects trades, assigns confidence (HIGH/MEDIUM/LOW), sets entry/target/stop using fixed formulas. |
| **3. Risk Validation** | Enforces R:R floor, position size bounds, max loss per trade. Quiet day: R:R floor drops to 2.0. |
| **3.5 Sector Guard** | Caps exposure at 3 positions per sector. |
| **3.6 ATR Sizer (P0)** | Replaces fixed 0.67% formula stop with ATR-based stop (`max(ATR × 1.2, 0.5%)`). Computes shares from constant $150 dollar risk so max loss is predictable regardless of stop width. ORB gate: if first-30-min opening range < 0.5 × ATR (choppy open), halve shares. Trades where ATR stop ≥ target (R:R < 1) are dropped. Trades with no ATR data pass through unchanged. |
| **3.75 Guardrails** | Blocks duplicates, price sanity check (>5% from market = reject), daily loss limit. |
| **4. Execute** | Opens bracket orders in Alpaca. Each trade splits into two legs (partial profit design). |

### 3.2 Intraday — Every 15 min, 10:00 AM–3:45 PM ET

- **Reconcile:** Detects positions closed by Alpaca bracket (stop/target fired). Records real exit price and P&L. `_reconcile_with_alpaca()` in `agents/intraday.py` fetches with `limit=500` and `after=today_start` to prevent UNFILLED misclassification on busy days.
- **Intraday scan guards (checked in order):**
  - UTC hour in window, max `INTRADAY_SCAN_MAX_RUNS` runs, min interval since last run
  - Open position count below `MAX_POSITIONS` (15)
  - **Daily entry cap:** total positions opened today (including closed ones) below `MAX_DAILY_ENTRIES` (12). Prevents 50-position blowups on volatile days where stops free concurrent slots faster than the cap can guard.
  - Net P&L above `DAILY_LOSS_LIMIT`, not already at `DAILY_BONUS_TARGET`
- **Sector conviction:** Before calling Claude, fetches live performance of 7 sector ETFs (XLK, XLF, XLE, XLV, XLI, XLC, XLY). Hot sectors (up >= `STRONG_SECTOR_THRESHOLD` = 2%) are highlighted as priorities; weak sectors (down >= 1%) are flagged for avoidance. Injected into the strategy prompt so Claude biases toward the sectors actually moving today.
- **Intraday pre-filter:** Score >= `STRATEGY_MIN_SCORE` = 4 (lower than premarket's 5 — live momentum signals provide extra confirmation).
- **Approved trades** pass through sector guard (MAX_PER_SECTOR cap) and ATR sizer before `open_positions`.
- **Hard stop safety net:** If `current_price < stop_loss` and the position is still in Alpaca after a 5-minute grace period, `portfolio.refresh_positions()` forces a market close via `close_position()`. Prevents positions that slip through the bracket order from bleeding beyond the stop.
- **Phantom STOP prevention:** When a position disappears from Alpaca with no fill price, it is classified as UNFILLED ($0 P&L) instead of STOP. Prevents the exit count and P&L statistics from being distorted by entries that never executed.
- **Refresh:** Syncs current price and unrealized P&L for open positions.
- **Lock-in logic:** Tier 1 ($716 realized) — let winners ride. Tier 2 ($1,000 total) — close everything.

### 3.3 EOD — 4:30 PM ET

- **Dedup guard:** checks `scan_results` for `run_eod_started` before proceeding — prevents double-run if GitHub Actions fires twice.
- **Observability:** `_log_run("eod", "started/completed/failed")` writes a status record to `scan_results`; EOD sends an email alert via `core/alerts.py` if positions are still open after close, or if the run crashes.
- **Phantom STOP cleanup:** Scans today's CLOSED positions with `close_reason=STOP` and no `fill_price`. Reclassifies them as UNFILLED to keep stop-exit counts and P&L statistics accurate.
- Records daily performance to `daily_performance` table.
- Runs eval against 30-day rolling window.
- Generates daily summary.

---

## 4. Trading Logic

### 4.1 Position Sizing

| Confidence | Size | Trigger |
|------------|------|---------|
| HIGH | $3,500 | Score ≥ 7, volume ratio > 1.8, 3+ signals |
| MEDIUM | $3,000 | Score 5–6 OR (score 4–5 + above VWAP + RS ≥ 1.5) |
| LOW | $2,500 | Score 4–5, weak VWAP/RS |

Confidence is assigned by Claude based on technical score, VWAP position, and relative strength vs SPY.

### 4.2 Trade Formulas (Hard Rules)

```
entry_price    = current ask price (Alpaca) or scanner close price
target_price   = round(entry * 1.04, 2)          # +4% ceiling (limit order on Leg B)
partial_target = round(entry * 1.01, 2)           # +1% partial exit (Leg A)

# ATR-based stop (P0) — applied by atr_sizer.py after sector guard:
stop_pct    = max(atr_pct * 1.2, 0.5%)           # stop is outside the noise band
stop_loss   = round(entry * (1 - stop_pct), 2)
shares      = min(int($150 / (entry * stop_pct)), int(position_size_cap / entry))
```

Reward:Risk = 4% / stop_pct (varies by ATR; typically 2.2:1–6.0:1)  
Reward:Risk floor on quiet days = **2:1** (Fear & Greed < 35)  
Trades with stop_pct ≥ 4% (ATR so wide that stop ≥ target) are dropped before execution.

### 4.3 Partial Profit Design

Each trade opens as **two bracket orders**:

- **Leg A** — half the shares, target = +1%. Locks in profit on smaller moves.
- **Leg B** — remaining shares, target = +4% ceiling. Rides the full move with native trailing stop.
- Both legs share the same stop price.

**Why:** Converts all-or-nothing bracket outcomes into graduated P&L. On quiet days where large moves are rare, Leg A frequently hits while Leg B trails out positive — net positive vs. net zero under the old design.

### 4.4 Native Trailing Stop

Alpaca's native trailing stop tracks the intraday peak in real-time and fires immediately on a 1% reversal from the high — no polling gap.

```
exit triggered when: price ≤ peak_since_entry × (1 − 1%)
```

**Why native over manual polling:** The previous manual high-watermark trail checked every 15 min. A stock that peaks at +1.8% and drops 1% within a single 15-min window would exit at the next poll — potentially at +0.5% or worse. Native trail fires the moment the 1% reversal occurs.

**The ceiling (2.5% limit order) and trail work together:**

```
Native trail (1% from peak)  →  exits most winning trades between +0.5% and +2.4%
2.5% limit order (ceiling)   →  captures strong momentum runs that push through
0.67% stop                   →  hard floor, unchanged
```

The ceiling is not a "close here" target in the traditional sense — it only fills if momentum is strong enough to push through. On most trades the trail does the work. The ceiling prevents leaving money on the table when a stock genuinely wants to run.

**Breakeven lock after Leg A closes:**  
When Leg A hits +1%, Leg B's stop is resubmitted at entry price (breakeven). The resubmit explicitly passes `use_native_trail=True` and `trail_pct` so Leg B continues trailing from breakeven rather than reverting to a fixed stop.

**After Tier 1 lock-in ($716 realized):** trail tightens to 0.5% on remaining open positions to protect the day's gains.

---

## 5. Risk Controls

Five independent layers, applied in sequence:

| Layer | What It Blocks |
|-------|---------------|
| **API Resilience** | Anthropic API timeouts/errors — 3-attempt retry with 15/30/45 s backoff; returns empty trades on total failure (no crash) |
| **Market Context** | Trading on extreme volatility days (futures < -1.5%) |
| **News Filter** | Earnings-day surprises, negative catalyst stocks |
| **ATR Quality Gate** | Scanner drops tickers with ATR% >5% before they reach Claude |
| **Risk Agent** | R:R below floor, position size out of bounds, stop too wide |
| **Sector Guard** | > 3 positions in any single sector (premarket and intraday scans) |
| **ATR Sizer** | Drops trades where ATR stop ≥ target (R:R < 1); applies ORB halving on choppy opens |
| **Guardrails** | Duplicates, price sanity (primary: >3% from live market; secondary: entry >25% from 30d historical avg to catch corrupted scanner data where both live price and entry are wrong), daily loss limit (-$500 net P&L) |
| **Fill Poll** | `submit_bracket_order` polls 15 s for fill confirmation; returns (None, None) on rejection so phantom positions are never written to DB |

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
| `TOTAL_CAPITAL` | $50,000 | Simulated account size |
| `TARGET_PCT` | 4% | Ceiling limit order on Leg B — only fills on straight-line momentum runs; trail exits earlier in most trades |
| `MAX_LOSS_PER_TRADE` | 0.67% | Formula stop (Claude's prompt reference; overridden by ATR sizer at runtime) |
| `ATR_STOP_MULTIPLIER` | 1.2 | ATR-based stop multiplier: stop = max(ATR × 1.2, 0.5%) |
| `ATR_STOP_FLOOR` | 0.5% | Minimum stop regardless of ATR — never tighter than this |
| `MAX_LOSS_DOLLARS` | $150 | Constant dollar risk per trade; shares = $150 / (entry × stop_pct) |
| `ORB_ATR_FLOOR` | 0.5 | ORB/ATR ratio below which the open is deemed choppy → halve shares |
| `MIN_REWARD_RISK` | 2.9 | Normal day R:R floor |
| `QUIET_DAY_MIN_REWARD_RISK` | 2.0 | Quiet day R:R floor |
| `QUIET_DAY_FG_THRESHOLD` | 35 | Fear & Greed threshold for quiet day |
| `PARTIAL_PROFIT_PCT` | 1% | Partial exit target (Leg A) |
| `USE_NATIVE_TRAILING_STOP` | True | Alpaca native trail — fires immediately on 1% reversal, no polling gap |
| `TRAIL_PCT` | 1.5% | Trail percentage from intraday peak (widened from 1% — 1% fired on normal chop before capturing the move) |
| `DAILY_LOCK_IN_TARGET` | $716 | Tier 1: let winners ride |
| `DAILY_BONUS_TARGET` | $1,000 | Tier 2: close everything |
| `DAILY_LOSS_LIMIT` | -$500 | Pause new entries if net P&L (realized + unrealized) drops below (1% of capital) |
| `MAX_POSITIONS` | 15 | Max concurrent open positions |
| `MAX_DAILY_ENTRIES` | 12 | Hard cap on total new positions opened per calendar day (includes closed ones) — prevents blowout days where stops free slots faster than the per-cycle guard can catch |
| `MAX_PER_SECTOR` | 3 | Sector concentration cap per scan batch |
| `SCORE_THRESHOLD` | 4 | Minimum scanner score (absolute value) — raised from 1 to cut noise |
| `PREMARKET_MIN_SCORE` | 5 | Pre-filter before Claude call at premarket — candidates haven't proved today's move yet |
| `STRATEGY_MIN_SCORE` | 4 | Pre-filter before Claude call at intraday — live momentum signals provide extra confirmation so the bar is lower |
| `STRONG_SECTOR_THRESHOLD` | 2.0% | Sector ETF up >= this — highlighted as hot in strategy prompt; Claude prioritizes stocks in this sector |
| `WEAK_SECTOR_THRESHOLD` | -1.0% | Sector ETF down >= 1% — flagged as weak; scanner applies -1 penalty to stocks in this sector |
| `MIN_AVG_VOLUME` | 1,000,000 | Liquidity floor |
| `MAX_SPREAD_PCT` | 0.5% | Bid-ask spread cap — wider spreads erode the 0.67% stop |
| `MAX_PREMARKET_GAP_PCT` | 8% | Pre-market gap cap — stocks already extended >8% can't reach 2.5% more |
| `MAX_INTRADAY_RANGE_PCT` | 5% | Avg daily H-L range cap — prevents stop-noise on hyper-volatile names |
| `MAX_ATR_PCT` | 5% | ATR quality gate in scanner — skips tickers where ATR sizer would produce R:R < 1 |
| `LARGE_CAP_AVG_VOLUME` | 15,000,000 | Volume above which the ratio threshold is relaxed for mega-caps |

---

## 7. Universe

**~450 tickers** (dynamic + static merged):

- **Large-cap stocks (~410):** Curated across mega-cap tech, semiconductors, software/AI, fintech, biotech, consumer, energy, financials, industrials, materials, utilities, and communication sectors. Junk tickers removed: crypto miners (MARA, RIOT, HUT, etc.), speculative quantum plays (RGTI, QUBT, QBTS), and small-cap biotech with insufficient liquidity. IONQ and RXRX retained (portfolio holdings).
- **Non-leveraged ETFs (41):** Sector, broad market, and thematic ETFs; no leveraged or inverse ETFs.
- **Dynamic:** Top ATR movers from the live S&P 500 list, refreshed monthly by `universe_refresh`. Sorted by ATR%, prepended to static list.

**Refresh pipeline (`agents/universe_refresh.py`, runs 1st of each month):**
1. Fetches live S&P 500 constituents from Wikipedia; writes `config/sp500_tickers.json` as fallback
2. Screens all tickers: price $5–$2000, avg volume ≥500K/day, ATR% ≥0.5%
3. Sorts survivors by ATR% descending (highest movers lead)
4. Writes `config/universe_cache.json` — committed to the repo by the workflow

**`load_universe()` at premarket (no Supabase call):**
- Reads `config/universe_cache.json` directly (local file, zero network latency)
- Cache valid for 35 days — survives a missed monthly run
- Falls back to `settings.py` static list if cache is missing or stale

**Retry strategy (`.github/workflows/universe_refresh.yml`):**
- 3 attempts per run with exponential backoff: 60s → 120s → 240s
- Fallback cron schedules on 2nd and 3rd of each month if 1st fails
- Skip step on 2nd/3rd: exits immediately if cache is < 25 days old (prevents re-runs after a success)

---

## 8. Data Flow

```
Scanner (yfinance / Alpaca fallback)
  → technical scores, OHLCV, RSI, MACD, Bollinger, SMA20/50
  → breakout freshness: FRESH (0-5% above SMA20 = +1), EXTENDED (>12% = -1), NORMAL
  → bid-ask spread filter: reject if (ask-bid)/ask > 0.5%
  → pre-market gap filter: reject if abs(preMarketPrice/prevClose - 1) > 8%
  → intraday range filter: reject if avg(H-L)/O > 5% (stops stop-noise)
  → ORB signal: above_orb = price > first-30-min high (5-min bars)
  → intraday VWAP: cumulative TP×V / cumV from 5-min bars; vwap_reclaim flag
  → Alpaca live ask prices (refresh step)
  → Alpaca VWAP, RS vs SPY, today % change (intraday signals)
  → ML model score (P(hit +2%) — XGBoost, trained on 6 months)

Market Context (parallel fetch)
  → VIX, Fear & Greed, US futures, intl markets, economic calendar
  → Sector rotation: 11 sector ETFs ranked by today's return (leading/lagging summary for Claude)

Claude (claude-sonnet-4-6)
  → selects trades, sets confidence, writes reasoning
  → prompt cache: ~1,745 tokens stored, reused across calls

Alpaca Paper Trading
  → bracket orders: limit entry + limit take-profit + stop-loss
  → two orders per trade (partial profit design)
  → reconciliation: intraday agent reads filled sell orders

Universe Cache (local file — no DB call at premarket)
  → config/universe_cache.json: ATR-sorted S&P 500 + ETFs, written monthly by universe_refresh
  → config/sp500_tickers.json: raw S&P 500 list fallback (Wikipedia fetch)
  → config/settings.py UNIVERSE: static hardcoded fallback if both files are stale/missing

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

**Run on June 8, 2026:** `python3 eval.py --days 14`

| Criterion | Threshold |
|-----------|-----------|
| Win rate | ≥ 80% |
| Avg daily P&L | ≥ $500 |
| No double-sell events | Confirmed |
| No integrity flags | Clean |
| Confidence score | ≥ 7/10 |

**Do not deploy real capital until all criteria pass.**

---

## 10. Changelog

| Version | Date | Changes |
|---------|------|---------|
| **v5.22** | 2026-05-26 | Six root-cause fixes: (1) `MAX_DAILY_ENTRIES=12` hard cap on total daily positions — prevents 50-position blowups when stops free concurrent slots mid-day; (2) Sector conviction prompt — live sector ETF performance injected into strategy call so Claude prioritizes hot sectors and avoids weak ones; (3) Hard stop safety net — `portfolio.refresh_positions()` force-closes via `close_position()` if price < stop_loss after 5-min grace; (4) 30d historical avg secondary sanity — cross-checks entry against yfinance 30d mean to catch corrupted scanner data (MU at $907 vs actual ~$115); (5) Phantom STOP reclassification — positions without fill_price marked UNFILLED (not STOP) at EOD and during portfolio refresh; (6) Split min-score: `PREMARKET_MIN_SCORE=5` vs `STRATEGY_MIN_SCORE=4` — intraday bar is lower because live momentum signals provide extra confirmation |
| **v5.21** | 2026-05-23 | Structural gap fixes: (1) Gap 1 — Anthropic retry: 3-attempt with 15/30/45 s backoff in `agents/strategy.py`; (2) Gap 2 — bracket exit reconciliation: `agents/intraday.py` `_reconcile_with_alpaca()` now calls `get_order_fill()` on filled_buys and writes close price + P&L to DB; (3) Gap 5 — EOD dedup guard (`run_eod_started` check in `scan_results`); intraday guard skips if no premarket scan today; (4) Gap 6 — `core/alerts.py` (Gmail SMTP); `_log_run()` writes start/complete/failed records; EOD alerts on crash or unclosed positions; `from __future__ import annotations` for Python 3.9 compat |
| **v5.20** | 2026-05-23 | Friction gap reconciliation: `STRATEGY_TAG="a"` in settings; bracket orders tagged `strata_{ticker}_{ts}`; `_alpaca_order_pnl()` in performance.py computes per-strategy P&L from Alpaca order history; `friction_gap` now meaningful (per-strategy, not combined A+B equity) |
| **v5.19** | 2026-05-23 | P1 fixes: (1) fill rate — `submit_bracket_order` polls 15 s for fill confirmation, returns `(order_id, fill_price)` tuple, blocks DB write on rejection/timeout; `fill_price` stored on positions; (2) friction breakdown — `performance.py` computes `avg_slippage_bps` / `fills_with_data` from entry vs fill price; (3) close_price=0.0 fix — replaced falsy `or` chain with `is None` check; (4) ATR quality gate — `MAX_ATR_PCT=5%` filter in scanner drops tickers before ATR sizer rejects them; (5) intraday completeness — sector guard + ATR sizer wired into `_maybe_run_intraday_scan`, market note updated |
| **v5.18** | 2026-05-23 | P0: ATR-based stop (P0-1) and ORB choppiness gate (P0-2) |

---

## 11. Strategy B — Overview

Strategy B is a companion live paper-trading system operating in parallel on the same Alpaca paper account. It uses a fundamentally different selection philosophy: instead of scanning 600+ tickers broadly, it maintains a curated behavioral pool of ~150 blue chip stocks and narrows to 8–10 daily elite picks using real-time signals.

**Repo:** `trading-agent-b/` (separate GitHub repository, independent deployment)

**Core idea:** Pre-qualify stocks through behavioral scoring before Claude ever sees them. Every candidate Claude receives in Strategy B has a `rolling_score` — a 7-day track record of how well that specific stock's behavior has matched this strategy's setup requirements.

### 11.1 A vs B Comparison

| Dimension | Strategy A | Strategy B |
|-----------|-----------|-----------|
| **Universe** | ~450 tickers (curated large-caps + ETFs) | ~150 blue chip large caps (Pool 1 → Pool 3) |
| **Daily candidates** | Scored tickers with \|score\| ≥ 4 (20–50 after filter) | 8–10 Pool 3 daily elite picks |
| **Capital** | $50,000 | $50,000 |
| **Position sizes** | $3.5K / $3K / $2.5K (HIGH/MED/LOW) | $3.5K / $3K / $2.5K (HIGH/MED/LOW) |
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
