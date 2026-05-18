"""
Strategy Agent: takes scan candidates and uses Claude to select
the best trades for the day with entry/target/stop levels.
"""
import json
import anthropic
from datetime import datetime
from config.settings import (
    ANTHROPIC_API_KEY, TOTAL_CAPITAL, DAILY_PROFIT_TARGET,
    MAX_POSITIONS, MAX_POSITION_PCT, MIN_POSITION_PCT,
    MAX_LOSS_PER_TRADE, MIN_REWARD_RISK, TARGET_PCT,
    POSITION_SIZE_BY_CONFIDENCE,
)

client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

SYSTEM = (
    "You are a professional day trader managing a $100,000 simulated portfolio. "
    "Your goal is $1,000 net profit per day with no net losses. "
    "Always respond with valid JSON only — no markdown, no explanation outside JSON."
)


def _build_prompt(candidates: list[dict], market_summary: str, max_positions: int) -> str:
    today = datetime.now().strftime("%A %B %d, %Y")
    sizes = POSITION_SIZE_BY_CONFIDENCE

    return f"""Today is {today}. You are selecting day trades from the scanned candidates below.

PRE-MARKET CONDITIONS:
{market_summary}

PORTFOLIO RULES:
- Total capital: ${TOTAL_CAPITAL:,}
- Daily profit target: ${DAILY_PROFIT_TARGET:,}
- Max positions today: {max_positions} (may be reduced due to market conditions)
- Position size by confidence: HIGH=${sizes['HIGH']:,}, MEDIUM=${sizes['MEDIUM']:,}, LOW=${sizes['LOW']:,}
- Profit target: {TARGET_PCT*100:.0f}% above entry (hard rule — set target_price = entry * {1+TARGET_PCT})
- Stop loss: max {MAX_LOSS_PER_TRADE*100:.0f}% below entry (hard rule — set stop_loss = entry * {1-MAX_LOSS_PER_TRADE})
- Minimum reward:risk ratio: {MIN_REWARD_RISK}:1
- All positions closed by end of day (no overnight holds)
- Protect the principal — if no high-conviction setups exist, select fewer trades
- If futures bias is BEARISH, prefer short setups or reduce position count
- If futures bias is BULLISH, favor momentum longs with strong volume signals

CANDIDATES (sorted by signal strength):
{json.dumps(candidates, indent=2, default=str)}

Select the best {max_positions} or fewer trades. For each trade provide SPECIFIC, REALISTIC prices based on the current price shown.

Respond in this exact JSON format:
{{
  "date": "{today}",
  "market_context": "2-3 sentence summary of today's market setup including VIX level, futures direction, and why these trades were selected",
  "trades": [
    {{
      "ticker": "TICKER",
      "action": "BUY",
      "entry_price": 0.00,
      "target_price": 0.00,
      "stop_loss": 0.00,
      "position_size": 0,
      "shares": 0,
      "estimated_profit": 0,
      "max_loss": 0,
      "reward_risk": 0.0,
      "confidence": "HIGH|MEDIUM|LOW",
      "reasoning": "2-3 sentences citing specific signals from the scan data"
    }}
  ],
  "total_estimated_profit": 0,
  "total_max_loss": 0,
  "risk_note": "one sentence on overall risk posture for today"
}}"""


def run(candidates: list[dict], market_summary: str = "", max_positions: int = MAX_POSITIONS) -> dict:
    if not candidates:
        return {"trades": [], "market_context": "No candidates found.", "total_estimated_profit": 0}

    response = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=3000,
        system=SYSTEM,
        messages=[{"role": "user", "content": _build_prompt(candidates, market_summary, max_positions)}],
    )

    raw = response.content[0].text.strip()
    # Extract JSON object/array — find the outermost { } or [ ]
    import re
    json_match = re.search(r'(\{[\s\S]*\}|\[[\s\S]*\])', raw)
    if json_match:
        raw = json_match.group(1)

    return json.loads(raw)
