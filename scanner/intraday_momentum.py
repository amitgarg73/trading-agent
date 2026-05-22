"""
Intraday momentum scanner: finds stocks already moving strongly today.

Logic:
- Alpaca mode: uses snapshot API (today_pct_change, above_vwap, rs_vs_spy)
- Simulation mode: uses yfinance 1-min data for today's open → current move

Candidates returned are in the same format as scanner.run_scan() output so they
can be passed directly into strategy.run() alongside or instead of prior-day
technical candidates.
"""
from __future__ import annotations
from config.settings import (
    MIN_INTRADAY_MOVE_PCT, MIN_INTRADAY_VOLUME_RATIO, STRATEGY_MIN_SCORE,
    LARGE_CAP_AVG_VOLUME, LARGE_CAP_VOLUME_RATIO, MIN_VOLUME_RATIO,
)


def _momentum_score(pct_change: float, rs_vs_spy: float | None) -> int:
    """
    Score a momentum candidate.
    4% move → score 4, 6% → 5, 8% → 6, 10% → 7, 15% → 8, 20%+ → 9
    RS vs SPY ≥ 2 adds +1 bonus (stock is outperforming the market).
    Always returns at least STRATEGY_MIN_SCORE so it survives the pre-filter.
    """
    base = max(STRATEGY_MIN_SCORE, 3 + int(pct_change / 2))
    if rs_vs_spy and rs_vs_spy >= 2.0:
        base += 1
    return min(10, base)


def scan_alpaca(universe: list[str]) -> list[dict]:
    """
    Fetch intraday signals for the full universe via Alpaca snapshot API.
    Returns candidates that are up >= MIN_INTRADAY_MOVE_PCT, above VWAP,
    and not too extended (< 30% — avoids chasing blow-off tops).
    """
    from agents import alpaca_broker

    signals = alpaca_broker.get_intraday_signals(universe)
    live    = alpaca_broker.get_live_prices(list(signals.keys()))

    # Fetch 20-day avg volumes to compute vol_ratio for each candidate
    avg_volumes = alpaca_broker.get_avg_daily_volumes(list(signals.keys()))

    candidates = []
    for ticker, sig in signals.items():
        pct        = sig.get("today_pct_change") or 0
        above_vwap = sig.get("above_vwap", False)
        rs         = sig.get("rs_vs_spy")

        if pct < MIN_INTRADAY_MOVE_PCT:
            continue
        if pct > 30:
            continue  # too extended — likely a blow-off or binary event, skip
        if not above_vwap:
            continue  # move not confirmed by VWAP structure

        avg_vol   = avg_volumes.get(ticker) or 0
        today_vol = sig.get("today_volume") or 0
        vol_ratio = round(today_vol / avg_vol, 2) if avg_vol > 0 else 0
        if vol_ratio < MIN_INTRADAY_VOLUME_RATIO:
            continue  # low volume = noise, not real momentum

        score = _momentum_score(pct, rs)
        price = live.get(ticker) or sig.get("vwap") or 0

        candidates.append({
            "ticker":            ticker,
            "technical_score":   score,
            "action":            "BUY",
            "current_price":     price,
            "entry_price":       price,
            "above_vwap":        above_vwap,
            "today_pct_change":  pct,
            "rs_vs_spy":         rs,
            "vwap":              sig.get("vwap"),
            "rsi":               50,
            "volume_ratio":      vol_ratio,
            "signal_type":       "INTRADAY_MOMENTUM",
        })

    # Sort strongest movers with best RS first
    candidates.sort(key=lambda x: (-(x.get("rs_vs_spy") or 0), -x["today_pct_change"]))
    return candidates


def scan_simulation(universe: list[str]) -> list[dict]:
    """
    Simulation fallback: use yfinance 1-min data to find today's movers.
    Scans in batches to avoid rate limits; gracefully skips failures.
    """
    import yfinance as yf
    import math

    import pandas as pd
    BATCH = 50  # yfinance bulk download batch size
    candidates = []

    for i in range(0, len(universe), BATCH):
        batch = universe[i:i + BATCH]
        try:
            data = yf.download(
                " ".join(batch),
                period="5d",   # 5 days gives prior-day avg volume for vol_ratio
                interval="5m",
                group_by="ticker",
                auto_adjust=True,
                progress=False,
                threads=True,
            )
        except Exception:
            continue

        for ticker in batch:
            try:
                if len(batch) == 1:
                    df = data
                else:
                    df = data[ticker] if ticker in data.columns.get_level_values(0) else None

                if df is None or df.empty:
                    continue

                # Separate today's bars from prior days
                tz       = df.index.tz
                today_dt = pd.Timestamp.now(tz=tz).date() if tz else pd.Timestamp.now().date()
                today_df = df[df.index.date == today_dt]
                prior_df = df[df.index.date < today_dt]

                if today_df.empty:
                    continue

                open_px    = float(today_df["Open"].iloc[0])
                current    = float(today_df["Close"].iloc[-1])
                vwap_proxy = float(today_df["Close"].mean())
                vol_today  = int(today_df["Volume"].sum())

                if open_px <= 0:
                    continue

                pct = (current - open_px) / open_px * 100
                if pct < MIN_INTRADAY_MOVE_PCT or pct > 30:
                    continue
                if current < vwap_proxy:
                    continue

                # Vol ratio vs prior days' average daily volume
                if not prior_df.empty:
                    prior_daily = prior_df.groupby(prior_df.index.date)["Volume"].sum()
                    avg_vol     = float(prior_daily.mean())
                    vol_ratio   = round(vol_today / avg_vol, 2) if avg_vol > 0 else 0
                else:
                    vol_ratio = 0

                if vol_ratio < MIN_INTRADAY_VOLUME_RATIO:
                    continue  # low volume = noise, not real momentum

                score = _momentum_score(pct, None)
                candidates.append({
                    "ticker":           ticker,
                    "technical_score":  score,
                    "action":           "BUY",
                    "current_price":    round(current, 2),
                    "entry_price":      round(current, 2),
                    "above_vwap":       True,
                    "today_pct_change": round(pct, 2),
                    "rs_vs_spy":        None,
                    "vwap":             round(vwap_proxy, 2),
                    "rsi":              50,
                    "volume_ratio":     vol_ratio,
                    "signal_type":      "INTRADAY_MOMENTUM",
                })
            except Exception:
                continue

    candidates.sort(key=lambda x: -x["today_pct_change"])
    return candidates


def scan(universe: list[str], broker: str = "simulation") -> list[dict]:
    """Entry point: returns momentum candidates for the given universe and broker."""
    try:
        if broker == "alpaca":
            return scan_alpaca(universe)
        return scan_simulation(universe)
    except Exception as e:
        print(f"        ⚠️  Momentum scan error: {e}")
        return []
