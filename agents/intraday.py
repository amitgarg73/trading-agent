"""
Intraday Agent: refreshes positions every 30 min, logs updates to DB.
Runs via GitHub Actions schedule during market hours.
"""
from datetime import datetime
from agents.portfolio import refresh_positions
from core import db


def _reconcile_with_alpaca():
    """
    Close any Supabase OPEN positions that don't exist in Alpaca.
    Catches the rare case where a bracket order was submitted but the entry leg never filled.
    Only runs in Alpaca mode (called explicitly).
    """
    from agents import alpaca_broker

    alpaca_tickers = alpaca_broker.get_open_tickers()
    open_positions = db.select("positions", filters={"status": "OPEN"})

    for pos in open_positions:
        if pos["ticker"] not in alpaca_tickers:
            print(f"  ⚠️  Reconciliation: {pos['ticker']} is OPEN in DB but not in Alpaca — marking UNFILLED")
            db.update("positions", {"id": pos["id"]}, {
                "status":       "CLOSED",
                "close_reason": "UNFILLED",
                "closed_at":    datetime.utcnow().isoformat(),
                "realized_pnl": 0,
                "close_price":  pos.get("entry_price"),
            })


def run(broker: str = "simulation") -> dict:
    now = datetime.utcnow().isoformat()

    if broker == "alpaca":
        _reconcile_with_alpaca()

    updated = refresh_positions(broker=broker)

    open_pos    = [p for p in updated if not p.get("close_reason")]
    just_closed = [p for p in updated if p.get("close_reason")]

    summary = {
        "checked_at":     now,
        "open_positions": len(open_pos),
        "just_closed":    len(just_closed),
        "unrealized_pnl": sum(p.get("unrealized_pnl") or 0 for p in open_pos),
        "realized_pnl":   sum(p.get("realized_pnl") or 0 for p in just_closed),
        "closed_details": [
            {
                "ticker":       p["ticker"],
                "reason":       p["close_reason"],
                "realized_pnl": p.get("realized_pnl", 0),
            }
            for p in just_closed
        ],
    }

    # Persist scan snapshot
    db.insert("scan_results", {
        "date":      datetime.utcnow().date().isoformat(),
        "scan_type": "intraday",
        "results":   summary,
    })

    return summary


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Sync open positions from Alpaca and check target/stop hits.")
    parser.add_argument("--broker", default="alpaca", choices=["alpaca", "simulation"],
                        help="Broker to sync from (default: alpaca)")
    args = parser.parse_args()

    result = run(broker=args.broker)
    print(f"\n  Open positions:  {result['open_positions']}")
    print(f"  Just closed:     {result['just_closed']}")
    print(f"  Unrealized P&L:  ${result['unrealized_pnl']:,.2f}")
    print(f"  Realized P&L:    ${result['realized_pnl']:,.2f}")
    if result["closed_details"]:
        print(f"\n  Closed this run:")
        for c in result["closed_details"]:
            print(f"    {c['ticker']:6s}  {c['reason']:8s}  ${c['realized_pnl']:,.2f}")
