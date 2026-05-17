"""
Eval script — run after at least one week of data to score the trading agent.
Usage: python3 eval.py [--days 5]
"""
import argparse
from datetime import date, timedelta
from core import db
from config.settings import DAILY_PROFIT_TARGET, TOTAL_CAPITAL


def run_eval(days: int = 5):
    print(f"\n{'='*60}")
    print(f"  TRADING AGENT EVAL — last {days} trading days")
    print(f"{'='*60}\n")

    cutoff = (date.today() - timedelta(days=days * 2)).isoformat()  # buffer for weekends

    # ── Performance summary ───────────────────────────────────────────
    perf_rows = db.select("daily_performance", order="date", limit=days)
    if not perf_rows:
        print("  No performance data yet. Run the agent for at least one day first.\n")
        return

    total_pnl       = sum(r["total_pnl"] or 0 for r in perf_rows)
    avg_daily_pnl   = total_pnl / len(perf_rows)
    win_days        = sum(1 for r in perf_rows if (r["total_pnl"] or 0) > 0)
    loss_days       = len(perf_rows) - win_days
    target_days     = sum(1 for r in perf_rows if (r["total_pnl"] or 0) >= DAILY_PROFIT_TARGET)
    avg_win_rate    = sum(r["win_rate"] or 0 for r in perf_rows) / len(perf_rows)
    latest_capital  = perf_rows[0]["ending_capital"] or TOTAL_CAPITAL
    total_return    = ((latest_capital - TOTAL_CAPITAL) / TOTAL_CAPITAL) * 100

    print("[ PERFORMANCE SUMMARY ]")
    print(f"  Days evaluated:       {len(perf_rows)}")
    print(f"  Total P&L:            ${total_pnl:,.2f}")
    print(f"  Avg daily P&L:        ${avg_daily_pnl:,.2f}  (target: ${DAILY_PROFIT_TARGET:,})")
    print(f"  Win days / Loss days: {win_days} / {loss_days}")
    print(f"  Days hitting target:  {target_days} / {len(perf_rows)}")
    print(f"  Avg trade win rate:   {avg_win_rate:.1f}%")
    print(f"  Portfolio value:      ${latest_capital:,.0f}")
    print(f"  Total return:         {total_return:+.2f}%")

    # ── Grade ─────────────────────────────────────────────────────────
    print(f"\n[ GRADE ]")
    pnl_score    = min(avg_daily_pnl / DAILY_PROFIT_TARGET * 40, 40)
    winday_score = (win_days / len(perf_rows)) * 30
    winrate_score = min(avg_win_rate / 100 * 30, 30)
    total_score  = pnl_score + winday_score + winrate_score

    if total_score >= 80:
        grade = "A — Excellent. Strategy is working."
    elif total_score >= 60:
        grade = "B — Good. Minor tuning may improve results."
    elif total_score >= 40:
        grade = "C — Mediocre. Review thresholds and universe."
    else:
        grade = "D — Poor. Strategy needs significant rework."

    print(f"  Score: {total_score:.0f}/100")
    print(f"  Grade: {grade}")

    # ── Trade-level breakdown ─────────────────────────────────────────
    positions = db.select("positions", filters={"status": "CLOSED"})
    if positions:
        wins   = [p for p in positions if (p.get("realized_pnl") or 0) > 0]
        losses = [p for p in positions if (p.get("realized_pnl") or 0) <= 0]
        avg_win  = sum(p["realized_pnl"] for p in wins) / len(wins) if wins else 0
        avg_loss = sum(p["realized_pnl"] for p in losses) / len(losses) if losses else 0
        rr = abs(avg_win / avg_loss) if avg_loss != 0 else 0

        print(f"\n[ TRADE BREAKDOWN ]")
        print(f"  Total closed trades:  {len(positions)}")
        print(f"  Winners:              {len(wins)} (avg +${avg_win:,.2f})")
        print(f"  Losers:               {len(losses)} (avg ${avg_loss:,.2f})")
        print(f"  Actual reward:risk:   {rr:.2f}x  (target: 2.0x)")

        # Close reason breakdown
        reasons = {}
        for p in positions:
            r = p.get("close_reason", "UNKNOWN")
            reasons[r] = reasons.get(r, 0) + 1
        print(f"\n  Close reasons:")
        for reason, count in sorted(reasons.items(), key=lambda x: -x[1]):
            print(f"    {reason:12s}: {count}")

        # Top winners and losers
        sorted_pos = sorted(positions, key=lambda x: x.get("realized_pnl") or 0, reverse=True)
        print(f"\n  Top 3 winners:")
        for p in sorted_pos[:3]:
            print(f"    {p['ticker']:6s}  +${p['realized_pnl']:,.2f}  ({p.get('close_reason','')})")
        print(f"\n  Top 3 losers:")
        for p in sorted_pos[-3:]:
            print(f"    {p['ticker']:6s}  ${p['realized_pnl']:,.2f}  ({p.get('close_reason','')})")

    # ── Strategy quality ──────────────────────────────────────────────
    plans = db.select("trade_plans", order="date", limit=days)
    planned = db.select("planned_trades")
    if plans and planned:
        approved = [t for t in planned if t.get("status") != "PLANNED"]
        rejected = [t for t in planned if t.get("status") == "PLANNED"]
        high_conf = [t for t in approved if t.get("confidence") == "HIGH"]
        high_wins = [p for p in positions if p.get("realized_pnl", 0) > 0
                     and any(t["id"] == p.get("planned_trade_id") and t.get("confidence") == "HIGH"
                             for t in planned)] if positions else []

        print(f"\n[ STRATEGY QUALITY ]")
        print(f"  Trades approved by risk agent: {len(approved)}")
        print(f"  Trades rejected by risk agent: {len(rejected)}")
        print(f"  HIGH confidence trades:        {len(high_conf)}")
        if high_conf:
            print(f"  HIGH confidence win rate:      {len(high_wins)}/{len(high_conf)} "
                  f"({len(high_wins)/len(high_conf)*100:.0f}%)")

    # ── Recommendations ───────────────────────────────────────────────
    print(f"\n[ RECOMMENDATIONS ]")
    if avg_daily_pnl < DAILY_PROFIT_TARGET * 0.5:
        print("  • P&L well below target — consider lowering SCORE_THRESHOLD to get more candidates")
    if avg_win_rate < 50:
        print("  • Win rate below 50% — tighten RSI thresholds or raise MIN_REWARD_RISK")
    if rr < 1.5 if positions else False:
        print("  • Actual reward:risk below 1.5x — stops may be too tight or targets too far")
    if win_days < len(perf_rows) * 0.5:
        print("  • Losing more days than winning — review which tickers are dragging performance")
    if avg_daily_pnl >= DAILY_PROFIT_TARGET:
        print("  • On target! Monitor for another week before making any changes.")
    if total_score >= 80:
        print("  • Strong performance. Consider increasing position sizes slightly.")

    print(f"\n{'='*60}\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=5, help="Number of trading days to evaluate")
    args = parser.parse_args()
    run_eval(args.days)
