"""
Orchestrator: chains all agents together.
Called by GitHub Actions with --mode premarket | intraday | eod
"""
from __future__ import annotations
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
from core import db, ledger
from core.alerts import send_alert
from config.settings import (UNIVERSE, STRATEGY_MIN_SCORE, PREMARKET_MIN_SCORE, TOTAL_CAPITAL,
                             MAX_POSITIONS, POSITION_SIZE_BY_CONFIDENCE, STRATEGY_TAG,
                             STRONG_SECTOR_THRESHOLD, WEAK_SECTOR_THRESHOLD)
from agents import alpaca_broker


def _sweep_and_verify() -> bool:
    """
    Close overnight Alpaca positions with one retry and a verification step.
    Returns True if Alpaca is clear after either attempt.
    Returns False if positions remain after both — halt flag is set and alert sent.

    Only acts on positions tracked in our DB as OPEN — skips Strategy B's positions
    on the shared Alpaca account. Closes use a strategy tag filter for the same reason.
    """
    import time

    overnight = alpaca_broker.get_open_tickers()
    if not overnight:
        return True

    # Cross-strategy guard: only act on positions we opened (in our DB as OPEN)
    our_open = {p["ticker"] for p in db.select("positions", filters={"status": "OPEN"})}
    ours_overnight = overnight & our_open
    if not ours_overnight:
        return True

    _tag = f"strat{STRATEGY_TAG}_"
    print(f"  ⚠️  OVERNIGHT POSITIONS DETECTED: {ours_overnight}")
    print("  Closing before day trading begins...")
    alpaca_broker.cancel_all_orders()
    alpaca_broker.close_all_positions(tag_prefix=_tag)

    time.sleep(10)
    remaining = alpaca_broker.get_open_tickers() & our_open
    if not remaining:
        print("  ✅ Morning sweep complete — Alpaca is clear.\n")
        return True

    print(f"  ⚠️  Positions still open after first sweep: {remaining} — retrying...")
    alpaca_broker.close_all_positions(tag_prefix=_tag)
    time.sleep(10)
    remaining = alpaca_broker.get_open_tickers() & our_open
    if not remaining:
        print("  ✅ Cleared on second attempt.\n")
        return True

    tickers = sorted(remaining)
    ledger.log("sweep_failed", {"tickers": tickers})
    db.insert("scan_results", {
        "date":      date.today().isoformat(),
        "scan_type": "halt_flag",
        "results": {
            "reason":     f"Morning sweep failed — positions still open: {tickers}",
            "halted_at":  datetime.utcnow().isoformat(),
            "halted_by":  "sweep_and_verify",
            "positions_closed": [],
        },
    })
    send_alert(
        "TRADING HALTED — Morning Sweep Failed",
        f"Positions still open after 2 close attempts: {', '.join(tickers)}\n\n"
        f"STEP 1 — Close positions manually in Alpaca:\n"
        f"  https://app.alpaca.markets/paper/dashboard/overview\n"
        f"  Find these tickers and close each one: {', '.join(tickers)}\n\n"
        f"STEP 2 — Restart the trading agent:\n"
        f"  https://github.com/amitgarg73/trading-agent/actions/workflows/restart.yml\n"
        f"  Click 'Run workflow' then confirm.\n\n"
        f"No new trades will open until you complete both steps.",
    )
    print(f"  ❌ Sweep failed after 2 attempts — premarket halted. Alert sent.")
    return False


def _log_run(mode: str, status: str, details: dict | None = None) -> None:
    """Write a run-status record to scan_results for observability."""
    payload = {"mode": mode, "status": status, "ts": datetime.utcnow().isoformat(),
               **(details or {})}
    ledger.log(f"run_{status}", {"mode": mode, **(details or {})})
    try:
        db.insert("scan_results", {
            "date":      date.today().isoformat(),
            "scan_type": f"run_{mode}_{status}",
            "results":   payload,
        })
    except Exception as e:
        print(f"  ⚠️  _log_run({mode}, {status}) failed: {e}")


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
    static ensures full 430+ curated coverage is always scanned regardless of refresh health.
    Reads from local cache file written by universe_refresh — no Supabase call."""
    from pathlib import Path
    cache_path = Path(__file__).parent / "config" / "universe_cache.json"
    try:
        cache = json.loads(cache_path.read_text())
        age_days = (date.today() - date.fromisoformat(cache["date"])).days
        if age_days <= 35:  # generous window — monthly refresh, covers missed months
            dynamic = cache["tickers"]
            merged = list(dict.fromkeys(dynamic + UNIVERSE))
            print(f"        Merged universe: {len(merged)} tickers "
                  f"({len(dynamic)} dynamic + {len(UNIVERSE)} static, "
                  f"cache from {cache['date']}, {age_days}d ago)")
            return merged
        print(f"        Universe cache is stale ({age_days}d old) — using static fallback")
    except Exception:
        pass
    print(f"        Static universe: {len(UNIVERSE)} tickers (no cache found)")
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
        if not _sweep_and_verify():
            return

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
    candidates = run_scan(universe=universe, skip_volume_surge=True)
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

    # 1.75 Strategy pre-filter — trim to bullish candidates above PREMARKET_MIN_SCORE.
    # Higher bar than intraday (5 vs 4) — premarket candidates haven't proved today's move yet.
    pre_filter_count = len(candidates)
    candidates = [c for c in candidates if c.get("technical_score", 0) >= PREMARKET_MIN_SCORE]
    post_prefilter_count = len(candidates)
    if post_prefilter_count < pre_filter_count:
        print(f"[ 1.75/4 ] Strategy pre-filter: {pre_filter_count} → {post_prefilter_count} candidates "
              f"(score ≥ {PREMARKET_MIN_SCORE})")

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
    # Also fetch sector ETF signals to build sector conviction guidance for Claude.
    _SECTOR_ETFS = ["XLK", "XLF", "XLE", "XLV", "XLI", "XLC", "XLY", "XLP", "XLB", "XLRE", "XLU"]
    intraday_sigs = {}
    if broker == "alpaca":
        tickers = [c["ticker"] for c in candidates]
        signal_tickers = list(set(tickers + _SECTOR_ETFS))
        with ThreadPoolExecutor(max_workers=2) as executor:
            f_prices  = executor.submit(alpaca_broker.get_live_prices, tickers)
            f_signals = executor.submit(alpaca_broker.get_intraday_signals, signal_tickers)
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

        # Drop stocks that are already extended from open on weak volume.
        # >3% above open + volume < 0.7x = chasing exhausted momentum; skip.
        pre_ext = len(candidates)
        candidates = [
            c for c in candidates
            if not (
                (c.get("today_pct_change") or 0) > 3.0
                and (c.get("volume_ratio") or 0) < 0.7
            )
        ]
        dropped = pre_ext - len(candidates)
        if dropped:
            print(f"[ 1.86/4 ] Extension filter: dropped {dropped} extended-low-vol candidate(s)")

        # Drop stocks still inside the opening range — no breakout confirmed.
        # above_orb=False means price is below the ORB high; entering is buying consolidation, not momentum.
        pre_orb = len(candidates)
        candidates = [c for c in candidates if c.get("above_orb") is not False]
        dropped_orb = pre_orb - len(candidates)
        if dropped_orb:
            print(f"[ 1.87/4 ] ORB filter: dropped {dropped_orb} inside-range candidate(s)")

        # Drop stocks priced in the top 15% of today's day range — entering near the high means
        # little upside left and outsized retracement risk.
        pre_top = len(candidates)
        candidates = [
            c for c in candidates
            if not (
                c.get("day_high") and c.get("day_low")
                and (c["day_high"] - c["day_low"]) > 0
                and ((c.get("current_price") or c.get("price") or 0) - c["day_low"]) /
                    (c["day_high"] - c["day_low"]) > 0.85
            )
        ]
        dropped_top = pre_top - len(candidates)
        if dropped_top:
            print(f"[ 1.88/4 ] Top-of-range filter: dropped {dropped_top} near-day-high candidate(s)")

    elif broker == "simulation":
        # Compute RS vs SPY via yfinance — gives Claude a relative-strength signal
        # that would otherwise require Alpaca live quotes (alpaca mode only).
        try:
            import yfinance as yf
            sim_tickers = [c["ticker"] for c in candidates] + ["SPY"]
            hist = yf.download(sim_tickers, period="2d", progress=False, auto_adjust=True)["Close"]
            day_ret = hist.pct_change().iloc[-1]
            spy_ret = float(day_ret["SPY"]) if "SPY" in day_ret and day_ret["SPY"] == day_ret["SPY"] else None
            if spy_ret:
                enriched = 0
                for c in candidates:
                    tkr = c["ticker"]
                    if tkr in day_ret and day_ret[tkr] == day_ret[tkr]:
                        stock_ret = float(day_ret[tkr])
                        c["rs_vs_spy"] = round(stock_ret / spy_ret, 2) if spy_ret != 0 else None
                        c["today_pct_change"] = round(stock_ret * 100, 2)
                        enriched += 1
                candidates.sort(key=lambda x: -(x.get("rs_vs_spy") or 0))
                above_spy = sum(1 for c in candidates if (c.get("rs_vs_spy") or 0) > 1.0)
                print(f"[ 1.85/4 ] RS vs SPY (simulation): {enriched}/{len(candidates)} enriched "
                      f"— {above_spy} outperforming SPY")
        except Exception as e:
            print(f"[ 1.85/4 ] RS vs SPY (simulation): skipped — {e}")

    # 2. Strategy
    print("[ 2/4 ] Running strategy agent...")
    full_market_summary = mkt["summary"]
    if intel["news_context"]:
        full_market_summary += "\n\n" + intel["news_context"]

    # Sector conviction guidance — tell Claude which sectors are hot/weak today
    sector_perf = {etf: intraday_sigs[etf]["today_pct_change"]
                   for etf in _SECTOR_ETFS if etf in intraday_sigs}
    if sector_perf:
        ranked = sorted(sector_perf.items(), key=lambda x: -x[1])
        perf_lines = "  ".join(f"{etf} {pct:+.1f}%" for etf, pct in ranked)
        hot  = [etf for etf, pct in sector_perf.items() if pct >= STRONG_SECTOR_THRESHOLD]
        weak = [etf for etf, pct in sector_perf.items() if pct <= WEAK_SECTOR_THRESHOLD]
        sector_guidance = f"\n\nSECTOR ETF PERFORMANCE TODAY: {perf_lines}"
        if hot:
            sector_guidance += f"\nHOT sectors (≥+{STRONG_SECTOR_THRESHOLD}%): {', '.join(hot)} — prioritize stocks from these sectors."
        if weak:
            sector_guidance += f"\nWEAK sectors (≤{WEAK_SECTOR_THRESHOLD}%): {', '.join(weak)} — max 1 pick each; avoid if alternatives exist."
        full_market_summary += sector_guidance
        print(f"        Sector guidance: hot={hot or 'none'} weak={weak or 'none'}")

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

    # 3.6 ATR sizing (P0) — replace formula stop with ATR-based stop + constant $150 risk
    atr_dropped = []
    if approved:
        from agents import atr_sizer
        candidates_atr = {c["ticker"]: c.get("atr_pct") for c in candidates}
        approved, atr_dropped = atr_sizer.apply(approved, candidates_atr)
        if atr_dropped:
            print(f"[ 3.6/4 ] ATR sizer dropped {len(atr_dropped)} trade(s) (R:R < 1 after ATR stop)")
        else:
            print(f"[ 3.6/4 ] ATR sizer applied to {len(approved)} trade(s)")

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
            atr_dropped +
            [g.get("reason") or str(g) for g in guardrail_blocked]
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
    # Enrich approved trades with scanner signals for persistence in planned_trades.
    signal_lookup = {c["ticker"]: c for c in candidates}
    for t in approved:
        sig = signal_lookup.get(t["ticker"], {})
        t["_technical_score"] = sig.get("technical_score")
        t["_rsi"]             = sig.get("rsi")
        t["_volume_ratio"]    = sig.get("volume_ratio")
        t["_scanner_signals"] = sig.get("signals", [])

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

    run_row = db.upsert("daily_runs", {
        "date":       date.today().isoformat(),
        "run_type":   "premarket",
        "run_number": 0,
        "started_at": datetime.utcnow().isoformat(),
    }, on_conflict="date,run_number")
    opened = open_positions(plan["id"], approved, broker=broker, run_id=run_row["id"])
    db.update("daily_runs", {"id": run_row["id"]}, {"positions_opened": len(opened)})
    print(f"        Opened {len(opened)} positions\n")

    for t in approved:
        pnl_str = f"${t['estimated_profit']:,.0f}"
        print(f"  {t['action']:10s} {t['ticker']:6s}  entry=${t['entry_price']:.2f}  "
              f"target=${t['target_price']:.2f}  stop=${t['stop_loss']:.2f}  "
              f"est.profit={pnl_str}  [{t['confidence']}]")

    print(f"\n  Total estimated profit: ${sum(t['estimated_profit'] for t in approved):,.0f}")
    print(f"  Total max loss:         ${sum(t['max_loss'] for t in approved):,.0f}")
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
    if not _is_trading_day():
        print(f"[orchestrator] {date.today()} is not a NYSE trading day — skipping EOD")
        return
    if _is_halted():
        return

    # Dedup — EOD should run exactly once per day
    today_iso = date.today().isoformat()
    if db.select("scan_results", filters={"date": today_iso, "scan_type": "run_eod_started"}):
        print(f"  ⚠️  EOD already ran for {today_iso} — skipping duplicate run.")
        return

    _log_run("eod", "started")

    try:
        # Fix 6: Reclassify phantom STOPs — positions where entry never filled (fill_price NULL)
        # but were labelled STOP instead of UNFILLED. Prevents them from inflating stop rate
        # and incorrectly contributing to daily loss limit checks.
        phantoms = [
            p for p in db.select("positions", filters={"status": "CLOSED"})
            if p.get("close_reason") == "STOP"
            and p.get("fill_price") is None
            and (p.get("realized_pnl") or 0) == 0
            and (p.get("opened_at") or "").startswith(today_iso)
        ]
        if phantoms:
            for p in phantoms:
                db.update("positions", {"id": p["id"]}, {"close_reason": "UNFILLED"})
            print(f"  🔧 Reclassified {len(phantoms)} phantom STOP(s) → UNFILLED: "
                  f"{[p['ticker'] for p in phantoms]}")

        # Alert if there are open positions that the close step should handle
        open_before = db.select("positions", filters={"status": "OPEN"})

        record = performance.run(broker=broker)
        if not record:
            print("  No trades today.")
            _log_run("eod", "completed", {"trades": 0})
            return

        # Alert if positions were open but nothing got closed
        if broker == "alpaca" and open_before:
            open_after = db.select("positions", filters={"status": "OPEN"})
            still_open = [p["ticker"] for p in open_after
                          if p["id"] in {x["id"] for x in open_before}]
            if still_open:
                send_alert(
                    f"[Trading Agent A] EOD close FAILED — {len(still_open)} position(s) still open",
                    f"Date: {today_iso}\nStill open: {still_open}\n"
                    f"These positions will carry overnight. Manual close required.",
                )

        icon = "✅" if record["total_pnl"] >= 1000 else "⚠️"
        print(f"  {icon} Daily P&L:     ${record['total_pnl']:,.2f}")
        print(f"  Ending capital: ${record['ending_capital']:,.2f}")
        print(f"  Trades: {record['total_trades']} | Win rate: {record['win_rate']}%")
        print(f"  Best:  {record['best_trade_ticker']} +${record['best_trade_pnl']:,.2f}")
        print(f"  Worst: {record['worst_trade_ticker']} ${record['worst_trade_pnl']:,.2f}\n")

        daily_summary.generate(record, broker=broker)
        _log_run("eod", "completed", {"total_pnl": record["total_pnl"], "trades": record["total_trades"]})

    except Exception as e:
        _log_run("eod", "failed", {"error": str(e)})
        send_alert(f"[Trading Agent A] EOD run FAILED — {today_iso}", f"Error: {e}")
        raise


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
