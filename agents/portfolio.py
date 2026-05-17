"""
Portfolio Agent: simulates trade execution and manages open positions.
Writes all state to Supabase.
"""
from __future__ import annotations
import yfinance as yf
from datetime import date, datetime
from core import db


def _current_price(ticker: str) -> float | None:
    try:
        t = yf.Ticker(ticker)
        data = t.history(period="1d", interval="1m")
        if data.empty:
            return None
        return round(float(data["Close"].iloc[-1]), 2)
    except Exception:
        return None


def open_positions(plan_id: str, approved_trades: list[dict]) -> list[dict]:
    """Simulate fills for all approved trades and write to DB."""
    opened = []
    for trade in approved_trades:
        ticker = trade["ticker"]
        price  = _current_price(ticker) or trade["entry_price"]

        planned = db.insert("planned_trades", {
            "plan_id":          plan_id,
            "ticker":           ticker,
            "action":           trade["action"],
            "entry_price":      trade["entry_price"],
            "target_price":     trade["target_price"],
            "stop_loss":        trade["stop_loss"],
            "position_size":    trade["position_size"],
            "shares":           trade["shares"],
            "estimated_profit": trade["estimated_profit"],
            "confidence":       trade["confidence"],
            "reasoning":        trade["reasoning"],
            "status":           "OPEN",
        })

        position = db.insert("positions", {
            "planned_trade_id": planned["id"],
            "ticker":           ticker,
            "action":           trade["action"],
            "entry_price":      price,
            "current_price":    price,
            "target_price":     trade["target_price"],
            "stop_loss":        trade["stop_loss"],
            "shares":           trade["shares"],
            "position_size":    trade["position_size"],
            "unrealized_pnl":   0,
            "status":           "OPEN",
        })

        opened.append(position)

    return opened


def refresh_positions() -> list[dict]:
    """Update current prices and unrealized P&L for all open positions."""
    open_pos = db.select("positions", filters={"status": "OPEN"})
    updated  = []

    for pos in open_pos:
        ticker = pos["ticker"]
        price  = _current_price(ticker)
        if price is None:
            continue

        shares = pos["shares"]
        entry  = pos["entry_price"]
        action = pos["action"]
        stop   = pos["stop_loss"]
        target = pos["target_price"]

        if action == "BUY":
            pnl = round(shares * (price - entry), 2)
        else:
            pnl = round(shares * (entry - price), 2)

        # Check stop or target hit
        close_reason = None
        if action == "BUY":
            if price <= stop:
                close_reason = "STOP"
            elif price >= target:
                close_reason = "TARGET"
        else:
            if price >= stop:
                close_reason = "STOP"
            elif price <= target:
                close_reason = "TARGET"

        if close_reason:
            db.update("positions", {"id": pos["id"]}, {
                "current_price":  price,
                "unrealized_pnl": 0,
                "realized_pnl":   pnl,
                "close_price":    price,
                "close_reason":   close_reason,
                "closed_at":      datetime.utcnow().isoformat(),
                "status":         "CLOSED",
            })
            db.update("planned_trades", {"id": pos["planned_trade_id"]}, {"status": "CLOSED"})
        else:
            db.update("positions", {"id": pos["id"]}, {
                "current_price":  price,
                "unrealized_pnl": pnl,
            })

        updated.append({**pos, "current_price": price, "unrealized_pnl": pnl, "close_reason": close_reason})

    return updated


def close_all_positions(reason: str = "EOD") -> list[dict]:
    """Force-close all open positions (end of day)."""
    open_pos = db.select("positions", filters={"status": "OPEN"})
    closed   = []

    for pos in open_pos:
        price = _current_price(pos["ticker"]) or pos["current_price"]
        if price is None:
            continue

        shares = pos["shares"]
        entry  = pos["entry_price"]
        action = pos["action"]
        pnl    = round(shares * (price - entry), 2) if action == "BUY" else round(shares * (entry - price), 2)

        db.update("positions", {"id": pos["id"]}, {
            "current_price": price,
            "realized_pnl":  pnl,
            "close_price":   price,
            "close_reason":  reason,
            "closed_at":     datetime.utcnow().isoformat(),
            "status":        "CLOSED",
        })
        db.update("planned_trades", {"id": pos["planned_trade_id"]}, {"status": "CLOSED"})
        closed.append({**pos, "realized_pnl": pnl})

    return closed
