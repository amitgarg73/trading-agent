"""
Universe Refresh Agent — runs weekly (Monday 8:30 AM ET).
Screens the full static UNIVERSE (settings.py) + curated high-momentum tickers
for liquidity + volatility, sorts by ATR%, saves to Supabase.

Criteria:
  price       $5 – $500
  avg volume  ≥ 500K/day (20-day)
  ATR %       ≥ 2% (14-day ATR / price)

Output stored in scan_results as scan_type="universe_refresh".
Orchestrator reads this at premarket; falls back to settings.py if stale.
"""
from __future__ import annotations
import time
import yfinance as yf
import pandas as pd
from datetime import date

MIN_PRICE      = 5.0
MAX_PRICE      = 500.0
MIN_AVG_VOLUME = 500_000
MIN_ATR_PCT    = 2.0
BATCH_SIZE     = 50

# Curated additions not reliably covered by index components
CURATED = [
    # Quantum computing
    "IONQ", "RGTI", "QUBT", "QBTS",
    # Crypto miners
    "MARA", "RIOT", "CLSK", "IREN", "WULF", "CORZ", "HUT", "BITF",
    # Space / eVTOL
    "ASTS", "RKLB", "LUNR", "OKLO", "ACHR", "JOBY",
    # AI / edge compute
    "SOUN", "BBAI", "APLD", "GFAI",
    # Biotech movers
    "MRNA", "BNTX", "CRSP", "BEAM", "EDIT", "NVAX", "VKTX", "RXRX",
    # Leveraged ETFs — high ATR by design
    "SOXL", "SOXS", "TQQQ", "SQQQ", "UVXY", "LABU", "LABD",
    # Fintech / crypto-adjacent
    "MSTR", "COIN", "HOOD",
    # CBRS post-IPO
    "CBRS",
]



def _screen_batch(batch: list[str]) -> tuple[list[dict], int]:
    passed = []
    failed = 0
    try:
        raw = yf.download(batch, period="30d", interval="1d",
                          progress=False, group_by="ticker", auto_adjust=True)
        if isinstance(raw.columns, pd.MultiIndex):
            # multi-ticker download
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
            # single ticker — raw IS the df
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
        # 14-day ATR
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
    print("\n[ Universe Refresh ] Building screening pool...")

    from config.settings import UNIVERSE as _STATIC
    combined = list(dict.fromkeys(_STATIC + CURATED))
    print(f"        Static universe: {len(_STATIC)} | Curated additions: {len(CURATED)} | Combined: {len(combined)}")

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

    print()  # newline after progress

    # Sort by ATR% desc — most volatile / day-tradeable first
    all_passed.sort(key=lambda x: x["atr_pct"], reverse=True)
    tickers = [s["ticker"] for s in all_passed]

    print(f"        Passed: {len(tickers)} | Filtered out: {total_failed}")
    print(f"        Top movers: {', '.join(tickers[:10])}")

    from core import db
    db.insert("scan_results", {
        "date":      date.today().isoformat(),
        "scan_type": "universe_refresh",
        "results": {
            "tickers":       tickers,
            "stats":         all_passed,
            "total_screened": len(combined),
            "passed":        len(tickers),
            "failed":        total_failed,
            "sources": {
                "sp500":    len(sp500),
                "nasdaq100": len(ndx100),
                "curated":  len(CURATED),
            },
        },
    })

    print(f"        Saved {len(tickers)} tickers to Supabase\n")
    return tickers


if __name__ == "__main__":
    tickers = run()
    print(f"Universe refresh complete — {len(tickers)} tickers saved to Supabase.")
    print(f"Top 20 by ATR%: {', '.join(tickers[:20])}")
