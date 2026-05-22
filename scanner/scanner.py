"""
Day-trading scanner: surfaces high-momentum candidates from the universe.
Scores -10 to +10. Candidates with |score| >= SCORE_THRESHOLD are passed
to the strategy agent.
"""
from __future__ import annotations
from concurrent.futures import ThreadPoolExecutor, as_completed
import yfinance as yf
import pandas as pd
import ta
from datetime import datetime, date, timedelta
from config.settings import (
    UNIVERSE, RSI_OVERSOLD, RSI_OVERBOUGHT,
    MIN_VOLUME_RATIO, MIN_PRICE, MIN_AVG_VOLUME, SCORE_THRESHOLD,
    LARGE_CAP_AVG_VOLUME, LARGE_CAP_VOLUME_RATIO, MAX_INTRADAY_RANGE_PCT,
)


def _fetch_alpaca(ticker: str) -> tuple[dict, pd.DataFrame | None]:
    """Alpaca daily bars fallback — used when yfinance is rate-limited."""
    try:
        import os
        from alpaca.data.historical import StockHistoricalDataClient
        from alpaca.data.requests import StockBarsRequest
        from alpaca.data.timeframe import TimeFrame
        data_client = StockHistoricalDataClient(
            api_key=os.environ.get("ALPACA_API_KEY"),
            secret_key=os.environ.get("ALPACA_SECRET_KEY"),
        )
        start = datetime.utcnow() - timedelta(days=100)
        req = StockBarsRequest(symbol_or_symbols=ticker, timeframe=TimeFrame.Day, start=start)
        bars = data_client.get_stock_bars(req)[ticker]
        if not bars or len(bars) < 20:
            return {}, None
        df = pd.DataFrame([{
            "open": b.open, "high": b.high, "low": b.low,
            "close": b.close, "volume": b.volume,
        } for b in bars], index=pd.DatetimeIndex([b.timestamp for b in bars]))
        avg_vol = int(df["volume"].tail(20).mean())
        info = {"averageVolume": avg_vol, "longName": ticker, "sector": "Unknown"}
        return info, df
    except Exception:
        return {}, None


def _fetch(ticker: str) -> tuple[dict, pd.DataFrame | None]:
    for attempt in range(2):
        try:
            t = yf.Ticker(ticker)
            info = t.info
            df = t.history(period="3mo")
            if df.empty or len(df) < 20:
                return {}, None
            df.columns = [c.lower() for c in df.columns]
            return info, df
        except Exception:
            if attempt == 0:
                import time; time.sleep(2)
    # yfinance failed — fall back to Alpaca daily bars
    return _fetch_alpaca(ticker)


def _technical(ticker: str, df: pd.DataFrame, skip_volume_surge: bool = False) -> dict:
    close  = df["close"]
    volume = df["volume"]
    signals = []
    score   = 0

    # RSI
    rsi = ta.momentum.RSIIndicator(close, 14).rsi().iloc[-1]
    if pd.notna(rsi):
        if rsi < RSI_OVERSOLD:
            score += 2; signals.append(f"RSI oversold ({rsi:.1f})")
        elif rsi > RSI_OVERBOUGHT:
            score -= 2; signals.append(f"RSI overbought ({rsi:.1f})")

    # MACD
    macd = ta.trend.MACD(close)
    hist = macd.macd_diff().iloc[-1]
    if pd.notna(hist):
        if hist > 0:
            score += 2; signals.append("MACD bullish")
        else:
            score -= 1; signals.append("MACD bearish")

    # Bollinger position
    bb     = ta.volatility.BollingerBands(close, 20, 2)
    bb_pct = bb.bollinger_pband().iloc[-1]
    if pd.notna(bb_pct):
        if bb_pct < 0.2:
            score += 2; signals.append("Near lower Bollinger (mean-reversion setup)")
        elif bb_pct > 0.8:
            score -= 1; signals.append("Near upper Bollinger")

    # Volume surge — skipped at premarket (partial day vs full-day avg is meaningless at open)
    avg_vol   = volume.rolling(20).mean().iloc[-1]
    vol_ratio = volume.iloc[-1] / avg_vol if avg_vol > 0 else 1.0
    threshold = LARGE_CAP_VOLUME_RATIO if avg_vol >= LARGE_CAP_AVG_VOLUME else MIN_VOLUME_RATIO
    if not skip_volume_surge and vol_ratio >= threshold:
        score += 2; signals.append(f"Volume surge ({vol_ratio:.1f}x avg)")

    # Trend: price vs SMA20 / SMA50
    sma20 = close.rolling(20).mean().iloc[-1]
    sma50 = close.rolling(50).mean().iloc[-1] if len(df) >= 50 else None
    price = close.iloc[-1]
    if sma50 and pd.notna(sma50):
        if price > sma20 > sma50:
            score += 1; signals.append("Uptrend: price > SMA20 > SMA50")
        elif price < sma20 < sma50:
            score -= 2; signals.append("Downtrend: price < SMA20 < SMA50")

    # ATR (volatility proxy — higher = more intraday range)
    atr = ta.volatility.AverageTrueRange(df["high"], df["low"], close, 14).average_true_range().iloc[-1]
    atr_pct = (atr / price * 100) if price > 0 else 0

    # Intraday range: avg (High - Low) / Open over the last 14 bars.
    # Unlike ATR this excludes overnight gaps — directly measures how much the
    # stock oscillates within a single trading day vs our stop+target corridor.
    intraday_ranges = ((df["high"] - df["low"]) / df["open"] * 100).replace([float("inf"), float("-inf")], pd.NA).dropna()
    intraday_range_pct = round(float(intraday_ranges.tail(14).mean()), 2) if not intraday_ranges.empty else 0.0

    # 52-week position
    high52 = close.rolling(252).max().iloc[-1] if len(df) >= 252 else close.max()
    low52  = close.rolling(252).min().iloc[-1] if len(df) >= 252 else close.min()
    range_pct = (price - low52) / (high52 - low52) if (high52 - low52) > 0 else 0.5

    dist_sma20 = round((price - sma20) / sma20, 4) if sma20 else None
    dist_sma50 = round((price - sma50) / sma50, 4) if sma50 and pd.notna(sma50) else None
    mom1 = round(float(close.pct_change(1).iloc[-1]), 4) if len(close) >= 2 else None
    mom5 = round(float(close.pct_change(5).iloc[-1]), 4) if len(close) >= 6 else None

    return {
        "technical_score": max(-10, min(10, score)),
        "signals": signals,
        "rsi": round(rsi, 1) if pd.notna(rsi) else None,
        "macd_hist": round(hist, 4) if pd.notna(hist) else None,
        "bb_pct": round(bb_pct, 3) if pd.notna(bb_pct) else None,
        "volume_ratio": round(vol_ratio, 2),
        "atr": round(float(atr), 2) if pd.notna(atr) else None,
        "atr_pct": round(atr_pct, 2),
        "intraday_range_pct": intraday_range_pct,
        "range_52w_pct": round(range_pct, 3),
        "dist_sma20": dist_sma20,
        "dist_sma50": dist_sma50,
        "mom1": mom1,
        "mom5": mom5,
        "sma20": round(sma20, 2),
        "price": round(price, 2),
    }


def _passes_filters(info: dict, price: float) -> bool:
    avg_vol = info.get("averageVolume", 0) or 0
    return price >= MIN_PRICE and avg_vol >= MIN_AVG_VOLUME


def _scan_ticker(ticker: str, skip_volume_surge: bool = False) -> dict | None:
    info, df = _fetch(ticker)
    if df is None:
        return None
    # Freshness check: reject if most recent data row is more than 6 calendar days old.
    # 6 days covers Thu premarket (bdate_range can snap to prior Thursday = 6 days back).
    # Anything older is genuinely stale yfinance cache — skip to avoid bad signals.
    latest_date = df.index[-1].date() if hasattr(df.index[-1], "date") else None
    if latest_date and latest_date < date.today() - timedelta(days=7):
        return None
    tech = _technical(ticker, df, skip_volume_surge=skip_volume_surge)
    price = tech["price"]
    if not _passes_filters(info, price):
        return None
    if abs(tech["technical_score"]) < SCORE_THRESHOLD:
        return None
    # Intraday volatility filter: skip stocks whose typical H-L swing is so wide
    # that a 0.67% stop gets hit by noise before the 1% target is reached.
    # Threshold = 5% → blocks quantum, crypto, leveraged ETFs; keeps blue chips.
    if tech["intraday_range_pct"] > MAX_INTRADAY_RANGE_PCT:
        return None
    return {
        "ticker": ticker,
        "name": info.get("longName", ticker),
        "sector": info.get("sector") or info.get("category", "ETF"),
        "price": price,
        "market_cap_b": round((info.get("marketCap") or 0) / 1e9, 1),
        "avg_volume": info.get("averageVolume", 0),
        "beta": info.get("beta"),
        "pe_forward": info.get("forwardPE"),
        "technical_score": tech["technical_score"],
        "signals": tech["signals"],
        "rsi": tech["rsi"],
        "macd_hist": tech["macd_hist"],
        "bb_pct": tech["bb_pct"],
        "volume_ratio": tech["volume_ratio"],
        "atr": tech["atr"],
        "atr_pct": tech["atr_pct"],
        "intraday_range_pct": tech["intraday_range_pct"],
        "range_52w_pct": tech["range_52w_pct"],
        "dist_sma20": tech["dist_sma20"],
        "dist_sma50": tech["dist_sma50"],
        "mom1": tech["mom1"],
        "mom5": tech["mom5"],
        "ml_score": None,
        "scanned_at": datetime.utcnow().isoformat(),
    }


def run_scan(universe=None, skip_volume_surge: bool = False) -> list[dict]:
    tickers = universe if universe is not None else UNIVERSE
    candidates = []
    with ThreadPoolExecutor(max_workers=20) as executor:
        futs = {executor.submit(_scan_ticker, t, skip_volume_surge): t for t in tickers}
        for fut in as_completed(futs):
            result = fut.result()
            if result is not None:
                candidates.append(result)
    candidates.sort(key=lambda x: abs(x["technical_score"]), reverse=True)
    return candidates


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Run the market scanner on the current universe.")
    parser.add_argument("--limit", type=int, default=20, help="Max candidates to display (default: 20)")
    args = parser.parse_args()

    import sys, os
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
    from orchestrator import load_universe

    print(f"\nLoading universe...")
    universe = load_universe()
    print(f"Scanning {len(universe)} tickers...\n")

    results = run_scan(universe=universe)
    display = results[:args.limit]

    print(f"{'Ticker':<8} {'Score':>6} {'Price':>8} {'RSI':>6} {'Vol Ratio':>10} {'ATR%':>6}  Signals")
    print("-" * 70)
    for c in display:
        print(f"{c['ticker']:<8} {c['technical_score']:>6.1f} ${c['price']:>7.2f}"
              f" {(c['rsi'] or 0):>6.1f} {(c['volume_ratio'] or 0):>10.2f}x"
              f" {(c['atr_pct'] or 0):>5.1f}%  {c['signals']}")
    print(f"\n{len(results)} candidates found (showing top {len(display)})")
