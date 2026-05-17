"""
Sector Guard (V2d): Prevents over-concentration in one sector.
Runs after risk agent, before portfolio agent — no Claude API call needed.

Fetches sector from yfinance for each approved trade (10-15 calls max).
Caps positions per sector at MAX_PER_SECTOR, dropping lowest-confidence excess.
"""
from __future__ import annotations
import yfinance as yf
from config.settings import MAX_PER_SECTOR, ETF_UNIVERSE

_CONFIDENCE_RANK = {"HIGH": 3, "MEDIUM": 2, "LOW": 1}
_ETF_SET = set(ETF_UNIVERSE)


def _get_sector(ticker: str) -> str:
    if ticker in _ETF_SET:
        return "ETF"
    try:
        info = yf.Ticker(ticker).info
        return info.get("sector") or "Unknown"
    except Exception:
        return "Unknown"


def run(risk_output: dict) -> dict:
    approved = list(risk_output.get("approved_trades", []))

    if not approved:
        return {**risk_output, "sector_blocked": []}

    # Fetch sectors
    for trade in approved:
        trade["sector"] = _get_sector(trade["ticker"])

    # Group by sector
    by_sector: dict[str, list] = {}
    for trade in approved:
        by_sector.setdefault(trade["sector"], []).append(trade)

    kept, blocked = [], []
    for sector, trades in by_sector.items():
        # Don't cap Unknown — yfinance can't classify these, no reason to penalise them
        if sector == "Unknown" or len(trades) <= MAX_PER_SECTOR:
            kept.extend(trades)
        else:
            # Keep top N by confidence then estimated_profit
            sorted_trades = sorted(
                trades,
                key=lambda t: (
                    _CONFIDENCE_RANK.get(t.get("confidence", "LOW"), 0),
                    t.get("estimated_profit", 0),
                ),
                reverse=True,
            )
            kept.extend(sorted_trades[:MAX_PER_SECTOR])
            for t in sorted_trades[MAX_PER_SECTOR:]:
                blocked.append({
                    "ticker": t["ticker"],
                    "sector": sector,
                    "reason": f"Sector cap: {len(trades)} {sector} picks, max {MAX_PER_SECTOR}",
                })

    return {
        **risk_output,
        "approved_trades":    kept,
        "total_estimated_profit": sum(t["estimated_profit"] for t in kept),
        "total_max_loss":         sum(t["max_loss"] for t in kept),
        "sector_blocked":     blocked,
    }
