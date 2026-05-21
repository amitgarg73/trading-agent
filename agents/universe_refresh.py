"""
Universe Refresh Agent — runs monthly (1st of each month, 8:30 AM ET).
Fetches the live S&P 500 constituent list from Wikipedia, combines with
non-leveraged sector ETFs, screens for liquidity + volatility, and saves
the approved list to Supabase.

Criteria:
  price       $5 – $2000
  avg volume  ≥ 500K/day (20-day)
  ATR %       ≥ 0.5% (14-day ATR / price) — light filter; scanner does deep filtering daily

Output stored in scan_results as scan_type="universe_refresh".
Orchestrator reads this at premarket; falls back to settings.py if stale.
"""
from __future__ import annotations
import time
import yfinance as yf
import pandas as pd
from datetime import date

MIN_PRICE      = 5.0
MAX_PRICE      = 2000.0
MIN_AVG_VOLUME = 500_000
MIN_ATR_PCT    = 0.5
BATCH_SIZE     = 50

_WIKI_URL = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"

# Non-leveraged ETFs to always include alongside the S&P 500 stocks
NON_LEVERAGED_ETFS = [
    # Broad market
    "SPY", "QQQ", "DIA", "VTI", "VOO", "IVV",
    # Mid / small cap
    "MDY", "IJH", "IJR", "VB", "IWO", "IWN",
    # Sector ETFs
    "XLK", "XLF", "XLE", "XLV", "XLI", "XLC",
    "XLY", "XLP", "XLB", "XLRE", "XLU",
    # Thematic
    "SOXX", "SMH", "HACK", "CIBR", "WCLD", "BUG",
    "ARKG", "ARKF",
    "GLD", "SLV", "GDX", "GDXJ", "USO", "UNG",
    "TLT", "HYG", "LQD", "TIP",
]

# Minimal fallback if Wikipedia is unreachable — core S&P 500 names
_SP500_FALLBACK = [
    "AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "META", "TSLA", "BRK-B",
    "AVGO", "LLY", "JPM", "V", "UNH", "XOM", "MA", "COST", "HD", "PG",
    "JNJ", "ABBV", "BAC", "MRK", "CRM", "ORCL", "CVX", "KO", "PEP",
    "AMD", "ADBE", "NFLX", "TMO", "CSCO", "WMT", "LIN", "ACN", "MCD",
    "ABT", "DHR", "GE", "TXN", "PM", "NEE", "INTC", "RTX", "NOW", "HON",
]


def fetch_sp500() -> list[str]:
    """Fetch current S&P 500 tickers from Wikipedia. Returns fallback list on error."""
    try:
        df = pd.read_html(_WIKI_URL)[0]
        tickers = (
            df["Symbol"]
            .str.strip()
            .str.replace(".", "-", regex=False)
            .tolist()
        )
        if len(tickers) < 400:
            raise ValueError(f"Only {len(tickers)} tickers — looks truncated")
        print(f"        S&P 500: fetched {len(tickers)} tickers from Wikipedia")
        return tickers
    except Exception as e:
        print(f"        ⚠️  Wikipedia fetch failed ({e}) — using fallback list ({len(_SP500_FALLBACK)} tickers)")
        return list(_SP500_FALLBACK)


def _screen_batch(batch: list[str]) -> tuple[list[dict], int]:
    passed = []
    failed = 0
    try:
        raw = yf.download(batch, period="30d", interval="1d",
                          progress=False, group_by="ticker", auto_adjust=True)
        if isinstance(raw.columns, pd.MultiIndex):
            for ticker in batch:
                try:
                    df = raw[ticker].dropna(how="all")
                    if df.empty or len(df) < 10:
                        failed += 1
                        continue
                    result = _score(ticker, df)
                    if result:
                        passed.append(result)
                    else:
                        failed += 1
                except Exception:
                    failed += 1
        else:
            ticker = batch[0]
            df = raw.dropna(how="all")
            result = _score(ticker, df)
            if result:
                passed.append(result)
            else:
                failed += 1
    except Exception:
        failed += len(batch)
    return passed, failed


def _score(ticker: str, df: pd.DataFrame) -> dict | None:
    try:
        df.columns = [c.lower() for c in df.columns]
        if len(df) < 10:
            return None
        price = float(df["close"].iloc[-1])
        if not (MIN_PRICE <= price <= MAX_PRICE):
            return None
        avg_vol = float(df["volume"].tail(20).mean())
        if avg_vol < MIN_AVG_VOLUME:
            return None
        high  = df["high"]
        low   = df["low"]
        close = df["close"]
        prev  = close.shift(1)
        tr = pd.concat([high - low,
                        (high - prev).abs(),
                        (low  - prev).abs()], axis=1).max(axis=1)
        atr = float(tr.rolling(14).mean().iloc[-1])
        atr_pct = round(atr / price * 100, 2)
        if atr_pct < MIN_ATR_PCT:
            return None
        return {
            "ticker":     ticker,
            "price":      round(price, 2),
            "avg_volume": int(avg_vol),
            "atr_pct":    atr_pct,
        }
    except Exception:
        return None


def run() -> list[str]:
    print("\n[ Universe Refresh ] Building S&P 500 screening pool...")

    sp500  = fetch_sp500()
    combined = list(dict.fromkeys(sp500 + NON_LEVERAGED_ETFS))
    print(f"        S&P 500: {len(sp500)} | ETFs: {len(NON_LEVERAGED_ETFS)} | Combined: {len(combined)}")

    print(f"        Screening {len(combined)} tickers "
          f"(price ${MIN_PRICE}–${MAX_PRICE}, vol ≥{MIN_AVG_VOLUME:,}, ATR ≥{MIN_ATR_PCT}%)...")

    all_passed: list[dict] = []
    total_failed = 0

    for i in range(0, len(combined), BATCH_SIZE):
        batch = combined[i:i + BATCH_SIZE]
        passed, failed = _screen_batch(batch)
        all_passed.extend(passed)
        total_failed += failed
        print(f"        {i + len(batch)}/{len(combined)} screened — "
              f"{len(all_passed)} passing so far", end="\r")
        time.sleep(0.3)

    print()

    all_passed.sort(key=lambda x: x["atr_pct"], reverse=True)
    tickers = [s["ticker"] for s in all_passed]

    print(f"        Passed: {len(tickers)} | Filtered out: {total_failed}")
    print(f"        Top movers: {', '.join(tickers[:10])}")

    from core import db
    db.insert("scan_results", {
        "date":      date.today().isoformat(),
        "scan_type": "universe_refresh",
        "results": {
            "tickers":        tickers,
            "stats":          all_passed,
            "total_screened": len(combined),
            "passed":         len(tickers),
            "failed":         total_failed,
            "sources": {
                "sp500":  len(sp500),
                "etfs":   len(NON_LEVERAGED_ETFS),
            },
        },
    })

    print(f"        Saved {len(tickers)} tickers to Supabase\n")
    return tickers


if __name__ == "__main__":
    tickers = run()
    print(f"Universe refresh complete — {len(tickers)} tickers saved to Supabase.")
    print(f"Top 20 by ATR%: {', '.join(tickers[:20])}")
