"""
Manual override control script — stop or restart the trading agent.

Sets / clears a halt_flag row in scan_results. The orchestrator checks this
flag at the top of every run and skips if it is set.

Usage:
    python control.py --action stop [--reason "Market circuit breaker"]
    python control.py --action restart
"""
import argparse
import sys
from datetime import date, datetime
from core import db


def _clear_existing_flags():
    rows = db.select("scan_results", filters={"scan_type": "halt_flag"})
    for row in rows:
        db.delete("scan_results", {"id": row["id"]})
    return len(rows)


def stop(reason: str):
    cleared = _clear_existing_flags()
    if cleared:
        print(f"  Replaced {cleared} existing halt flag(s).")

    db.insert("scan_results", {
        "date":      date.today().isoformat(),
        "scan_type": "halt_flag",
        "results": {
            "reason":     reason,
            "halted_at":  datetime.now().isoformat(),
            "halted_by":  "manual override (GitHub Actions)",
        },
    })
    print(f"🛑  Trading agent HALTED")
    print(f"    Reason:     {reason}")
    print(f"    Since:      {datetime.now().strftime('%Y-%m-%d %H:%M UTC')}")
    print(f"    To resume:  trigger the 'Restart Trading Agent' workflow")


def restart():
    cleared = _clear_existing_flags()
    if cleared == 0:
        print("✅  No halt flag found — agent is already running.")
    else:
        print(f"✅  Halt flag cleared — trading agent will resume on next scheduled run.")
        print(f"    Cleared {cleared} flag(s).")
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
    args = parser.parse_args()

    if args.action == "stop":
        stop(args.reason)
    elif args.action == "restart":
        restart()
    elif args.action == "status":
        status()
