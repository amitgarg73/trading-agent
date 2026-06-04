"""
Market Context Agent — runs before the scanner.
Checks VIX, Fear & Greed Index, US futures, international markets,
and economic calendar (FOMC / CPI / NFP days).

VIX thresholds:
  < 20  — normal, full trading
  20-25 — elevated, reduce to 10 positions
  25-30 — high fear, reduce to 5 positions
  30-45 — crisis, reduce to 3 positions
  > 45  — extreme crisis, reduce to 2 positions
  (no VIX-only hard skip — futures gate handles genuine crash days)

Fear & Greed (CNN/alternative.me, 0-100):
  < 25  — extreme fear, reduce to 5 positions
  25-45 — fear, reduce to 10 positions
  45-80 — neutral to greed, no change
  > 80  — extreme greed, reduce to 10 (overextended risk)

Futures thresholds (% change from prior close):
  > +0.5%  — bullish bias, favor longs
  < -0.5%  — bearish bias, favor shorts or reduce longs
  < -1.5%  — strong sell-off pre-market, skip trading

Economic calendar:
  FOMC decision day  — reduce to min(current, 8)
  CPI / NFP day      — reduce to min(current, 10)
"""
from __future__ import annotations
from typing import Optional
from concurrent.futures import ThreadPoolExecutor
import yfinance as yf
import pandas as pd
import urllib.request
import json
from datetime import datetime, date


VIX_EXTREME     = 45.0   # reduce to 2 positions
VIX_CRISIS      = 30.0   # reduce to 3 positions
VIX_CAUTION_H   = 25.0   # reduce to 5 positions
VIX_CAUTION_L   = 20.0   # reduce to 10 positions
FUTURES_SKIP    = -1.5
FUTURES_CAUTION = -0.5
FG_EXTREME_FEAR = 15
FG_FEAR         = 45
FG_EXTREME_GREED = 80
from config.settings import QUIET_DAY_FG_THRESHOLD

# ── Economic calendar — update annually from official sources ──────────────
# FOMC decision days: federalreserve.gov/monetarypolicy/fomccalendars.htm
FOMC_DATES = {
    "2025-01-29", "2025-03-19", "2025-05-07", "2025-06-18",
    "2025-07-30", "2025-09-17", "2025-10-29", "2025-12-10",
    "2026-01-28", "2026-03-18", "2026-05-06", "2026-06-17",
    "2026-07-29", "2026-09-16", "2026-10-28", "2026-12-09",
}
# CPI release dates: bls.gov/schedule/news_release/cpi.htm
CPI_DATES = {
    "2025-01-15", "2025-02-12", "2025-03-12", "2025-04-10",
    "2025-05-13", "2025-06-11", "2025-07-15", "2025-08-12",
    "2025-09-10", "2025-10-15", "2025-11-13", "2025-12-10",
    "2026-01-14", "2026-02-11", "2026-03-11", "2026-04-10",
    "2026-05-13", "2026-06-10", "2026-07-14", "2026-08-12",
    "2026-09-11", "2026-10-14", "2026-11-12", "2026-12-10",
}
# NFP (Non-Farm Payrolls): first Friday of each month
NFP_DATES = {
    "2025-01-10", "2025-02-07", "2025-03-07", "2025-04-04",
    "2025-05-02", "2025-06-06", "2025-07-03", "2025-08-01",
    "2025-09-05", "2025-10-03", "2025-11-07", "2025-12-05",
    "2026-01-02", "2026-02-06", "2026-03-06", "2026-04-03",
    "2026-05-01", "2026-06-05", "2026-07-03", "2026-08-07",
    "2026-09-04", "2026-10-02", "2026-11-06", "2026-12-04",
}


_FUTURES_MAP = {"S&P500": "ES=F", "Nasdaq": "NQ=F", "Dow": "YM=F"}
_SECTOR_ETFS = ["XLK", "XLF", "XLE", "XLV", "XLI", "XLC", "XLY", "XLP", "XLB", "XLRE", "XLU", "SOXX", "SMH"]
_INTL_MAP    = {
    "Nikkei (Japan)":  "^N225",
    "FTSE (UK)":       "^FTSE",
    "DAX (Germany)":   "^GDAXI",
    "Hang Seng (HK)":  "^HSI",
    "Shanghai":        "000001.SS",
}


def _fetch_market_data() -> dict:
    """
    Single batched yfinance download for VIX, futures, and international markets.
    One request instead of 9 eliminates Invalid Crumb rate-limit errors.
    """
    all_syms = ["^VIX"] + list(_FUTURES_MAP.values()) + list(_INTL_MAP.values())
    try:
        raw = yf.download(all_syms, period="5d", interval="1d",
                          progress=False, group_by="ticker")
    except Exception:
        return {"vix": None, "futures": {}, "intl": {}}

    def _sym_close(sym: str) -> Optional[pd.Series]:
        """Extract Close series for one ticker from the multi-ticker DataFrame."""
        try:
            if isinstance(raw.columns, pd.MultiIndex):
                # group_by="ticker" → (ticker, column)
                col = (sym, "Close")
                if col in raw.columns:
                    return raw[col].dropna()
            elif "Close" in raw.columns:
                return raw["Close"].dropna()
        except Exception:
            pass
        return None

    # VIX
    vix = None
    try:
        s = _sym_close("^VIX")
        if s is not None and len(s) >= 1:
            vix = round(float(s.iloc[-1]), 2)
    except Exception:
        pass

    # Futures — need 2 rows for prev/curr comparison
    futures = {}
    for name, sym in _FUTURES_MAP.items():
        try:
            s = _sym_close(sym)
            if s is not None and len(s) >= 2:
                prev = float(s.iloc[-2])
                curr = float(s.iloc[-1])
                change_pct = round((curr - prev) / prev * 100, 2)
                futures[name] = {"price": round(curr, 2), "change_pct": change_pct}
            elif s is not None:
                print(f"        ⚠️  Futures {name} ({sym}): insufficient data ({len(s)} rows)")
        except Exception as e:
            print(f"        ⚠️  Futures {name} ({sym}): {e}")

    # International markets
    intl = {}
    for name, sym in _INTL_MAP.items():
        try:
            s = _sym_close(sym)
            if s is not None and len(s) >= 2:
                prev = float(s.iloc[-2])
                curr = float(s.iloc[-1])
                change_pct = round((curr - prev) / prev * 100, 2)
                intl[name] = {"change_pct": change_pct}
        except Exception:
            pass

    return {"vix": vix, "futures": futures, "intl": intl}


def _fetch_sector_rotation() -> dict:
    """Fetch today's return for each sector ETF via Alpaca. Returns {ETF: change_pct} sorted best→worst."""
    try:
        from datetime import datetime, timedelta
        from alpaca.data.requests import StockBarsRequest
        from alpaca.data.timeframe import TimeFrame
        from agents.alpaca_broker import _dclient
        end   = datetime.utcnow()
        start = end - timedelta(days=5)
        req   = StockBarsRequest(symbol_or_symbols=_SECTOR_ETFS, timeframe=TimeFrame.Day,
                                 start=start, end=end)
        bars  = _dclient().get_stock_bars(req).data
        rotation = {}
        for etf in _SECTOR_ETFS:
            etf_bars = bars.get(etf) or []
            if len(etf_bars) >= 2:
                prev = etf_bars[-2].close
                curr = etf_bars[-1].close
                rotation[etf] = round((curr - prev) / prev * 100, 2)
        return dict(sorted(rotation.items(), key=lambda x: x[1], reverse=True))
    except Exception:
        return {}


def _fetch_fear_greed() -> Optional[dict]:
    """Fetch CNN Fear & Greed Index from alternative.me (free, no API key)."""
    try:
        url = "https://api.alternative.me/fng/"
        with urllib.request.urlopen(url, timeout=5) as resp:
            data = json.loads(resp.read())
            entry = data["data"][0]
            return {
                "value":          int(entry["value"]),
                "classification": entry["value_classification"],
            }
    except Exception:
        return None


def _check_economic_calendar() -> list[str]:
    """Return list of economic events happening today (FOMC / CPI / NFP)."""
    today_str = date.today().isoformat()
    events = []
    if today_str in FOMC_DATES:
        events.append("FOMC")
    if today_str in CPI_DATES:
        events.append("CPI")
    if today_str in NFP_DATES:
        events.append("NFP")
    return events


def run() -> dict:
    """
    Returns:
        decision:          'GO' | 'CAUTION' | 'SKIP'
        max_positions:     int — override for today
        vix:               float
        fear_greed:        dict | None — {value, classification}
        economic_events:   list[str] — e.g. ['FOMC', 'CPI']
        futures:           dict
        intl_markets:      dict
        futures_bias:      str
        summary:           str — human-readable context for strategy agent
        skip_reason:       str | None
    """
    print("[ 0/4 ] Checking market conditions...")

    # Batch yfinance download + Fear&Greed + calendar + sector rotation in parallel
    with ThreadPoolExecutor(max_workers=4) as _pool:
        _f_mkt    = _pool.submit(_fetch_market_data)
        _f_fg     = _pool.submit(_fetch_fear_greed)
        _f_econ   = _pool.submit(_check_economic_calendar)
        _f_sector = _pool.submit(_fetch_sector_rotation)
        _mkt          = _f_mkt.result()
        fear_greed    = _f_fg.result()
        econ_events   = _f_econ.result()
        sector_rotation = _f_sector.result()

    vix     = _mkt["vix"]
    futures = _mkt["futures"]
    intl    = _mkt["intl"]

    # ── VIX gate ──────────────────────────────────────────────────────
    skip_reason   = None
    decision      = "GO"
    max_positions = 15

    if vix is not None:
        if vix > VIX_EXTREME:
            decision      = "CAUTION"
            max_positions = 2
        elif vix > VIX_CRISIS:
            decision      = "CAUTION"
            max_positions = 3
        elif vix > VIX_CAUTION_H:
            decision      = "CAUTION"
            max_positions = 5
        elif vix > VIX_CAUTION_L:
            decision      = "CAUTION"
            max_positions = 10

    # ── Fear & Greed gate — confirming signal only ────────────────────
    # F&G alone is a lagging sentiment indicator that reads low AFTER
    # a selloff, often during recoveries. Only reduce positions when
    # F&G confirms an already-bearish VIX or futures reading.
    if fear_greed and decision != "SKIP":
        fg          = fear_greed["value"]
        vix_bearish = vix is not None and vix > VIX_CAUTION_L   # VIX > 20
        fut_bearish = (futures and
                       sum(v["change_pct"] for v in futures.values()) / len(futures) < FUTURES_CAUTION)
        confirmed   = vix_bearish or fut_bearish

        if fg < FG_EXTREME_FEAR and confirmed:
            # Extreme Fear + at least one other bearish signal → moderate reduction
            max_positions = min(max_positions, 10)
            if decision == "GO":
                decision = "CAUTION"
        elif fg > FG_EXTREME_GREED:
            # Extreme Greed — mild caution, markets can be overextended
            max_positions = min(max_positions, 12)
        # F&G 25-80 alone → informational only, no position change

    # ── Futures gate ──────────────────────────────────────────────────
    futures_bias = "NEUTRAL"
    if futures:
        avg_futures_chg = sum(v["change_pct"] for v in futures.values()) / len(futures)
        if avg_futures_chg < FUTURES_SKIP and decision != "SKIP":
            decision    = "SKIP"
            skip_reason = (f"Futures down {avg_futures_chg:.1f}% pre-market — "
                           f"strong sell-off, skipping trading today")
        elif avg_futures_chg < FUTURES_CAUTION:
            if decision == "GO":
                decision      = "CAUTION"
                max_positions = min(max_positions, 8)
            futures_bias = "BEARISH"
        elif avg_futures_chg > 0.5:
            futures_bias = "BULLISH"

    # ── Economic calendar gate ────────────────────────────────────────
    if econ_events and decision != "SKIP":
        if decision == "GO":
            decision = "CAUTION"
        if "FOMC" in econ_events:
            max_positions = min(max_positions, 8)
        if "CPI" in econ_events or "NFP" in econ_events:
            max_positions = min(max_positions, 10)

    # ── International markets summary ─────────────────────────────────
    intl_positive = sum(1 for v in intl.values() if v["change_pct"] > 0)
    intl_negative = sum(1 for v in intl.values() if v["change_pct"] < 0)
    intl_bias = "mixed"
    if intl_positive >= 4:   intl_bias = "broadly positive"
    elif intl_negative >= 4: intl_bias = "broadly negative"

    # ── Build summary for strategy agent ──────────────────────────────
    vix_str     = f"VIX at {vix}" if vix else "VIX unavailable"
    fg_str      = (f"Fear & Greed: {fear_greed['value']} ({fear_greed['classification']})"
                   if fear_greed else "Fear & Greed unavailable")
    futures_str = ", ".join(
        f"{k} {'+' if v['change_pct'] >= 0 else ''}{v['change_pct']}%"
        for k, v in futures.items()
    ) if futures else "futures unavailable"
    intl_str    = f"International markets {intl_bias} ({intl_positive} up, {intl_negative} down)"
    econ_str    = f"Economic events today: {', '.join(econ_events)}" if econ_events else ""

    summary = (
        f"Pre-market conditions: {vix_str}. {fg_str}. "
        f"Futures: {futures_str} — {futures_bias} bias. "
        f"{intl_str}."
    )
    if econ_str:
        summary += f" ⚠️ {econ_str} — position count reduced."
    if sector_rotation:
        items = list(sector_rotation.items())
        top = ", ".join(f"{k} {'+' if v >= 0 else ''}{v:.1f}%" for k, v in items[:3])
        bot = ", ".join(f"{k} {v:.1f}%" for k, v in items[-3:])
        summary += f" Sector rotation — leading: {top}. Lagging: {bot}."
        print(f"        📊 Sector leaders: {top} | Laggards: {bot}")

    # ── Print status ──────────────────────────────────────────────────
    vix_icon = "🟢" if (vix or 0) < VIX_CAUTION_L else "🟡" if (vix or 0) < VIX_CRISIS else "🔴"
    fut_icon = "🟢" if futures_bias == "BULLISH" else "🔴" if futures_bias == "BEARISH" else "⚪"
    if fear_greed:
        fg = fear_greed["value"]
        fg_icon = "🔴" if fg < FG_EXTREME_FEAR else "🟡" if fg < FG_FEAR else "🟢" if fg < FG_EXTREME_GREED else "🟡"
        print(f"        {fg_icon} Fear & Greed: {fear_greed['value']} ({fear_greed['classification']})")
    print(f"        {vix_icon} VIX: {vix}  |  {fut_icon} Futures: {futures_str}")
    print(f"        🌍 International: {intl_str}")
    if econ_events:
        print(f"        📅 Economic events: {', '.join(econ_events)} — positions reduced")
    fg_value  = fear_greed["value"] if fear_greed else None
    quiet_day = fg_value is not None and fg_value < QUIET_DAY_FG_THRESHOLD

    print(f"        Decision: {decision} | Max positions today: {max_positions}"
          + (" | 🤫 Quiet day — relaxed R:R (2:1)" if quiet_day else ""))
    if skip_reason:
        print(f"        ⛔ {skip_reason}")

    return {
        "decision":        decision,
        "max_positions":   max_positions,
        "quiet_day":       quiet_day,
        "vix":             vix,
        "fear_greed":      fear_greed,
        "economic_events": econ_events,
        "futures":         futures,
        "intl_markets":    intl,
        "futures_bias":    futures_bias,
        "sector_rotation": sector_rotation,
        "summary":         summary,
        "skip_reason":     skip_reason,
    }


if __name__ == "__main__":
    result = run()
    print(f"\n  Summary: {result['summary']}")
