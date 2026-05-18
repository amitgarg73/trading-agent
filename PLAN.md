# Trading Agent — Execution Friction Fix Plan
**Goal:** Fix all 5 execution friction issues before adding new features.  
**Why:** Paper vs. real money gap is ~40% — mostly slippage, stale prices, and slow exits. Fix these first.

---

## Known Issues — Resolved

| Issue | Observed | Resolution |
|-------|----------|------------|
| GitHub Actions scheduled run delay | 2026-05-18 | ✅ Fixed — cron-job.org external triggers replacing GitHub scheduler |
| Mode detection mis-classified late runs as intraday | 2026-05-18 | ✅ Fixed — time windows in trading.yml instead of exact minute match |
| STRATEGY_MIN_SCORE=5 too aggressive (93→4 candidates) | 2026-05-18 | ✅ Fixed — lowered to 4 |
| Futures unavailable on Mondays | 2026-05-18 | ✅ Fixed — period="5d" in market_context.py |
| Streamlit ImportError on TRAIL_PCT | 2026-05-18 | ✅ Fixed — stale Streamlit Cloud deployment, rebooted |

**cron-job.org setup:**
- Premarket: `0 13 * * 1-5` UTC → `{"ref": "main", "inputs": {"mode": "premarket", "broker": "alpaca"}}`
- Intraday: `0,30 14-19 * * 1-5` UTC → `{"ref": "main", "inputs": {"mode": "intraday", "broker": "alpaca"}}`
- EOD: `30 20 * * 1-5` UTC → `{"ref": "main", "inputs": {"mode": "eod", "broker": "alpaca"}}`
- Auth: classic GitHub PAT with `repo` + `workflow` scopes in Authorization header
- GitHub schedule kept as backup; concurrent run lock handles duplicates

---

## Status

| # | Fix | Status | Est. |
|---|-----|--------|------|
| 1 | Raise liquidity floor (500K → 1M avg volume) | ✅ DONE | — |
| 2 | Real-time Alpaca price refresh before Claude call | ✅ DONE | — |
| 3 | Limit order entries instead of market orders | ✅ DONE | — |
| 4 | Skip first 15 min (no entries before 9:45 AM ET) | ✅ DONE | — |
| 5 | Native Alpaca trailing stop (OTO-OCO) | ✅ DONE — live on paper 2026-05-18 | — |
| 5b | 15-min intraday checks (pragmatic fix for fix 5) | ✅ DONE | — |
| 6 | Reliable cron triggering (cron-job.org) | ✅ DONE | — |

**Total remaining: ~12–17 hrs across 2 sessions**

---

## Part 0 — Morning: Validate Live Run (9:00 AM ET)

Check GitHub Actions logs after the premarket run fires. Confirm all v5.3/v5.4 features working:

- [ ] `[ 1.75/4 ] Strategy pre-filter: X → Y candidates (score ≥ 5)` — pre-filter working
- [ ] `💾 Prompt cache WRITE: 1,243 tokens stored` — caching working
- [ ] `📐 TICKER: size $X → $7,000 (HIGH)` — confidence sizing working
- [ ] Supabase `positions` table: `high_watermark` column populated on OPEN rows
- [ ] Dashboard Summary tab: "Trail $X.XX ↑" appears on In Flight cards (if any stop ratcheted)
- [ ] Fewer thinly-traded tickers in the plan (effect of 1M volume floor)

Run after 4:30 PM EOD close:
```bash
python3 eval.py --days 1
```

---

## Part 1 — Session 1: Fix the Entry Side (~6–8 hrs)

### Fix 2: Real-time Alpaca price refresh
**File:** `orchestrator.py` + `agents/alpaca_broker.py`  
**What:** After the scan (step 1.75), fetch live prices from Alpaca REST API for all remaining
candidates and update their `current_price` before passing to Claude.  
**Why:** yfinance prices are 15-min delayed — Claude is setting entry prices on stale data.
A stock at $50 in yfinance might be at $51.50 at 9 AM — target is already eaten.  
**How:**
- Add `get_live_prices(tickers: list) -> dict` in `alpaca_broker.py` using Alpaca's
  `/v2/stocks/snapshots` endpoint (batch, one call for all tickers)
- In orchestrator step 1.75, after pre-filter, call `get_live_prices()` and update
  `candidate["current_price"]` for each ticker
- Log: `[ 1.8/4 ] Live prices refreshed for X candidates`

### Fix 3: Limit order entries
**File:** `agents/alpaca_broker.py` → `submit_bracket_order()`  
**What:** Change entry leg from `market` to `limit` at ask price (or `current_price + 0.01`).  
**Why:** Market orders pay the spread; limit orders at the ask still fill quickly on liquid stocks
but avoid moving the price against you.  
**How:**
- Change `order_class="bracket"` entry type from `type="market"` to `type="limit"`
  with `limit_price = round(entry_price * 1.001, 2)` (0.1% above — ensures fill on liquid stocks)
- Handle non-fill: in `intraday.py` reconciliation, if an OPEN position has no Alpaca fill
  after 30 min → mark UNFILLED (already exists), cancel the order
- Log: `  📋 Limit order: TICKER @ $X.XX (was market)`

---

## Part 2 — Session 2: Fix the Exit and Timing Side (~6–8 hrs)

### Fix 4: Skip first 15 minutes
**File:** `.github/workflows/trading.yml` + `orchestrator.py`  
**What:** Move order submission to 9:45 AM ET instead of 9:00 AM ET.  
**Why:** 9:30–9:45 AM has widest spreads of the day — market makers finding price, retail
piling in. Waiting 15 min gives cleaner entries and tighter spreads.  
**How — option A (simpler):** Split premarket into two steps in trading.yml:
  - 9:00 AM: scan + Claude selection → save approved trades to Supabase (new status: `PENDING_ENTRY`)
  - 9:45 AM: new `--mode submit` step reads PENDING_ENTRY trades → submits to Alpaca
- Add `submit` mode to `orchestrator.py` that reads today's approved trades and opens positions
- **Option B (simpler but less precise):** Keep 9:00 AM run, add `time.sleep()` in
  `open_positions()` until 9:45 ET — simpler but blocks the Actions runner for 45 min (not ideal)

**Recommended: Option A**

### Fix 5: Native Alpaca trailing stop
**File:** `agents/alpaca_broker.py`, `agents/portfolio.py`, `agents/intraday.py`  
**What:** Replace manual `high_watermark` trailing stop check (every 30 min) with Alpaca's
native `trail_percent` order on the stop-loss leg of the bracket.  
**Why:** Manual 30-min check can miss a fast reversal — stock peaks and drops 1.5% in 8 min,
you don't catch it until next cycle. Native trailing stop fires in real-time.  
**How:**
- In `submit_bracket_order()`: replace fixed `stop_loss` bracket leg with
  `trail_percent = TRAIL_PCT * 100` (Alpaca uses percentage as integer, e.g., 1.0 for 1%)
- Remove `high_watermark` update logic from `refresh_positions()` in Alpaca mode
  (keep it for simulation mode — simulation still needs the manual check)
- `intraday.py`: no longer needs to check trail manually for Alpaca positions —
  Alpaca fires the stop automatically; just sync fills
- Dashboard: `fmt_stop()` in Alpaca mode can show "Trail 1% (native)" since we don't
  have real-time watermark from Alpaca

**Note:** Keep `high_watermark` column and simulation trail logic intact —
simulation mode still needs the manual approach.

---

## After All Fixes — Rerun Backtest

Once all fixes are validated over 1 week of live paper trading, run a fresh backtest:
```bash
python3 backtest.py --days 30 --top 15
```
Compare to current baseline ($716/day, 90% win days).

---

## ⚠️ Pre-Real-Money Checklist — DO NOT SKIP

Before deploying any real capital, complete ALL of the following:

| # | Item | Status |
|---|------|--------|
| 1 | Native Alpaca trailing stop (OTO-OCO) | ✅ DONE — USE_NATIVE_TRAILING_STOP=True as of 2026-05-18 |
| 2 | 2-week paper trading validation (all friction fixes live) | 🔄 IN PROGRESS — started 2026-05-18, gate: 2026-06-01. On June 1: run `python3 eval.py --days 14`, check win rate ≥80%, avg P&L ≥$500/day, no double-sell, "Trail 1% ↑ (native)" on dashboard, Alpaca order history shows trailing stop leg filled correctly |
| 3 | Backtest rerun post-fixes | ⬜ TODO — run after 2-week gate |
| 4 | Real money confidence assessment (target: 7/10 minimum) | 🔄 IN PROGRESS — current: 5/10, target: 7/10 after validation |
| 5 | DAILY_LOCK_IN_TARGET review for real capital | ⬜ TODO — after capital amount decided |

**Native trailing stop is P0 for real money** — 15-min polling is acceptable on paper,
unacceptable on real capital where a fast reversal can cost hundreds of dollars.

---

## v5.5 — ML Scoring + Target Tuning (Complete)

| # | Feature | Status |
|---|---------|--------|
| 1 | Target 3% → 2%, stop 1% → 0.67% (maintains 3:1 R:R, break-even 25%) | ✅ DONE |
| 2 | ML scorer — train_model.py (HistGradientBoosting, 429 tickers, 2y, AUC 0.78) | ✅ DONE |
| 3 | scanner/ml_scorer.py — step 1.76 in orchestrator, sorts by P(hit +2%) | ✅ DONE |
| 4 | Monthly retrain workflow — retrain_model.yml, auto-commits pkl to main | ✅ DONE |
| 5 | Architecture diagrams updated to v5.5 (13 steps, ML feedback loop, interdependencies) | ✅ DONE |
| 6 | All docs updated to v5.5 (generate_doc.py, generate_prd.py, generate_features.py) | ✅ DONE |

---

## v5.6 — Native Trailing Stop + Automated Validation (2026-05-18)

| # | Feature | Status |
|---|---------|--------|
| 1 | `USE_NATIVE_TRAILING_STOP` feature flag in settings.py (default True as of 2026-05-18) | ✅ DONE |
| 2 | `submit_bracket_order()` — `StopLossRequest(trail_percent=...)` when flag True | ✅ DONE |
| 3 | `portfolio.py` — passes flag on open; stores `native_trail_active` in DB; skips manual trail check when True | ✅ DONE |
| 4 | `dashboard/app.py` — `fmt_stop()` shows "Trail 1% ↑ (native)" for native trail positions | ✅ DONE |
| 5 | `exit_mechanism` column on positions — NATIVE_TRAIL, TARGET, MANUAL_TRAIL, STOP, EOD | ✅ DONE |
| 6 | `alpaca_broker.get_order_fill()` — returns NATIVE_TRAIL when trailing_stop leg fires | ✅ DONE |
| 7 | `eval.py` — VERDICT summary (plain-language What's working / Watch / Action required) | ✅ DONE |
| 8 | `eval.py` — annotated metrics with ✅/⚠️/❌ flags and benchmark targets | ✅ DONE |
| 9 | `eval.py` — TRAILING STOP VALIDATION section (native vs manual cohort comparison) | ✅ DONE |
| 10 | `eval.py` — INTEGRITY CHECKS (UNFILLED rate, orphaned positions, duplicates, missing exit_mechanism) | ✅ DONE |
| 11 | `eval.py` — CLAUDE QUALITY CHECKS (R:R violations, size violations, confidence cohort performance) | ✅ DONE |
| 12 | Supabase: `native_trail_active` and `exit_mechanism` columns added to positions table | ✅ DONE |

---

## Then: Next Feature Sprint

After June 1 validation gate, pick from `Trading_Agent_Features.docx` Section 10 (Priority Summary):
1. **Backtest rerun post-fixes** — `python3 backtest.py --days 30 --top 15`; compare to $716/day baseline
2. **Real money capital sizing** — decide capital amount, rescale POSITION_SIZE_BY_CONFIDENCE and DAILY_LOCK_IN_TARGET
3. **ML model live validation** — 30 days paper; compare win rate vs baseline (no scorer)
4. Post-earnings momentum agent
5. Options flow signal
6. Insider buying (Form 4)
