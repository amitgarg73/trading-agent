"""
Performance Agent: runs EOD, closes all positions, writes daily P&L record.
"""
from __future__ import annotations
from datetime import date, datetime
from agents.portfolio import close_all_positions
from core import db, ledger
from config.settings import TOTAL_CAPITAL, STRATEGY_TAG
import yfinance as yf


def _alpaca_order_pnl(tag_prefix: str, real_closed: list) -> tuple[float | None, str]:
    """
    Compute per-strategy realized P&L from today's tagged Alpaca bracket orders.
    For bracket exits (target/stop fired by Alpaca): uses actual entry + exit fill prices.
    For manual closes (EOD/lock-in, where bracket legs are cancelled): falls back to DB realized_pnl.
    Returns (pnl, status_note) or (None, reason) if no tagged orders found.
    """
    from agents import alpaca_broker
    from alpaca.trading.requests import GetOrdersRequest
    from alpaca.trading.enums import QueryOrderStatus
    from datetime import time as _t, timezone

    today_start = datetime.combine(date.today(), _t.min).replace(tzinfo=timezone.utc)

    try:
        all_orders = alpaca_broker._client().get_orders(GetOrdersRequest(
            status=QueryOrderStatus.CLOSED,
            after=today_start,
            limit=500,
        ))
    except Exception as e:
        return None, f"order fetch failed: {e}"

    tagged_buys = [
        o for o in all_orders
        if str(o.client_order_id or "").startswith(tag_prefix)
        and str(o.side).lower() == "buy"
        and o.filled_avg_price
    ]

    if not tagged_buys:
        return None, "no tagged orders found"

    db_by_order_id = {
        p.get("alpaca_order_id"): p
        for p in real_closed
        if p.get("alpaca_order_id")
    }

    total = 0.0
    bracket_exits = 0
    manual_fallbacks = 0

    for o in tagged_buys:
        entry_fill = float(o.filled_avg_price)
        qty = float(o.filled_qty or 0)
        if qty == 0:
            continue

        # Bracket exit leg (target or stop fired by Alpaca)
        exit_fill = None
        for leg in (o.legs or []):
            if str(leg.status).lower() in ("filled", "partially_filled") and leg.filled_avg_price:
                exit_fill = float(leg.filled_avg_price)
                break

        if exit_fill is not None:
            total += (exit_fill - entry_fill) * qty
            bracket_exits += 1
        else:
            # Manual close — bracket legs cancelled; use DB realized_pnl as fallback
            db_pos = db_by_order_id.get(str(o.id))
            if db_pos and db_pos.get("realized_pnl") is not None:
                total += float(db_pos["realized_pnl"])
                manual_fallbacks += 1

    note = f"{bracket_exits}b/{manual_fallbacks}m/{len(tagged_buys)} orders"
    return round(total, 2), note


def run(broker: str = "simulation") -> dict:
    today = date.today().isoformat()

    # Close anything still open
    closed = close_all_positions(reason="EOD", broker=broker)

    # Fetch all closed positions for today
    all_closed = db.select("positions", filters={"status": "CLOSED"})
    today_closed = [
        p for p in all_closed
        if p.get("closed_at", "").startswith(today)
    ]

    if not today_closed:
        print("No closed positions today.")
        return {}

    # Exclude operational closes that don't represent real trading P&L
    _EXCLUDE = {"CLEANUP", "UNFILLED"}
    real_closed = [p for p in today_closed if p.get("close_reason") not in _EXCLUDE]
    scored = real_closed if real_closed else today_closed  # fallback if all are excluded

    pnls = [p.get("realized_pnl", 0) for p in scored]
    total_pnl = round(sum(pnls), 2)
    wins  = [p for p in pnls if p > 0]
    losses= [p for p in pnls if p <= 0]

    best  = max(scored, key=lambda p: p.get("realized_pnl", 0))
    worst = min(scored, key=lambda p: p.get("realized_pnl", 0))

    # Retrieve previous ending capital — skip today's own row to handle re-run case
    prev = db.select("daily_performance", order="date", limit=2)
    prev_day = [r for r in prev if r["date"] < today]
    starting_capital = prev_day[0]["ending_capital"] if prev_day else TOTAL_CAPITAL
    ending_capital   = round(starting_capital + total_pnl, 2)

    # Alpaca reconciliation — per-strategy P&L from tagged bracket orders
    alpaca_equity  = None
    friction_gap   = None
    friction_breakdown = None
    if broker == "alpaca":
        try:
            from agents import alpaca_broker
            account = alpaca_broker._client().get_account()
            alpaca_equity = round(float(account.equity), 2)
            print(f"  💰 Alpaca account equity: ${alpaca_equity:,.2f} (combined A+B — informational)")
        except Exception as e:
            print(f"  ⚠️  Alpaca equity fetch failed: {e}")

        tag_prefix = f"strat{STRATEGY_TAG}_"
        alpaca_pnl, note = _alpaca_order_pnl(tag_prefix, real_closed)
        if alpaca_pnl is not None:
            friction_gap = round(alpaca_pnl - total_pnl, 2)
            gap_sign = "+" if friction_gap >= 0 else ""
            print(f"  📊 Strategy {STRATEGY_TAG.upper()} order P&L: ${alpaca_pnl:,.2f} | Our calc: ${total_pnl:,.2f} | Gap: {gap_sign}${friction_gap:,.2f} ({note})")
        else:
            print(f"  📊 Order reconciliation: {note}")

        fills = [
            p for p in real_closed
            if p.get("fill_price") is not None and float(p.get("entry_price") or 0) > 0
        ]
        if fills:
            slippages = [
                abs(float(p["fill_price"]) - float(p["entry_price"])) / float(p["entry_price"]) * 10_000
                for p in fills
            ]
            friction_breakdown = {
                "total_entry_slippage_bps": round(sum(slippages), 1),
                "avg_slippage_bps":         round(sum(slippages) / len(slippages), 1),
                "fills_with_data":          len(fills),
            }
            print(f"  📊 Friction: avg slip {friction_breakdown['avg_slippage_bps']}bps "
                  f"over {len(fills)} fill(s) | total {friction_breakdown['total_entry_slippage_bps']}bps")

    record = {
        "date":                today,
        "starting_capital":    starting_capital,
        "ending_capital":      ending_capital,
        "total_pnl":           total_pnl,
        "win_count":           len(wins),
        "loss_count":          len(losses),
        "total_trades":        len(scored),
        "win_rate":            round(len(wins) / len(scored) * 100, 1) if scored else 0,
        "best_trade_ticker":   best["ticker"],
        "best_trade_pnl":      best.get("realized_pnl", 0),
        "worst_trade_ticker":  worst["ticker"],
        "worst_trade_pnl":     worst.get("realized_pnl", 0),
        "notes":               f"Target: $1,000 | Actual: ${total_pnl:,.2f} | {'✅ HIT' if total_pnl >= 1000 else '⚠️ MISSED'}",
        "alpaca_equity":       alpaca_equity,
        "friction_gap":        friction_gap,
        "friction_breakdown":  friction_breakdown,
    }

    db.upsert("daily_performance", record, on_conflict="date")
    ledger.log("pnl_recorded", {
        "date":            today,
        "total_pnl":       total_pnl,
        "trades":          len(scored),
        "win_rate":        record["win_rate"],
        "ending_capital":  ending_capital,
        "alpaca_equity":   alpaca_equity,
        "friction_gap":    friction_gap,
    })

    _print_unfilled_analysis(today_closed)

    return record


def _print_unfilled_analysis(today_closed: list) -> None:
    """For UNFILLED positions, check if target would have been hit intraday."""
    unfilled = [p for p in today_closed if p.get("close_reason") == "UNFILLED"]
    if not unfilled:
        return

    # Dedupe by ticker (partial splits create two rows per ticker)
    seen = set()
    unique = []
    for p in unfilled:
        if p["ticker"] not in seen:
            seen.add(p["ticker"])
            unique.append(p)

    print(f"\n  📋 UNFILLED order analysis ({len(unique)} ticker(s)):")
    for p in unique:
        ticker = p["ticker"]
        target = p.get("target_price")
        stop   = p.get("stop_loss")
        entry  = p.get("entry_price")
        try:
            data = yf.Ticker(ticker).history(period="1d", interval="5m")
            if data.empty or target is None:
                print(f"     {ticker:6s}  no intraday data available")
                continue
            intraday_high = round(float(data["High"].max()), 2)
            intraday_low  = round(float(data["Low"].min()), 2)
            would_hit_target = intraday_high >= target
            would_hit_stop   = intraday_low  <= stop if stop else False
            outcome = "✅ TARGET would hit" if would_hit_target else ("🔴 STOP would hit" if would_hit_stop else "➖ Neither hit")
            print(f"     {ticker:6s}  entry ${entry}  target ${target}  day high ${intraday_high}  →  {outcome}")
        except Exception as e:
            print(f"     {ticker:6s}  analysis failed: {e}")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Run EOD close and calculate today's P&L.")
    parser.add_argument("--broker", default="alpaca", choices=["alpaca", "simulation"],
                        help="Broker to close positions through (default: alpaca)")
    args = parser.parse_args()

    result = run(broker=args.broker)
    if result:
        print(f"\n  Date:            {result['date']}")
        print(f"  Total P&L:       ${result['total_pnl']:,.2f}")
        print(f"  Trades:          {result['total_trades']}  ({result['win_count']}W / {result['loss_count']}L)")
        print(f"  Win rate:        {result['win_rate']:.1f}%")
        print(f"  Best trade:      {result['best_trade_ticker']}  +${result['best_trade_pnl']:,.2f}")
        print(f"  Worst trade:     {result['worst_trade_ticker']}  ${result['worst_trade_pnl']:,.2f}")
        print(f"  Ending capital:  ${result['ending_capital']:,.0f}")
