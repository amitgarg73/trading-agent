"""
Orchestrator: chains all agents together.
Called by GitHub Actions with --mode premarket | intraday | eod
"""
import sys
import json
import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta
from scanner.scanner import run_scan
from scanner.ml_scorer import score_candidates as ml_score_candidates, is_available as ml_available
from agents import strategy, risk, sector_guard, guardrails, performance, market_context, news_intel, universe_refresh, daily_summary
from agents.portfolio import open_positions
from agents.intraday import run as run_intraday
from core import db
from config.settings import UNIVERSE, STRATEGY_MIN_SCORE, TOTAL_CAPITAL, MAX_POSITIONS, POSITION_SIZE_BY_CONFIDENCE
from agents import alpaca_broker


def _is_trading_day() -> bool:
    """Return False on weekends and NYSE holidays using Alpaca's calendar."""
    if date.today().weekday() >= 5:
        return False
    try:
        from agents.alpaca_broker import _client
        from alpaca.trading.client import TradingClient
        import os
        client = TradingClient(
            os.environ.get("ALPACA_API_KEY", ""),
            os.environ.get("ALPACA_SECRET_KEY", ""),
            paper=True,
        )
        cal = client.get_calendar(start=str(date.today()), end=str(date.today()))
        return len(cal) > 0
    except Exception:
        return True  # fail open


def _is_halted() -> bool:
    """Return True if a manual halt flag is active in Supabase."""
    rows = db.select("scan_results", filters={"scan_type": "halt_flag"})
    if rows:
        r = rows[0].get("results", {})
        print(f"\n🛑  SYSTEM HALTED — {r.get('reason', 'manual override')}")
        print(f"    Halted at: {r.get('halted_at', 'unknown')}")
        print(f"    Trigger the 'Restart Trading Agent' GitHub Actions workflow to resume.\n")
        return True
    return False


def load_universe() -> list:
    """Merge dynamic refresh with static universe. Dynamic adds ATR-screened movers on top;
    static ensures full 430+ curated coverage is always scanned regardless of refresh health."""
    rows = db.select("scan_results", filters={"scan_type": "universe_refresh"},
                     order="created_at", limit=1)
    if rows:
        row = rows[0]
        age_days = (date.today() - date.fromisoformat(row["date"])).days
        if age_days <= 7:
            dynamic = row["results"]["tickers"]
            # Merge: dynamic first (sorted by ATR), then static additions, deduplicated
            merged = list(dict.fromkeys(dynamic + UNIVERSE))
            print(f"        Merged universe: {len(merged)} tickers "
                  f"({len(dynamic)} dynamic + {len(UNIVERSE)} static, refreshed {row['date']}, {age_days}d ago)")
            return merged
    print(f"        Static universe: {len(UNIVERSE)} tickers (no recent refresh)")
    return UNIVERSE


def premarket(broker: str = "simulation"):
    print(f"\n{'='*60}")
    print(f"  PREMARKET RUN — {datetime.now().strftime('%Y-%m-%d %H:%M ET')} [{broker}]")
    print(f"{'='*60}\n")

    if not _is_trading_day():
        print(f"[orchestrator] {date.today()} is not a NYSE trading day — skipping")
        return

    if _is_halted():
        return

    # Concurrent run lock — bail out if premarket already completed today
    today_iso = date.today().isoformat()
    existing_scan = db.select("scan_results", filters={"date": today_iso, "scan_type": "premarket"})
    if existing_scan:
        print(f"  ⚠️  Premarket already ran for {today_iso} — skipping duplicate run.\n")
        return

    # 0a. Morning sweep — close any overnight positions before trading begins
    if broker == "alpaca":
        overnight = alpaca_broker.get_open_tickers()
        if overnight:
            print(f"  ⚠️  OVERNIGHT POSITIONS DETECTED: {overnight}")
            print(f"  Closing before day trading begins...\n")
            alpaca_broker.cancel_all_orders()
            swept = alpaca_broker.close_all_positions()
            print(f"  Swept {len(swept)} overnight position(s). These will appear in today's Alpaca equity delta.\n")

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
    quiet_day           = mkt.get("quiet_day", False)

    # Cap by available capital — never plan more trades than capital can fund
    _open_pos    = db.select("positions", filters={"status": "OPEN"})
    _deployed    = sum(float(p.get("position_size") or 0) for p in _open_pos)
    _available   = TOTAL_CAPITAL - _deployed
    _min_size    = min(POSITION_SIZE_BY_CONFIDENCE.values())
    _capital_cap = max(0, int(_available // _min_size))
    today_max_positions = min(today_max_positions, _capital_cap, MAX_POSITIONS)
    print(f"        Capital: ${_available:,.0f} available → max {today_max_positions} positions "
          f"(market gate: {mkt['max_positions']}, capital gate: {_capital_cap})")

    # 1. Scan
    print("[ 1/4 ] Running market scan...")
    universe = load_universe()
    candidates = run_scan(universe=universe, skip_volume_surge=(mode == "premarket"))
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

    scan_row = db.insert("scan_results", {
        "date":      date.today().isoformat(),
        "scan_type": "premarket",
        "results":   {
            "candidates":        candidates,
            "vix":               mkt["vix"],
            "fear_greed":        mkt["fear_greed"],
            "economic_events":   mkt["economic_events"],
            "futures":           mkt["futures"],
            "intl_markets":      mkt["intl_markets"],
            "futures_bias":      mkt["futures_bias"],
            "blackout_tickers":  intel["blackout_tickers"],
            "sector_blocked":    [],
            "guardrail_blocked": [],
        },
    })

    if not candidates:
        print("        All candidates blocked (earnings). No trades today.")
        return

    # 1.75 Strategy pre-filter — trim to bullish candidates above STRATEGY_MIN_SCORE.
    # Reduces Claude input tokens by ~60-70% without meaningful loss of trade quality.
    # See config/settings.py STRATEGY_MIN_SCORE for full tradeoff documentation.
    pre_filter_count = len(candidates)
    candidates = [c for c in candidates if c.get("technical_score", 0) >= STRATEGY_MIN_SCORE]
    post_prefilter_count = len(candidates)
    if post_prefilter_count < pre_filter_count:
        print(f"[ 1.75/4 ] Strategy pre-filter: {pre_filter_count} → {post_prefilter_count} candidates "
              f"(score ≥ {STRATEGY_MIN_SCORE})")

    if not candidates:
        print("        No candidates above strategy threshold. No trades today.")
        return

    # 1.76 ML scoring — add ml_score probability to each candidate and re-rank.
    # Model predicts P(stock hits +2% intraday tomorrow). If model not found, skips gracefully.
    if ml_available():
        candidates = ml_score_candidates(candidates, vix=mkt.get("vix"))
        candidates.sort(key=lambda x: x.get("ml_score") or 0, reverse=True)
        top_score = candidates[0].get("ml_score", 0) if candidates else 0
        print(f"[ 1.76/4 ] ML scoring: {len(candidates)} candidates ranked by P(hit +2%) "
              f"— top score: {top_score:.2f}")
    else:
        print("[ 1.76/4 ] ML model not found — skipping (run train_model.py to enable)")
    ml_scored_count = sum(1 for c in candidates if c.get("ml_score") is not None)

    # Pipeline tracking — captured after each filter step for dashboard display
    live_price_updated = 0
    vwap_enriched_count = 0
    above_vwap_count = 0

    # 1.8 + 1.85 Live price refresh and intraday signals — run concurrently (Alpaca only).
    if broker == "alpaca":
        tickers = [c["ticker"] for c in candidates]
        with ThreadPoolExecutor(max_workers=2) as executor:
            f_prices  = executor.submit(alpaca_broker.get_live_prices, tickers)
            f_signals = executor.submit(alpaca_broker.get_intraday_signals, tickers)
            live          = f_prices.result()
            intraday_sigs = f_signals.result()

        updated = 0
        for c in candidates:
            ask = live.get(c["ticker"])
            cur = c.get("current_price") or c.get("price") or 0
            if ask and cur and abs(ask - cur) / cur < 0.10:
                c["current_price"] = ask
                updated += 1
        live_price_updated = updated
        print(f"[ 1.8/4 ] Live price refresh: {updated}/{len(candidates)} tickers updated from Alpaca")

        enriched = 0
        for c in candidates:
            sig = intraday_sigs.get(c["ticker"])
            if sig:
                c.update(sig)
                enriched += 1
        candidates.sort(
            key=lambda x: (not x.get("above_vwap", False), -(x.get("rs_vs_spy") or 0))
        )
        above_vwap_count    = sum(1 for c in candidates if c.get("above_vwap"))
        vwap_enriched_count = enriched
        print(f"[ 1.85/4 ] Intraday signals: {enriched}/{len(candidates)} enriched — "
              f"{above_vwap_count} above VWAP")

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
    risk_out = risk.run(strategy_out, quiet_day=quiet_day)
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

    # 3.75 Guardrails (V5)
    guardrail_blocked = []
    if approved:
        print("[ 3.75/4 ] Running guardrails...")
        guard_out = guardrails.filter_trades(approved, broker=broker, universe=universe)
        approved = guard_out["approved_trades"]
        guardrail_blocked = guard_out.get("guardrail_blocked", [])
        if guardrail_blocked:
            print(f"        Guardrail-blocked: {len(guardrail_blocked)}")
        else:
            print(f"        All trades passed guardrails")

    # Persist final enriched state to scan_results for dashboard.
    # Re-save candidates list (now post-filter + VWAP-enriched) so Today tab shows live signals.
    # vwap_signals: {ticker: signals} lookup for position card badges — empty in simulation mode.
    vwap_signals = {
        c["ticker"]: {k: c[k] for k in ("above_vwap", "vwap", "today_pct_change", "rs_vs_spy") if k in c}
        for c in candidates if "above_vwap" in c
    }

    # Capture halt reasons — why no trades were placed (risk/sector/guardrail rejections)
    halt_reasons = []
    if not approved:
        halt_reasons = (
            [r.get("reason") or str(r) for r in rejected] +
            [s.get("reason") or str(s) for s in sector_blocked] +
            guardrail_blocked
        )

    db.update("scan_results", {"id": scan_row["id"]}, {
        "results": {
            **scan_row["results"],
            "candidates":        candidates,
            "sector_blocked":    sector_blocked,
            "guardrail_blocked": guardrail_blocked,
            "vwap_signals":      vwap_signals,
            "halt_reasons":      halt_reasons,
            "pipeline_counts": {
                "post_blackout":      pre_filter_count,
                "post_prefilter":     post_prefilter_count,
                "prefilter_dropped":  pre_filter_count - post_prefilter_count,
                "ml_scored":          ml_scored_count,
                "live_price_updated": live_price_updated,
                "vwap_enriched":      vwap_enriched_count,
                "above_vwap":         above_vwap_count,
                "final_count":        len(candidates),
            },
        }
    })

    if not approved:
        reasons_str = "; ".join(halt_reasons[:3]) if halt_reasons else "unknown"
        print(f"        🛑 No approved trades — {reasons_str}")
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

    run_row = db.insert("daily_runs", {
        "date":       date.today().isoformat(),
        "run_type":   "premarket",
        "run_number": 0,
        "started_at": datetime.utcnow().isoformat(),
    })
    opened = open_positions(plan["id"], approved, broker=broker, run_id=run_row["id"])
    db.update("daily_runs", {"id": run_row["id"]}, {"positions_opened": len(opened)})
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
    if _is_halted():
        return
    # Guard: require a successful premarket scan for today before managing positions.
    # If premarket didn't run (GitHub Actions glitch, crash), skip intraday entirely
    # rather than acting on absent or stale scan data.
    today_iso = date.today().isoformat()
    premarket_today = db.select("scan_results", filters={"date": today_iso, "scan_type": "premarket"})
    if not premarket_today:
        print(f"  ⚠️  INTRADAY SKIPPED — no premarket scan found for {today_iso}. "
              f"Premarket must complete successfully before intraday runs.")
        return
    result = run_intraday(broker=broker)
    print(f"  Open: {result['open_positions']} | "
          f"Unrealized P&L: ${result['unrealized_pnl']:,.2f} | "
          f"Closed this check: {result['just_closed']}")
    for c in result.get("closed_details", []):
        icon = "✅" if (c["realized_pnl"] or 0) > 0 else "🔴"
        print(f"  {icon} {c['ticker']} closed ({c['reason']}): ${c['realized_pnl']:,.2f}")


def eod(broker: str = "simulation"):
    print(f"\n{'='*60}")
    print(f"  EOD RUN — {datetime.now().strftime('%Y-%m-%d %H:%M ET')} [{broker}]")
    print(f"{'='*60}\n")
    if _is_halted():
        return
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

    daily_summary.generate(record, broker=broker)


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
