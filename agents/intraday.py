"""
Intraday Agent: refreshes positions every 30 min, logs updates to DB.
Runs via GitHub Actions schedule during market hours.
"""
from datetime import date, datetime
from agents.portfolio import refresh_positions, close_all_positions
from core import db, ledger
from config.settings import (
    DAILY_LOCK_IN_TARGET, DAILY_BONUS_TARGET,
    MAX_POSITIONS, MAX_DAILY_ENTRIES, DAILY_LOSS_LIMIT,
    INTRADAY_SCAN_UTC_START, INTRADAY_SCAN_UTC_END, INTRADAY_ENTRY_CUTOFF_UTC,
    INTRADAY_SCAN_MAX_RUNS, INTRADAY_SCAN_MIN_INTERVAL_MINS,
    INTRADAY_TARGET_PCT, INTRADAY_STOP_PCT, MIN_INTRADAY_MOVE_PCT,
    STRATEGY_MIN_SCORE, STRONG_SECTOR_THRESHOLD, WEAK_SECTOR_THRESHOLD,
    UNIVERSE, TOTAL_CAPITAL, MAX_PER_SECTOR, MIN_SPY_MOVE_PCT,
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
        from datetime import timezone
        today = datetime.utcnow().date().isoformat()
        today_start = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0,
                                                tzinfo=timezone.utc)
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
        # Map order_id → fill_price for positions awaiting backfill
        filled_by_order_id = {
            str(o.id): float(o.filled_avg_price)
            for o in all_orders
            if getattr(o.side, "value", o.side) == "buy"
            and getattr(o.status, "value", o.status) == "filled"
            and o.filled_avg_price
        }
        # Map ticker → order object for pending buys (to check age for stale cancellation)
        pending_buy_orders = {
            str(o.symbol): o
            for o in all_orders
            if getattr(o.side, "value", o.side) == "buy"
            and getattr(o.status, "value", o.status) in ("pending_new", "accepted", "new", "held", "partially_filled")
            and str(o.submitted_at or "").startswith(today[:10])
        }
        pending_buys = set(pending_buy_orders.keys())
        # Map trail_order_id → fill_price for native trailing stop exits
        trail_orders_filled = {
            str(o.id): float(o.filled_avg_price)
            for o in all_orders
            if getattr(o.side, "value", o.side) == "sell"
            and "trailing" in str(getattr(o, "order_type", "") or "").lower()
            and getattr(o.status, "value", o.status) == "filled"
            and o.filled_avg_price
        }
    except Exception as e:
        print(f"  ⚠️  Reconciliation: order fetch failed — {e}")
        ledger.log("reconcile_failed", {"error": str(e)})
        db.insert("scan_results", {
            "date":      datetime.utcnow().date().isoformat(),
            "scan_type": "reconcile_failed",
            "results":   {"error": str(e), "ts": datetime.utcnow().isoformat()},
        })
        from core import alerts
        alerts.send_alert(
            "Reconciliation Failed",
            f"Order fetch exception: {e}\nCycle skipped — unfilled orders undetected this cycle.",
        )
        return

    # Backfill fill_price (and submit trail order) for positions whose entry was still
    # pending at submission time but have since filled.
    from config.settings import USE_NATIVE_TRAILING_STOP, TRAIL_PCT
    for pos in open_positions:
        if pos.get("fill_price") is None:
            order_id = pos.get("alpaca_order_id")
            if order_id and order_id in filled_by_order_id:
                fill_px = filled_by_order_id[order_id]
                try:
                    db.update("positions", {"id": pos["id"]}, {"fill_price": fill_px})
                    print(f"  fill_price backfilled: {pos['ticker']} @ ${fill_px:.2f}")
                except Exception:
                    pass
                # Submit trailing stop now that entry is confirmed filled.
                if USE_NATIVE_TRAILING_STOP and not pos.get("trail_order_id"):
                    trail_id = alpaca_broker.submit_trailing_stop(
                        ticker=pos["ticker"],
                        shares=int(pos["shares"]),
                        trail_pct=TRAIL_PCT,
                    )
                    if trail_id:
                        try:
                            db.update("positions", {"id": pos["id"]}, {
                                "trail_order_id":      trail_id,
                                "native_trail_active": True,
                            })
                            print(f"  Trail stop submitted (post-fill): {pos['ticker']} {TRAIL_PCT*100:.1f}% → {trail_id}")
                        except Exception:
                            pass

    for pos in open_positions:
        if pos["ticker"] not in alpaca_tickers:
            if pos["ticker"] in pending_buys:
                order = pending_buy_orders[pos["ticker"]]
                try:
                    submitted_str = str(order.submitted_at or "")[:19].replace("Z", "")
                    age_min = (datetime.utcnow() - datetime.fromisoformat(submitted_str)).total_seconds() / 60
                except Exception:
                    age_min = 0
                if age_min <= 5:
                    print(f"  ⏳ Reconciliation: {pos['ticker']} buy pending ({age_min:.0f}m) — waiting")
                    continue
                # Stale limit order: cancel in Alpaca and mark UNFILLED
                print(f"  ⚠️  Reconciliation: {pos['ticker']} limit order stale ({age_min:.0f}m) — cancelling")
                try:
                    alpaca_broker._client().cancel_order_by_id(str(order.id))
                except Exception as ce:
                    print(f"  ⚠️  Cancel failed for {pos['ticker']}: {ce}")
                ledger.log("trade_unfilled", {"ticker": pos["ticker"], "reason": "stale_limit",
                                              "age_min": round(age_min, 1)})
                db.update("positions", {"id": pos["id"]}, {
                    "status": "CLOSED", "close_reason": "UNFILLED",
                    "exit_mechanism": "UNFILLED",
                    "closed_at": datetime.utcnow().isoformat(),
                    "realized_pnl": 0, "close_price": pos.get("entry_price"),
                })
                db.update("planned_trades", {"id": pos["planned_trade_id"]}, {"status": "CLOSED"})
                continue
            # Check trailing stop exit before bracket — trail fires in real-time,
            # so if both trail and bracket-stop triggered in the same cycle, trail wins.
            trail_id = pos.get("trail_order_id")
            if trail_id and trail_id in trail_orders_filled:
                close_price = trail_orders_filled[trail_id]
                entry  = float(pos.get("fill_price") or pos["entry_price"])
                shares = int(pos["shares"])
                pnl    = round(shares * (close_price - entry), 2)
                alpaca_broker.cancel_order(pos.get("alpaca_order_id", ""))  # cancel bracket legs
                db.update("positions", {"id": pos["id"]}, {
                    "status":         "CLOSED",
                    "close_reason":   "NATIVE_TRAIL",
                    "exit_mechanism": "NATIVE_TRAIL",
                    "close_price":    close_price,
                    "realized_pnl":   pnl,
                    "closed_at":      datetime.utcnow().isoformat(),
                })
                ledger.log("trade_closed", {
                    "ticker":      pos["ticker"],
                    "mechanism":   "NATIVE_TRAIL",
                    "close_price": close_price,
                    "pnl":         pnl,
                    "shares":      shares,
                })
                print(f"  🔒 Native trail exit: {pos['ticker']} @ ${close_price:.2f} P&L=${pnl:+.2f}")
                continue

            if pos["ticker"] in filled_buys:
                # Entry filled but position gone from Alpaca — bracket exited natively.
                # Resolve the exit price and mechanism via the parent order's filled legs.
                order_id = pos.get("alpaca_order_id")
                if order_id:
                    close_price, mechanism = alpaca_broker.get_order_fill(order_id)
                    if close_price:
                        entry  = float(pos.get("fill_price") or pos["entry_price"])
                        shares = int(pos["shares"])
                        pnl    = round(shares * (close_price - entry), 2)
                        # Cancel trailing stop order if it exists — bracket already exited
                        if pos.get("trail_order_id"):
                            alpaca_broker.cancel_order(pos["trail_order_id"])
                        db.update("positions", {"id": pos["id"]}, {
                            "status":         "CLOSED",
                            "close_reason":   mechanism or "BRACKET",
                            "exit_mechanism": mechanism or "BRACKET",
                            "close_price":    close_price,
                            "realized_pnl":   pnl,
                            "closed_at":      datetime.utcnow().isoformat(),
                        })
                        ledger.log("trade_closed", {
                            "ticker":      pos["ticker"],
                            "mechanism":   mechanism or "BRACKET",
                            "close_price": close_price,
                            "pnl":         pnl,
                            "shares":      int(pos["shares"]),
                        })
                        print(f"  ✅ Bracket exit: {pos['ticker']} → {mechanism} @ ${close_price:.2f} P&L=${pnl:+.2f}")
                    else:
                        print(f"  ⚠️  {pos['ticker']} gone from Alpaca but get_order_fill returned no price — will retry next cycle")
                else:
                    print(f"  ⚠️  {pos['ticker']} bracket exited but no alpaca_order_id stored — cannot resolve P&L")
                continue
            # No filled buy and no pending buy — entry truly never executed
            print(f"  ⚠️  Reconciliation: {pos['ticker']} is OPEN in DB but not in Alpaca — marking UNFILLED")
            ledger.log("trade_unfilled", {"ticker": pos["ticker"], "entry_price": pos.get("entry_price")})
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
    closed = db.select("positions", filters={"status": "CLOSED"},
                       filters_gte={"closed_at": f"{today}T00:00:00"})
    return sum(
        p.get("realized_pnl", 0) or 0
        for p in closed
        if p.get("close_reason") not in ("CLEANUP", "UNFILLED")
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
    if now_utc.hour >= INTRADAY_ENTRY_CUTOFF_UTC:
        return None

    today = now_utc.date().isoformat()

    prior_scans = db.select("scan_results", filters={"date": today, "scan_type": "intraday_scan"})
    prior_runs  = db.select("daily_runs",   filters={"date": today, "run_type": "intraday"})

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

    # Daily entry cap — prevents 50+ positions on high-volatility days where stops free slots quickly
    all_pos_today = db.select("positions", filters_gte={"opened_at": f"{today}T00:00:00"})
    daily_opened  = len(all_pos_today)
    if daily_opened >= MAX_DAILY_ENTRIES:
        print(f"  📊 Intraday scan skipped: daily entry cap hit ({daily_opened}/{MAX_DAILY_ENTRIES})")
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

    # SPY gate — require SPY up ≥MIN_SPY_MOVE_PCT% for intraday entries.
    # Prevents opening positions on flat/down market days where momentum fails.
    if broker == "alpaca":
        try:
            from agents import alpaca_broker as _ab
            _spy_pct = _ab.get_intraday_signals(["SPY"]).get("SPY", {}).get("today_pct_change", 0)
            _threshold = MIN_SPY_MOVE_PCT * 100  # settings stores 0.003; today_pct_change is already in %
            if _spy_pct < _threshold:
                print(f"  ⛔ Intraday scan skipped: SPY {_spy_pct:+.2f}% < {_threshold:.1f}% gate")
                _save_scan_result(today, now_utc, {"candidates": 0, "reason": f"SPY gate {_spy_pct:+.2f}%"})
                return None
            print(f"  ✅ SPY gate: {_spy_pct:+.2f}% ≥ {_threshold:.1f}% — intraday scan allowed")
        except Exception as _e:
            print(f"  ⚠️  SPY gate check failed: {_e} — proceeding anyway")

    run_num         = len(prior_runs) + 1
    available_slots = min(MAX_POSITIONS - open_count, MAX_DAILY_ENTRIES - daily_opened)
    print(f"\n  🔍 Intraday scan #{run_num}: {open_count}/{MAX_POSITIONS} slots used | "
          f"{daily_opened}/{MAX_DAILY_ENTRIES} daily entries | "
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

        # Sector conviction guidance — fetch ETF signals to tell Claude which sectors are hot/weak
        from agents import alpaca_broker as _ab
        _sector_etfs = ["XLK", "XLF", "XLE", "XLV", "XLI", "XLC", "XLY"]
        _sector_sigs = _ab.get_intraday_signals(_sector_etfs) if broker == "alpaca" else {}
        _sector_perf = {etf: _sector_sigs[etf]["today_pct_change"]
                        for etf in _sector_etfs if etf in _sector_sigs}
        sector_note = ""
        if _sector_perf:
            perf_str = "  ".join(f"{etf} {pct:+.1f}%" for etf, pct in
                                 sorted(_sector_perf.items(), key=lambda x: -x[1]))
            _hot  = [e for e, p in _sector_perf.items() if p >= STRONG_SECTOR_THRESHOLD]
            _weak = [e for e, p in _sector_perf.items() if p <= WEAK_SECTOR_THRESHOLD]
            sector_note = f"\nSECTOR ETF PERFORMANCE TODAY: {perf_str}"
            if _hot:
                sector_note += f"\nHOT sectors (≥+{STRONG_SECTOR_THRESHOLD}%): {', '.join(_hot)} — prioritize these."
            if _weak:
                sector_note += f"\nWEAK sectors (≤{WEAK_SECTOR_THRESHOLD}%): {', '.join(_weak)} — max 1 pick each."

        quiet_note = (
            "\nQUIET DAY (Fear & Greed < 35): apply quiet-day confidence criteria — "
            "above_vwap=True AND rs_vs_spy ≥ 1.5 qualifies as MEDIUM even at technical_score 3–4."
            if quiet_day else ""
        )
        market_note = (
            f"{mkt.get('summary', '')}{sector_note}{quiet_note}\n\n"
            f"INTRADAY SCAN #{run_num}: Focus on momentum plays already moving today. "
            f"Prefer stocks with today_pct_change > {int(MIN_INTRADAY_MOVE_PCT)}% and rs_vs_spy > 1.5. "
            f"Set stop ~1% below entry and target ~{int(INTRADAY_TARGET_PCT * 100)}% above entry."
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

        # ATR sizer skipped for intraday. Instead apply INTRADAY_STOP_PCT (1%) override:
        # Claude sets 0.67% stop (MAX_LOSS_PER_TRADE default). We widen to 1% here so
        # normal intraday chop on 2-4% ATR stocks doesn't fire the stop before the move.
        # Target is already capped at INTRADAY_TARGET_PCT (2%) by _cap_intraday_targets().
        for t in approved:
            entry = t.get("entry_price", 0)
            if entry > 0 and t.get("signal_type") == "INTRADAY_MOMENTUM":
                t["stop_loss"] = round(entry * (1 - INTRADAY_STOP_PCT), 2)

        if not approved:
            print("  📊 Intraday scan: all trades rejected by risk/sector")
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
