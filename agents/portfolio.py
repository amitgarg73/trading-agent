"""
Portfolio Agent: executes trades and manages open positions.
broker="simulation" uses yfinance price simulation (default, no external account needed).
broker="alpaca"     submits real bracket orders to Alpaca paper trading.
"""
from __future__ import annotations
import yfinance as yf
from datetime import date, datetime
from core import db
from config.settings import (
    TRAIL_PCT, LOCK_IN_TRAIL_PCT, DAILY_LOCK_IN_TARGET,
    USE_NATIVE_TRAILING_STOP, PARTIAL_PROFIT_ENABLED, PARTIAL_PROFIT_PCT,
    TARGET_PCT, PRICE_SANITY_PCT,
)


def _current_price(ticker: str) -> float | None:
    try:
        t = yf.Ticker(ticker)
        data = t.history(period="1d", interval="1m")
        if data.empty:
            return None
        return round(float(data["Close"].iloc[-1]), 2)
    except Exception:
        return None


def _open_single_position(plan_id, trade, price, broker, leg_label="", run_id=None):
    """Insert one planned_trade + position record and optionally submit to Alpaca."""
    ticker = trade["ticker"]
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

    alpaca_order_id  = None
    effective_entry  = trade["entry_price"]
    effective_stop   = trade["stop_loss"]
    effective_target = trade["target_price"]

    if broker == "alpaca":
        from agents import alpaca_broker
        try:
            # Anchor stop/target to the live price at submission time, not the
            # (potentially stale) scanner price.  When a stock reverses 2-4%
            # between scan and execution, a stop calculated from the scan price
            # ends up above the fill price and fires the bracket immediately.
            live = alpaca_broker.get_live_prices([ticker])
            live_px = live.get(ticker)
            if live_px and live_px > 0 and trade["entry_price"] > 0:
                deviation = abs(live_px - trade["entry_price"]) / trade["entry_price"]
                if deviation > PRICE_SANITY_PCT:
                    print(f"        ⚠️ Price drift: {ticker} plan={trade['entry_price']:.2f} "
                          f"live={live_px:.2f} ({deviation*100:.1f}%) — skipping")
                    db.update("planned_trades", {"id": planned["id"]}, {"status": "CANCELLED"})
                    return None
                stop_pct         = (trade["entry_price"] - trade["stop_loss"])   / trade["entry_price"]
                target_pct       = (trade["target_price"] - trade["entry_price"]) / trade["entry_price"]
                effective_entry  = live_px
                effective_stop   = round(live_px * (1 - stop_pct),   2)
                effective_target = round(live_px * (1 + target_pct), 2)

            alpaca_order_id = alpaca_broker.submit_bracket_order(
                ticker=ticker,
                shares=trade["shares"],
                entry_price=effective_entry,
                target_price=effective_target,
                stop_price=effective_stop,
                action=trade["action"],
                use_native_trail=USE_NATIVE_TRAILING_STOP,
                trail_pct=TRAIL_PCT,
            )
            print(f"        Alpaca order placed: {ticker}{leg_label} → {alpaca_order_id}")
        except Exception as e:
            print(f"        ⚠️  Alpaca order failed for {ticker}{leg_label}: {e}")

    # Don't write a phantom position if order submission failed.
    # Mark the planned_trade CANCELLED so it's excluded from P&L reports.
    if broker == "alpaca" and alpaca_order_id is None:
        print(f"        ⚠️  No order confirmed for {ticker}{leg_label} — skipping DB insert")
        db.update("planned_trades", {"id": planned["id"]}, {"status": "CANCELLED"})
        return None

    native_trail = broker == "alpaca" and USE_NATIVE_TRAILING_STOP
    db_entry = effective_entry if broker == "alpaca" else price
    return db.insert("positions", {
        "planned_trade_id":    planned["id"],
        "ticker":              ticker,
        "action":              trade["action"],
        "entry_price":         db_entry,
        "current_price":       db_entry,
        "target_price":        effective_target,
        "stop_loss":           effective_stop,
        "shares":              trade["shares"],
        "position_size":       trade["position_size"],
        "unrealized_pnl":      0,
        "status":              "OPEN",
        "alpaca_order_id":     alpaca_order_id,
        "high_watermark":      db_entry,
        "native_trail_active": native_trail,
        "run_id":              run_id,
    })


def open_positions(plan_id: str, approved_trades: list, broker: str = "simulation",
                   enable_partial: bool | None = None, run_id: str | None = None) -> list:
    """
    Execute fills for all approved trades and write to DB.
    enable_partial: override PARTIAL_PROFIT_ENABLED (None = use config default).
                    Pass False for intraday entries where target == PARTIAL_PROFIT_PCT.
    """
    use_partial = PARTIAL_PROFIT_ENABLED if enable_partial is None else enable_partial
    opened = []
    for trade in approved_trades:
        ticker = trade["ticker"]
        price  = _current_price(ticker) or trade["entry_price"]
        shares = trade["shares"]

        # Partial profit: split into two legs when shares allow
        # Leg A (half shares, PARTIAL_PROFIT_PCT target) locks in early profit
        # Leg B (remaining shares, full target) rides to the original 2% target
        if use_partial and shares >= 4:
            half    = shares // 2
            rest    = shares - half
            p_entry = trade["entry_price"]
            p_stop  = trade["stop_loss"]

            leg_a = {**trade, "shares": half,
                     "target_price":     round(p_entry * (1 + PARTIAL_PROFIT_PCT), 2),
                     "position_size":    round(half * p_entry, 2),
                     "estimated_profit": round(half * (round(p_entry * (1 + PARTIAL_PROFIT_PCT), 2) - p_entry), 2)}
            leg_b = {**trade, "shares": rest,
                     "position_size":    round(rest * p_entry, 2),
                     "estimated_profit": round(rest * (trade["target_price"] - p_entry), 2)}

            pos_a = _open_single_position(plan_id, leg_a, price, broker, leg_label=" [partial]", run_id=run_id)
            pos_b = _open_single_position(plan_id, leg_b, price, broker, leg_label=" [full]", run_id=run_id)
            opened.extend(p for p in [pos_a, pos_b] if p is not None)
        else:
            pos = _open_single_position(plan_id, trade, price, broker, run_id=run_id)
            if pos is not None:
                opened.append(pos)

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
                # Still open in Alpaca — sync price and P&L
                data = alpaca_broker.get_position_data(ticker)
                if data:
                    current     = data["current_price"]
                    entry       = pos["entry_price"]
                    native_trail = pos.get("native_trail_active") or False

                    if native_trail:
                        # Alpaca's trailing stop bracket handles exit in real-time — just sync
                        db.update("positions", {"id": pos["id"]}, {
                            "current_price":  current,
                            "unrealized_pnl": data["unrealized_pnl"],
                        })
                        updated.append({**pos, **data, "close_reason": None})
                    else:
                        # Manual trailing stop: track high watermark, fire if price pulls back
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
                                "exit_mechanism": "MANUAL_TRAIL",
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
                close_price, exit_mechanism = None, "CLOSED"
                if order_id:
                    close_price, exit_mechanism = alpaca_broker.get_order_fill(order_id)

                # Race-condition guard: market bracket fill may not be visible in
                # Alpaca API within the same GH Actions run that submitted the order.
                # Leave OPEN — the next cycle (15 min later) has confirmed fill status.
                if close_price is None and order_id:
                    try:
                        age_s = (datetime.utcnow() -
                                 datetime.fromisoformat((pos.get("opened_at") or "")[:19])
                                 ).total_seconds()
                        if age_s < 120:
                            updated.append({**pos, "close_reason": None})
                            continue
                    except Exception:
                        pass

                close_price    = close_price    or pos.get("current_price") or pos["entry_price"]
                exit_mechanism = exit_mechanism or "CLOSED"
                close_reason   = "TARGET" if exit_mechanism == "TARGET" else "STOP"

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
                    "exit_mechanism": exit_mechanism,
                    "closed_at":      datetime.utcnow().isoformat(),
                    "status":         "CLOSED",
                })
                db.update("planned_trades", {"id": pos["planned_trade_id"]}, {"status": "CLOSED"})
                updated.append({**pos, "current_price": close_price,
                                "unrealized_pnl": 0, "close_reason": close_reason})

        # Breakeven lock: when Leg A hits TARGET, move Leg B stop to entry
        for u in updated:
            if u.get("close_reason") == "TARGET" and _is_partial_leg(u):
                _lock_breakeven(open_pos, u, broker="alpaca")

        return updated

    # Simulation mode — yfinance price checks
    # Determine effective trail: tighter after Tier 1 lock-in to protect gains while letting winners run
    today = date.today().isoformat()
    _today_closed = db.select("positions", filters={"status": "CLOSED"})
    _today_realized = sum(
        p.get("realized_pnl", 0) or 0
        for p in _today_closed
        if (p.get("closed_at") or "").startswith(today)
        and p.get("close_reason") not in ("CLEANUP", "UNFILLED", "LOCK_IN")
    )
    effective_trail = LOCK_IN_TRAIL_PCT if _today_realized >= DAILY_LOCK_IN_TARGET else TRAIL_PCT

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
        # After Tier 1 lock-in, effective_trail tightens to LOCK_IN_TRAIL_PCT (0.5%) to protect gains
        high_wm     = float(pos.get("high_watermark") or entry)
        new_high_wm = max(high_wm, price)
        eff_stop    = max(stop, round(new_high_wm * (1 - effective_trail), 4))

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
            sim_mechanism = "MANUAL_TRAIL" if close_reason == "STOP" and eff_stop > stop else close_reason
            db.update("positions", {"id": pos["id"]}, {
                "current_price":  price,
                "unrealized_pnl": 0,
                "realized_pnl":   pnl,
                "close_price":    price,
                "close_reason":   close_reason,
                "exit_mechanism": sim_mechanism,
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

    # Breakeven lock: when Leg A hits TARGET, move Leg B stop to entry
    for u in updated:
        if u.get("close_reason") == "TARGET" and _is_partial_leg(u):
            _lock_breakeven(open_pos, u, broker="simulation")

    return updated


def _is_partial_leg(pos: dict) -> bool:
    """True if this position is Leg A (partial exit at ~1% target)."""
    if not PARTIAL_PROFIT_ENABLED:
        return False
    partial_target = round(pos["entry_price"] * (1 + PARTIAL_PROFIT_PCT), 2)
    return abs(pos["target_price"] - partial_target) < 0.03


def _lock_breakeven(open_pos: list, closed_leg_a: dict, broker: str) -> None:
    """After Leg A exits at TARGET, move Leg B's stop to entry (breakeven)."""
    entry = closed_leg_a["entry_price"]
    partial_target = round(entry * (1 + PARTIAL_PROFIT_PCT), 2)
    for leg_b in open_pos:
        if (leg_b["ticker"] == closed_leg_a["ticker"]
                and abs(leg_b["entry_price"] - entry) < 0.02
                and leg_b["target_price"] > partial_target
                and leg_b.get("stop_loss", 0) < entry):   # only if stop not already at breakeven
            db.update("positions", {"id": leg_b["id"]}, {"stop_loss": entry})
            leg_b["stop_loss"] = entry   # update in-memory too
            print(f"  🔒 Breakeven lock: {leg_b['ticker']} Leg B stop → ${entry:.2f}")

            if broker == "alpaca":
                from agents import alpaca_broker
                order_id = leg_b.get("alpaca_order_id")
                if order_id:
                    cancelled = alpaca_broker.cancel_order(order_id)
                    if cancelled:
                        new_id = alpaca_broker.submit_bracket_order(
                            ticker=leg_b["ticker"],
                            shares=leg_b["shares"],
                            entry_price=entry,
                            target_price=leg_b["target_price"],
                            stop_price=entry,
                            action=leg_b["action"],
                        )
                        if new_id:
                            db.update("positions", {"id": leg_b["id"]}, {"alpaca_order_id": new_id})
                            print(f"        Resubmitted Leg B bracket with breakeven stop → {new_id}")


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
            "current_price":  price,
            "realized_pnl":   pnl,
            "close_price":    price,
            "close_reason":   reason,
            "exit_mechanism": reason,
            "closed_at":      datetime.utcnow().isoformat(),
            "status":         "CLOSED",
        })
        db.update("planned_trades", {"id": pos["planned_trade_id"]}, {"status": "CLOSED"})
        closed.append({**pos, "realized_pnl": pnl})

    # Safety sweep: close any Alpaca positions not tracked in our DB (orphaned bracket buys)
    if broker == "alpaca":
        from agents import alpaca_broker
        alpaca_broker.close_all_positions()

    return closed
