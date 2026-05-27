# CLAUDE.md — Trading Agent

## ⛔ FEATURE FREEZE UNTIL JUNE 8, 2026

The system is in a 2-week paper trading validation window.
**Do not build new features. Do not refactor. Do not add complexity.**

Allowed before June 1:
- Bug fixes if something is actively broken
- Anything Amit explicitly asks for after being reminded of the freeze

On any new feature request, say: "We're in feature freeze until June 1. Want to note this for after the gate?"

## Docs to Update After Every Change

After any meaningful change to logic, config, or pipeline — update these files and regenerate:

| File | Command | What it covers |
|------|---------|----------------|
| `DESIGN.md` | edit directly | Architecture, pipeline, trading logic, config, risk, roadmap |
| `Trading_Agent_Design.docx` | `python3 generate_design.py` | Formatted version of DESIGN.md |
| `Trading_Agent_Changelog.docx` | `python3 generate_changelog.py` | Version history |
| `Trading_Agent_Documentation.docx` | `python3 generate_doc.py` | Operational runbook |
| `Trading_Agent_PRD.docx` | `python3 generate_prd.py` | Product requirements |

**Minimum per change:** update `DESIGN.md` + run `generate_design.py`. Full doc suite before any milestone release.

## Validation Gate

Run on June 8: `python3 eval.py --days 14`

Pass criteria:
- Win rate ≥ 70%
- Avg daily P&L ≥ $500
- NATIVE_TRAIL exits confirmed (no double-sells)
- No integrity flags
- Confidence ≥ 7/10

Do not deploy real capital until all criteria pass.

## Post-June-1 Backlog (build after eval gate passes)

### P0 — Pre-real-money
- **P&L reconciliation:** Pull Alpaca `get_account().equity` at EOD as source of truth; replace fill-price calc. Add `friction_breakdown` dict (commission, spread, slippage, entry buffer) to daily_performance.
- **Stop-loss: reduce monitoring interval (Layer 2):** Change GHA cron and cron-job.org from `*/15` to `*/5` minutes. Reduces exposure window from 15 min to 5 min for positions using bracket hard stop only. No code change needed; update `.github/workflows` YAML and external cron schedule.
- **Stop-loss: WebSocket real-time monitor (Layer 3):** Replace polling cron with a persistent process subscribed to Alpaca's trade update stream. Reacts in seconds. Major architectural change — requires 2-week paper validation before enabling on real capital.

### P1 — Capability expansion
- **Intraday trade entries:** Run scan + strategy + risk + order flow inside the intraday agent (every 30 min). Only enter if: (a) daily realized P&L is not negative, (b) open position count is below MAX_POSITIONS, (c) sector guard passes. Factor time-of-day into targets — shorter window to close means tighter targets. Adds compounding risk; validate on paper for 2 weeks before enabling on real capital.
- **Replace yfinance with Alpaca for price data:** yfinance hits rate limits under repeated/concurrent runs. Move `_current_price()` (portfolio.py) and intraday momentum price/VWAP checks to Alpaca snapshot + bars API. Keep yfinance only for what Alpaca can't serve: futures (ES=F, NQ=F, YM=F), VIX (^VIX), international indices, and the scanner's 50-200 bar technical history (SMA/RSI/MACD).

