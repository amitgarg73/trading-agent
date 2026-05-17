"""
30-day backtest — simulates what the trading agent would have done over the last 30 trading days.
Uses real historical price data. No Claude API calls needed — uses top scanner scores as proxy
for strategy selection (same logic, deterministic).

Usage: python3 backtest.py [--days 30] [--top 3]
"""
import argparse
import yfinance as yf
import pandas as pd
import ta
from datetime import datetime, timedelta
from config.settings import (
    UNIVERSE, RSI_OVERSOLD, RSI_OVERBOUGHT,
    MIN_VOLUME_RATIO, MIN_PRICE, MIN_AVG_VOLUME, SCORE_THRESHOLD,
    TOTAL_CAPITAL, MAX_POSITION_PCT, MAX_POSITIONS, DAILY_PROFIT_TARGET
)

STOP_PCT   = 0.01   # 1% stop loss
TARGET_PCT = 0.03   # 3% target (3:1 reward:risk)


def get_trading_days(n: int) -> list:
    days = []
    d = datetime.today().date() - timedelta(days=1)
    while len(days) < n:
        if d.weekday() < 5:  # Mon–Fri
            days.append(d)
        d -= timedelta(days=1)
    return list(reversed(days))


def score_ticker(df: pd.DataFrame, as_of_idx: int) -> float:
    """Score a ticker using data up to as_of_idx (simulates premarket scan)."""
    sub = df.iloc[:as_of_idx]
    if len(sub) < 20:
        return 0
    close  = sub["Close"]
    volume = sub["Volume"]
    score  = 0

    rsi = ta.momentum.RSIIndicator(close, 14).rsi().iloc[-1]
    if pd.notna(rsi):
        if rsi < RSI_OVERSOLD:   score += 2
        elif rsi > RSI_OVERBOUGHT: score -= 2

    hist = ta.trend.MACD(close).macd_diff().iloc[-1]
    if pd.notna(hist):
        score += 2 if hist > 0 else -1

    bb_pct = ta.volatility.BollingerBands(close, 20, 2).bollinger_pband().iloc[-1]
    if pd.notna(bb_pct):
        if bb_pct < 0.2:   score += 2
        elif bb_pct > 0.8: score -= 1

    avg_vol = volume.rolling(20).mean().iloc[-1]
    if avg_vol > 0 and volume.iloc[-1] / avg_vol >= MIN_VOLUME_RATIO:
        score += 2

    sma20 = close.rolling(20).mean().iloc[-1]
    sma50 = close.rolling(50).mean().iloc[-1] if len(sub) >= 50 else None
    price = close.iloc[-1]
    if sma50 and pd.notna(sma50):
        if price > sma20 > sma50:   score += 1
        elif price < sma20 < sma50: score -= 2

    return max(-10, min(10, score))


def run_backtest(days: int = 30, top_n: int = 3):
    print(f"\n{'='*60}")
    print(f"  BACKTEST — last {days} trading days")
    print(f"  Capital: ${TOTAL_CAPITAL:,}  |  Max positions: {top_n}")
    print(f"  Stop: {STOP_PCT*100:.1f}%  |  Target: {TARGET_PCT*100:.1f}%")
    print(f"{'='*60}\n")

    trading_days = get_trading_days(days)
    start_date   = trading_days[0] - timedelta(days=90)  # need history for indicators

    print(f"  Fetching price history for {len(UNIVERSE)} tickers...")
    all_data = {}
    for ticker in UNIVERSE:
        try:
            df = yf.download(ticker, start=start_date,
                             end=datetime.today().date() + timedelta(days=1),
                             progress=False, auto_adjust=True)
            if df is None or len(df) < 20:
                continue
            # Flatten multi-level columns if present
            if isinstance(df.columns, pd.MultiIndex):
                df.columns = df.columns.get_level_values(0)
            all_data[ticker] = df
        except Exception:
            pass
    print(f"  Loaded data for {len(all_data)} tickers\n")

    # ── Day-by-day simulation ──────────────────────────────────────────────────
    capital        = float(TOTAL_CAPITAL)
    daily_results  = []
    all_trades     = []
    win_count      = 0
    loss_count     = 0

    for day in trading_days:
        day_str = day.strftime("%Y-%m-%d")

        # Score each ticker using data available before this day
        scored = []
        for ticker, df in all_data.items():
            dates = df.index.date
            idx = next((i for i, d in enumerate(dates) if d >= day), None)
            if idx is None or idx < 20:
                continue
            price = float(df["Close"].iloc[idx - 1])
            if price < MIN_PRICE:
                continue
            s = score_ticker(df, idx)
            if s >= SCORE_THRESHOLD:
                scored.append((ticker, s, price, idx))

        scored.sort(key=lambda x: -x[1])
        selected = scored[:top_n]

        if not selected:
            daily_results.append({
                "date": day_str, "trades": 0,
                "pnl": 0.0, "capital": capital
            })
            continue

        # Simulate each trade
        day_pnl = 0.0
        position_size = min(capital * MAX_POSITION_PCT,
                            capital / max(len(selected), 1))

        for ticker, score, entry_price, idx in selected:
            df = all_data[ticker]
            dates = df.index.date

            # Find the actual trading day row
            day_idx = next((i for i, d in enumerate(dates) if d == day), None)
            if day_idx is None:
                continue

            # Use open as entry, check high/low for target/stop
            open_price = float(df["Open"].iloc[day_idx])
            high_price = float(df["High"].iloc[day_idx])
            low_price  = float(df["Low"].iloc[day_idx])
            close_price= float(df["Close"].iloc[day_idx])

            entry   = open_price
            target  = entry * (1 + TARGET_PCT)
            stop    = entry * (1 - STOP_PCT)
            shares  = int(position_size / entry)

            if shares == 0:
                continue

            # Determine outcome
            if high_price >= target:
                exit_price  = target
                close_reason = "TARGET"
                win_count   += 1
            elif low_price <= stop:
                exit_price   = stop
                close_reason = "STOP"
                loss_count  += 1
            else:
                exit_price   = close_price
                close_reason = "EOD"
                if exit_price > entry:
                    win_count += 1
                else:
                    loss_count += 1

            pnl = (exit_price - entry) * shares
            day_pnl += pnl

            all_trades.append({
                "date":         day_str,
                "ticker":       ticker,
                "score":        score,
                "entry":        round(entry, 2),
                "exit":         round(exit_price, 2),
                "shares":       shares,
                "pnl":          round(pnl, 2),
                "close_reason": close_reason,
            })

        capital += day_pnl
        daily_results.append({
            "date":    day_str,
            "trades":  len(selected),
            "pnl":     round(day_pnl, 2),
            "capital": round(capital, 2),
        })

        icon = "✅" if day_pnl >= 0 else "🔴"
        print(f"  {icon} {day_str}  trades={len(selected)}  "
              f"P&L=${day_pnl:+,.2f}  capital=${capital:,.0f}")

    # ── Summary ────────────────────────────────────────────────────────────────
    total_pnl    = capital - TOTAL_CAPITAL
    total_trades = win_count + loss_count
    win_rate     = (win_count / total_trades * 100) if total_trades else 0
    win_days     = sum(1 for r in daily_results if r["pnl"] > 0)
    loss_days    = sum(1 for r in daily_results if r["pnl"] <= 0)
    avg_daily    = total_pnl / len(daily_results) if daily_results else 0
    total_return = (total_pnl / TOTAL_CAPITAL) * 100
    annualized   = total_return * (252 / days)

    wins_list    = [t["pnl"] for t in all_trades if t["pnl"] > 0]
    losses_list  = [t["pnl"] for t in all_trades if t["pnl"] <= 0]
    avg_win      = sum(wins_list)  / len(wins_list)  if wins_list  else 0
    avg_loss     = sum(losses_list)/ len(losses_list) if losses_list else 0
    rr           = abs(avg_win / avg_loss) if avg_loss != 0 else 0

    targets_hit  = sum(1 for t in all_trades if t["close_reason"] == "TARGET")
    stops_hit    = sum(1 for t in all_trades if t["close_reason"] == "STOP")
    eod_closed   = sum(1 for t in all_trades if t["close_reason"] == "EOD")

    print(f"\n{'='*60}")
    print(f"  BACKTEST RESULTS — {days} trading days")
    print(f"{'='*60}")
    print(f"  Starting capital:   ${TOTAL_CAPITAL:,}")
    print(f"  Ending capital:     ${capital:,.0f}")
    print(f"  Total P&L:          ${total_pnl:+,.2f}")
    print(f"  Total return:       {total_return:+.2f}%")
    print(f"  Annualized return:  {annualized:+.1f}%")
    print(f"  Avg daily P&L:      ${avg_daily:+,.2f}  (target: ${DAILY_PROFIT_TARGET:,})")
    print(f"")
    print(f"  Win days:           {win_days} / {len(daily_results)}")
    print(f"  Loss days:          {loss_days} / {len(daily_results)}")
    print(f"  Total trades:       {total_trades}")
    print(f"  Win rate:           {win_rate:.1f}%")
    print(f"  Avg winner:         ${avg_win:+,.2f}")
    print(f"  Avg loser:          ${avg_loss:,.2f}")
    print(f"  Actual reward:risk: {rr:.2f}x")
    print(f"")
    print(f"  Targets hit:        {targets_hit}")
    print(f"  Stops hit:          {stops_hit}")
    print(f"  EOD closes:         {eod_closed}")

    # Top and worst tickers
    by_ticker = {}
    for t in all_trades:
        by_ticker.setdefault(t["ticker"], []).append(t["pnl"])
    ticker_pnl = [(k, sum(v), len(v)) for k, v in by_ticker.items()]
    ticker_pnl.sort(key=lambda x: -x[1])

    print(f"\n  Top 5 tickers:")
    for ticker, pnl, n in ticker_pnl[:5]:
        print(f"    {ticker:6s}  ${pnl:+,.2f}  ({n} trades)")

    print(f"\n  Worst 5 tickers:")
    for ticker, pnl, n in ticker_pnl[-5:]:
        print(f"    {ticker:6s}  ${pnl:+,.2f}  ({n} trades)")

    # Grade
    score = 0
    score += min(avg_daily / DAILY_PROFIT_TARGET * 40, 40)
    score += (win_days / len(daily_results)) * 30
    score += min(win_rate / 100 * 30, 30)

    if score >= 80:   grade = "A — Strong. Strategy is working historically."
    elif score >= 60: grade = "B — Good. Minor tuning may improve results."
    elif score >= 40: grade = "C — Mediocre. Review thresholds and universe."
    else:             grade = "D — Poor. Strategy needs rework."

    print(f"\n  Score: {score:.0f}/100")
    print(f"  Grade: {grade}")
    print(f"{'='*60}\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=30)
    parser.add_argument("--top",  type=int, default=3,
                        help="Number of top-scored tickers to trade per day")
    args = parser.parse_args()
    run_backtest(args.days, args.top)
