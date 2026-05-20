"""
Risk Agent: validates and adjusts trades from the strategy agent.
Enforces hard rules before anything touches the portfolio.
"""
from __future__ import annotations
from config.settings import (
    TOTAL_CAPITAL, MAX_POSITION_PCT, MIN_POSITION_PCT,
    MAX_LOSS_PER_TRADE, MIN_REWARD_RISK, MAX_POSITIONS,
    POSITION_SIZE_BY_CONFIDENCE, QUIET_DAY_MIN_REWARD_RISK,
)


def _validate_trade(trade: dict, min_rr: float = MIN_REWARD_RISK) -> tuple[bool, str]:
    entry  = trade.get("entry_price", 0)
    target = trade.get("target_price", 0)
    stop   = trade.get("stop_loss", 0)
    size   = trade.get("position_size", 0)
    action = trade.get("action", "BUY")

    if not all([entry, target, stop, size]):
        return False, "Missing required fields"

    if entry <= 0:
        return False, "Invalid entry price"

    # Direction checks
    if action == "BUY":
        if target <= entry:
            return False, f"Target {target} must be above entry {entry} for BUY"
        if stop >= entry:
            return False, f"Stop {stop} must be below entry {entry} for BUY"
        potential_gain = (target - entry) / entry
        potential_loss = (entry - stop) / entry
    else:  # SELL_SHORT
        if target >= entry:
            return False, f"Target {target} must be below entry {entry} for SHORT"
        if stop <= entry:
            return False, f"Stop {stop} must be above entry {entry} for SHORT"
        potential_gain = (entry - target) / entry
        potential_loss = (stop - entry) / entry

    # Position size bounds
    max_size = TOTAL_CAPITAL * MAX_POSITION_PCT
    min_size = TOTAL_CAPITAL * MIN_POSITION_PCT
    if size > max_size:
        return False, f"Position size ${size:,} exceeds max ${max_size:,}"
    if size < min_size:
        return False, f"Position size ${size:,} below min ${min_size:,}"

    # Max loss check (round to 2dp — prices have 2dp so ratio is never exact)
    dollar_loss = size * potential_loss
    if round(potential_loss, 4) > round(MAX_LOSS_PER_TRADE, 4) + 0.0001:
        return False, f"Stop too wide: {potential_loss*100:.1f}% loss > {MAX_LOSS_PER_TRADE*100:.0f}% max"

    # Reward:risk check (round to 2dp — price discreteness means ratio is never exact)
    if potential_loss > 0:
        rr = potential_gain / potential_loss
        if round(rr, 2) < min_rr:
            return False, f"Reward:risk {rr:.2f} below minimum {min_rr}"

    return True, "OK"


def _apply_confidence_sizing(trade: dict) -> dict:
    """Override position_size based on confidence level before validation."""
    confidence = (trade.get("confidence") or "MEDIUM").upper()
    correct_size = POSITION_SIZE_BY_CONFIDENCE.get(confidence, POSITION_SIZE_BY_CONFIDENCE["MEDIUM"])
    if trade.get("position_size") != correct_size:
        print(f"        📐 {trade.get('ticker')}: size ${trade.get('position_size', '?'):,} → ${correct_size:,} ({confidence})")
    trade["position_size"] = correct_size
    return trade


def _compute_shares(trade: dict) -> int:
    size  = trade.get("position_size", 0)
    entry = trade.get("entry_price", 1)
    return max(1, int(size / entry))


def run(strategy_output: dict, quiet_day: bool = False) -> dict:
    raw_trades = strategy_output.get("trades", [])
    approved   = []
    rejected   = []
    min_rr     = QUIET_DAY_MIN_REWARD_RISK if quiet_day else MIN_REWARD_RISK
    if quiet_day:
        print(f"        🤫 Quiet day mode — R:R floor relaxed to {min_rr} (from {MIN_REWARD_RISK})")

    for trade in raw_trades[:MAX_POSITIONS]:
        trade = _apply_confidence_sizing(trade)
        ok, reason = _validate_trade(trade, min_rr=min_rr)
        if ok:
            trade["shares"]  = _compute_shares(trade)
            trade["status"]  = "PLANNED"
            # Recompute estimated profit/loss with share count
            shares = trade["shares"]
            entry  = trade["entry_price"]
            target = trade["target_price"]
            stop   = trade["stop_loss"]
            action = trade["action"]
            if action == "BUY":
                trade["estimated_profit"] = round(shares * (target - entry), 2)
                trade["max_loss"]         = round(shares * (entry - stop), 2)
            else:
                trade["estimated_profit"] = round(shares * (entry - target), 2)
                trade["max_loss"]         = round(shares * (stop - entry), 2)
            trade["reward_risk"] = round(
                trade["estimated_profit"] / trade["max_loss"], 2
            ) if trade["max_loss"] > 0 else 0
            approved.append(trade)
        else:
            rejected.append({"ticker": trade.get("ticker"), "reason": reason})

    return {
        "approved_trades": approved,
        "rejected_trades": rejected,
        "market_context":  strategy_output.get("market_context", ""),
        "risk_note":       strategy_output.get("risk_note", ""),
        "total_estimated_profit": sum(t["estimated_profit"] for t in approved),
        "total_max_loss":         sum(t["max_loss"] for t in approved),
    }
