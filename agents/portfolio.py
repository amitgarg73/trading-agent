"""
Portfolio Agent: executes trades and manages open positions.
broker="simulation" uses yfinance price simulation (default, no external account needed).
broker="alpaca"     submits real bracket orders to Alpaca paper trading.
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


def open_positions(plan_id: str, approved_trades: list, broker: str = "simulation") -> list:
    """Execute fills for all approved trades and write to DB."""
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

        alpaca_order_id = None
        if broker == "alpaca":
            from agents import alpaca_broker
            try:
                alpaca_order_id = alpaca_broker.submit_bracket_order(
                    ticker=ticker,
                    shares=trade["shares"],
                    target_price=trade["target_price"],
                    stop_price=trade["stop_loss"],
                    action=trade["action"],
                )
                print(f"        Alpaca order submitted: {ticker} → {alpaca_order_id}")
            except Exception as e:
                print(f"        ⚠️  Alpaca order failed for {ticker}: {e}")

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
            "alpaca_order_id":  alpaca_order_id,
        })

        opened.append(position)

    return opened


def refresh_positions(broker: str = "simulation") -> list:
    """Update current prices and P&L for all open positions. Close on target/stop hit."""
    open_pos = db.select("positions", filters={"status": "OPEN"})
    updated  = []

    if broker == "alpaca":
        from agents import alpaca_broker
        alpaca_open = alpaca_broker.get_open_tickers()

        for pos in open_pos:
            ticker = pos["ticker"]

            if ticker in alpaca_open:
                # Still open in Alpaca — sync price and unrealized P&L
                data = alpaca_broker.get_position_data(ticker)
                if data:
                    db.update("positions", {"id": pos["id"]}, {
                        "current_price":  data["current_price"],
                        "unrealized_pnl": data["unrealized_pnl"],
                    })
                updated.append({**pos, **( data or {}), "close_reason": None})
            else:
                # Gone from Alpaca — bracket order exited the position
                order_id = pos.get("alpaca_order_id")
                close_price, close_reason = None, "CLOSED"
                if order_id:
                    close_price, close_reason = alpaca_broker.get_order_fill(order_id)

                close_price = close_price or pos.get("current_price") or pos["entry_price"]
                close_reason = close_reason or "CLOSED"

                shares = pos["shares"]
                entry  = pos["entry_price"]
                action = pos["action"]
                pnl = round(shares * (close_price - entry), 2) if action == "BUY" \
                      else round(shares * (entry - close_price), 2)

                db.update("positions", {"id": pos["id"]}, {
                    "current_price":  close_price,
                    "unrealized_pnl": 0,
                    "realized_pnl":   pnl,
                    "close_price":    close_price,
                    "close_reason":   close_reason,
                    "closed_at":      datetime.utcnow().isoformat(),
                    "status":         "CLOSED",
                })
                db.update("planned_trades", {"id": pos["planned_trade_id"]}, {"status": "CLOSED"})
                updated.append({**pos, "current_price": close_price,
                                "unrealized_pnl": 0, "close_reason": close_reason})
        return updated

    # Simulation mode — yfinance price checks
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

        updated.append({**pos, "current_price": price, "unrealized_pnl": pnl,
                        "close_reason": close_reason})

    return updated


def close_all_positions(reason: str = "EOD", broker: str = "simulation") -> list:
    """Force-close all open positions."""
    open_pos = db.select("positions", filters={"status": "OPEN"})
    closed   = []

    if broker == "alpaca":
        from agents import alpaca_broker
        alpaca_broker.cancel_all_orders()

    for pos in open_pos:
        ticker = pos["ticker"]

        alpaca_fill = None
        if broker == "alpaca":
            from agents import alpaca_broker
            _, alpaca_fill = alpaca_broker.close_position(ticker)

        price = alpaca_fill or _current_price(ticker) or pos.get("current_price") or pos["entry_price"]
        shares = pos["shares"]
        entry  = pos["entry_price"]
        action = pos["action"]
        pnl = round(shares * (price - entry), 2) if action == "BUY" \
              else round(shares * (entry - price), 2)

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
