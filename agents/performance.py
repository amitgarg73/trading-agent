"""
Performance Agent: runs EOD, closes all positions, writes daily P&L record.
"""
from datetime import date, datetime
from agents.portfolio import close_all_positions
from core import db
from config.settings import TOTAL_CAPITAL


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

    pnls = [p.get("realized_pnl", 0) for p in today_closed]
    total_pnl = round(sum(pnls), 2)
    wins  = [p for p in pnls if p > 0]
    losses= [p for p in pnls if p <= 0]

    best  = max(today_closed, key=lambda p: p.get("realized_pnl", 0))
    worst = min(today_closed, key=lambda p: p.get("realized_pnl", 0))

    # Retrieve previous ending capital or use default
    prev = db.select("daily_performance", order="date", limit=1)
    starting_capital = prev[0]["ending_capital"] if prev else TOTAL_CAPITAL
    ending_capital   = round(starting_capital + total_pnl, 2)

    record = {
        "date":                today,
        "starting_capital":    starting_capital,
        "ending_capital":      ending_capital,
        "total_pnl":           total_pnl,
        "win_count":           len(wins),
        "loss_count":          len(losses),
        "total_trades":        len(today_closed),
        "win_rate":            round(len(wins) / len(today_closed) * 100, 1) if today_closed else 0,
        "best_trade_ticker":   best["ticker"],
        "best_trade_pnl":      best.get("realized_pnl", 0),
        "worst_trade_ticker":  worst["ticker"],
        "worst_trade_pnl":     worst.get("realized_pnl", 0),
        "notes":               f"Target: $1,000 | Actual: ${total_pnl:,.2f} | {'✅ HIT' if total_pnl >= 1000 else '⚠️ MISSED'}",
    }

    db.upsert("daily_performance", record, on_conflict="date")
    return record


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
