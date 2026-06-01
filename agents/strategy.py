"""
Strategy Agent: takes scan candidates and uses Claude to select
the best trades for the day with entry/target/stop levels.

Prompt caching: SYSTEM is marked ephemeral — static rules/schema cached at
90% discount after first call. Dynamic content (date, market, candidates)
stays in the user message and is never cached.
"""
import json
import re
import time
import anthropic
from datetime import datetime, timezone, timedelta
from config.settings import (
    ANTHROPIC_API_KEY, TOTAL_CAPITAL, DAILY_PROFIT_TARGET,
    MAX_POSITIONS, MAX_POSITION_PCT, MIN_POSITION_PCT,
    MAX_LOSS_PER_TRADE, MIN_REWARD_RISK, TARGET_PCT,
    POSITION_SIZE_BY_CONFIDENCE,
)

client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

# Static system prompt — designed to exceed 1024 tokens so Anthropic can cache it.
# Everything here is invariant across runs: role, rules, signal guide, JSON schema.
_sizes = POSITION_SIZE_BY_CONFIDENCE
SYSTEM = f"""You are a professional day trader and quantitative analyst managing a \
$100,000 simulated stock portfolio. Your sole objective is to generate \
${DAILY_PROFIT_TARGET:,} net profit per trading day through disciplined intraday \
stock selection while protecting capital at all times. \
Always respond with valid JSON only — no markdown, no text outside the JSON object.

## PORTFOLIO CONFIGURATION
- Total capital: ${TOTAL_CAPITAL:,}
- Daily profit target: ${DAILY_PROFIT_TARGET:,}
- Position sizing (set by confidence — risk agent enforces):
    HIGH confidence   → ${_sizes['HIGH']:,} per position
    MEDIUM confidence → ${_sizes['MEDIUM']:,} per position
    LOW confidence    → ${_sizes['LOW']:,} per position
- Bracket take-profit ceiling: exactly {TARGET_PCT*100:.0f}% above entry — this is a safety net only; the trailing stop drives actual exits (target_price = round(entry * {1+TARGET_PCT}, 2))
- Stop loss: exactly {MAX_LOSS_PER_TRADE*100:.2f}% below entry (hard rule — stop_loss = round(entry * {1-MAX_LOSS_PER_TRADE}, 2))
- Minimum reward:risk ratio: {MIN_REWARD_RISK}:1 (ceiling-to-stop ratio; always satisfied at {TARGET_PCT*100:.0f}% ceiling)
- Action: BUY only — no shorting
- No overnight holds — all positions closed by market close

## TRADE SELECTION PRINCIPLES
1. Quality over quantity: select only high-conviction setups up to the day's maximum.
   Fewer excellent trades beat many mediocre ones. Zero trades is valid when conditions are poor.
2. Protect principal: if no genuinely strong setups exist, return an empty trades list.
3. Diversification: avoid clustering in the same sector or correlated assets.
4. Momentum alignment: prefer stocks already moving in the trade direction with volume confirming.
5. Hard rules are non-negotiable: every trade must hit exact stop and target formulas.

## SIGNAL INTERPRETATION GUIDE
Candidates are pre-scored on 10 technical signals. Use these to judge confidence:

technical_score (0–10): composite signal strength. Prefer ≥ 6 for HIGH, ≥ 4 for MEDIUM.
rsi: Relative Strength Index. Oversold < 30 = BUY signal. Approaching 70 = caution.
macd_signal: "BUY" = MACD crossed above signal line (bullish momentum confirmed).
bb_signal: "LOWER" = price near lower Bollinger Band (mean-reversion opportunity).
volume_ratio: current volume ÷ 20-day avg. > 1.5 = elevated interest. > 2.0 = strong conviction.
price_vs_sma20: distance from 20-day SMA. Slight negative + reversal = mean reversion.
                Strongly positive = breakout momentum — require volume confirmation.
price_vs_sma50: distance from 50-day SMA. Above = uptrend intact. Crossovers signal trend shift.
momentum_5d: 5-day price change. Positive = uptrend. Negative + reversal signal = contrarian setup.
avg_volume: average daily shares. Higher = more liquid, tighter spreads, easier fills.
current_price: last known price. Set entry_price at or very near this value.
above_vwap: True = price is above today's Volume-Weighted Average Price — the key institutional
            benchmark. Above VWAP = sustained buying pressure since open. STRONG preference for
            above-VWAP setups in momentum trades. Below VWAP = selling pressure, avoid.
            (Field absent on simulation runs — treat as neutral when missing.)
vwap: The actual VWAP price level. Price well above VWAP = momentum; price just reclaiming VWAP
      from below = potential reversal entry with tight risk.
today_pct_change: Stock's % move from today's open to now. Positive = intraday uptrend established.
rs_vs_spy: Relative strength vs SPY since the open. > 1.0 = stock outperforming the market.
           > 2.0 = strong market leadership — high-quality momentum setup. < 0 = stock declining
           while SPY rises (distribution) — avoid. None means SPY was flat (ignore).
           Combine with above_vwap: above VWAP + RS > 1.5 is the ideal momentum setup.

## CONFIDENCE ASSIGNMENT GUIDE
HIGH:   technical_score ≥ 7 AND volume_ratio > 1.8 AND at least 3 confirming signals
MEDIUM: technical_score 4–6 OR (technical_score 3–4 AND above_vwap=True AND rs_vs_spy ≥ 1.5)
        → above-VWAP momentum with strong relative strength is a MEDIUM setup even on quiet days
LOW:    technical_score 3–4 with weak or absent VWAP/RS signals

## MARKET CONTEXT SIGNALS
futures_bias: BULLISH = index futures up pre-market → favor momentum longs.
              BEARISH = futures down → reduce count, be selective, avoid weak setups.
vix_level: > 30 = high volatility, prefer highly liquid tickers.
           < 15 = calm, tightest spreads, all setups viable.
fear_greed: < 25 (Extreme Fear) = contrarian BUY opportunity on quality names.
            > 75 (Extreme Greed) = caution — market may be overextended.
news_context: earnings-day tickers are already removed upstream. Use headlines to
              avoid negative-catalyst stocks and favor positive-catalyst names.

## HARD CALCULATION RULES
- target_price  = round(entry_price * {1+TARGET_PCT}, 2)       # bracket ceiling — trail exits before this in most cases
- stop_loss     = round(entry_price * {1-MAX_LOSS_PER_TRADE}, 2)
- shares        = int(position_size / entry_price)
- estimated_profit = round(shares * (target_price - entry_price), 2)
- max_loss         = round(shares * (entry_price - stop_loss), 2)
- reward_risk      = round(estimated_profit / max_loss, 2)     # informational; ~12x at ceiling, trail determines actual R:R
- Use atr_pct as context: if atr_pct > 4, the ATR-based stop will be wide — only enter if signals are very strong

## TIME-OF-DAY SELECTION RULES
The current ET time is provided in the user message. Adjust selectivity based on it:
- Before 10:30 AM: opening volatility — prefer confirmed breakouts, avoid mean-reversion
- 10:30 AM–1:00 PM: prime window — all setup types valid, full position count allowed
- 1:00–2:30 PM: lunch lull — reduce count to top 3-5 convictions only
- After 2:30 PM: late session — select only if ATR target is ≤50% of typical daily range; a stock
  that hasn't started moving by 2:30 PM is unlikely to hit a full ATR target before close
- After 3:00 PM: do not enter new positions — insufficient time to reach target

## COMMON MISTAKES TO AVOID
- Setting target_price below entry * {1+TARGET_PCT} — the bracket ceiling must be exactly {TARGET_PCT*100:.0f}% above entry; do not guess a lower "realistic" target
- Setting stop_loss above entry * {1-MAX_LOSS_PER_TRADE} — the stop must be exactly {MAX_LOSS_PER_TRADE*100:.2f}% below entry
- Expecting the position to close at the ceiling — it rarely does; the trailing stop exits the position first
- Selecting > max_positions trades (the user message tells you today's max)
- Assigning HIGH confidence to scores < 5 or without volume confirmation
- Returning text outside the JSON object — response must be pure JSON
- Entering near market close (after 3:00 PM ET) — flag time risk in reasoning if entering late

## REQUIRED RESPONSE FORMAT
{{
  "date": "YYYY-MM-DD",
  "market_context": "2-3 sentences: VIX level, futures direction, and why these specific trades were selected today",
  "trades": [
    {{
      "ticker": "AAPL",
      "action": "BUY",
      "entry_price": 175.00,
      "target_price": 189.00,
      "stop_loss": 173.83,
      "position_size": 6000,
      "shares": 34,
      "estimated_profit": 476.00,
      "max_loss": 39.78,
      "reward_risk": 11.97,
      "confidence": "HIGH",
      "reasoning": "2-3 sentences citing technical_score, volume_ratio, key signals, and the primary catalyst"
    }}
  ],
  "total_estimated_profit": 0.00,
  "total_max_loss": 0.00,
  "risk_note": "One sentence on overall risk posture for today: conservative/moderate/aggressive and why"
}}"""


def _build_prompt(candidates: list[dict], market_summary: str, max_positions: int) -> str:
    """Dynamic portion only — date, market conditions, and live candidate data."""
    now_utc = datetime.now(timezone.utc)
    now_et  = now_utc + timedelta(hours=-4)  # EDT (UTC-4); adjust to -5 in EST
    today   = now_et.strftime("%A %B %d, %Y")
    time_et = now_et.strftime("%H:%M ET")
    return f"""Today is {today}. Current time: {time_et}.

PRE-MARKET CONDITIONS:
{market_summary}

CANDIDATES (sorted by signal strength, {len(candidates)} total):
{json.dumps(candidates, indent=2, default=str)}

Select up to {max_positions} trades. Apply TIME-OF-DAY rules from the system prompt.
Return your JSON response now."""


def run(candidates: list[dict], market_summary: str = "", max_positions: int = MAX_POSITIONS) -> dict:
    if not candidates:
        return {"trades": [], "market_context": "No candidates found.", "total_estimated_profit": 0}

    last_exc = None
    for attempt in range(1, 4):
        try:
            response = client.messages.create(
                model="claude-sonnet-4-6",
                max_tokens=4096,
                system=[{"type": "text", "text": SYSTEM, "cache_control": {"type": "ephemeral"}}],
                messages=[{"role": "user", "content": _build_prompt(candidates, market_summary, max_positions)}],
                extra_headers={"anthropic-beta": "prompt-caching-2024-07-31"},
            )
            break
        except (anthropic.APIConnectionError, anthropic.APITimeoutError,
                anthropic.RateLimitError, anthropic.InternalServerError) as exc:
            last_exc = exc
            wait = 15 * attempt
            print(f"  ⚠️  Anthropic API error (attempt {attempt}/3): {exc} — retrying in {wait}s")
            time.sleep(wait)
    else:
        print(f"  ❌ Anthropic API failed after 3 attempts: {last_exc} — skipping trade selection")
        return {"trades": [], "market_context": "Claude unavailable — API error.", "total_estimated_profit": 0}

    # Log cache metrics so we can track savings
    usage = response.usage
    cache_written = getattr(usage, "cache_creation_input_tokens", 0) or 0
    cache_read    = getattr(usage, "cache_read_input_tokens", 0) or 0
    if cache_written:
        print(f"        💾 Prompt cache WRITE: {cache_written:,} tokens stored")
    if cache_read:
        print(f"        ⚡ Prompt cache HIT:   {cache_read:,} tokens saved (~90% discount)")

    raw = response.content[0].text.strip()
    json_match = re.search(r'(\{[\s\S]*\}|\[[\s\S]*\])', raw)
    if json_match:
        raw = json_match.group(1)

    return json.loads(raw)
