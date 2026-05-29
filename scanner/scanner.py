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
    MAX_SPREAD_PCT, MAX_PREMARKET_GAP_PCT, MAX_ATR_PCT,
    STRONG_SECTOR_THRESHOLD, WEAK_SECTOR_THRESHOLD, GAP_AND_GO_VOLUME_MIN,
)

# Populated once per scan by _prefetch_batch(); each thread reads from here
# instead of making individual yfinance calls.
# Structure: {ticker: {"df": pd.DataFrame, "info": dict}}
_batch_data_cache: dict[str, dict] = {}

# Maps yfinance sector labels → SPDR sector ETF tickers
_SECTOR_ETF_MAP: dict[str, str] = {
    "Technology":             "XLK",
    "Financial Services":     "XLF",
    "Financial":              "XLF",
    "Healthcare":             "XLV",
    "Energy":                 "XLE",
    "Industrials":            "XLI",
    "Consumer Cyclical":      "XLY",
    "Consumer Defensive":     "XLP",
    "Basic Materials":        "XLB",
    "Real Estate":            "XLRE",
    "Utilities":              "XLU",
    "Communication Services": "XLC",
}


def _intraday_bars_alpaca(ticker: str) -> pd.DataFrame | None:
    """Alpaca 5-min bars fallback for ORB/VWAP — used when yfinance is rate-limited."""
    try:
        import os
        from alpaca.data.requests import StockBarsRequest
        from alpaca.data.timeframe import TimeFrame, TimeFrameUnit
        from agents.alpaca_broker import _dclient
        today_start = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
        req = StockBarsRequest(
            symbol_or_symbols=ticker,
            timeframe=TimeFrame(5, TimeFrameUnit.Minute),
            start=today_start,
        )
        resp = _dclient().get_stock_bars(req)
        bars = (resp.data.get(ticker) or []) if hasattr(resp, "data") else []
        if len(bars) < 6:
            return None
        df = pd.DataFrame([{
            "open": b.open, "high": b.high, "low": b.low,
            "close": b.close, "volume": b.volume,
        } for b in bars], index=pd.DatetimeIndex([b.timestamp for b in bars]))
        df.index = pd.to_datetime(df.index)
        return df
    except Exception:
        return None


def _intraday_bars(ticker: str) -> pd.DataFrame | None:
    """Fetch today's 5-min bars. Returns None if market is closed or fewer than 6 bars.
    Tries yfinance first; falls back to Alpaca when yfinance is rate-limited."""
    try:
        df = yf.download(ticker, period="1d", interval="5m", progress=False, auto_adjust=True)
        if df is None or df.empty:
            raise ValueError("empty")
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = [c[0].lower() for c in df.columns]
        else:
            df.columns = [c.lower() for c in df.columns]
        df.index = pd.to_datetime(df.index)
        today_bars = df[df.index.date == date.today()]
        if len(today_bars) >= 6:
            return today_bars
    except Exception:
        pass
    return _intraday_bars_alpaca(ticker)


def _intraday_signals(ticker: str) -> dict:
    """ORB and intraday VWAP signals from 5-min bars. Empty dict when market is closed."""
    bars = _intraday_bars(ticker)
    if bars is None:
        return {}
    try:
        cur_price = float(bars["close"].iloc[-1])

        orb_high  = float(bars.head(6)["high"].max())
        above_orb = cur_price > orb_high

        df = bars.copy()
        df["typical"]   = (df["high"] + df["low"] + df["close"]) / 3
        df["cum_tpvol"] = (df["typical"] * df["volume"]).cumsum()
        df["cum_vol"]   = df["volume"].cumsum()
        df["vwap"]      = df["cum_tpvol"] / df["cum_vol"]

        vwap_now       = float(df["vwap"].iloc[-1])
        above_vwap_now = cur_price > vwap_now

        mid       = max(1, len(df) // 2)
        was_below = bool((df["close"].iloc[:mid] < df["vwap"].iloc[:mid]).any())

        return {
            "above_orb":    above_orb,
            "above_vwap":   above_vwap_now,
            "vwap_reclaim": above_vwap_now and was_below,
            "vwap":         round(vwap_now, 2),
        }
    except Exception:
        return {}


def _fetch_alpaca(ticker: str) -> tuple[dict, pd.DataFrame | None]:
    """Alpaca daily bars fallback — used when yfinance is rate-limited."""
    try:
        from alpaca.data.requests import StockBarsRequest
        from alpaca.data.timeframe import TimeFrame
        from agents.alpaca_broker import _dclient
        start = datetime.utcnow() - timedelta(days=100)
        req = StockBarsRequest(symbol_or_symbols=ticker, timeframe=TimeFrame.Day, start=start)
        resp = _dclient().get_stock_bars(req)
        bars = (resp.data.get(ticker) or []) if hasattr(resp, "data") else []
        if len(bars) < 20:
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


def _get_market_context() -> dict:
    """Fetch SPY + sector ETF intraday performance once per scan."""
    ctx: dict = {}
    try:
        from alpaca.data.requests import StockSnapshotRequest
        from agents.alpaca_broker import _dclient
        sector_etfs = list(set(_SECTOR_ETF_MAP.values()))
        req   = StockSnapshotRequest(symbol_or_symbols=["SPY"] + sector_etfs)
        snaps = _dclient().get_stock_snapshot(req)
        spy   = snaps.get("SPY")
        if spy and spy.daily_bar:
            open_px = getattr(spy.daily_bar, "open", None)
            price   = getattr(spy.latest_trade, "price", None) or getattr(spy.daily_bar, "close", None)
            if open_px and price and float(open_px) > 0:
                ctx["spy_pct"] = round((float(price) - float(open_px)) / float(open_px) * 100, 2)
        sector_pct: dict[str, float] = {}
        for etf in sector_etfs:
            snap = snaps.get(etf)
            if snap and snap.daily_bar:
                o = getattr(snap.daily_bar, "open", None)
                p = getattr(snap.latest_trade, "price", None) or getattr(snap.daily_bar, "close", None)
                if o and p and float(o) > 0:
                    sector_pct[etf] = round((float(p) - float(o)) / float(o) * 100, 2)
        ctx["sector_pct"] = sector_pct
        return ctx
    except Exception:
        pass
    return {}


def _prefetch_batch(tickers: list[str]) -> None:
    """
    Replace sequential yfinance calls with 2 Alpaca batch API calls before the
    ThreadPoolExecutor runs. Results are stored in _batch_data_cache; each
    _fetch() call reads from the cache instead of hitting yfinance.

    Call 1 — StockBarsRequest daily (all tickers, ~70 bars): 3 months of OHLCV
              for RSI/MACD/Bollinger/ATR/SMA. Also computes 20-day avg volume.
    Call 2 — StockSnapshotRequest (all tickers): current bid/ask (spread filter),
              today's daily bar open (premarket gap reference), prev-day close
              (premarket gap pct).

    Fields Alpaca cannot supply (sector, beta, forwardPE, marketCap, longName)
    are left absent from the cached info dict; _scan_ticker handles missing values
    gracefully via .get() with defaults.
    """
    from alpaca.data.requests import StockBarsRequest, StockSnapshotRequest
    from alpaca.data.timeframe import TimeFrame
    from agents.alpaca_broker import _dclient
    global _batch_data_cache
    _batch_data_cache = {}

    # --- Call 1: ~70 calendar days of daily bars (covers 3 trading months) ---
    hist_start = (datetime.utcnow() - timedelta(days=100)).date().isoformat()
    bars_by_ticker: dict[str, pd.DataFrame] = {}
    avg_vols: dict[str, int] = {}
    try:
        resp = _dclient().get_stock_bars(
            StockBarsRequest(
                symbol_or_symbols=list(tickers),
                timeframe=TimeFrame.Day,
                start=hist_start,
            )
        )
        bars_dict = resp.data if hasattr(resp, "data") else (dict(resp) if resp else {})
        for ticker, bar_list in bars_dict.items():
            if len(bar_list) < 20:
                continue
            df = pd.DataFrame([{
                "open": b.open, "high": b.high, "low": b.low,
                "close": b.close, "volume": b.volume,
            } for b in bar_list], index=pd.DatetimeIndex([b.timestamp for b in bar_list]))
            df.index = pd.to_datetime(df.index)
            bars_by_ticker[ticker] = df
            avg_vols[ticker] = int(df["volume"].tail(20).mean())
        print(f"[scanner] batch daily bars: {len(bars_by_ticker)}/{len(tickers)} tickers returned")
    except Exception as e:
        print(f"[scanner] batch daily bars failed: {e}")

    # --- Call 2: snapshots for bid/ask and premarket gap ---
    snapshots: dict = {}
    try:
        clean = [t for t in tickers if "-" not in t]
        snapshots = _dclient().get_stock_snapshot(
            StockSnapshotRequest(symbol_or_symbols=clean)
        ) or {}
        print(f"[scanner] batch snapshots: {len(snapshots)}/{len(clean)} symbols returned")
    except Exception as e:
        print(f"[scanner] batch snapshots failed: {e}")

    # --- Build per-ticker cache entries ---
    for ticker in tickers:
        df = bars_by_ticker.get(ticker)
        if df is None:
            continue
        snap = snapshots.get(ticker)
        info: dict = {"averageVolume": avg_vols.get(ticker, 0)}
        if snap:
            latest_trade = getattr(snap, "latest_trade", None)
            latest_quote = getattr(snap, "latest_quote", None)
            prev_day     = getattr(snap, "prev_day_bar", None)
            if latest_quote:
                bid = float(getattr(latest_quote, "bid_price", 0) or 0)
                ask = float(getattr(latest_quote, "ask_price", 0) or 0)
                if bid > 0:
                    info["bid"] = bid
                if ask > 0:
                    info["ask"] = ask
            if prev_day:
                prev_close = float(getattr(prev_day, "close", 0) or 0)
                if prev_close > 0:
                    info["regularMarketPreviousClose"] = prev_close
            if latest_trade:
                cur_price = float(getattr(latest_trade, "price", 0) or 0)
                if cur_price > 0:
                    info["preMarketPrice"] = cur_price
        _batch_data_cache[ticker] = {"df": df, "info": info}


def _fetch(ticker: str) -> tuple[dict, pd.DataFrame | None]:
    # Read from batch cache when available (populated by _prefetch_batch before run_scan)
    cached = _batch_data_cache.get(ticker)
    if cached is not None:
        return cached["info"], cached["df"]

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


def _technical(ticker: str, df: pd.DataFrame, skip_volume_surge: bool = False, market_ctx: dict | None = None, ticker_sector: str = "") -> dict:
    close  = df["close"]
    volume = df["volume"]
    signals = []
    score   = 0

    # Neutralise overbought/extended penalties on strong broad-market OR strong-sector days.
    # strong_day: SPY up >= 1% — broad tape supports momentum
    # strong_sector_day: this stock's sector ETF up >= STRONG_SECTOR_THRESHOLD (2%) even if SPY is flat
    #   e.g. semis run 4% on an MU earnings day while SPY is +0.1% — overbought semis are continuation, not reversal
    strong_day = bool(market_ctx and market_ctx.get("spy_pct", 0) >= 1.0)
    sector_etf = _SECTOR_ETF_MAP.get(ticker_sector, "") if ticker_sector else ""
    sector_pct_today = (market_ctx or {}).get("sector_pct", {}).get(sector_etf, 0) if sector_etf else 0
    strong_sector_day = sector_pct_today >= STRONG_SECTOR_THRESHOLD
    weak_sector_day   = bool(sector_etf and sector_pct_today <= WEAK_SECTOR_THRESHOLD)
    strong_tape = strong_day or strong_sector_day

    # RSI
    rsi = ta.momentum.RSIIndicator(close, 14).rsi().iloc[-1]
    if pd.notna(rsi):
        if rsi < RSI_OVERSOLD:
            score += 2; signals.append(f"RSI oversold ({rsi:.1f})")
        elif rsi > RSI_OVERBOUGHT:
            if not strong_tape:
                score -= 2; signals.append(f"RSI overbought ({rsi:.1f})")
            else:
                ctx_label = "sector" if (strong_sector_day and not strong_day) else "broad"
                signals.append(f"RSI overbought ({rsi:.1f}) — momentum continuation on strong {ctx_label} tape")

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
            if not strong_tape:
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

    # Breakout freshness — just crossed SMA20 has better continuation odds than an extended trend
    breakout_freshness = "NORMAL"
    if dist_sma20 is not None:
        if 0 < dist_sma20 <= 0.05:
            score += 1; signals.append("Fresh SMA20 breakout — continuation setup")
            breakout_freshness = "FRESH"
        elif dist_sma20 > 0.12:
            if not strong_tape:
                score -= 1; signals.append("Extended >12% above SMA20 — mean-reversion risk")
            breakout_freshness = "EXTENDED"

    # Market/sector momentum bonus: +1 on strong broad tape or strong sector day
    if market_ctx:
        spy_pct = market_ctx.get("spy_pct", 0)
        if spy_pct >= 1.0 and score > 0:
            score += 1
            signals.append(f"Market tailwind: SPY +{spy_pct:.1f}%")
        elif strong_sector_day and not strong_day and score > 0:
            score += 1
            signals.append(f"Sector tailwind: {sector_etf} +{sector_pct_today:.1f}%")

        if weak_sector_day and score > 0:
            score -= 1
            signals.append(f"Sector headwind: {sector_etf} {sector_pct_today:+.1f}%")

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
        "breakout_freshness": breakout_freshness,
        "sma20": round(sma20, 2),
        "price": round(price, 2),
    }


def _passes_filters(info: dict, price: float) -> bool:
    avg_vol = info.get("averageVolume", 0) or 0
    return price >= MIN_PRICE and avg_vol >= MIN_AVG_VOLUME


def _scan_ticker(ticker: str, skip_volume_surge: bool = False, market_ctx: dict | None = None) -> dict | None:
    info, df = _fetch(ticker)
    if df is None:
        return None
    # Freshness check: reject if most recent data row is more than 6 calendar days old.
    # 6 days covers Thu premarket (bdate_range can snap to prior Thursday = 6 days back).
    # Anything older is genuinely stale yfinance cache — skip to avoid bad signals.
    latest_date = df.index[-1].date() if hasattr(df.index[-1], "date") else None
    if latest_date and latest_date < date.today() - timedelta(days=7):
        return None
    ticker_sector = info.get("sector") or info.get("category", "ETF")
    tech = _technical(ticker, df, skip_volume_surge=skip_volume_surge, market_ctx=market_ctx, ticker_sector=ticker_sector)
    price = tech["price"]
    if not _passes_filters(info, price):
        return None
    # Bid-ask spread filter — wide spread eats directly into the 0.67% stop
    bid = info.get("bid") or 0
    ask = info.get("ask") or 0
    if bid > 0 and ask > 0:
        spread_pct = (ask - bid) / ask
        if spread_pct > MAX_SPREAD_PCT:
            return None
    # Pre-market gap filter:
    #   > 15%  → hard block (binary event, spread risk)
    #   8-15%  → gap-and-go: allow only if above VWAP + volume confirms conviction
    #   < 8%   → pass through; let technical scoring handle extended signals
    premarket_price = info.get("preMarketPrice")
    prev_close      = info.get("regularMarketPreviousClose")
    premarket_gap_pct = None
    if premarket_price and prev_close and float(prev_close) > 0:
        premarket_gap_pct = round((float(premarket_price) - float(prev_close)) / float(prev_close), 4)
        if abs(premarket_gap_pct) > MAX_PREMARKET_GAP_PCT:
            return None
        if abs(premarket_gap_pct) > 0.08:
            # Gap-and-go window: qualify with intraday signals before allowing through
            _gap_intra = _intraday_signals(ticker)
            if not _gap_intra.get("above_vwap") or tech.get("volume_ratio", 0) < GAP_AND_GO_VOLUME_MIN:
                return None
    if abs(tech["technical_score"]) < SCORE_THRESHOLD:
        return None
    # Intraday volatility filter: skip stocks whose typical H-L swing is so wide
    # that a 0.67% stop gets hit by noise before the 1% target is reached.
    # Threshold = 5% → blocks quantum, crypto, leveraged ETFs; keeps blue chips.
    if tech["intraday_range_pct"] > MAX_INTRADAY_RANGE_PCT:
        return None
    # ATR quality gate: skip stocks where ATR sizer would produce R:R < 1
    # (stop_pct = ATR × 1.2 ≥ 4% target) — saves a guardrails rejection cycle.
    if tech["atr_pct"] > MAX_ATR_PCT:
        return None
    # ORB + intraday VWAP — only computed after score gate (avoids 5-min fetch for every ticker)
    intraday = _intraday_signals(ticker)
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
        "breakout_freshness":  tech["breakout_freshness"],
        "premarket_gap_pct":   premarket_gap_pct,
        "above_orb":    intraday.get("above_orb"),
        "above_vwap":   intraday.get("above_vwap"),
        "vwap_reclaim": intraday.get("vwap_reclaim"),
        "vwap":         intraday.get("vwap"),
        "ml_score": None,
        "scanned_at": datetime.utcnow().isoformat(),
    }


def run_scan(universe=None, skip_volume_surge: bool = False) -> list[dict]:
    tickers    = universe if universe is not None else UNIVERSE
    market_ctx = _get_market_context()

    # Batch-prefetch all tickers before launching threads. Replaces 450+ sequential
    # yfinance calls with 2 Alpaca batch API calls, eliminating rate-limit retries.
    try:
        import os
        if os.getenv("ALPACA_API_KEY"):
            _prefetch_batch(tickers)
    except Exception as e:
        print(f"[scanner] batch prefetch failed — falling back to yfinance per-ticker: {e}")

    candidates = []
    with ThreadPoolExecutor(max_workers=20) as executor:
        futs = {executor.submit(_scan_ticker, t, skip_volume_surge, market_ctx): t for t in tickers}
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
