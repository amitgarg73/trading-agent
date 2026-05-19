"""
Manual override control script — stop or restart the trading agent.

Sets / clears a halt_flag row in scan_results. The orchestrator checks this
flag at the top of every run and skips if it is set.

Usage:
    python control.py --action stop [--reason "Market circuit breaker"] [--close-positions]
    python control.py --action restart
    python control.py --action status
"""
import argparse
import os
from datetime import date, datetime
from core import db


def _delete_active_flags():
    """Hard-delete active halt_flags (used by stop() to avoid double-flag edge case)."""
    rows = db.select("scan_results", filters={"scan_type": "halt_flag"})
    for row in rows:
        db.delete("scan_results", {"id": row["id"]})
    return len(rows)


def stop(reason: str, close_positions: bool = False):
    closed_tickers = []

    if close_positions:
        alpaca_key    = os.getenv("ALPACA_API_KEY")
        alpaca_secret = os.getenv("ALPACA_SECRET_KEY")
        if not alpaca_key or not alpaca_secret:
            print("⚠️  ALPACA_API_KEY / ALPACA_SECRET_KEY not set — skipping position close.")
        else:
            print("📤  Closing all open Alpaca positions before halting...")
            from agents import alpaca_broker
            alpaca_broker.cancel_all_orders()
            results = alpaca_broker.close_all_positions()
            closed_tickers = [r["ticker"] for r in results if r["success"]]
            failed = [r["ticker"] for r in results if not r["success"]]
            if failed:
                print(f"  ⚠️  Failed to close: {', '.join(failed)} — check Alpaca manually.")
            print(f"  Closed {len(closed_tickers)} position(s).")

    cleared = _delete_active_flags()
    if cleared:
        print(f"  Replaced {cleared} existing halt flag(s).")

    db.insert("scan_results", {
        "date":      date.today().isoformat(),
        "scan_type": "halt_flag",
        "results": {
            "reason":           reason,
            "halted_at":        datetime.now().isoformat(),
            "halted_by":        "manual override (GitHub Actions)",
            "positions_closed": closed_tickers,
        },
    })
    print(f"🛑  Trading agent HALTED")
    print(f"    Reason:          {reason}")
    print(f"    Since:           {datetime.now().strftime('%Y-%m-%d %H:%M UTC')}")
    print(f"    Positions closed: {', '.join(closed_tickers) if closed_tickers else 'none (left open — Alpaca native stops active)'}")
    print(f"    To resume:       trigger the 'Restart Trading Agent' workflow")


def restart():
    active = db.select("scan_results", filters={"scan_type": "halt_flag"})
    if not active:
        print("✅  No halt flag found — agent is already running.")
        return
    for row in active:
        db.update("scan_results", {"id": row["id"]}, {
            "scan_type": "halt_flag_cleared",
            "results": {
                **row.get("results", {}),
                "resumed_at": datetime.now().isoformat(),
            },
        })
    print(f"✅  Halt flag cleared — trading agent will resume on next scheduled run.")
    print(f"    Cleared {len(active)} flag(s).")
    print(f"    Next premarket run: 9:45 AM ET on the next trading day.")


def status():
    rows = db.select("scan_results", filters={"scan_type": "halt_flag"})
    if rows:
        r = rows[0]["results"]
        print(f"🛑  HALTED")
        print(f"    Reason:    {r.get('reason', 'unknown')}")
        print(f"    Since:     {r.get('halted_at', 'unknown')}")
    else:
        print("✅  RUNNING — no halt flag set.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Manual override for trading agent")
    parser.add_argument("--action", choices=["stop", "restart", "status"], required=True,
                        help="stop: set halt flag | restart: clear halt flag | status: check current state")
    parser.add_argument("--reason", default="Manual stop via GitHub Actions",
                        help="Reason logged when stopping (shown in dashboard halt banner)")
    parser.add_argument("--close-positions", action="store_true",
                        help="Market-close all open Alpaca positions before setting halt flag")
    args = parser.parse_args()

    if args.action == "stop":
        stop(args.reason, close_positions=args.close_positions)
    elif args.action == "restart":
        restart()
    elif args.action == "status":
        status()
