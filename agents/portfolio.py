"""
Portfolio Agent: executes trades and manages open positions.
broker="simulation" uses yfinance price simulation (default, no external account needed).
broker="alpaca"     submits real bracket orders to Alpaca paper trading.
"""
from __future__ import annotations
import yfinance as yf
from datetime import date, datetime
from core import db
from config.settings import TRAIL_PCT


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
                    entry_price=trade["entry_price"],
                    target_price=trade["target_price"],
                    stop_price=trade["stop_loss"],
                    action=trade["action"],
                )
                limit_price = round(trade["entry_price"] * 1.001, 2)
                print(f"        Alpaca limit order: {ticker} @ ${limit_price} → {alpaca_order_id}")
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
            "high_watermark":   price,
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
                # Still open in Alpaca — sync price, update watermark, check trailing stop
                data = alpaca_broker.get_position_data(ticker)
                if data:
                    current     = data["current_price"]
                    entry       = pos["entry_price"]
                    high_wm     = float(pos.get("high_watermark") or entry)
                    new_high_wm = max(high_wm, current)
                    eff_stop    = max(pos["stop_loss"], round(new_high_wm * (1 - TRAIL_PCT), 4))
                    trail_hit   = current <= eff_stop and current > pos["stop_loss"]

                    if trail_hit:
                        print(f"  🔔 TRAIL STOP: {ticker} peak ${new_high_wm:.2f} → ${current:.2f} (stop ${eff_stop:.2f})")
                        success, fill = alpaca_broker.close_position(ticker)
                        fill = fill or current
                        pnl  = round(pos["shares"] * (fill - entry), 2)
                        db.update("positions", {"id": pos["id"]}, {
                            "current_price":  fill,
                            "unrealized_pnl": 0,
                            "realized_pnl":   pnl,
                            "close_price":    fill,
                            "close_reason":   "STOP",
                            "closed_at":      datetime.utcnow().isoformat(),
                            "status":         "CLOSED",
                            "high_watermark": new_high_wm,
                        })
                        db.update("planned_trades", {"id": pos["planned_trade_id"]}, {"status": "CLOSED"})
                        updated.append({**pos, "current_price": fill,
                                        "unrealized_pnl": 0, "close_reason": "STOP"})
                    else:
                        db.update("positions", {"id": pos["id"]}, {
                            "current_price":  current,
                            "unrealized_pnl": data["unrealized_pnl"],
                            "high_watermark": new_high_wm,
                        })
                        updated.append({**pos, **data, "close_reason": None})
                else:
                    updated.append({**pos, "close_reason": None})
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

        # Trailing stop: ratchet stop up from peak; falls back to hard stop when at a loss
        high_wm     = float(pos.get("high_watermark") or entry)
        new_high_wm = max(high_wm, price)
        eff_stop    = max(stop, round(new_high_wm * (1 - TRAIL_PCT), 4))

        if action == "BUY":
            pnl = round(shares * (price - entry), 2)
        else:
            pnl = round(shares * (entry - price), 2)

        close_reason = None
        if action == "BUY":
            if price <= eff_stop:
                close_reason = "STOP"
            elif price >= target:
                close_reason = "TARGET"
        else:
            if price >= eff_stop:
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
                "high_watermark": new_high_wm,
            })
            db.update("planned_trades", {"id": pos["planned_trade_id"]}, {"status": "CLOSED"})
        else:
            db.update("positions", {"id": pos["id"]}, {
                "current_price":  price,
                "unrealized_pnl": pnl,
                "high_watermark": new_high_wm,
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
            import time
            success, alpaca_fill = alpaca_broker.close_position(ticker)
            if not success:
                time.sleep(2)
                success, alpaca_fill = alpaca_broker.close_position(ticker)
            if not success:
                print(f"        ⚠️  WARNING: Could not close {ticker} on Alpaca — manual close required")

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
