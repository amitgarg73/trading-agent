"""
Eval script — scores the trading agent and optionally writes results to Supabase.
Usage:
  python3 eval.py [--days 5]          # print to console
  python3 eval.py [--days 30] --write # also save to Supabase (used by EOD GitHub Action)
"""
from __future__ import annotations
import argparse
from collections import defaultdict
from datetime import date
from core import db
from config.settings import (
    DAILY_PROFIT_TARGET, TOTAL_CAPITAL,
    MIN_REWARD_RISK, MAX_POSITION_PCT, MIN_POSITION_PCT,
    DAILY_LOSS_LIMIT, DAILY_LOCK_IN_TARGET, DAILY_BONUS_TARGET,
)


def _tailwind_analysis(all_closed_in_window: list, eval_dates: set, perf_rows: list) -> dict | None:
    """
    For each day where realized P&L crossed the $716 floor, identify which positions
    rode the tailwind (closed after Tier 1 triggered) and how much extra was captured
    above the floor vs. what would have been locked in under the old all-or-nothing close.
    """
    perf_by_date = {r["date"]: r for r in perf_rows}

    # Group positions by day, excluding noise; sort by close time
    pos_by_date: dict = defaultdict(list)
    for p in all_closed_in_window:
        day = (p.get("closed_at") or "")[:10]
        if day in eval_dates and p.get("close_reason") not in ("CLEANUP", "UNFILLED"):
            pos_by_date[day].append(p)
    for day in pos_by_date:
        pos_by_date[day].sort(key=lambda p: p.get("closed_at") or "")

    tailwind_days = []

    for day in sorted(eval_dates):
        day_positions = pos_by_date.get(day, [])
        final_pnl     = float((perf_by_date.get(day) or {}).get("total_pnl") or 0)

        if final_pnl < DAILY_LOCK_IN_TARGET:
            continue  # Tier 1 never triggered

        # Walk positions in close-time order; find where cumulative first crossed $716
        cumulative = 0.0
        tier1_idx  = -1
        floor_pnl  = 0.0
        for i, pos in enumerate(day_positions):
            cumulative += float(pos.get("realized_pnl") or 0)
            if tier1_idx == -1 and cumulative >= DAILY_LOCK_IN_TARGET:
                tier1_idx = i
                floor_pnl = cumulative
                break

        if tier1_idx == -1:
            continue

        # Positions that closed AFTER the Tier 1 trigger = rode the tailwind
        riders = [
            {
                "ticker":       pos["ticker"],
                "close_reason": pos.get("close_reason") or "UNKNOWN",
                "pnl":          round(float(pos.get("realized_pnl") or 0), 2),
            }
            for pos in day_positions[tier1_idx + 1:]
        ]

        tailwind_days.append({
            "date":           day,
            "floor_pnl":      round(floor_pnl, 2),
            "final_day_pnl":  round(final_pnl, 2),
            "extra_captured": round(final_pnl - floor_pnl, 2),
            "tier2_hit":      final_pnl >= DAILY_BONUS_TARGET,
            "riders":         riders,
            "rider_count":    len(riders),
        })

    if not tailwind_days:
        return None

    total_extra = sum(d["extra_captured"] for d in tailwind_days)
    return {
        "tailwind_day_count":   len(tailwind_days),
        "tier2_day_count":      sum(1 for d in tailwind_days if d["tier2_hit"]),
        "total_extra_captured": round(total_extra, 2),
        "avg_extra_per_day":    round(total_extra / len(tailwind_days), 2),
        "tailwind_days":        tailwind_days,
    }


def _vwap_signal_analysis(positions: list, eval_dates: set) -> dict | None:
    """
    Validate Thread 1: do above-VWAP + high-RS entries actually produce better outcomes?
    Loads vwap_signals from each day's premarket scan_result and cross-references with
    closed positions. Returns cohort stats for above/below VWAP and high/low RS.
    Returns None if no VWAP data exists (simulation runs or pre-Thread-1 history).
    """
    all_scans    = db.select("scan_results", filters={"scan_type": "premarket"})
    vwap_by_date = {
        s["date"]: s["results"].get("vwap_signals", {})
        for s in all_scans
        if s["date"] in eval_dates and s.get("results", {}).get("vwap_signals")
    }
    if not vwap_by_date:
        return None

    above_pnls, below_pnls, high_rs_pnls, low_rs_pnls = [], [], [], []
    matched = 0
    for pos in positions:
        day    = (pos.get("closed_at") or "")[:10]
        ticker = pos["ticker"]
        sig    = vwap_by_date.get(day, {}).get(ticker)
        if not sig:
            continue
        matched += 1
        pnl = float(pos.get("realized_pnl") or 0)
        (above_pnls if sig.get("above_vwap") else below_pnls).append(pnl)
        rs = sig.get("rs_vs_spy")
        if rs is not None:
            (high_rs_pnls if rs >= 1.5 else low_rs_pnls).append(pnl)

    if matched == 0:
        return None

    def _cohort(pnl_list, label):
        if not pnl_list:
            return None
        wins = [p for p in pnl_list if p > 0]
        return {
            "label":     label,
            "count":     len(pnl_list),
            "win_rate":  round(len(wins) / len(pnl_list) * 100, 1),
            "avg_pnl":   round(sum(pnl_list) / len(pnl_list), 2),
            "total_pnl": round(sum(pnl_list), 2),
        }

    return {
        "matched":    matched,
        "total":      len(positions),
        "above_vwap": _cohort(above_pnls,   "Above VWAP"),
        "below_vwap": _cohort(below_pnls,   "Below VWAP"),
        "high_rs":    _cohort(high_rs_pnls, "RS ≥ 1.5×"),
        "low_rs":     _cohort(low_rs_pnls,  "RS < 1.5×"),
    }


def _compute_metrics(days: int) -> dict | None:
    perf_rows = db.select("daily_performance", order="date", limit=days)
    if not perf_rows:
        return None

    total_pnl     = sum(r["total_pnl"] or 0 for r in perf_rows)
    avg_daily_pnl = total_pnl / len(perf_rows)
    win_days      = sum(1 for r in perf_rows if (r["total_pnl"] or 0) > 0)
    avg_win_rate  = sum(r["win_rate"] or 0 for r in perf_rows) / len(perf_rows)
    latest_cap    = perf_rows[0]["ending_capital"] or TOTAL_CAPITAL
    total_return  = ((latest_cap - TOTAL_CAPITAL) / TOTAL_CAPITAL) * 100
    ann_return    = total_return / len(perf_rows) * 250
    target_days   = sum(1 for r in perf_rows if (r["total_pnl"] or 0) >= DAILY_PROFIT_TARGET)

    eval_dates = {r["date"] for r in perf_rows}

    # All closed positions in window — used for both perf and integrity metrics
    all_closed = db.select("positions", filters={"status": "CLOSED"})
    all_closed_in_window = [p for p in all_closed
                            if (p.get("closed_at") or "")[:10] in eval_dates]
    positions = [p for p in all_closed_in_window
                 if p.get("close_reason") not in ("CLEANUP", "UNFILLED")]

    # --- Integrity metrics ---
    unfilled_count  = sum(1 for p in all_closed_in_window if p.get("close_reason") == "UNFILLED")
    cleanup_count   = sum(1 for p in all_closed_in_window if p.get("close_reason") == "CLEANUP")
    lock_in_count   = sum(1 for p in all_closed_in_window if p.get("close_reason") == "LOCK_IN")
    loss_limit_days = sum(1 for r in perf_rows if (r["total_pnl"] or 0) < DAILY_LOSS_LIMIT)
    lock_in_days    = sum(1 for r in perf_rows if (r["total_pnl"] or 0) >= DAILY_LOCK_IN_TARGET)
    missing_exit    = sum(1 for p in positions if not p.get("exit_mechanism"))

    # Orphaned open positions (opened before today, still OPEN)
    today_str   = date.today().isoformat()
    all_open    = db.select("positions", filters={"status": "OPEN"})
    orphaned    = [p for p in all_open if (p.get("opened_at") or "")[:10] < today_str]

    # Duplicate ticker same day (guardrail should block, but eval confirms)
    ticker_day = defaultdict(int)
    for p in all_closed_in_window:
        ticker_day[(p["ticker"], (p.get("closed_at") or "")[:10])] += 1
    duplicate_count = sum(1 for v in ticker_day.values() if v > 1)

    # --- Planned trades for Claude quality checks ---
    all_planned      = db.select("planned_trades")
    pt_ids_in_window = {p["planned_trade_id"] for p in positions if p.get("planned_trade_id")}
    planned_in_window = [pt for pt in all_planned if pt["id"] in pt_ids_in_window]
    pt_lookup        = {pt["id"]: pt for pt in planned_in_window}

    # R:R integrity — guardrails don't check this; Claude could slip a bad trade through
    rr_violations = []
    for pt in planned_in_window:
        entry  = float(pt.get("entry_price") or 0)
        target = float(pt.get("target_price") or 0)
        stop   = float(pt.get("stop_loss") or 0)
        if entry and target and stop and (entry - stop) != 0:
            rr = (target - entry) / (entry - stop) if pt.get("action") == "BUY" \
                 else (entry - target) / (stop - entry)
            if rr < MIN_REWARD_RISK:
                rr_violations.append({"ticker": pt["ticker"], "rr": round(rr, 2)})

    # Position size violations
    min_size = MIN_POSITION_PCT * TOTAL_CAPITAL
    max_size = MAX_POSITION_PCT * TOTAL_CAPITAL
    size_violations = [
        {"ticker": pt["ticker"], "size": pt.get("position_size")}
        for pt in planned_in_window
        if pt.get("position_size") and
        not (min_size <= float(pt["position_size"]) <= max_size)
    ]

    # Confidence cohort — validates HIGH/MEDIUM/LOW sizing delivers ROI
    conf_buckets: dict = {"HIGH": [], "MEDIUM": [], "LOW": []}
    for pos in positions:
        pt = pt_lookup.get(pos.get("planned_trade_id") or "")
        if pt:
            conf = (pt.get("confidence") or "").upper()
            if conf in conf_buckets:
                conf_buckets[conf].append(pos.get("realized_pnl") or 0)

    def _conf_stats(pnl_list: list) -> dict | None:
        if not pnl_list:
            return None
        wins = [p for p in pnl_list if p > 0]
        return {
            "count":    len(pnl_list),
            "win_rate": round(len(wins) / len(pnl_list) * 100, 1),
            "avg_pnl":  round(sum(pnl_list) / len(pnl_list), 2),
            "total_pnl": round(sum(pnl_list), 2),
        }

    confidence_stats = {k: _conf_stats(v) for k, v in conf_buckets.items()}

    wins   = [p for p in positions if (p.get("realized_pnl") or 0) > 0]
    losses = [p for p in positions if (p.get("realized_pnl") or 0) <= 0]
    avg_win  = sum(p["realized_pnl"] for p in wins)  / len(wins)   if wins   else 0
    avg_loss = sum(p["realized_pnl"] for p in losses) / len(losses) if losses else 0
    actual_rr = abs(avg_win / avg_loss) if avg_loss != 0 else 0

    sorted_pos = sorted(positions, key=lambda x: x.get("realized_pnl") or 0, reverse=True)
    best  = sorted_pos[0]  if sorted_pos else None
    worst = sorted_pos[-1] if sorted_pos else None

    close_reasons: dict = {}
    for p in positions:
        r = p.get("close_reason", "UNKNOWN")
        close_reasons[r] = close_reasons.get(r, 0) + 1

    # Grade
    pnl_score     = min(avg_daily_pnl / DAILY_PROFIT_TARGET * 40, 40) if DAILY_PROFIT_TARGET else 0
    winday_score  = (win_days / len(perf_rows)) * 30
    winrate_score = min(avg_win_rate / 100 * 30, 30)
    total_score   = pnl_score + winday_score + winrate_score

    if total_score >= 80:   grade = "A"
    elif total_score >= 60: grade = "B"
    elif total_score >= 40: grade = "C"
    else:                   grade = "D"

    recs = []
    if avg_daily_pnl < DAILY_PROFIT_TARGET * 0.5:
        recs.append("P&L well below target — consider lowering SCORE_THRESHOLD to get more candidates")
    if avg_win_rate < 50:
        recs.append("Win rate below 50% — tighten RSI thresholds or raise MIN_REWARD_RISK")
    if actual_rr < 1.5 and positions:
        recs.append("Actual reward:risk below 1.5x — stops may be too tight or targets too far")
    if win_days < len(perf_rows) * 0.5:
        recs.append("Losing more days than winning — review which tickers are dragging performance")
    if avg_daily_pnl >= DAILY_PROFIT_TARGET:
        recs.append("On target! Monitor for another week before making any changes.")
    if total_score >= 80:
        recs.append("Strong performance. Consider increasing position sizes slightly.")
    if not recs:
        recs.append("Keep monitoring.")

    # Tailwind analysis — which positions rode past the $716 floor
    tailwind = _tailwind_analysis(all_closed_in_window, eval_dates, perf_rows)

    # VWAP signal quality — validates Thread 1: do above-VWAP + high-RS entries outperform?
    vwap_analysis = _vwap_signal_analysis(positions, eval_dates)

    # Native trail validation — compare native vs manual trail positions
    native  = [p for p in positions if p.get("native_trail_active")]
    manual  = [p for p in positions if not p.get("native_trail_active")]

    def _cohort_stats(cohort):
        if not cohort:
            return None
        w = [p for p in cohort if (p.get("realized_pnl") or 0) > 0]
        exits = {}
        for p in cohort:
            m = p.get("exit_mechanism") or "UNKNOWN"
            exits[m] = exits.get(m, 0) + 1
        return {
            "count":     len(cohort),
            "win_rate":  round(len(w) / len(cohort) * 100, 1),
            "avg_pnl":   round(sum(p.get("realized_pnl") or 0 for p in cohort) / len(cohort), 2),
            "exits":     exits,
        }

    return {
        "days":           len(perf_rows),
        "score":          round(total_score, 1),
        "grade":          grade,
        "total_pnl":      round(total_pnl, 2),
        "avg_daily_pnl":  round(avg_daily_pnl, 2),
        "win_days":       win_days,
        "loss_days":      len(perf_rows) - win_days,
        "target_days":    target_days,
        "avg_win_rate":   round(avg_win_rate, 1),
        "latest_capital": round(latest_cap, 2),
        "total_return":   round(total_return, 2),
        "ann_return":     round(ann_return, 1),
        "total_trades":   len(positions),
        "winners":        len(wins),
        "losers":         len(losses),
        "avg_win":        round(avg_win, 2),
        "avg_loss":       round(avg_loss, 2),
        "actual_rr":      round(actual_rr, 2),
        "best_ticker":    best["ticker"]  if best  else None,
        "best_pnl":       round(best["realized_pnl"]  or 0, 2) if best  else 0,
        "worst_ticker":   worst["ticker"] if worst else None,
        "worst_pnl":      round(worst["realized_pnl"] or 0, 2) if worst else 0,
        "close_reasons":    close_reasons,
        "recommendations":  recs,
        "native_trail":     _cohort_stats(native),
        "manual_trail":     _cohort_stats(manual),
        "pnl_score":        round(pnl_score, 1),
        "winday_score":     round(winday_score, 1),
        "winrate_score":    round(winrate_score, 1),
        # Integrity
        "unfilled_count":   unfilled_count,
        "cleanup_count":    cleanup_count,
        "lock_in_count":    lock_in_count,
        "loss_limit_days":  loss_limit_days,
        "lock_in_days":     lock_in_days,
        "missing_exit":     missing_exit,
        "orphaned":         orphaned,
        "duplicate_count":  duplicate_count,
        "total_attempted":  len(all_closed_in_window),
        # Claude quality
        "rr_violations":    rr_violations,
        "size_violations":  size_violations,
        "confidence_stats": confidence_stats,
        # Tailwind
        "tailwind":         tailwind,
        # VWAP signal quality (Thread 1 validation)
        "vwap_analysis":    vwap_analysis,
    }


def _flag(val, good, warn, higher=True):
    """Return ✅/⚠️/❌ based on thresholds."""
    if higher:
        return "✅" if val >= good else "⚠️ " if val >= warn else "❌"
    else:
        return "✅" if val <= good else "⚠️ " if val <= warn else "❌"


def _print_summary(m: dict):
    """Plain-language verdict at the top — 10-second read before diving into details."""
    days  = m["days"]
    grade = m["grade"]
    score = m["score"]

    grade_word = {"A": "strong", "B": "good", "C": "mixed", "D": "poor"}.get(grade, "")
    print(f"\n{'='*60}")
    print(f"  VERDICT — last {days} day{'s' if days != 1 else ''}  |  Grade {grade} ({score:.0f}/100)  |  {grade_word.upper()}")
    print(f"{'='*60}")

    wins   = []
    watchs = []
    actions = []

    # P&L vs target
    pnl_pct = m["avg_daily_pnl"] / DAILY_PROFIT_TARGET * 100 if DAILY_PROFIT_TARGET else 0
    if m["avg_daily_pnl"] >= DAILY_PROFIT_TARGET:
        wins.append(f"Avg daily P&L ${m['avg_daily_pnl']:,.0f} — on or above ${DAILY_PROFIT_TARGET:,} target")
    elif pnl_pct >= 60:
        watchs.append(f"Avg daily P&L ${m['avg_daily_pnl']:,.0f} is {pnl_pct:.0f}% of ${DAILY_PROFIT_TARGET:,} target — close but not there yet")
    else:
        actions.append(f"Avg daily P&L ${m['avg_daily_pnl']:,.0f} well below ${DAILY_PROFIT_TARGET:,} target ({pnl_pct:.0f}%) — review score threshold and universe")

    # Win day rate
    win_day_pct = m["win_days"] / days * 100 if days else 0
    if win_day_pct >= 80:
        wins.append(f"{m['win_days']}/{days} profitable days ({win_day_pct:.0f}%) — consistent daily execution")
    elif win_day_pct >= 60:
        watchs.append(f"{m['win_days']}/{days} profitable days ({win_day_pct:.0f}%) — more losing days than ideal")
    else:
        actions.append(f"Only {m['win_days']}/{days} profitable days — strategy inconsistency, review entry timing")

    # Trade win rate
    if m["avg_win_rate"] >= 60:
        wins.append(f"{m['avg_win_rate']:.0f}% trade win rate — well above 25% break-even for 3:1 R:R")
    elif m["avg_win_rate"] >= 50:
        watchs.append(f"{m['avg_win_rate']:.0f}% trade win rate — above break-even but room to improve")
    else:
        watchs.append(f"{m['avg_win_rate']:.0f}% trade win rate — approaching break-even; tighten RSI or entry criteria")

    # Actual R:R
    if m["actual_rr"] >= 3.0:
        wins.append(f"Reward:risk {m['actual_rr']:.1f}x — meeting 3:1 target; wins outpacing losses")
    elif m["actual_rr"] >= 2.0:
        watchs.append(f"Reward:risk {m['actual_rr']:.1f}x — below 3.0x target; losers running slightly large or winners cutting early")
    else:
        actions.append(f"Reward:risk {m['actual_rr']:.1f}x — significantly below target; stops may be too tight or targets too far")

    # Exit mix
    cr = m.get("close_reasons", {})
    total_cr = sum(cr.values()) or 1
    tgt_pct = cr.get("TARGET", 0) / total_cr * 100
    if tgt_pct >= 50:
        wins.append(f"{tgt_pct:.0f}% of exits hit target — momentum strategy executing as designed")
    elif cr.get("STOP", 0) / total_cr > 0.5:
        watchs.append(f"More stops than targets ({cr.get('STOP',0)} vs {cr.get('TARGET',0)}) — entries may be too late in the move")

    # Confidence cohort
    cs = m.get("confidence_stats", {})
    high, low = cs.get("HIGH"), cs.get("LOW")
    if high and low:
        if high["avg_pnl"] > low["avg_pnl"]:
            wins.append(f"HIGH confidence trades earning ${high['avg_pnl']:,.0f} avg vs ${low['avg_pnl']:,.0f} for LOW — sizing justified")
        else:
            watchs.append(f"LOW confidence trades outperforming HIGH (${low['avg_pnl']:,.0f} vs ${high['avg_pnl']:,.0f}) — confidence signal unreliable")

    # Trailing stop validation
    native = m.get("native_trail")
    if native:
        nt_exits = native.get("exits", {}).get("NATIVE_TRAIL", 0)
        if nt_exits > 0:
            wins.append(f"Native trailing stop confirmed — {nt_exits} clean exits, no double-sells")
        else:
            watchs.append("Native trailing stop enabled but no stop exits yet — need a reversal day to validate")

    # VWAP signal quality verdict
    vwap = m.get("vwap_analysis")
    if vwap:
        above = vwap.get("above_vwap")
        below = vwap.get("below_vwap")
        if above and below:
            delta = above["avg_pnl"] - below["avg_pnl"]
            if delta > 10:
                wins.append(f"VWAP filter confirmed — above-VWAP entries +${delta:.0f} avg vs below ({vwap['matched']} trades matched)")
            elif delta >= 0:
                watchs.append(f"VWAP edge marginal (+${delta:.0f}) — more trades needed to confirm Thread 1 signal quality")
            else:
                watchs.append(f"No VWAP edge yet (${delta:+.0f} delta) — below-VWAP matching above; check if below-VWAP trades should be filtered")

    # Integrity flags
    orphaned = m.get("orphaned", [])
    if orphaned:
        actions.append(f"{len(orphaned)} orphaned position(s) stuck OPEN from a prior day — manual review required")
    if m.get("rr_violations"):
        actions.append(f"{len(m['rr_violations'])} trade(s) submitted below {MIN_REWARD_RISK}x R:R — Claude constraint drift")
    if m.get("duplicate_count", 0) > 0:
        actions.append(f"{m['duplicate_count']} duplicate ticker(s) same day — guardrail may have failed")
    unfill_pct = m.get("unfilled_count", 0) / m.get("total_attempted", 1) * 100
    if unfill_pct >= 15:
        actions.append(f"{unfill_pct:.0f}% unfilled rate — limit entry price too tight, orders not filling")
    elif unfill_pct >= 5:
        watchs.append(f"{unfill_pct:.0f}% unfilled rate — monitor; acceptable now but rising trend is a problem")

    if wins:
        print(f"\n  What's working:")
        for w in wins:
            print(f"    ✅ {w}")
    if watchs:
        print(f"\n  Watch:")
        for w in watchs:
            print(f"    ⚠️  {w}")
    if actions:
        print(f"\n  Action required:")
        for a in actions:
            print(f"    ❌ {a}")
    if not actions:
        print(f"\n  No action required — keep monitoring.")
    print()


def _print_metrics(m: dict):
    days = m["days"]
    _print_summary(m)
    print(f"\n{'='*60}")
    print(f"  DETAIL — last {days} trading day{'s' if days != 1 else ''}")
    print(f"{'='*60}\n")

    win_day_pct = m["win_days"] / days * 100 if days else 0

    print("[ PERFORMANCE SUMMARY ]")
    print(f"  Days evaluated:       {days}")
    print(f"  Total P&L:            ${m['total_pnl']:,.2f}")
    print(f"  Avg daily P&L:        ${m['avg_daily_pnl']:,.2f}  {_flag(m['avg_daily_pnl'], DAILY_PROFIT_TARGET, DAILY_PROFIT_TARGET*0.5)}"
          f"  (target: ${DAILY_PROFIT_TARGET:,})")
    print(f"  Win days / Loss days: {m['win_days']} / {m['loss_days']}  {_flag(win_day_pct, 80, 60)}"
          f"  ({win_day_pct:.0f}% — target: ≥80%)")
    print(f"  Days hitting target:  {m['target_days']} / {days}"
          f"  ({m['target_days']/days*100:.0f}% of days cleared ${DAILY_PROFIT_TARGET:,})")
    print(f"  Avg trade win rate:   {m['avg_win_rate']:.1f}%  {_flag(m['avg_win_rate'], 60, 50)}"
          f"  (target: ≥60%; break-even at 3:1 R:R is just 25%)")
    print(f"  Portfolio value:      ${m['latest_capital']:,.0f}")
    print(f"  Total return:         {m['total_return']:+.2f}%  {_flag(m['total_return'], 1.0, 0, higher=True)}")
    print(f"  Annualized return:    {m['ann_return']:+.1f}%  {_flag(m['ann_return'], 50, 20)}"
          f"  (>50% = exceptional; >20% = strong for intraday)")

    print(f"\n[ GRADE ]")
    print(f"  Score: {m['score']:.0f}/100  →  Grade: {m['grade']}")
    grade_desc = {
        "A": "Excellent — strategy is working, consider scaling up.",
        "B": "Good — minor tuning may improve results.",
        "C": "Mediocre — review score thresholds and stock universe.",
        "D": "Poor — strategy needs significant rework.",
    }
    print(f"  {grade_desc.get(m['grade'], '')}")
    print(f"  Score breakdown:")
    print(f"    P&L vs target:  {m['pnl_score']:.1f}/40 pts  (avg daily P&L / ${DAILY_PROFIT_TARGET:,} target × 40)")
    print(f"    Win day rate:   {m['winday_score']:.1f}/30 pts  (win days / total days × 30)")
    print(f"    Trade win rate: {m['winrate_score']:.1f}/30 pts  (avg trade win rate × 30)")

    if m["total_trades"]:
        total = m["total_trades"]
        win_pct = m["winners"] / total * 100 if total else 0
        print(f"\n[ TRADE BREAKDOWN ]")
        print(f"  Total closed trades:  {total}")
        print(f"  Winners / Losers:     {m['winners']} / {m['losers']}  ({win_pct:.0f}% win rate per trade)")
        print(f"    Avg winner:  +${m['avg_win']:,.2f}  |  Avg loser: ${m['avg_loss']:,.2f}")
        print(f"  Actual reward:risk:   {m['actual_rr']:.2f}x  {_flag(m['actual_rr'], 3.0, 2.0)}"
              f"  (target: 3.0x — avg win / avg loss; below 2.0x means stops too tight or targets too far)")
        if m["best_ticker"]:
            print(f"  Best trade:           {m['best_ticker']}  +${m['best_pnl']:,.2f}")
        if m["worst_ticker"]:
            print(f"  Worst trade:          {m['worst_ticker']}  ${m['worst_pnl']:,.2f}")

        if m["close_reasons"]:
            cr    = m["close_reasons"]
            total_cr = sum(cr.values())
            tgt_pct  = cr.get("TARGET", 0) / total_cr * 100 if total_cr else 0
            stop_pct = cr.get("STOP",   0) / total_cr * 100 if total_cr else 0
            eod_pct  = cr.get("EOD",    0) / total_cr * 100 if total_cr else 0
            print(f"\n  Exit reasons:  (healthy mix: TARGET >50%, STOP <35%, EOD <20%)")
            for reason, count in sorted(cr.items(), key=lambda x: -x[1]):
                pct = count / total_cr * 100
                print(f"    {reason:14s}: {count:3d}  ({pct:.0f}%)")
            if tgt_pct >= 50:
                print(f"  ✅ TARGET-led exits — momentum strategy executing as designed")
            if stop_pct > 50:
                print(f"  ⚠️  STOP-heavy — entries may be too late or targets too ambitious")
            if eod_pct > 25:
                print(f"  ⚠️  High EOD exits — positions not resolving intraday; consider tighter targets")

    native = m.get("native_trail")
    manual = m.get("manual_trail")
    if native or manual:
        print(f"\n[ TRAILING STOP VALIDATION ]")
        print(f"  Comparing native Alpaca trail (real-time) vs manual high_watermark check (every 15 min).")
        print(f"  Goal: confirm native trail fires correctly and improves or matches manual exit quality.\n")
        for label, cohort in [("Native trail (Alpaca)", native), ("Manual trail (high_watermark)", manual)]:
            if not cohort:
                continue
            print(f"  {label}:")
            print(f"    Trades:    {cohort['count']}")
            print(f"    Win rate:  {cohort['win_rate']:.1f}%  {_flag(cohort['win_rate'], 60, 50)}")
            print(f"    Avg P&L:   ${cohort['avg_pnl']:,.2f}  per trade")
            if cohort["exits"]:
                exits_str = "  |  ".join(f"{k}: {v}" for k, v in sorted(cohort["exits"].items()))
                print(f"    Exits:     {exits_str}")
        if native and manual:
            pnl_delta = native["avg_pnl"] - manual["avg_pnl"]
            wr_delta  = native["win_rate"] - manual["win_rate"]
            print(f"\n  Native vs manual delta:")
            print(f"    Avg P&L:   {pnl_delta:+.2f}  {'✅ native better' if pnl_delta > 0 else '⚠️  manual better'}")
            print(f"    Win rate:  {wr_delta:+.1f}%  {'✅ native better' if wr_delta > 0 else '⚠️  manual better'}")
        if native:
            if native.get("exits", {}).get("NATIVE_TRAIL", 0) > 0:
                print(f"  ✅ Native trail exits confirmed — Alpaca trailing stop leg firing correctly")
            else:
                print(f"  ⏳ No NATIVE_TRAIL exits yet — stop hasn't fired; need a reversal day to validate")

    tw = m.get("tailwind")
    print(f"\n[ TAILWIND ANALYSIS ]")
    print(f"  Tracks extra P&L captured by letting winners ride past the ${DAILY_LOCK_IN_TARGET:,} floor")
    print(f"  to the ${DAILY_BONUS_TARGET:,} ceiling, instead of closing everything at Tier 1.\n")

    if not tw:
        print(f"  No tailwind days yet — realized P&L hasn't crossed ${DAILY_LOCK_IN_TARGET:,} in the eval window.")
        print(f"  Once Tier 1 fires, riders and extra capture will appear here.")
    else:
        td_count = tw["tailwind_day_count"]
        t2_count = tw["tier2_day_count"]
        total_ex = tw["total_extra_captured"]
        avg_ex   = tw["avg_extra_per_day"]

        print(f"  Tailwind days (realized ≥ ${DAILY_LOCK_IN_TARGET:,}):  {td_count} / {m['days']} days")
        print(f"  Tier 2 ceiling hit (${DAILY_BONUS_TARGET:,} total):   {t2_count} / {td_count} tailwind days"
              f"  {'🏆' if t2_count > 0 else ''}")
        print(f"  Total extra captured above floor:      ${total_ex:,.2f}")
        print(f"  Avg extra per tailwind day:            ${avg_ex:,.2f}")

        print(f"\n  Day-by-day breakdown:")
        for d in tw["tailwind_days"]:
            t2_badge = "  🏆 Tier 2 ceiling" if d["tier2_hit"] else ""
            print(f"\n  {d['date']}  "
                  f"Floor ${d['floor_pnl']:,.2f} → Final ${d['final_day_pnl']:,.2f}  "
                  f"(+${d['extra_captured']:,.2f} extra){t2_badge}")
            if d["riders"]:
                for r in d["riders"]:
                    pnl_str = f"+${r['pnl']:,.2f}" if r["pnl"] >= 0 else f"-${abs(r['pnl']):,.2f}"
                    note = ""
                    if r["close_reason"] == "LOCK_IN":
                        note = "  ← Tier 2 ceiling close"
                    elif r["close_reason"] == "STOP" and r["pnl"] > 0:
                        note = "  ← trail caught reversal, still profitable"
                    elif r["close_reason"] == "STOP" and r["pnl"] <= 0:
                        note = "  ← stopped out after riding"
                    print(f"    {r['ticker']:<6}  {r['close_reason']:<8}  {pnl_str}{note}")
            else:
                print(f"    (no riders — all positions closed before or at Tier 1 trigger)")

        # Summary verdict
        print()
        if total_ex > 0:
            print(f"  ✅ Tailwind mode captured ${total_ex:,.2f} extra vs. locking in at ${DAILY_LOCK_IN_TARGET:,} floor.")
        else:
            print(f"  ⚠️  No extra captured yet — riders may be closing at a loss after Tier 1.")

    vwap = m.get("vwap_analysis")
    print(f"\n[ VWAP SIGNAL QUALITY ]")
    print(f"  Thread 1 validation: do above-VWAP + high-RS entries produce better outcomes?\n")
    if not vwap:
        print(f"  No VWAP data yet — available for Alpaca runs starting 2026-05-18.")
        print(f"  Will appear once enough enriched trades have closed in the eval window.")
    else:
        total_matched = vwap["matched"]
        total_pos     = vwap["total"]
        print(f"  Matched {total_matched} / {total_pos} positions to VWAP entry signals.\n")
        print(f"  {'Cohort':<18} {'Trades':>6} {'Win%':>6} {'Avg P&L':>9} {'Total P&L':>10}")
        print(f"  {'-'*52}")
        for key in ("above_vwap", "below_vwap", "high_rs", "low_rs"):
            c = vwap.get(key)
            if c:
                flag = _flag(c["win_rate"], 60, 50)
                print(f"  {c['label']:<18} {c['count']:>6} {c['win_rate']:>5.1f}% {flag}"
                      f"  ${c['avg_pnl']:>7,.2f}   ${c['total_pnl']:>8,.2f}")
        above = vwap.get("above_vwap")
        below = vwap.get("below_vwap")
        if above and below:
            d_pnl = above["avg_pnl"] - below["avg_pnl"]
            d_wr  = above["win_rate"] - below["win_rate"]
            print(f"\n  Above vs Below VWAP:  avg P&L {d_pnl:+.2f}  |  win rate {d_wr:+.1f}%")
            if d_pnl > 10:
                print(f"  ✅ VWAP filter working — above-VWAP entries meaningfully outperform")
            elif d_pnl >= 0:
                print(f"  ⚠️  Slight VWAP edge — more data needed to confirm")
            else:
                print(f"  ❌ No VWAP edge yet — consider whether below-VWAP entries should be filtered")
        high_rs = vwap.get("high_rs")
        low_rs  = vwap.get("low_rs")
        if high_rs and low_rs:
            d_rs = high_rs["avg_pnl"] - low_rs["avg_pnl"]
            print(f"\n  High RS vs Low RS:    avg P&L {d_rs:+.2f}")
            if d_rs > 10:
                print(f"  ✅ RS filter working — high relative strength entries outperform")
            elif d_rs >= 0:
                print(f"  ⚠️  Slight RS edge — more data needed")
            else:
                print(f"  ❌ No RS edge detected — low-RS entries matching or beating high-RS")

    total_attempted = m.get("total_attempted", 0)
    unfilled = m.get("unfilled_count", 0)
    print(f"\n[ INTEGRITY CHECKS ]")
    print(f"  Orders attempted:     {total_attempted}")
    unfill_pct = unfilled / total_attempted * 100 if total_attempted else 0
    print(f"  UNFILLED (no fill):   {unfilled}  ({unfill_pct:.0f}%)  "
          f"{'✅ low' if unfill_pct < 10 else '⚠️  high — limit price may be too tight'}")
    print(f"  CLEANUP removed:      {m.get('cleanup_count', 0)}  (bad data, excluded from metrics)")
    lock_in = m.get("lock_in_count", 0)
    print(f"  LOCK_IN closes:       {lock_in}  "
          f"({'daily profit target hit early — good' if lock_in > 0 else 'none fired'})")
    print(f"  Loss-limit days:      {m.get('loss_limit_days', 0)} / {m['days']}  "
          f"({'⚠️  frequent — review entry quality' if m.get('loss_limit_days', 0) > 2 else '✅'})")
    print(f"  Lock-in days:         {m.get('lock_in_days', 0)} / {m['days']}  "
          f"(days where realized P&L hit ${DAILY_LOCK_IN_TARGET:,} target early)")
    missing = m.get("missing_exit", 0)
    print(f"  Missing exit_mech:    {missing}  "
          f"{'✅ all exits tracked' if missing == 0 else f'⚠️  {missing} positions have no exit_mechanism — code path gap'}")
    dups = m.get("duplicate_count", 0)
    print(f"  Duplicate tickers:    {dups}  "
          f"{'✅ none' if dups == 0 else f'❌ {dups} ticker(s) opened twice same day — guardrail missed'}")
    orphaned = m.get("orphaned", [])
    if orphaned:
        print(f"  Orphaned open pos:    ❌ {len(orphaned)} position(s) stuck OPEN from a prior day:")
        for p in orphaned:
            print(f"    {p['ticker']}  opened {(p.get('opened_at') or '')[:10]}  entry ${p['entry_price']:.2f}")
    else:
        print(f"  Orphaned open pos:    ✅ none")

    print(f"\n[ CLAUDE QUALITY CHECKS ]")
    rr_v = m.get("rr_violations", [])
    if rr_v:
        print(f"  R:R violations:  ❌ {len(rr_v)} trade(s) submitted with R:R below {MIN_REWARD_RISK}x minimum:")
        for v in rr_v:
            print(f"    {v['ticker']}  R:R {v['rr']:.2f}x  (should be ≥{MIN_REWARD_RISK}x)")
    else:
        print(f"  R:R violations:  ✅ none — all trades met ≥{MIN_REWARD_RISK}x reward:risk minimum")
    sz_v = m.get("size_violations", [])
    min_s = MIN_POSITION_PCT * TOTAL_CAPITAL
    max_s = MAX_POSITION_PCT * TOTAL_CAPITAL
    if sz_v:
        print(f"  Size violations: ⚠️  {len(sz_v)} trade(s) outside ${min_s:,.0f}–${max_s:,.0f} range:")
        for v in sz_v:
            print(f"    {v['ticker']}  ${v['size']:,.0f}")
    else:
        print(f"  Size violations: ✅ none — all positions within ${min_s:,.0f}–${max_s:,.0f} bounds")

    cs = m.get("confidence_stats", {})
    if any(cs.get(k) for k in ("HIGH", "MEDIUM", "LOW")):
        print(f"\n  Confidence cohort performance:  (validates HIGH→$7K / MEDIUM→$6K / LOW→$5K sizing)")
        print(f"  {'Level':<8} {'Trades':>6} {'Win%':>6} {'Avg P&L':>9} {'Total P&L':>10}")
        print(f"  {'-'*44}")
        for level in ("HIGH", "MEDIUM", "LOW"):
            s = cs.get(level)
            if s:
                flag = _flag(s["win_rate"], 65, 50)
                print(f"  {level:<8} {s['count']:>6} {s['win_rate']:>5.1f}% {flag} "
                      f"  ${s['avg_pnl']:>7,.2f}   ${s['total_pnl']:>8,.2f}")
        high = cs.get("HIGH")
        low  = cs.get("LOW")
        if high and low:
            delta = high["avg_pnl"] - low["avg_pnl"]
            print(f"\n  HIGH vs LOW avg P&L: {delta:+.2f}  "
                  f"{'✅ HIGH outperforming — sizing justified' if delta > 0 else '⚠️  LOW outperforming HIGH — confidence signal unreliable'}")

    print(f"\n[ RECOMMENDATIONS ]")
    for rec in m["recommendations"]:
        print(f"  • {rec}")

    print(f"\n{'='*60}\n")


def _write_to_supabase(metrics: dict):
    today = date.today().isoformat()
    existing = db.select("scan_results", filters={"date": today, "scan_type": "eval"})
    if existing:
        db.update("scan_results", {"id": existing[0]["id"]}, {"results": metrics})
        print(f"  ✓ Updated eval results in Supabase for {today}")
    else:
        db.insert("scan_results", {"date": today, "scan_type": "eval", "results": metrics})
        print(f"  ✓ Saved eval results to Supabase for {today}")


def run_eval(days: int = 5, write: bool = False):
    metrics = _compute_metrics(days)
    if not metrics:
        print(f"\n  No performance data yet. Run the agent for at least one day first.\n")
        return

    _print_metrics(metrics)

    if write:
        _write_to_supabase(metrics)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--days",  type=int,  default=5,     help="Number of trading days to evaluate")
    parser.add_argument("--write", action="store_true",       help="Save results to Supabase")
    args = parser.parse_args()
    run_eval(args.days, args.write)
