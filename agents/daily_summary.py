"""
Daily Summary Agent: generates a plain-English EOD narrative via Claude.
Called at the end of the EOD run with today's performance record and positions.
Stores result in scan_results as scan_type="daily_summary".
"""
import anthropic
from datetime import date, datetime
from core import db
from config.settings import ANTHROPIC_API_KEY, DAILY_PROFIT_TARGET, DAILY_LOSS_LIMIT

client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)


def _build_prompt(record: dict, positions: list, recent_rows: list) -> str:
    today       = record["date"]
    total_pnl   = record.get("total_pnl", 0)
    win_count   = record.get("win_count", 0)
    loss_count  = record.get("loss_count", 0)
    total_trades= record.get("total_trades", 0)
    win_rate    = record.get("win_rate", 0)
    best_t      = record.get("best_trade_ticker", "—")
    best_pnl    = record.get("best_trade_pnl", 0)
    worst_t     = record.get("worst_trade_ticker", "—")
    worst_pnl   = record.get("worst_trade_pnl", 0)
    ending_cap  = record.get("ending_capital", 0)

    # Exit reason breakdown
    exit_counts: dict = {}
    for p in positions:
        r = p.get("close_reason", "UNKNOWN")
        exit_counts[r] = exit_counts.get(r, 0) + 1
    total_exits = sum(exit_counts.values()) or 1
    exit_str = "  |  ".join(
        f"{k} {v} ({v/total_exits*100:.0f}%)"
        for k, v in sorted(exit_counts.items(), key=lambda x: -x[1])
    ) or "no closed positions"

    # Recent context
    if recent_rows:
        recent_pnls = [r["total_pnl"] or 0 for r in recent_rows if r["date"] != today]
        avg_recent  = sum(recent_pnls) / len(recent_pnls) if recent_pnls else 0
        win_days    = sum(1 for p in recent_pnls if p > 0)
        recent_ctx  = (
            f"Prior {len(recent_pnls)} day(s): avg P&L ${avg_recent:,.0f}, "
            f"{win_days}/{len(recent_pnls)} profitable."
        )
    else:
        recent_ctx = "First trading day — no prior history."

    vs_target = (
        f"{'✅ HIT' if total_pnl >= DAILY_PROFIT_TARGET else '⚠️ MISSED'} "
        f"(target ${DAILY_PROFIT_TARGET:,})"
    )

    return f"""You are a trading performance analyst reviewing today's automated AI trading session.

Date: {today}
Total P&L: ${total_pnl:,.2f}  {vs_target}
Trades: {total_trades} ({win_count}W / {loss_count}L)  |  Win rate: {win_rate:.0f}%
Best:  {best_t}  ${best_pnl:+,.2f}
Worst: {worst_t}  ${worst_pnl:+,.2f}
Exits: {exit_str}
Portfolio value: ${ending_cap:,.2f}
{recent_ctx}

Write a 3-4 sentence plain-English summary of today's session. Cover: what happened overall, what worked, what didn't, and one specific actionable observation for tomorrow. Be direct and specific — no filler, no padding. Do not repeat the raw numbers verbatim; interpret them."""


def generate(record: dict, broker: str = "simulation") -> str | None:
    today = record.get("date") or date.today().isoformat()

    # Load today's closed positions for exit breakdown
    all_closed = db.select("positions", filters={"status": "CLOSED"})
    positions  = [p for p in all_closed if (p.get("closed_at") or "").startswith(today)]

    # Load last 5 days for context (excluding today)
    recent_rows = db.select("daily_performance", order="date", limit=6)

    try:
        response = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=300,
            messages=[{"role": "user", "content": _build_prompt(record, positions, recent_rows)}],
        )
        summary_text = response.content[0].text.strip()
    except Exception as e:
        print(f"  ⚠️  Daily summary generation failed: {e}")
        return None

    # Store in scan_results
    payload = {
        "date":      today,
        "scan_type": "daily_summary",
        "results": {
            "summary":      summary_text,
            "total_pnl":    record.get("total_pnl"),
            "grade":        None,
            "generated_at": datetime.now().isoformat(),
            "broker":       broker,
        },
    }
    existing = db.select("scan_results", filters={"date": today, "scan_type": "daily_summary"})
    if existing:
        db.update("scan_results", {"id": existing[0]["id"]}, {"results": payload["results"]})
    else:
        db.insert("scan_results", payload)

    print(f"  📝 Daily summary written to Supabase.")
    print(f"     {summary_text[:120]}{'...' if len(summary_text) > 120 else ''}")
    return summary_text
