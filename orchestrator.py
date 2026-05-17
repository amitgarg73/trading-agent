"""
Orchestrator: chains all agents together.
Called by GitHub Actions with --mode premarket | intraday | eod
"""
import sys
import json
import argparse
from datetime import date, datetime
from scanner.scanner import run_scan
from agents import strategy, risk, performance
from agents.portfolio import open_positions
from agents.intraday import run as run_intraday
from core import db


def premarket():
    print(f"\n{'='*60}")
    print(f"  PREMARKET RUN — {datetime.now().strftime('%Y-%m-%d %H:%M ET')}")
    print(f"{'='*60}\n")

    # 1. Scan
    print("[ 1/4 ] Running market scan...")
    candidates = run_scan()
    print(f"        Found {len(candidates)} candidates")

    db.insert("scan_results", {
        "date":      date.today().isoformat(),
        "scan_type": "premarket",
        "results":   {"candidates": candidates},
    })

    if not candidates:
        print("        No candidates — markets may be closed. Exiting.")
        return

    # 2. Strategy
    print("[ 2/4 ] Running strategy agent...")
    strategy_out = strategy.run(candidates)
    print(f"        Selected {len(strategy_out.get('trades', []))} trades")
    print(f"        Market: {strategy_out.get('market_context', '')[:120]}")

    # 3. Risk validation
    print("[ 3/4 ] Running risk agent...")
    risk_out = risk.run(strategy_out)
    approved = risk_out["approved_trades"]
    rejected = risk_out["rejected_trades"]
    print(f"        Approved: {len(approved)} | Rejected: {len(rejected)}")
    for r in rejected:
        print(f"        ✗ {r['ticker']}: {r['reason']}")

    if not approved:
        print("        No approved trades — not enough conviction today.")
        return

    # 4. Open positions
    print("[ 4/4 ] Opening simulated positions...")
    plan = db.upsert("trade_plans", {
        "date":                    date.today().isoformat(),
        "market_context":          risk_out["market_context"],
        "total_estimated_profit":  risk_out["total_estimated_profit"],
        "risk_note":               risk_out["risk_note"],
    })

    opened = open_positions(plan["id"], approved)
    print(f"        Opened {len(opened)} positions\n")

    for t in approved:
        pnl_str = f"${t['estimated_profit']:,.0f}"
        print(f"  {t['action']:10s} {t['ticker']:6s}  entry=${t['entry_price']:.2f}  "
              f"target=${t['target_price']:.2f}  stop=${t['stop_loss']:.2f}  "
              f"est.profit={pnl_str}  [{t['confidence']}]")

    print(f"\n  Total estimated profit: ${risk_out['total_estimated_profit']:,.0f}")
    print(f"  Total max loss:         ${risk_out['total_max_loss']:,.0f}")
    print(f"  Risk note: {risk_out['risk_note']}\n")


def intraday():
    print(f"\n[ INTRADAY ] {datetime.now().strftime('%H:%M ET')}")
    result = run_intraday()
    print(f"  Open: {result['open_positions']} | "
          f"Unrealized P&L: ${result['unrealized_pnl']:,.2f} | "
          f"Closed this check: {result['just_closed']}")
    for c in result.get("closed_details", []):
        icon = "✅" if c["realized_pnl"] > 0 else "🔴"
        print(f"  {icon} {c['ticker']} closed ({c['reason']}): ${c['realized_pnl']:,.2f}")


def eod():
    print(f"\n{'='*60}")
    print(f"  EOD RUN — {datetime.now().strftime('%Y-%m-%d %H:%M ET')}")
    print(f"{'='*60}\n")
    record = performance.run()
    if not record:
        print("  No trades today.")
        return
    icon = "✅" if record["total_pnl"] >= 1000 else "⚠️"
    print(f"  {icon} Daily P&L:     ${record['total_pnl']:,.2f}")
    print(f"  Ending capital: ${record['ending_capital']:,.2f}")
    print(f"  Trades: {record['total_trades']} | Win rate: {record['win_rate']}%")
    print(f"  Best:  {record['best_trade_ticker']} +${record['best_trade_pnl']:,.2f}")
    print(f"  Worst: {record['worst_trade_ticker']} ${record['worst_trade_pnl']:,.2f}\n")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=["premarket", "intraday", "eod"], required=True)
    args = parser.parse_args()

    if args.mode == "premarket":
        premarket()
    elif args.mode == "intraday":
        intraday()
    elif args.mode == "eod":
        eod()


if __name__ == "__main__":
    main()
