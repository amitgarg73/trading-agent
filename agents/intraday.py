"""
Intraday Agent: refreshes positions every 30 min, logs updates to DB.
Runs via GitHub Actions schedule during market hours.
"""
from datetime import date, datetime
from agents.portfolio import refresh_positions, close_all_positions
from core import db
from config.settings import (
    DAILY_LOCK_IN_TARGET, DAILY_BONUS_TARGET,
    MAX_POSITIONS, DAILY_LOSS_LIMIT,
    INTRADAY_SCAN_UTC_START, INTRADAY_SCAN_UTC_END,
    INTRADAY_SCAN_MAX_RUNS, INTRADAY_SCAN_MIN_INTERVAL_MINS,
    INTRADAY_TARGET_PCT, MIN_INTRADAY_MOVE_PCT, STRATEGY_MIN_SCORE, UNIVERSE,
    TOTAL_CAPITAL, MAX_PER_SECTOR,
)


def _reconcile_with_alpaca():
    """
    Close any Supabase OPEN positions that don't exist in Alpaca.
    Distinguishes two cases:
      - Entry filled + bracket closed (stop/target fired) → mark STOP/TARGET with real P&L
      - Entry never filled → mark UNFILLED with $0 P&L
    Only runs in Alpaca mode (called explicitly).
    """
    from agents import alpaca_broker
    from alpaca.trading.requests import GetOrdersRequest
    from alpaca.trading.enums import QueryOrderStatus

    alpaca_tickers = alpaca_broker.get_open_tickers()
    open_positions = db.select("positions", filters={"status": "OPEN"})

    if not open_positions:
        return

    # Fetch today's buy orders — two sets:
    #   filled_buys:  entry DID fill → position exited via bracket; let portfolio.refresh_positions()
    #                 resolve the exit via get_order_fill(order_id). Do NOT mark UNFILLED.
    #   pending_buys: entry in flight  → leave OPEN, check next cycle.
    #
    # NOTE: get_orders() returns parent orders only — bracket child legs (stop/target) are
    # NOT included. filled_sells would always be empty for bracket exits, so we don't use it.
    # portfolio.refresh_positions() correctly resolves bracket exits via get_order_fill().
    try:
        today = datetime.utcnow().date().isoformat()
        today_start = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
        all_orders = alpaca_broker._client().get_orders(
            GetOrdersRequest(status=QueryOrderStatus.ALL, limit=500, after=today_start)
        )
        filled_buys = {
            str(o.symbol)
            for o in all_orders
            if getattr(o.side, "value", o.side) == "buy"
            and getattr(o.status, "value", o.status) == "filled"
            and str(o.filled_at or o.submitted_at or "").startswith(today[:10])
        }
        pending_buys = {
            str(o.symbol)
            for o in all_orders
            if getattr(o.side, "value", o.side) == "buy"
            and getattr(o.status, "value", o.status) in ("pending_new", "accepted", "new", "held", "partially_filled")
            and str(o.submitted_at or "").startswith(today[:10])
        }
    except Exception as e:
        print(f"  ⚠️  Reconciliation: order fetch failed — {e}")
        filled_buys  = set()
        pending_buys = set()

    for pos in open_positions:
        if pos["ticker"] not in alpaca_tickers:
            if pos["ticker"] in pending_buys:
                # Buy still in flight — leave OPEN, check next cycle
                print(f"  ⏳ Reconciliation: {pos['ticker']} buy order pending — waiting for fill")
                continue
            if pos["ticker"] in filled_buys:
                # Entry filled — bracket exited; portfolio.refresh_positions() resolves P&L
                continue
            # No filled buy and no pending buy — entry truly never executed
            print(f"  ⚠️  Reconciliation: {pos['ticker']} is OPEN in DB but not in Alpaca — marking UNFILLED")
            db.update("positions", {"id": pos["id"]}, {
                "status":         "CLOSED",
                "close_reason":   "UNFILLED",
                "exit_mechanism": "UNFILLED",
                "closed_at":      datetime.utcnow().isoformat(),
                "realized_pnl":   0,
                "close_price":    pos.get("entry_price"),
            })


def _today_realized_pnl() -> float:
    today = date.today().isoformat()
    closed = db.select("positions", filters={"status": "CLOSED"})
    return sum(
        p.get("realized_pnl", 0) or 0
        for p in closed
        if (p.get("closed_at") or "").startswith(today)
        and p.get("close_reason") not in ("CLEANUP", "UNFILLED", "LOCK_IN")
    )


def _cap_intraday_targets(trades: list) -> list:
    """
    Cap target_price at INTRADAY_TARGET_PCT (1%) for intraday entries.
    Less time remaining in the day means smaller achievable targets.
    Also clamps estimated_profit to match the adjusted target.
    """
    result = []
    for t in trades:
        entry      = t["entry_price"]
        max_target = round(entry * (1 + INTRADAY_TARGET_PCT), 2)
        if t.get("target_price", 0) > max_target:
            t = {**t,
                 "target_price":     max_target,
                 "estimated_profit": round(t.get("shares", 0) * (max_target - entry), 2)}
        result.append(t)
    return result


def _last_scan_minutes_ago(prior_scans: list, now_utc: datetime) -> float:
    """Return minutes since the most recent intraday scan, or infinity if none."""
    timestamps = [
        (s.get("results") or {}).get("scanned_at", "")
        for s in prior_scans
    ]
    timestamps = [t for t in timestamps if t]
    if not timestamps:
        return float("inf")
    try:
        last_dt = datetime.fromisoformat(max(timestamps))
        return (now_utc - last_dt).total_seconds() / 60
    except Exception:
        return float("inf")


def _maybe_run_intraday_scan(broker: str):
    """
    Run a mid-day momentum scan + strategy + risk + open cycle during the entry window.

    Guards (all must pass):
    - UTC hour in [INTRADAY_SCAN_UTC_START, INTRADAY_SCAN_UTC_END) — 11 AM–2 PM ET
    - Max INTRADAY_SCAN_MAX_RUNS runs per day
    - Min INTRADAY_SCAN_MIN_INTERVAL_MINS minutes since last run
    - Open position slots available (< MAX_POSITIONS)
    - Realized P&L above daily loss limit
    - Total P&L not already at bonus target

    Momentum scan (new):
    - Finds stocks already up >= MIN_INTRADAY_MOVE_PCT today, above VWAP
    - Merged with prior-day technical candidates (deduped)
    - All intraday entries capped at INTRADAY_TARGET_PCT (1%) target
    - Partial profit splitting disabled (1% target = same as Leg A target)
    """
    now_utc = datetime.utcnow()
    if not (INTRADAY_SCAN_UTC_START <= now_utc.hour < INTRADAY_SCAN_UTC_END):
        return None

    today = now_utc.date().isoformat()

    prior_scans = db.select("scan_results", filters={"date": today, "scan_type": "intraday_scan"})

    # Max-runs guard
    if len(prior_scans) >= INTRADAY_SCAN_MAX_RUNS:
        return None

    # Min-interval guard
    mins_ago = _last_scan_minutes_ago(prior_scans, now_utc)
    if mins_ago < INTRADAY_SCAN_MIN_INTERVAL_MINS:
        return None

    open_pos   = db.select("positions", filters={"status": "OPEN"})
    open_count = len(open_pos)
    if open_count >= MAX_POSITIONS:
        print(f"  📊 Intraday scan skipped: {open_count}/{MAX_POSITIONS} slots full")
        return None

    realized   = _today_realized_pnl()
    unrealized = sum(p.get("unrealized_pnl", 0) or 0 for p in open_pos)
    total      = realized + unrealized

    if total <= DAILY_LOSS_LIMIT:
        print(f"  ⛔ Intraday scan skipped: net P&L ${total:,.2f} ≤ loss limit ${DAILY_LOSS_LIMIT:,.0f} "
              f"(1% of ${TOTAL_CAPITAL:,}). Resumes when net P&L recovers.")
        return None
    if total >= DAILY_BONUS_TARGET:
        print(f"  🏆 Intraday scan skipped: exceptional day (${total:,.2f}) — protecting gains")
        return None

    run_num         = len(prior_scans) + 1
    available_slots = MAX_POSITIONS - open_count
    print(f"\n  🔍 Intraday scan #{run_num}: {open_count}/{MAX_POSITIONS} slots used | "
          f"realized ${realized:,.2f} | {available_slots} slot(s) available")

    try:
        from agents import market_context, strategy, risk
        from scanner.scanner import run_scan
        from scanner.intraday_momentum import scan as momentum_scan
        from agents.portfolio import open_positions

        mkt       = market_context.run()
        quiet_day = mkt.get("quiet_day", False)

        # Tickers already traded today — don't re-enter (open or closed)
        today_closed = db.select("positions", filters={"status": "CLOSED"})
        traded_today = (
            {p["ticker"] for p in open_pos if p.get("ticker")}
            | {p["ticker"] for p in today_closed
               if p.get("ticker") and (p.get("opened_at") or "").startswith(today)}
        )

        # ── Momentum candidates (stocks already moving today) ────────
        momentum_candidates = [c for c in momentum_scan(UNIVERSE, broker=broker)
                               if c["ticker"] not in traded_today]
        print(f"        Momentum movers: {len(momentum_candidates)} stocks "
              f"up ≥{int(MIN_INTRADAY_MOVE_PCT)}% above VWAP")

        # ── Prior-day technical candidates (fallback / supplement) ───
        technical_candidates = run_scan()
        technical_candidates = [
            c for c in technical_candidates
            if c.get("technical_score", 0) >= STRATEGY_MIN_SCORE
            and c["ticker"] not in traded_today
        ]

        # Merge: momentum first (higher conviction), dedupe by ticker
        seen             = {c["ticker"] for c in momentum_candidates}
        technical_extra  = [c for c in technical_candidates if c["ticker"] not in seen]
        token_cap        = available_slots * 3   # limit tokens sent to Claude
        merged           = (momentum_candidates + technical_extra)[:token_cap]

        if not merged:
            print("  📊 Intraday scan: no candidates found")
            _save_scan_result(today, now_utc, {"candidates": 0, "momentum": 0})
            return None

        print(f"        Total candidates sent to Claude: {len(merged)} "
              f"({min(len(momentum_candidates), token_cap)} momentum + {len(technical_extra[:max(0, token_cap-len(momentum_candidates))])} technical)")

        # Respect market context max_positions (CAUTION may cap below available_slots)
        ctx_max  = mkt.get("max_positions") or available_slots
        strategy_slots = min(available_slots, ctx_max)

        market_note = (
            f"{mkt.get('summary', '')}\n\n"
            f"INTRADAY SCAN #{run_num}: Focus on momentum plays already moving today. "
            f"Prefer stocks with today_pct_change > {int(MIN_INTRADAY_MOVE_PCT)}% and rs_vs_spy > 1.5. "
            f"Use ATR-based stops (0.5% floor) and {int(INTRADAY_TARGET_PCT * 100)}% intraday targets."
        )
        strategy_out = strategy.run(merged, market_summary=market_note,
                                    max_positions=strategy_slots)
        trades = (strategy_out.get("trades") or [])[:strategy_slots]

        if not trades:
            print("  📊 Intraday scan: no trades selected by strategy")
            _save_scan_result(today, now_utc,
                              {"candidates": len(merged), "momentum": len(momentum_candidates), "trades": 0})
            return None

        strategy_out = {**strategy_out, "trades": trades}
        risk_out     = risk.run(strategy_out, quiet_day=quiet_day)
        approved     = risk_out.get("approved_trades") or []

        # Cap targets at 1% after risk validation — risk sees original targets,
        # but actual entries use the tighter intraday cap
        approved = _cap_intraday_targets(approved)

        # Sector guard — prevent overweighting one sector in this scan batch
        approved_by_sector: dict[str, int] = {}
        sector_passed = []
        for t in approved:
            sector = t.get("sector") or "Unknown"
            if approved_by_sector.get(sector, 0) < MAX_PER_SECTOR:
                sector_passed.append(t)
                approved_by_sector[sector] = approved_by_sector.get(sector, 0) + 1
            else:
                print(f"  📊 Sector guard: {t['ticker']} blocked — {sector} at {MAX_PER_SECTOR} limit")
        approved = sector_passed

        # ATR sizer — apply ATR-based stops if ATR data available; passes through otherwise
        if approved:
            from agents import atr_sizer
            intraday_atr = {c["ticker"]: c.get("atr_pct") for c in merged}
            approved, atr_dropped = atr_sizer.apply(approved, intraday_atr)
            if atr_dropped:
                print(f"  📊 ATR sizer dropped {len(atr_dropped)} intraday trade(s): {atr_dropped}")

        if not approved:
            print("  📊 Intraday scan: all trades rejected by risk/sector/ATR")
            _save_scan_result(today, now_utc,
                              {"candidates": len(merged), "momentum": len(momentum_candidates),
                               "rejected": len(trades)})
            return None

        existing = db.select("trade_plans", filters={"date": today})
        plan     = existing[0] if existing else db.insert("trade_plans", {"date": today, "status": "EXECUTED"})

        run_row = db.insert("daily_runs", {
            "date":       today,
            "run_type":   "intraday",
            "run_number": run_num,
            "started_at": now_utc.isoformat(),
        })
        # Disable partial profit split — 1% target == Leg A target, splitting is redundant
        opened = open_positions(plan["id"], approved, broker=broker, enable_partial=False,
                                run_id=run_row["id"])
        db.update("daily_runs", {"id": run_row["id"]}, {"positions_opened": len(opened)})

        print(f"  ✅ Intraday scan #{run_num}: opened {len(opened)} new position(s)")
        result = {
            "candidates": len(merged), "momentum": len(momentum_candidates),
            "approved": len(approved), "opened": len(opened),
        }
        _save_scan_result(today, now_utc, result)
        return result

    except Exception as e:
        print(f"  ⚠️  Intraday scan error: {e}")
        return None


def _save_scan_result(today: str, now_utc: datetime, result: dict) -> None:
    db.insert("scan_results", {
        "date":      today,
        "scan_type": "intraday_scan",
        "results":   {**result, "scanned_at": now_utc.isoformat()},
    })


def run(broker: str = "simulation") -> dict:
    now = datetime.utcnow().isoformat()

    if broker == "alpaca":
        _reconcile_with_alpaca()

    _maybe_run_intraday_scan(broker=broker)

    updated = refresh_positions(broker=broker)

    # Tiered lock-in logic
    # Tier 1 ($716 realized): stop closing everything — let open positions ride with tighter trail
    # Tier 2 ($1,000 realized+unrealized): close everything — protect the exceptional day
    realized   = _today_realized_pnl()
    still_open = [p for p in updated if not p.get("close_reason")]
    unrealized = sum(p.get("unrealized_pnl", 0) or 0 for p in still_open)
    total      = realized + unrealized

    if total >= DAILY_BONUS_TARGET and still_open:
        print(f"\n  🏆 BONUS TARGET: Total P&L ${total:,.2f} ≥ ${DAILY_BONUS_TARGET:,.0f} — locking in exceptional day")
        print(f"     Closing {len(still_open)} position(s).\n")
        close_all_positions(reason="LOCK_IN", broker=broker)
    elif realized >= DAILY_LOCK_IN_TARGET:
        if still_open:
            print(f"\n  🎯 LOCK-IN Tier 1: Realized ${realized:,.2f} ≥ ${DAILY_LOCK_IN_TARGET:,.0f}")
            print(f"     Letting {len(still_open)} position(s) ride to ${DAILY_BONUS_TARGET:,.0f} target (total ${total:,.2f}).")
            print(f"     Tighter trail active on open positions (simulation) — Alpaca native trail continues.\n")
        else:
            print(f"  🎯 Daily target locked in (${realized:,.2f}) — no open positions remaining.")

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
            pnl = c.get('realized_pnl') or 0
            print(f"    {c['ticker']:6s}  {c['reason']:8s}  ${pnl:,.2f}")
