"""
News Intelligence Agent (V2b) — runs after scanner, before strategy.

Two jobs:
1. Earnings blackout — remove any candidate reporting earnings today or tomorrow.
   Earnings = binary event = unacceptable gap risk for a day-trading system.

2. News sentiment — fetch recent headlines for remaining candidates and
   return a structured summary for the strategy agent to factor in.
"""
from __future__ import annotations
from typing import Optional
import yfinance as yf
from datetime import date, timedelta


def _get_earnings_date(ticker: str) -> Optional[date]:
    """Return next earnings date for ticker, or None if unknown."""
    try:
        t = yf.Ticker(ticker)
        cal = t.calendar
        if cal is None:
            return None
        # calendar can be a dict or DataFrame depending on yfinance version
        if hasattr(cal, 'columns'):
            # DataFrame format
            if 'Earnings Date' in cal.columns:
                val = cal['Earnings Date'].iloc[0]
                return val.date() if hasattr(val, 'date') else None
        elif isinstance(cal, dict):
            val = cal.get('Earnings Date')
            if val is None:
                return None
            if isinstance(val, list):
                val = val[0]
            return val.date() if hasattr(val, 'date') else None
    except Exception:
        return None
    return None


def _get_news(ticker: str, max_headlines: int = 3) -> list[str]:
    """Return recent news headlines for a ticker."""
    try:
        t = yf.Ticker(ticker)
        news = t.news or []
        headlines = []
        for item in news[:max_headlines]:
            title = item.get('title') or item.get('headline', '')
            if title:
                headlines.append(title)
        return headlines
    except Exception:
        return []


def run(candidates: list[dict]) -> dict:
    """
    Args:
        candidates: list of scanner candidates (each has 'ticker' key)

    Returns:
        filtered_candidates: candidates with earnings-day tickers removed
        blackout_tickers:    list of removed tickers + reason
        news_context:        str summary of headlines for strategy agent
        news_by_ticker:      dict of ticker → headlines
    """
    if not candidates:
        return {
            "filtered_candidates": [],
            "blackout_tickers":    [],
            "news_context":        "",
            "news_by_ticker":      {},
        }

    print("[ 1.5/4 ] Running news intelligence agent...")

    today    = date.today()
    tomorrow = today + timedelta(days=1)

    blackout_tickers = []
    filtered         = []
    news_by_ticker   = {}

    for c in candidates:
        ticker = c["ticker"]

        # Earnings blackout — skip tickers reporting today or tomorrow
        earnings_dt = _get_earnings_date(ticker)
        if earnings_dt and earnings_dt in (today, tomorrow):
            blackout_tickers.append({"ticker": ticker, "reason": f"earnings {earnings_dt}"})
            continue

        filtered.append(c)

        # ── Fetch news headlines ───────────────────────────────────────
        headlines = _get_news(ticker)
        if headlines:
            news_by_ticker[ticker] = headlines

    # ── Build news context string for strategy prompt ─────────────────
    news_lines = []
    for ticker, headlines in news_by_ticker.items():
        for h in headlines:
            news_lines.append(f"  {ticker}: {h}")

    news_context = ""
    if news_lines:
        news_context = "Recent news headlines for top candidates:\n" + "\n".join(news_lines)

    blocked = len(blackout_tickers)
    remaining = len(filtered)
    print(f"        Earnings blackout: {blocked} ticker(s) removed | {remaining} candidates remaining")
    if news_by_ticker:
        print(f"        News fetched for {len(news_by_ticker)} tickers")

    return {
        "filtered_candidates": filtered,
        "blackout_tickers":    blackout_tickers,
        "news_context":        news_context,
        "news_by_ticker":      news_by_ticker,
    }
