# AI Trading Agent

Fully autonomous AI-driven paper trading system. Scans 450+ stocks daily, selects high-conviction trades using Claude AI, manages positions through Alpaca Paper Trading, and tracks performance on a live dashboard — zero manual intervention after setup.

**Backtest result (30 days, Apr–May 2026):** $716/day avg · 90% win days · 202% annualized · Grade B

---

## How it works

```
9:00 AM ET (Mon–Fri)
  ├── Market Context    VIX + futures + Fear & Greed → GO / CAUTION / SKIP
  ├── Scanner           Score 450+ tickers on RSI, MACD, Bollinger, volume, ATR
  ├── News Filter       Remove earnings-risk tickers; add news headlines
  ├── Strategy (Claude) Pick up to 15 highest-conviction trades
  ├── Risk (Claude)     Enforce 3% target, 1% stop, 3:1 R:R
  ├── Sector Guard      Cap at 3 positions per sector
  ├── Guardrails        6 safety checks before any order is placed
  └── Alpaca Orders     Submit bracket orders (entry + take-profit + stop-loss)

Every 30 min (10AM–3:30PM ET)
  └── Intraday Sync     Reconcile positions, close on target/stop hit

4:30 PM ET
  ├── EOD Close         Close remaining positions via Alpaca
  ├── P&L Record        Write daily_performance to Supabase
  └── Auto Eval         Score last 30 days → Agent Scorecard on dashboard
```

---

## Stack

| Component | Technology |
|-----------|-----------|
| AI (strategy + risk) | Anthropic Claude API (`claude-sonnet-4-6`) |
| Market data | yfinance (free, no key needed) |
| Technical analysis | `ta` Python library |
| Database | Supabase (PostgreSQL) |
| Scheduler | GitHub Actions (cron) |
| Dashboard | Streamlit Cloud |
| Broker | Alpaca Paper Trading (`alpaca-py`) |

**Cost:** ~$0.15–0.30/day in Claude API calls. All cloud infrastructure on free tiers.

---

## Quick start (local)

```bash
# 1. Clone and install
git clone https://github.com/amitgarg73/trading-agent
cd trading-agent
pip3 install -r requirements.txt

# 2. Set up secrets
cp .env.example .env
# Edit .env with your API keys

# 3. Run the full premarket pipeline (simulation mode — no Alpaca orders)
python3 orchestrator.py --mode premarket --broker simulation

# 4. View dashboard locally
python3 -m streamlit run dashboard/app.py
```

---

## Runnable skills

See **[SKILLS.md](SKILLS.md)** for the full list of commands. Quick reference:

| Command | What it does |
|---------|-------------|
| `python3 agents/market_context.py` | Check VIX, futures, Fear & Greed right now |
| `python3 scanner/scanner.py [--limit 20]` | Run the stock scanner, show top candidates |
| `python3 agents/universe_refresh.py` | Manually refresh the stock universe |
| `python3 agents/intraday.py [--broker alpaca]` | Sync positions from Alpaca |
| `python3 agents/performance.py [--broker alpaca]` | Run EOD close and calculate P&L |
| `python3 eval.py [--days 5] [--write]` | Score agent performance (A/B/C/D) |
| `python3 backtest.py [--days 30] [--top 15]` | Historical backtest |
| `python3 health_check.py` | Check all 5 systems (Supabase, Alpaca, Anthropic, etc.) |
| `python3 orchestrator.py --mode premarket --broker alpaca` | Full premarket pipeline |

---

## Configuration

All tunable parameters in `config/settings.py`:

| Parameter | Value | Notes |
|-----------|-------|-------|
| `TOTAL_CAPITAL` | $100,000 | Starting portfolio size |
| `MAX_POSITIONS` | 15 | Max concurrent positions (reduced by VIX gate) |
| `TARGET_PCT` | 3% | Profit target per trade (hard rule) |
| `MAX_LOSS_PER_TRADE` | 1% | Stop loss per trade (hard rule) |
| `MIN_REWARD_RISK` | 3.0 | Minimum reward:risk ratio |
| `MAX_PER_SECTOR` | 3 | Max positions per sector |
| `DAILY_LOSS_LIMIT` | -$300 | Stop new trades if day P&L drops below this |
| `PRICE_SANITY_PCT` | 5% | Reject if entry >5% from market price |

---

## Secrets required

| Secret | Used by |
|--------|---------|
| `ANTHROPIC_API_KEY` | Strategy + risk agents (Claude API) |
| `SUPABASE_URL` | All DB reads/writes |
| `SUPABASE_KEY` | All DB reads/writes |
| `ALPACA_API_KEY` | Alpaca broker |
| `ALPACA_SECRET_KEY` | Alpaca broker |
| `DASHBOARD_PASSWORD` | Streamlit dashboard login |
| `GMAIL_USER` | Health check email alerts |
| `GMAIL_APP_PASSWORD` | Health check email alerts |
| `ALERT_EMAIL` | Where health check alerts are sent |

Set in: GitHub Secrets (for Actions), Streamlit Cloud Secrets (for dashboard), and `.env` (local dev).

---

## Dashboard

Live at your Streamlit Cloud URL · password protected · mobile accessible

| Tab | What you see |
|-----|-------------|
| **Summary** | Daily cockpit: KPIs, In Flight positions, Today's Plan table, Trade Heatmap |
| **Today** | Step-by-step pipeline: market conditions → scan → strategy/risk → live positions |
| **Positions** | All open + closed positions with P&L |
| **Performance** | Agent Scorecard (auto-updated EOD), P&L charts, portfolio history |
| **Scan Log** | Audit trail of every premarket and intraday scan |

---

## Safety guardrails (V5)

Six checks run before any trade is placed:
1. **Daily loss limit** — halt if realized P&L < -$300
2. **Action whitelist** — BUY only, rejects rogue SELL/SHORT
3. **Ticker whitelist** — must be in current universe
4. **Duplicate guard** — no same ticker twice in one day
5. **Price sanity** — entry within 5% of actual market price
6. **Capital check** — Alpaca buying power must cover position size

Plus: concurrent run lock (no duplicate premarket runs), EOD retry, intraday Alpaca reconciliation (UNFILLED detection).

---

## Key files

```
orchestrator.py              Main controller — chains all agents
agents/market_context.py     VIX gate + futures + Fear & Greed + calendar
agents/strategy.py           Claude picks trades
agents/risk.py               Claude validates risk rules
agents/sector_guard.py       Max 3 positions per sector
agents/guardrails.py         6 pre-trade safety checks
agents/alpaca_broker.py      Alpaca Paper Trading integration
agents/intraday.py           Position sync + reconciliation
agents/performance.py        EOD P&L calculation
agents/universe_refresh.py   Weekly S&P500+Nasdaq100 screener
scanner/scanner.py           Technical scanner (RSI, MACD, BB, ATR)
config/settings.py           All tunable parameters
dashboard/app.py             Streamlit dashboard
eval.py                      Performance grading (auto-runs EOD)
backtest.py                  Historical backtesting
health_check.py              Pre-market 5-system health check
schema.sql                   Supabase database schema
```

---

## Documentation

- `Trading_Agent_Documentation.docx` — full technical build log (v5.1)
- `Trading_Agent_PRD.docx` — product requirements document (v5.1)
- `SKILLS.md` — all runnable commands with examples
- `generate_doc.py` / `generate_prd.py` — regenerate Word docs

---

*Built with [Claude Code](https://claude.ai/code) · Anthropic API · May 2026*
