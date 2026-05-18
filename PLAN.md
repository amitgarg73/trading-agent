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
| 5 | Native Alpaca trailing stop (OTO-OCO) | ⏸ DEFERRED — real money only | 6–8 hrs |
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

## Fix 5 — Native Trailing Stop (Deferred)

Native Alpaca trailing stop requires moving from bracket orders to OTO-OCO structure.
Build this before deploying real money. Key risks to mitigate:
- Gap window: position unprotected between entry fill and OCO submission (up to 15 min now)
- Double-sell: if take-profit and trailing stop both fire, accidental short position
- OCO failure: no exit orders if API call fails

Mitigation plan when building:
- `USE_NATIVE_TRAILING_STOP = False` feature flag in settings.py
- Track `entry_filled_at` in positions table
- Idempotent OCO submission guard
- Unit tests: mock Alpaca client, test all state transitions
- A/B test on paper for 2 weeks before enabling on real money

---

## After All Fixes — Rerun Backtest

Once all 5 fixes are live, run a fresh backtest to see impact on simulated performance:
```bash
python3 backtest.py --days 30 --top 15
```
Then compare to current baseline ($716/day, 90% win days).

---

## Then: Next Feature Sprint

After friction fixes are validated, pick from `Trading_Agent_Features.docx` Section 9 (Real Edge):
1. Real-time Alpaca data (already partially done by Fix 2)
2. Post-earnings momentum agent
3. Market regime classifier
4. Options flow signal
5. Insider buying (Form 4)
