"""
Guardrails (V5): Safety checks before opening any trade.
Runs as step 3.75, after sector guard and before portfolio.

Checks applied in order:
  1. Daily loss limit  — stop all trading if today's realized P&L < DAILY_LOSS_LIMIT
  2. Action whitelist  — only BUY is permitted
  3. Ticker whitelist  — must be in our approved universe
  4. Duplicate guard   — same ticker can't be opened twice in one day
  5. Price sanity      — entry price must be within PRICE_SANITY_PCT of current market
  6. Capital check     — Alpaca buying_power must cover position_size (alpaca broker only)
"""
from __future__ import annotations
import yfinance as yf
from datetime import date
from core import db
from config.settings import DAILY_LOSS_LIMIT, PRICE_SANITY_PCT, TOTAL_CAPITAL


def _current_price(ticker: str) -> float | None:
    # Primary: Alpaca live quote (ask price — what you actually pay to BUY)
    try:
        from agents import alpaca_broker
        prices = alpaca_broker.get_live_prices([ticker])
        if prices.get(ticker):
            return prices[ticker]
    except Exception:
        pass
    # Fallback: yfinance 1-min intraday
    try:
        data = yf.Ticker(ticker).history(period="1d", interval="1m")
        if not data.empty:
            return round(float(data["Close"].iloc[-1]), 2)
    except Exception:
        pass
    return None


_EXCLUDE_CLOSE_REASONS = {"CLEANUP", "UNFILLED"}

def _today_realized_pnl() -> float:
    today = date.today().isoformat()
    closed = db.select("positions", filters={"status": "CLOSED"})
    today_closed = [
        p for p in closed
        if (p.get("closed_at") or "").startswith(today)
        and p.get("close_reason") not in _EXCLUDE_CLOSE_REASONS
    ]
    return sum(p.get("realized_pnl", 0) or 0 for p in today_closed)


def filter_trades(approved_trades: list, broker: str = "simulation",
                  universe: list | None = None) -> dict:
    """
    Run all guardrail checks against approved_trades.
    Returns: {"approved_trades": [...], "guardrail_blocked": [{ticker, reason}, ...]}
    """
    if not approved_trades:
        return {"approved_trades": [], "guardrail_blocked": []}

    universe_set = set(universe) if universe else set()

    # Check 1: Daily loss limit — block all new trades if we've already hit the limit
    today_pnl = _today_realized_pnl()
    if today_pnl < DAILY_LOSS_LIMIT:
        print(f"  🛑 GUARDRAIL: Daily loss limit hit (${today_pnl:,.2f} < ${DAILY_LOSS_LIMIT:,.0f}). Blocking all trades.")
        return {
            "approved_trades": [],
            "guardrail_blocked": [
                {"ticker": t["ticker"],
                 "reason": f"Daily loss limit: P&L ${today_pnl:,.2f} below ${DAILY_LOSS_LIMIT:,.0f}"}
                for t in approved_trades
            ],
        }

    # Check 2: Available capital (fetch once)
    # Alpaca: use live buying_power. Simulation: use TOTAL_CAPITAL as ceiling.
    if broker == "alpaca":
        try:
            from agents import alpaca_broker
            buying_power = alpaca_broker.get_buying_power()
        except Exception:
            buying_power = None
    else:
        buying_power = float(TOTAL_CAPITAL)

    committed_capital = 0.0  # cumulative position sizes approved in this batch

    # Check 3: Build traded-today set.
    # Uses opened_at (when we created the position) not closed_at — catches stocks that
    # were stopped out and re-queued in the same scan batch (parallel run race condition),
    # and blocks same-day re-entry after a stop without waiting for the next reconcile cycle.
    today_str = date.today().isoformat()
    open_pos  = db.select("positions", filters={"status": "OPEN"})
    all_pos   = db.select("positions")
    traded_today = {
        p["ticker"] for p in all_pos
        if p.get("status") == "OPEN"
        or (p.get("opened_at") or "").startswith(today_str)
    }

    passed, blocked = [], []
    for trade in approved_trades:
        ticker = trade["ticker"]
        reason = None

        # Action whitelist
        if trade.get("action", "BUY") != "BUY":
            reason = f"Action not allowed: {trade.get('action')} (only BUY permitted)"

        # Ticker whitelist
        elif universe_set and ticker not in universe_set:
            reason = "Ticker not in approved universe"

        # Duplicate position guard
        elif ticker in traded_today:
            reason = f"Duplicate: {ticker} already open or traded today"

        # Price sanity check — fail closed: no fresh price = no trade
        else:
            market_price = _current_price(ticker)
            if market_price is None:
                reason = "Price sanity: could not fetch current market price — blocking trade (no stale data risk)"
            else:
                entry = trade["entry_price"]
                deviation = abs(entry - market_price) / market_price
                if deviation > PRICE_SANITY_PCT:
                    reason = (
                        f"Price sanity: entry ${entry:.2f} is {deviation*100:.1f}% "
                        f"from market ${market_price:.2f} (max {PRICE_SANITY_PCT*100:.0f}%)"
                    )

        # Capital check — cumulative across this batch (no margin, no over-deployment)
        if reason is None and buying_power is not None:
            remaining = buying_power - committed_capital
            if trade["position_size"] > remaining:
                reason = (
                    f"Insufficient capital: ${committed_capital:,.0f} already committed, "
                    f"need ${trade['position_size']:,.0f} but only ${remaining:,.0f} remaining "
                    f"of ${buying_power:,.0f} available"
                )

        if reason:
            blocked.append({"ticker": ticker, "reason": reason})
            print(f"        🛑 {ticker}: {reason}")
        else:
            passed.append(trade)
            committed_capital += trade.get("position_size", 0)

    return {"approved_trades": passed, "guardrail_blocked": blocked}
