"""
Eval script — scores the trading agent and optionally writes results to Supabase.
Usage:
  python3 eval.py [--days 5]          # print to console
  python3 eval.py [--days 30] --write # also save to Supabase (used by EOD GitHub Action)
"""
from __future__ import annotations
import argparse
from datetime import date
from core import db
from config.settings import DAILY_PROFIT_TARGET, TOTAL_CAPITAL


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
    positions = db.select("positions", filters={"status": "CLOSED"})
    positions = [
        p for p in positions
        if p.get("close_reason") not in ("CLEANUP", "UNFILLED")
        and (p.get("closed_at") or "")[:10] in eval_dates
    ]

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
        "close_reasons":   close_reasons,
        "recommendations": recs,
        "native_trail":    _cohort_stats(native),
        "manual_trail":    _cohort_stats(manual),
        "pnl_score":       round(pnl_score, 1),
        "winday_score":    round(winday_score, 1),
        "winrate_score":   round(winrate_score, 1),
    }


def _flag(val, good, warn, higher=True):
    """Return ✅/⚠️/❌ based on thresholds."""
    if higher:
        return "✅" if val >= good else "⚠️ " if val >= warn else "❌"
    else:
        return "✅" if val <= good else "⚠️ " if val <= warn else "❌"


def _print_metrics(m: dict):
    days = m["days"]
    print(f"\n{'='*60}")
    print(f"  TRADING AGENT EVAL — last {days} trading day{'s' if days != 1 else ''}")
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
