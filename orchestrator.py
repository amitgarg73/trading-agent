"""
Orchestrator: chains all agents together.
Called by GitHub Actions with --mode premarket | intraday | eod
"""
import sys
import json
import argparse
from datetime import date, datetime, timedelta
from scanner.scanner import run_scan
from agents import strategy, risk, sector_guard, performance, market_context, news_intel, universe_refresh
from agents.portfolio import open_positions
from agents.intraday import run as run_intraday
from core import db
from config.settings import UNIVERSE


def load_universe() -> list:
    """Return dynamic universe from Supabase if refreshed within 7 days, else static fallback."""
    rows = db.select("scan_results", filters={"scan_type": "universe_refresh"},
                     order="created_at", limit=1)
    if rows:
        row = rows[0]
        age_days = (date.today() - date.fromisoformat(row["date"])).days
        if age_days <= 7:
            tickers = row["results"]["tickers"]
            print(f"        Dynamic universe: {len(tickers)} tickers "
                  f"(refreshed {row['date']}, {age_days}d ago)")
            return tickers
    print(f"        Static universe: {len(UNIVERSE)} tickers (no recent refresh)")
    return UNIVERSE


def premarket(broker: str = "simulation"):
    print(f"\n{'='*60}")
    print(f"  PREMARKET RUN — {datetime.now().strftime('%Y-%m-%d %H:%M ET')} [{broker}]")
    print(f"{'='*60}\n")

    # 0. Market context — volatility gate + futures signal
    mkt = market_context.run()
    if mkt["decision"] == "SKIP":
        print(f"\n  ⛔ TRADING SKIPPED: {mkt['skip_reason']}\n")
        db.insert("scan_results", {
            "date":      date.today().isoformat(),
            "scan_type": "premarket",
            "results":   {
                "skipped":         True,
                "reason":          mkt["skip_reason"],
                "vix":             mkt["vix"],
                "fear_greed":      mkt["fear_greed"],
                "economic_events": mkt["economic_events"],
            },
        })
        return

    today_max_positions = mkt["max_positions"]

    # 1. Scan
    print("[ 1/4 ] Running market scan...")
    candidates = run_scan(universe=load_universe())
    print(f"        Found {len(candidates)} candidates")

    if not candidates:
        print("        No candidates — markets may be closed. Exiting.")
        return

    # 1.5 News intelligence — earnings blackout + news sentiment
    intel = news_intel.run(candidates)
    candidates = intel["filtered_candidates"]

    if intel["blackout_tickers"]:
        for b in intel["blackout_tickers"]:
            print(f"        ⛔ {b['ticker']}: {b['reason']}")

    db.insert("scan_results", {
        "date":      date.today().isoformat(),
        "scan_type": "premarket",
        "results":   {
            "candidates":       candidates,
            "vix":              mkt["vix"],
            "fear_greed":       mkt["fear_greed"],
            "economic_events":  mkt["economic_events"],
            "futures":          mkt["futures"],
            "intl_markets":     mkt["intl_markets"],
            "futures_bias":     mkt["futures_bias"],
            "blackout_tickers": intel["blackout_tickers"],
            "sector_blocked":   [],  # populated after sector guard runs
        },
    })

    if not candidates:
        print("        All candidates blocked (earnings). No trades today.")
        return

    # 2. Strategy
    print("[ 2/4 ] Running strategy agent...")
    full_market_summary = mkt["summary"]
    if intel["news_context"]:
        full_market_summary += "\n\n" + intel["news_context"]

    strategy_out = strategy.run(candidates, market_summary=full_market_summary,
                                max_positions=today_max_positions)
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

    # 3.5 Sector correlation guard (V2d)
    print("[ 3.5/4 ] Running sector guard...")
    sector_out = sector_guard.run(risk_out)
    approved = sector_out["approved_trades"]
    sector_blocked = sector_out.get("sector_blocked", [])
    if sector_blocked:
        print(f"        Sector-blocked: {len(sector_blocked)}")
        for s in sector_blocked:
            print(f"        ✗ {s['ticker']}: {s['reason']}")
    else:
        print(f"        No sector concentration issues")

    if not approved:
        print("        No approved trades after sector guard.")
        return

    # 4. Open positions
    mode_label = "Alpaca paper" if broker == "alpaca" else "simulated"
    print(f"[ 4/4 ] Opening {mode_label} positions...")
    existing = db.select("trade_plans", filters={"date": date.today().isoformat()})
    if existing:
        plan = existing[0]
    else:
        plan = db.insert("trade_plans", {
            "date":                    date.today().isoformat(),
            "market_context":          sector_out["market_context"],
            "total_estimated_profit":  sector_out["total_estimated_profit"],
            "risk_note":               sector_out["risk_note"],
        })

    opened = open_positions(plan["id"], approved, broker=broker)
    print(f"        Opened {len(opened)} positions\n")

    for t in approved:
        pnl_str = f"${t['estimated_profit']:,.0f}"
        print(f"  {t['action']:10s} {t['ticker']:6s}  entry=${t['entry_price']:.2f}  "
              f"target=${t['target_price']:.2f}  stop=${t['stop_loss']:.2f}  "
              f"est.profit={pnl_str}  [{t['confidence']}]")

    print(f"\n  Total estimated profit: ${risk_out['total_estimated_profit']:,.0f}")
    print(f"  Total max loss:         ${risk_out['total_max_loss']:,.0f}")
    print(f"  Risk note: {risk_out['risk_note']}\n")


def intraday(broker: str = "simulation"):
    print(f"\n[ INTRADAY ] {datetime.now().strftime('%H:%M ET')} [{broker}]")
    result = run_intraday(broker=broker)
    print(f"  Open: {result['open_positions']} | "
          f"Unrealized P&L: ${result['unrealized_pnl']:,.2f} | "
          f"Closed this check: {result['just_closed']}")
    for c in result.get("closed_details", []):
        icon = "✅" if c["realized_pnl"] > 0 else "🔴"
        print(f"  {icon} {c['ticker']} closed ({c['reason']}): ${c['realized_pnl']:,.2f}")


def eod(broker: str = "simulation"):
    print(f"\n{'='*60}")
    print(f"  EOD RUN — {datetime.now().strftime('%Y-%m-%d %H:%M ET')} [{broker}]")
    print(f"{'='*60}\n")
    record = performance.run(broker=broker)
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
    parser.add_argument("--mode", choices=["premarket", "intraday", "eod", "universe_refresh"],
                        required=True)
    parser.add_argument("--broker", choices=["simulation", "alpaca"], default="simulation",
                        help="Execution broker (default: simulation)")
    args = parser.parse_args()

    if args.mode == "premarket":
        premarket(broker=args.broker)
    elif args.mode == "intraday":
        intraday(broker=args.broker)
    elif args.mode == "eod":
        eod(broker=args.broker)
    elif args.mode == "universe_refresh":
        universe_refresh.run()


if __name__ == "__main__":
    main()
