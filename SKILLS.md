# Trading Agent — Runnable Skills

All commands run from the project root: `/Users/amitgarg/Claude Projects/trading-agent/`

Requires `.env` with all secrets set (see `.env.example`).

---

## Full Pipeline

### `orchestrator.py` — Run any pipeline mode
```bash
python3 orchestrator.py --mode premarket   --broker alpaca      # Full morning run
python3 orchestrator.py --mode intraday    --broker alpaca      # Position sync
python3 orchestrator.py --mode eod         --broker alpaca      # EOD close + P&L
python3 orchestrator.py --mode premarket   --broker simulation  # Dry run (no Alpaca orders)
```
| Flag | Options | Default |
|------|---------|---------|
| `--mode` | `premarket`, `intraday`, `eod` | required |
| `--broker` | `alpaca`, `simulation` | `simulation` |

---

## Individual Agent Skills

### `agents/market_context.py` — Check market conditions right now
```bash
python3 agents/market_context.py
```
**What it does:** Fetches VIX, S&P/Nasdaq/Dow futures, Fear & Greed index, international markets (Nikkei/FTSE/DAX), and economic calendar. Prints decision (GO / CAUTION / SKIP) and max positions for today.

**When to use:** Any time you want a quick market read before deciding whether to run the pipeline manually.

---

### `scanner/scanner.py` — Run the stock scanner
```bash
python3 scanner/scanner.py                 # Show top 20 candidates
python3 scanner/scanner.py --limit 50      # Show top 50
```
| Flag | Default | Description |
|------|---------|-------------|
| `--limit` | 20 | Number of candidates to display |

**What it does:** Loads the current universe from Supabase (or static fallback), scores all tickers on RSI, MACD, Bollinger Bands, volume, and ATR. Prints a ranked table of candidates with scores.

**When to use:** Spot-check which stocks are showing technical setups right now, or verify the scanner is working after a universe update.

---

### `agents/universe_refresh.py` — Refresh the stock universe
```bash
python3 agents/universe_refresh.py
```
**What it does:** Fetches S&P 500 + Nasdaq 100 from Wikipedia, adds ~41 curated high-momentum tickers (quantum, crypto miners, AI, leveraged ETFs), screens all ~550 for price $5–$500, avg volume ≥500K, ATR% ≥2%. Saves passing tickers to Supabase. Normally runs automatically every Monday 8:30 AM ET — use this to trigger a manual refresh.

**When to use:** Force a mid-week universe update after a major index rebalance, or to verify the refresh is working.

---

### `agents/intraday.py` — Sync open positions from Alpaca
```bash
python3 agents/intraday.py                          # Sync from Alpaca (default)
python3 agents/intraday.py --broker simulation      # Sync from yfinance prices
```
| Flag | Options | Default |
|------|---------|---------|
| `--broker` | `alpaca`, `simulation` | `alpaca` |

**What it does:** Runs Alpaca reconciliation (marks UNFILLED positions), then syncs current price and unrealized P&L for all open positions. Closes any that hit +3% target or -1% stop. Logs an intraday snapshot to scan_results.

**When to use:** Manually sync positions mid-day if you want an immediate update outside the 30-min GitHub Actions schedule, or after a network issue.

---

### `agents/performance.py` — Run EOD close and calculate P&L
```bash
python3 agents/performance.py                       # Close via Alpaca (default)
python3 agents/performance.py --broker simulation   # Close via yfinance prices
```
| Flag | Options | Default |
|------|---------|---------|
| `--broker` | `alpaca`, `simulation` | `alpaca` |

**What it does:** Closes all remaining open positions, calculates total P&L, win rate, best/worst trade, and writes the daily_performance record to Supabase. Normally runs automatically at 4:30 PM ET — use this for a manual EOD close.

**When to use:** If the scheduled EOD GitHub Actions run fails, run this manually to close positions and record the day's P&L.

---

## Analysis & Evaluation

### `eval.py` — Score the agent's performance
```bash
python3 eval.py                       # Last 5 trading days (console only)
python3 eval.py --days 30             # Last 30 days
python3 eval.py --days 30 --write     # Save results to Supabase (updates dashboard Scorecard)
```
| Flag | Default | Description |
|------|---------|-------------|
| `--days` | 5 | Number of trading days to evaluate |
| `--write` | off | Save score/grade/recommendations to Supabase |

**What it does:** Grades the agent A/B/C/D based on avg daily P&L vs target, win days, and trade win rate. Shows best/worst trades, close reason breakdown, and tuning recommendations. The `--write` flag saves results to Supabase so the Performance tab Agent Scorecard updates. Runs automatically with `--write` after every EOD close via GitHub Actions.

---

### `backtest.py` — Historical backtest
```bash
python3 backtest.py --days 30 --top 15     # 30-day backtest, top 15 picks/day
python3 backtest.py --days 5  --top 10     # Quick 5-day test
```
| Flag | Default | Description |
|------|---------|-------------|
| `--days` | 30 | Number of trading days to replay |
| `--top` | 15 | Number of top-scored tickers to simulate per day |

**What it does:** Replays historical trading days using real yfinance price data and a rule-based scorer (no Claude API). Simulates entry at open, checks daily high/low for 3% target or 1% stop hit, falls back to close price at EOD.

**When to use:** Validate configuration changes (target %, stop %, position count) before pushing to live. Run after editing `config/settings.py`.

---

### `health_check.py` — Pre-market system check
```bash
python3 health_check.py
```
**What it does:** Checks 5 systems — Supabase connectivity, Alpaca buying power (≥$10K), Anthropic API credit, universe freshness (≤7 days), stale open positions. Sends email alert to `ALERT_EMAIL` if anything fails. Runs automatically at 8:45 AM ET every trading day via GitHub Actions.

**When to use:** Run manually any time you want to verify all systems are healthy before a trading day.

---

## GitHub Actions (Automatic — no manual action needed)

| Schedule | Job | What runs |
|----------|-----|-----------|
| Mon 8:30 AM ET | `universe_refresh.yml` | `agents/universe_refresh.py` — weekly ticker refresh |
| Mon–Fri 8:45 AM ET | `health_check.yml` | `health_check.py` — 5-system check + email on failure |
| Mon–Fri 9:00 AM ET | `trading.yml` (premarket) | Full pipeline: market → scan → strategy → risk → sector guard → guardrails → Alpaca orders |
| Mon–Fri every 30 min 10AM–3:30PM | `trading.yml` (intraday) | Position sync + reconciliation |
| Mon–Fri 4:30 PM ET | `trading.yml` (eod) | EOD close + P&L + `eval.py --days 30 --write` |

Manual trigger: github.com/amitgarg73/trading-agent → Actions → select workflow → Run workflow

---

## Agents NOT designed for standalone use

These run as pipeline steps only — they depend on outputs from prior agents:

| Agent | Why pipeline-only |
|-------|------------------|
| `agents/strategy.py` | Needs scanner candidates + market context as input |
| `agents/risk.py` | Needs strategy agent's proposed trades as input |
| `agents/sector_guard.py` | Needs risk-approved trades as input |
| `agents/guardrails.py` | Needs sector-filtered trades + universe as input |
| `agents/portfolio.py` | Needs guardrail-approved trades to open positions |
| `agents/news_intel.py` | Needs scanner candidates as input |
| `agents/alpaca_broker.py` | Library — called by portfolio and intraday agents |
