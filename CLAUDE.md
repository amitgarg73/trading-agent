# CLAUDE.md — Trading Agent

## ⛔ FEATURE FREEZE UNTIL JUNE 1, 2026

The system is in a 2-week paper trading validation window.
**Do not build new features. Do not refactor. Do not add complexity.**

Allowed before June 1:
- Bug fixes if something is actively broken
- Anything Amit explicitly asks for after being reminded of the freeze

On any new feature request, say: "We're in feature freeze until June 1. Want to note this for after the gate?"

## Validation Gate

Run on June 1: `python3 eval.py --days 14`

Pass criteria:
- Win rate ≥ 80%
- Avg daily P&L ≥ $500
- NATIVE_TRAIL exits confirmed (no double-sells)
- No integrity flags
- Confidence ≥ 7/10

Do not deploy real capital until all criteria pass.

## Post-June-1 Backlog (build after eval gate passes)

### P0 — Pre-real-money
- **P&L reconciliation:** Pull Alpaca `get_account().equity` at EOD as source of truth; replace fill-price calc. Add `friction_breakdown` dict (commission, spread, slippage, entry buffer) to daily_performance.

### P1 — Capability expansion
- **Intraday trade entries:** Run scan + strategy + risk + order flow inside the intraday agent (every 30 min). Only enter if: (a) daily realized P&L is not negative, (b) open position count is below MAX_POSITIONS, (c) sector guard passes. Factor time-of-day into targets — shorter window to close means tighter targets. Adds compounding risk; validate on paper for 2 weeks before enabling on real capital.

