"""
Market Context Agent — runs before the scanner.
Checks VIX (volatility gate) and US futures (direction bias).
Returns a go/no-go decision and market context for the strategy agent.

VIX thresholds:
  < 15  — low fear, full trading
  15-20 — normal, full trading
  20-25 — elevated, reduce to 10 positions
  25-30 — high fear, reduce to 5 positions
  > 30  — extreme fear, skip trading

Futures thresholds (% change from prior close):
  > +0.5%  — bullish bias, favor longs
  < -0.5%  — bearish bias, favor shorts or reduce longs
  < -1.5%  — strong sell-off pre-market, skip trading
"""
from __future__ import annotations
from typing import Optional
import yfinance as yf
import pandas as pd
from datetime import datetime


VIX_SKIP        = 30.0   # skip trading above this VIX
VIX_CAUTION_H   = 25.0   # reduce to 5 positions
VIX_CAUTION_L   = 20.0   # reduce to 10 positions
FUTURES_SKIP    = -1.5   # skip if futures down more than this %
FUTURES_CAUTION = -0.5   # reduce positions if futures down more than this %


def _fetch_vix() -> Optional[float]:
    try:
        df = yf.download("^VIX", period="2d", interval="1d", progress=False)
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
        if df.empty:
            return None
        return round(float(df["Close"].iloc[-1]), 2)
    except Exception:
        return None


def _fetch_futures() -> dict:
    tickers = {"S&P500": "ES=F", "Nasdaq": "NQ=F", "Dow": "YM=F"}
    results = {}
    for name, symbol in tickers.items():
        try:
            df = yf.download(symbol, period="2d", interval="1d", progress=False)
            if isinstance(df.columns, pd.MultiIndex):
                df.columns = df.columns.get_level_values(0)
            if len(df) >= 2:
                prev  = float(df["Close"].iloc[-2])
                curr  = float(df["Close"].iloc[-1])
                change_pct = round((curr - prev) / prev * 100, 2)
                results[name] = {"price": round(curr, 2), "change_pct": change_pct}
        except Exception:
            pass
    return results


def _fetch_intl_markets() -> dict:
    """Key international indices as proxies."""
    tickers = {
        "Nikkei (Japan)":  "^N225",
        "FTSE (UK)":       "^FTSE",
        "DAX (Germany)":   "^GDAXI",
        "Hang Seng (HK)":  "^HSI",
        "Shanghai":        "000001.SS",
    }
    results = {}
    for name, symbol in tickers.items():
        try:
            df = yf.download(symbol, period="2d", interval="1d", progress=False)
            if isinstance(df.columns, pd.MultiIndex):
                df.columns = df.columns.get_level_values(0)
            if len(df) >= 2:
                prev  = float(df["Close"].iloc[-2])
                curr  = float(df["Close"].iloc[-1])
                change_pct = round((curr - prev) / prev * 100, 2)
                results[name] = {"change_pct": change_pct}
        except Exception:
            pass
    return results


def run() -> dict:
    """
    Returns:
        decision:       'GO' | 'CAUTION' | 'SKIP'
        max_positions:  int — override for today
        vix:            float
        futures:        dict
        intl_markets:   dict
        summary:        str — human-readable context for strategy agent
        skip_reason:    str | None
    """
    print("[ 0/4 ] Checking market conditions...")

    vix      = _fetch_vix()
    futures  = _fetch_futures()
    intl     = _fetch_intl_markets()

    # ── VIX gate ──────────────────────────────────────────────────────
    skip_reason  = None
    decision     = "GO"
    max_positions = 15  # default from settings

    if vix is not None:
        if vix > VIX_SKIP:
            decision    = "SKIP"
            skip_reason = f"VIX {vix} exceeds skip threshold ({VIX_SKIP}) — extreme fear, no trading today"
        elif vix > VIX_CAUTION_H:
            decision      = "CAUTION"
            max_positions = 5
        elif vix > VIX_CAUTION_L:
            decision      = "CAUTION"
            max_positions = 10

    # ── Futures gate ──────────────────────────────────────────────────
    futures_bias = "NEUTRAL"
    if futures:
        avg_futures_chg = sum(v["change_pct"] for v in futures.values()) / len(futures)
        if avg_futures_chg < FUTURES_SKIP and decision != "SKIP":
            decision     = "SKIP"
            skip_reason  = (f"Futures down {avg_futures_chg:.1f}% pre-market — "
                           f"strong sell-off, skipping trading today")
        elif avg_futures_chg < FUTURES_CAUTION:
            if decision == "GO":
                decision      = "CAUTION"
                max_positions = min(max_positions, 8)
            futures_bias  = "BEARISH"
        elif avg_futures_chg > 0.5:
            futures_bias  = "BULLISH"

    # ── International markets summary ─────────────────────────────────
    intl_positive = sum(1 for v in intl.values() if v["change_pct"] > 0)
    intl_negative = sum(1 for v in intl.values() if v["change_pct"] < 0)
    intl_bias = "mixed"
    if intl_positive >= 4:   intl_bias = "broadly positive"
    elif intl_negative >= 4: intl_bias = "broadly negative"

    # ── Build summary for strategy agent ──────────────────────────────
    vix_str     = f"VIX at {vix}" if vix else "VIX unavailable"
    futures_str = ", ".join(
        f"{k} {'+' if v['change_pct'] >= 0 else ''}{v['change_pct']}%"
        for k, v in futures.items()
    ) if futures else "futures unavailable"
    intl_str = f"International markets {intl_bias} ({intl_positive} up, {intl_negative} down)"

    summary = (
        f"Pre-market conditions: {vix_str} ({decision}). "
        f"Futures: {futures_str} — {futures_bias} bias. "
        f"{intl_str}."
    )

    # ── Print status ──────────────────────────────────────────────────
    vix_icon = "🟢" if (vix or 0) < VIX_CAUTION_L else "🟡" if (vix or 0) < VIX_SKIP else "🔴"
    fut_icon = "🟢" if futures_bias == "BULLISH" else "🔴" if futures_bias == "BEARISH" else "⚪"
    print(f"        {vix_icon} VIX: {vix}  |  {fut_icon} Futures: {futures_str}")
    print(f"        🌍 International: {intl_str}")
    print(f"        Decision: {decision} | Max positions today: {max_positions}")
    if skip_reason:
        print(f"        ⛔ {skip_reason}")

    return {
        "decision":      decision,
        "max_positions": max_positions,
        "vix":           vix,
        "futures":       futures,
        "intl_markets":  intl,
        "futures_bias":  futures_bias,
        "summary":       summary,
        "skip_reason":   skip_reason,
    }
