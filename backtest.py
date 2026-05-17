"""
Backtest — simulates what the trading agent would have done over a historical window.
Uses real historical price data. No Claude API calls — top scanner scores proxy strategy selection.

Runs TWO simulations automatically:
  1. Baseline — no gates, trades every day, max top_n positions
  2. V2-gated — applies VIX, Fear & Greed, futures, and economic calendar gates

The comparison shows directly whether the intelligence layer adds value.

Usage:
  python3 backtest.py --days 30 --top 15
  python3 backtest.py --start-date 2008-01-01 --end-date 2008-12-31 --top 15
  python3 backtest.py --start-date 2003-01-01 --end-date 2003-12-31 --top 15
"""
import argparse
import yfinance as yf
import pandas as pd
import ta
import urllib.request
import json
from datetime import datetime, timedelta, date
from config.settings import (
    UNIVERSE, RSI_OVERSOLD, RSI_OVERBOUGHT,
    MIN_VOLUME_RATIO, MIN_PRICE, MIN_AVG_VOLUME, SCORE_THRESHOLD,
    TOTAL_CAPITAL, MAX_POSITION_PCT, MAX_POSITIONS, DAILY_PROFIT_TARGET
)

STOP_PCT   = 0.01
TARGET_PCT = 0.03

# ── Economic calendar (same as market_context.py) ─────────────────────────────
FOMC_DATES = {
    "2025-01-29", "2025-03-19", "2025-05-07", "2025-06-18",
    "2025-07-30", "2025-09-17", "2025-10-29", "2025-12-10",
    "2026-01-28", "2026-03-18", "2026-05-06", "2026-06-17",
    "2026-07-29", "2026-09-16", "2026-10-28", "2026-12-09",
}
CPI_DATES = {
    "2025-01-15", "2025-02-12", "2025-03-12", "2025-04-10",
    "2025-05-13", "2025-06-11", "2025-07-15", "2025-08-12",
    "2025-09-10", "2025-10-15", "2025-11-13", "2025-12-10",
    "2026-01-14", "2026-02-11", "2026-03-11", "2026-04-10",
    "2026-05-13", "2026-06-10", "2026-07-14", "2026-08-12",
    "2026-09-11", "2026-10-14", "2026-11-12", "2026-12-10",
}
NFP_DATES = {
    "2025-01-10", "2025-02-07", "2025-03-07", "2025-04-04",
    "2025-05-02", "2025-06-06", "2025-07-03", "2025-08-01",
    "2025-09-05", "2025-10-03", "2025-11-07", "2025-12-05",
    "2026-01-02", "2026-02-06", "2026-03-06", "2026-04-03",
    "2026-05-01", "2026-06-05", "2026-07-03", "2026-08-07",
    "2026-09-04", "2026-10-02", "2026-11-06", "2026-12-04",
}


def get_trading_days(n=None, start_date=None, end_date=None) -> list:
    if start_date and end_date:
        days = []
        d = start_date
        while d <= end_date:
            if d.weekday() < 5:
                days.append(d)
            d += timedelta(days=1)
        return days
    days = []
    d = datetime.today().date() - timedelta(days=1)
    while len(days) < n:
        if d.weekday() < 5:
            days.append(d)
        d -= timedelta(days=1)
    return list(reversed(days))


def fetch_vix_history(start_date, end_date=None) -> dict:
    """Returns {date_str: vix_close}."""
    fetch_end = (end_date + timedelta(days=1)) if end_date else (datetime.today().date() + timedelta(days=1))
    try:
        df = yf.download("^VIX", start=start_date, end=fetch_end,
                         progress=False, auto_adjust=True)
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
        return {str(d.date()): round(float(v), 2)
                for d, v in zip(df.index, df["Close"]) if pd.notna(v)}
    except Exception:
        return {}


def fetch_futures_history(start_date, end_date=None) -> dict:
    """Returns {date_str: avg_pct_change} — open vs prior close for ES/NQ/YM."""
    fetch_end = (end_date + timedelta(days=1)) if end_date else (datetime.today().date() + timedelta(days=1))
    symbols = {"S&P500": "ES=F", "Nasdaq": "NQ=F", "Dow": "YM=F"}
    series = {}
    for name, sym in symbols.items():
        try:
            df = yf.download(sym, start=start_date, end=fetch_end,
                             progress=False, auto_adjust=True)
            if isinstance(df.columns, pd.MultiIndex):
                df.columns = df.columns.get_level_values(0)
            if len(df) < 2:
                continue
            pct = ((df["Open"] - df["Close"].shift(1)) / df["Close"].shift(1) * 100).dropna()
            for d, v in zip(pct.index, pct):
                if pd.notna(v):
                    series.setdefault(str(d.date()), []).append(round(float(v), 2))
        except Exception:
            pass
    return {d: round(sum(vs) / len(vs), 2) for d, vs in series.items() if vs}


def fetch_fear_greed_history(limit: int = 365) -> dict:
    """Returns {date_str: {value, classification}} from alternative.me (free)."""
    try:
        url = f"https://api.alternative.me/fng/?limit={limit}"
        with urllib.request.urlopen(url, timeout=10) as resp:
            data = json.loads(resp.read())
        result = {}
        for entry in data.get("data", []):
            d = datetime.fromtimestamp(int(entry["timestamp"])).strftime("%Y-%m-%d")
            result[d] = {
                "value":          int(entry["value"]),
                "classification": entry["value_classification"],
            }
        return result
    except Exception:
        return {}


def apply_v2_gates(day_str: str, vix_hist: dict, futures_hist: dict,
                   fg_hist: dict, top_n: int) -> tuple:
    """
    Returns (decision, effective_positions, reasons).
    decision: 'GO' | 'CAUTION' | 'SKIP'
    """
    decision   = "GO"
    max_pos    = top_n
    reasons    = []

    # VIX gate — tiered reduction, no hard skip (futures gate handles crash days)
    vix = vix_hist.get(day_str)
    if vix is not None:
        if vix > 45:
            max_pos = min(max_pos, 2)
            decision = "CAUTION"
            reasons.append(f"VIX {vix:.1f} (extreme crisis → 2 pos)")
        elif vix > 30:
            max_pos = min(max_pos, 3)
            decision = "CAUTION"
            reasons.append(f"VIX {vix:.1f} (crisis → 3 pos)")
        elif vix > 25:
            max_pos = min(max_pos, 5)
            decision = "CAUTION"
            reasons.append(f"VIX {vix:.1f}")
        elif vix > 20:
            max_pos = min(max_pos, 10)
            decision = "CAUTION"
            reasons.append(f"VIX {vix:.1f}")

    # Fear & Greed gate — confirming signal only (not standalone)
    fg = fg_hist.get(day_str)
    vix_val     = vix_hist.get(day_str)
    fut_val     = futures_hist.get(day_str)
    vix_bearish = vix_val is not None and vix_val > 20
    fut_bearish = fut_val is not None and fut_val < -0.5
    if fg:
        v         = fg["value"]
        confirmed = vix_bearish or fut_bearish
        if v < 25 and confirmed:
            max_pos = min(max_pos, 10)
            if decision == "GO": decision = "CAUTION"
            reasons.append(f"F&G {v} ({fg['classification']}+confirmed)")
        elif v > 80:
            max_pos = min(max_pos, 12)
            reasons.append(f"F&G {v} (Extreme Greed)")

    # Futures gate
    avg_fut = futures_hist.get(day_str)
    if avg_fut is not None:
        if avg_fut < -1.5:
            return "SKIP", 0, [f"Futures {avg_fut:.1f}% (sell-off)"]
        elif avg_fut < -0.5:
            max_pos = min(max_pos, 8)
            if decision == "GO": decision = "CAUTION"
            reasons.append(f"Futures {avg_fut:.1f}%")

    # Economic calendar
    if day_str in FOMC_DATES:
        max_pos = min(max_pos, 8)
        if decision == "GO": decision = "CAUTION"
        reasons.append("FOMC")
    if day_str in CPI_DATES or day_str in NFP_DATES:
        max_pos = min(max_pos, 10)
        if decision == "GO": decision = "CAUTION"
        reasons.append("CPI/NFP")

    return decision, max_pos, reasons


def score_ticker(df: pd.DataFrame, as_of_idx: int) -> float:
    sub = df.iloc[:as_of_idx]
    if len(sub) < 20:
        return 0
    close  = sub["Close"]
    volume = sub["Volume"]
    score  = 0

    rsi = ta.momentum.RSIIndicator(close, 14).rsi().iloc[-1]
    if pd.notna(rsi):
        if rsi < RSI_OVERSOLD:    score += 2
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
        if price > sma20 > sma50:    score += 1
        elif price < sma20 < sma50:  score -= 2

    return max(-10, min(10, score))


def simulate_day(day, scored_candidates, position_count, capital):
    """Simulate trades for one day. Returns (day_pnl, trades_list)."""
    selected = scored_candidates[:position_count]
    if not selected:
        return 0.0, []

    position_size = min(capital * MAX_POSITION_PCT, capital / max(len(selected), 1))
    day_pnl = 0.0
    trades  = []

    for ticker, score, entry_price, idx, df in selected:
        dates = df.index.date
        day_idx = next((i for i, d in enumerate(dates) if d == day), None)
        if day_idx is None:
            continue

        open_price  = float(df["Open"].iloc[day_idx])
        high_price  = float(df["High"].iloc[day_idx])
        low_price   = float(df["Low"].iloc[day_idx])
        close_price = float(df["Close"].iloc[day_idx])

        entry  = open_price
        target = entry * (1 + TARGET_PCT)
        stop   = entry * (1 - STOP_PCT)
        shares = int(position_size / entry)
        if shares == 0:
            continue

        if high_price >= target:
            exit_price   = target
            close_reason = "TARGET"
            win          = True
        elif low_price <= stop:
            exit_price   = stop
            close_reason = "STOP"
            win          = False
        else:
            exit_price   = close_price
            close_reason = "EOD"
            win          = exit_price > entry

        pnl = (exit_price - entry) * shares
        day_pnl += pnl
        trades.append({
            "ticker": ticker, "score": score, "entry": round(entry, 2),
            "exit": round(exit_price, 2), "shares": shares,
            "pnl": round(pnl, 2), "close_reason": close_reason, "win": win,
        })

    return day_pnl, trades


def run_simulation(label, trading_days, scored_per_day, top_n,
                   vix_hist, futures_hist, fg_hist, use_gates=False):
    """Run one full simulation pass. Returns summary dict."""
    capital       = float(TOTAL_CAPITAL)
    daily_results = []
    all_trades    = []

    print(f"\n{'─'*60}")
    print(f"  {label}")
    print(f"{'─'*60}")

    for day in trading_days:
        day_str    = day.strftime("%Y-%m-%d")
        candidates = scored_per_day.get(day_str, [])
        gate_info  = ""

        if use_gates:
            decision, effective_n, reasons = apply_v2_gates(
                day_str, vix_hist, futures_hist, fg_hist, top_n)
            if decision == "SKIP":
                gate_info = f"  ⛔ SKIP: {', '.join(reasons)}"
                print(f"  ⛔ {day_str}  SKIPPED — {', '.join(reasons)}")
                daily_results.append({
                    "date": day_str, "trades": 0, "pnl": 0.0,
                    "capital": round(capital, 2), "gate": "SKIP",
                    "gate_reasons": reasons,
                })
                continue
        else:
            effective_n = top_n
            decision    = "GO"
            reasons     = []

        day_pnl, trades = simulate_day(day, candidates, effective_n, capital)
        capital += day_pnl

        gate_tag = ""
        if use_gates and decision == "CAUTION":
            gate_tag = f" [CAUTION: {', '.join(reasons)} → {effective_n} pos]"

        icon = "✅" if day_pnl >= 0 else "🔴"
        print(f"  {icon} {day_str}  trades={len(trades)}  "
              f"P&L=${day_pnl:+,.2f}  capital=${capital:,.0f}{gate_tag}")

        for t in trades:
            t["date"] = day_str
        all_trades.extend(trades)
        daily_results.append({
            "date": day_str, "trades": len(trades), "pnl": round(day_pnl, 2),
            "capital": round(capital, 2),
            "gate": decision if use_gates else "GO",
            "gate_reasons": reasons,
        })

    # ── Summary stats ──────────────────────────────────────────────────────────
    total_pnl    = capital - TOTAL_CAPITAL
    trade_wins   = [t for t in all_trades if t["win"]]
    trade_losses = [t for t in all_trades if not t["win"]]
    total_trades = len(all_trades)
    win_rate     = len(trade_wins) / total_trades * 100 if total_trades else 0
    win_days     = sum(1 for r in daily_results if r["pnl"] > 0)
    skip_days    = sum(1 for r in daily_results if r.get("gate") == "SKIP")
    caution_days = sum(1 for r in daily_results if r.get("gate") == "CAUTION")
    avg_daily    = total_pnl / len(daily_results) if daily_results else 0
    total_return = total_pnl / TOTAL_CAPITAL * 100
    annualized   = total_return * (252 / len(trading_days))

    avg_win  = sum(t["pnl"] for t in trade_wins)  / len(trade_wins)  if trade_wins  else 0
    avg_loss = sum(t["pnl"] for t in trade_losses) / len(trade_losses) if trade_losses else 0
    rr       = abs(avg_win / avg_loss) if avg_loss != 0 else 0

    targets_hit = sum(1 for t in all_trades if t["close_reason"] == "TARGET")
    stops_hit   = sum(1 for t in all_trades if t["close_reason"] == "STOP")
    eod_closed  = sum(1 for t in all_trades if t["close_reason"] == "EOD")

    by_ticker = {}
    for t in all_trades:
        by_ticker.setdefault(t["ticker"], []).append(t["pnl"])
    ticker_pnl = sorted([(k, sum(v), len(v)) for k, v in by_ticker.items()],
                        key=lambda x: -x[1])

    score = 0
    score += min(avg_daily / DAILY_PROFIT_TARGET * 40, 40)
    score += (win_days / len(daily_results)) * 30 if daily_results else 0
    score += min(win_rate / 100 * 30, 30)
    if score >= 80:   grade = "A"
    elif score >= 60: grade = "B"
    elif score >= 40: grade = "C"
    else:             grade = "D"

    print(f"\n  Total P&L:    ${total_pnl:+,.2f}  |  Avg/day: ${avg_daily:+,.2f}")
    print(f"  Win days:     {win_days}/{len(daily_results)}  "
          f"({win_days/len(daily_results)*100:.0f}%)")
    if use_gates:
        print(f"  Skip days:    {skip_days}  |  Caution days: {caution_days}")
    print(f"  Win rate:     {win_rate:.1f}%  |  R:R: {rr:.2f}x")
    print(f"  Targets hit:  {targets_hit}  |  Stops: {stops_hit}  |  EOD: {eod_closed}")
    print(f"  Annualized:   {annualized:+.1f}%  |  Grade: {grade}")

    if use_gates and skip_days > 0:
        print(f"\n  Skip day breakdown:")
        for r in daily_results:
            if r.get("gate") == "SKIP":
                print(f"    {r['date']}  Reason: {', '.join(r['gate_reasons'])}")

    return {
        "label":       label,
        "total_pnl":   total_pnl,
        "avg_daily":   avg_daily,
        "win_days":    win_days,
        "skip_days":   skip_days,
        "caution_days":caution_days,
        "win_rate":    win_rate,
        "rr":          rr,
        "annualized":  annualized,
        "grade":       grade,
        "score":       score,
        "top_tickers": ticker_pnl[:5],
        "worst_tickers": ticker_pnl[-5:],
    }


def fetch_index_returns(start_date, end_date=None) -> dict:
    """Returns {name: {start, end, pct, pnl_on_100k}} for SPY, QQQ, DIA."""
    fetch_end = (end_date + timedelta(days=1)) if end_date else (datetime.today().date() + timedelta(days=1))
    result = {}
    for name, sym in [("S&P 500 (SPY)", "SPY"), ("Nasdaq 100 (QQQ)", "QQQ"), ("Dow Jones (DIA)", "DIA")]:
        try:
            df = yf.download(sym, start=start_date, end=fetch_end,
                             progress=False, auto_adjust=True)
            if isinstance(df.columns, pd.MultiIndex):
                df.columns = df.columns.get_level_values(0)
            if df.empty:
                continue
            s = float(df["Close"].iloc[0])
            e = float(df["Close"].iloc[-1])
            pct = (e - s) / s * 100
            result[name] = {"start": s, "end": e, "pct": pct, "pnl": TOTAL_CAPITAL * pct / 100}
        except Exception:
            pass
    return result


def run_backtest(days: int = 30, top_n: int = 15, start_date=None, end_date=None):
    if start_date and end_date:
        label = f"{start_date} → {end_date}"
    else:
        label = f"last {days} trading days"

    print(f"\n{'='*60}")
    print(f"  BACKTEST — {label}  |  top {top_n} per day")
    print(f"  Capital: ${TOTAL_CAPITAL:,}  |  Stop: {STOP_PCT*100:.1f}%  "
          f"Target: {TARGET_PCT*100:.1f}%")
    print(f"{'='*60}\n")

    trading_days = get_trading_days(days, start_date, end_date)
    data_start   = trading_days[0] - timedelta(days=120)  # extra lookback for indicators
    data_end     = trading_days[-1]

    # ── Fetch all price data ───────────────────────────────────────────────────
    print("  Fetching price history for universe...")
    fetch_end = data_end + timedelta(days=1)
    all_data = {}
    for ticker in UNIVERSE:
        try:
            df = yf.download(ticker, start=data_start, end=fetch_end,
                             progress=False, auto_adjust=True)
            if df is None or len(df) < 20:
                continue
            if isinstance(df.columns, pd.MultiIndex):
                df.columns = df.columns.get_level_values(0)
            all_data[ticker] = df
        except Exception:
            pass
    print(f"  Loaded {len(all_data)} tickers")

    # ── Fetch V2 intelligence data ─────────────────────────────────────────────
    print("  Fetching VIX history...")
    vix_hist = fetch_vix_history(data_start, data_end)
    print(f"  VIX data: {len(vix_hist)} days")

    print("  Fetching futures history...")
    futures_hist = fetch_futures_history(data_start, data_end)
    print(f"  Futures data: {len(futures_hist)} days")

    print("  Fetching Fear & Greed history...")
    fg_hist = fetch_fear_greed_history(limit=min(days + 120, 3000) if not start_date else 3000)
    print(f"  Fear & Greed data: {len(fg_hist)} days"
          + (" (not available for historical periods pre-2020)" if not fg_hist else ""))

    # ── Pre-score all candidates per day (shared between both simulations) ─────
    print("\n  Pre-scoring candidates per day...")
    scored_per_day = {}
    for day in trading_days:
        day_str  = day.strftime("%Y-%m-%d")
        scored   = []
        for ticker, df in all_data.items():
            dates = df.index.date
            idx   = next((i for i, d in enumerate(dates) if d >= day), None)
            if idx is None or idx < 20:
                continue
            price = float(df["Close"].iloc[idx - 1])
            if price < MIN_PRICE:
                continue
            s = score_ticker(df, idx)
            if s >= SCORE_THRESHOLD:
                scored.append((ticker, s, price, idx, df))
        scored.sort(key=lambda x: -x[1])
        scored_per_day[day_str] = scored

    # ── Run both simulations ───────────────────────────────────────────────────
    baseline = run_simulation(
        "BASELINE (no intelligence gates)", trading_days, scored_per_day,
        top_n, vix_hist, futures_hist, fg_hist, use_gates=False
    )
    v2 = run_simulation(
        "V2-GATED (VIX + Fear & Greed + Futures + Economic Calendar)",
        trading_days, scored_per_day,
        top_n, vix_hist, futures_hist, fg_hist, use_gates=True
    )

    # ── Comparison ────────────────────────────────────────────────────────────
    print(f"\n{'='*60}")
    print(f"  COMPARISON")
    print(f"{'='*60}")
    print(f"  {'Metric':<22} {'Baseline':>12} {'V2-Gated':>12} {'Delta':>10}")
    print(f"  {'─'*58}")

    def row(label, b, v, fmt="{:+,.0f}", suffix=""):
        delta = v - b
        print(f"  {label:<22} {fmt.format(b):>12}{suffix} "
              f"{fmt.format(v):>12}{suffix} {fmt.format(delta):>10}{suffix}")

    row("Total P&L ($)",      baseline["total_pnl"],  v2["total_pnl"])
    row("Avg daily P&L ($)",  baseline["avg_daily"],   v2["avg_daily"])
    row("Win days",           baseline["win_days"],    v2["win_days"],    fmt="{:,.0f}", suffix="")
    row("Win rate (%)",       baseline["win_rate"],    v2["win_rate"],    fmt="{:+.1f}", suffix="%")
    row("Reward:risk",        baseline["rr"],          v2["rr"],          fmt="{:+.2f}", suffix="x")
    row("Annualized (%)",     baseline["annualized"],  v2["annualized"],  fmt="{:+.1f}", suffix="%")

    print(f"\n  Grade:   Baseline → {baseline['grade']}   V2-Gated → {v2['grade']}")
    print(f"  Skip days avoided (V2): {v2['skip_days']}  "
          f"|  Caution days reduced: {v2['caution_days']}")

    pnl_delta = v2["total_pnl"] - baseline["total_pnl"]
    verdict = ("V2 gates IMPROVED performance" if pnl_delta > 0
               else "V2 gates REDUCED performance (gates were over-cautious)")
    print(f"\n  Verdict: {verdict}  (${abs(pnl_delta):,.0f} difference)")

    # Top tickers (V2)
    print(f"\n  Top 5 tickers (V2-gated):")
    for ticker, pnl, n in v2["top_tickers"]:
        print(f"    {ticker:6s}  ${pnl:+,.2f}  ({n} trades)")
    print(f"\n  Worst 5 tickers (V2-gated):")
    for ticker, pnl, n in v2["worst_tickers"]:
        print(f"    {ticker:6s}  ${pnl:+,.2f}  ({n} trades)")

    # ── Index comparison ──────────────────────────────────────────────────────
    print(f"\n{'='*60}")
    print(f"  VS. MARKET INDEXES (same window, $100K buy-and-hold)")
    print(f"{'='*60}")
    idx_returns = fetch_index_returns(trading_days[0], trading_days[-1])
    if idx_returns:
        print(f"  {'Index':<22} {'Return':>10} {'P&L on $100K':>16}")
        print(f"  {'─'*50}")
        for name, d in idx_returns.items():
            print(f"  {name:<22} {d['pct']:>+9.1f}%  ${d['pnl']:>+14,.0f}")
        print(f"  {'─'*50}")
        print(f"  {'Agent (V2-gated)':<22} {'':>10}  ${v2['total_pnl']:>+14,.0f}  "
              f"({v2['annualized']:+.1f}% ann.)")
        best_idx = max(idx_returns.values(), key=lambda x: x["pnl"])
        alpha = v2["total_pnl"] - best_idx["pnl"]
        print(f"\n  Alpha vs best index: ${alpha:+,.0f}")
    else:
        print("  (Index data unavailable for this period)")

    print(f"\n{'='*60}\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--days",       type=int, default=30)
    parser.add_argument("--top",        type=int, default=15,
                        help="Max positions per day (before gate reduction)")
    parser.add_argument("--start-date", type=str, default=None,
                        help="Start date YYYY-MM-DD (use with --end-date)")
    parser.add_argument("--end-date",   type=str, default=None,
                        help="End date YYYY-MM-DD (use with --start-date)")
    args = parser.parse_args()

    start = date.fromisoformat(args.start_date) if args.start_date else None
    end   = date.fromisoformat(args.end_date)   if args.end_date   else None
    run_backtest(args.days, args.top, start_date=start, end_date=end)
