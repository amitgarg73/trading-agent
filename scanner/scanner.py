"""
Day-trading scanner: surfaces high-momentum candidates from the universe.
Scores -10 to +10. Candidates with |score| >= SCORE_THRESHOLD are passed
to the strategy agent.
"""
from __future__ import annotations
import yfinance as yf
import pandas as pd
import ta
from datetime import datetime
from config.settings import (
    UNIVERSE, RSI_OVERSOLD, RSI_OVERBOUGHT,
    MIN_VOLUME_RATIO, MIN_PRICE, MIN_AVG_VOLUME, SCORE_THRESHOLD
)


def _fetch(ticker: str) -> tuple[dict, pd.DataFrame | None]:
    try:
        t = yf.Ticker(ticker)
        info = t.info
        df = t.history(period="3mo")
        if df.empty or len(df) < 20:
            return {}, None
        df.columns = [c.lower() for c in df.columns]
        return info, df
    except Exception:
        return {}, None


def _technical(ticker: str, df: pd.DataFrame) -> dict:
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

    # Volume surge
    avg_vol = volume.rolling(20).mean().iloc[-1]
    vol_ratio = volume.iloc[-1] / avg_vol if avg_vol > 0 else 1.0
    if vol_ratio >= MIN_VOLUME_RATIO:
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

    # 52-week position
    high52 = close.rolling(252).max().iloc[-1] if len(df) >= 252 else close.max()
    low52  = close.rolling(252).min().iloc[-1] if len(df) >= 252 else close.min()
    range_pct = (price - low52) / (high52 - low52) if (high52 - low52) > 0 else 0.5

    return {
        "technical_score": max(-10, min(10, score)),
        "signals": signals,
        "rsi": round(rsi, 1) if pd.notna(rsi) else None,
        "macd_hist": round(hist, 4) if pd.notna(hist) else None,
        "bb_pct": round(bb_pct, 3) if pd.notna(bb_pct) else None,
        "volume_ratio": round(vol_ratio, 2),
        "atr_pct": round(atr_pct, 2),
        "range_52w_pct": round(range_pct, 3),
        "sma20": round(sma20, 2),
        "price": round(price, 2),
    }


def _passes_filters(info: dict, price: float) -> bool:
    avg_vol = info.get("averageVolume", 0) or 0
    return price >= MIN_PRICE and avg_vol >= MIN_AVG_VOLUME


def run_scan(universe=None) -> list[dict]:
    candidates = []
    tickers = universe if universe is not None else UNIVERSE

    for ticker in tickers:
        info, df = _fetch(ticker)
        if df is None:
            continue

        tech = _technical(ticker, df)
        price = tech["price"]

        if not _passes_filters(info, price):
            continue

        if abs(tech["technical_score"]) < SCORE_THRESHOLD:
            continue

        candidates.append({
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
            "atr_pct": tech["atr_pct"],
            "range_52w_pct": tech["range_52w_pct"],
            "scanned_at": datetime.utcnow().isoformat(),
        })

    candidates.sort(key=lambda x: abs(x["technical_score"]), reverse=True)
    return candidates
