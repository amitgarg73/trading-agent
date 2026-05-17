"""
Intraday Agent: refreshes positions every 30 min, logs updates to DB.
Runs via GitHub Actions schedule during market hours.
"""
from datetime import datetime
from agents.portfolio import refresh_positions
from core import db


def run(broker: str = "simulation") -> dict:
    now     = datetime.utcnow().isoformat()
    updated = refresh_positions(broker=broker)

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
