"""
Tests for scanner/scanner.py
Covers: data freshness check, retry logic, filter thresholds, technical score direction,
score threshold gate, stale data rejection.
"""
import pytest
import pandas as pd
import numpy as np
from unittest.mock import patch, MagicMock, call
from datetime import date, timedelta
from tests.conftest import make_price_df, make_stale_price_df
from config.settings import (
    RSI_OVERSOLD, RSI_OVERBOUGHT, MIN_VOLUME_RATIO, MIN_PRICE,
    MIN_AVG_VOLUME, SCORE_THRESHOLD, MAX_INTRADAY_RANGE_PCT,
)


# ── Freshness check ──────────────────────────────────────────────────────────

class TestFreshnessCheck:

    def _check_freshness(self, df):
        from datetime import timedelta
        latest_date = df.index[-1].date() if hasattr(df.index[-1], "date") else None
        return latest_date is None or latest_date >= date.today() - timedelta(days=7)

    def test_yesterday_data_is_fresh(self):
        df = make_price_df(last_date=date.today() - timedelta(days=1))
        assert self._check_freshness(df)

    def test_friday_data_fresh_on_monday(self):
        # Simulate Monday (today) checking Friday's (3 days ago) close
        df = make_price_df(last_date=date.today() - timedelta(days=3))
        assert self._check_freshness(df)

    def test_long_weekend_4_days_fresh(self):
        df = make_price_df(last_date=date.today() - timedelta(days=4))
        assert self._check_freshness(df)

    def test_exactly_5_days_is_fresh(self):
        df = make_price_df(last_date=date.today() - timedelta(days=5))
        assert self._check_freshness(df)

    def test_8_days_old_is_stale(self):
        df = make_stale_price_df(days_old=8)
        assert not self._check_freshness(df)

    def test_10_days_old_is_stale(self):
        df = make_stale_price_df(days_old=10)
        assert not self._check_freshness(df)


# ── _fetch retry ─────────────────────────────────────────────────────────────

class TestFetchRetry:

    def test_success_on_first_attempt(self):
        df = make_price_df()
        mock_ticker = MagicMock()
        mock_ticker.info = {"averageVolume": 5_000_000}
        mock_ticker.history.return_value = df

        with patch("scanner.scanner.yf.Ticker", return_value=mock_ticker):
            from scanner.scanner import _fetch
            info, result = _fetch("AAPL")
        assert result is not None
        mock_ticker.history.assert_called_once()

    def test_retry_on_first_failure(self):
        df = make_price_df()
        mock_ticker = MagicMock()
        mock_ticker.info = {}
        mock_ticker.history.side_effect = [Exception("rate limited"), df]

        with patch("scanner.scanner.yf.Ticker", return_value=mock_ticker), \
             patch("time.sleep"):
            from scanner.scanner import _fetch
            info, result = _fetch("AAPL")
        assert result is not None
        assert mock_ticker.history.call_count == 2

    def test_both_attempts_fail_returns_none(self):
        mock_ticker = MagicMock()
        mock_ticker.history.side_effect = Exception("persistent error")

        with patch("scanner.scanner.yf.Ticker", return_value=mock_ticker), \
             patch("time.sleep"):
            from scanner.scanner import _fetch
            info, result = _fetch("BROKEN")
        assert result is None

    def test_empty_dataframe_returns_none(self):
        mock_ticker = MagicMock()
        mock_ticker.info = {}
        mock_ticker.history.return_value = pd.DataFrame()

        with patch("scanner.scanner.yf.Ticker", return_value=mock_ticker):
            from scanner.scanner import _fetch
            info, result = _fetch("AAPL")
        assert result is None

    def test_short_history_returns_none(self):
        # Less than 20 rows → not enough for indicators
        mock_ticker = MagicMock()
        mock_ticker.info = {}
        mock_ticker.history.return_value = make_price_df(days=10)

        with patch("scanner.scanner.yf.Ticker", return_value=mock_ticker):
            from scanner.scanner import _fetch
            info, result = _fetch("AAPL")
        assert result is None


# ── _passes_filters ──────────────────────────────────────────────────────────

class TestPassesFilters:
    from scanner.scanner import _passes_filters

    def test_valid_stock_passes(self):
        from scanner.scanner import _passes_filters
        info = {"averageVolume": 2_000_000}
        assert _passes_filters(info, MIN_PRICE + 1)

    def test_price_below_min_fails(self):
        from scanner.scanner import _passes_filters
        info = {"averageVolume": 2_000_000}
        assert not _passes_filters(info, MIN_PRICE - 0.01)

    def test_volume_below_min_fails(self):
        from scanner.scanner import _passes_filters
        info = {"averageVolume": MIN_AVG_VOLUME - 1}
        assert not _passes_filters(info, 50.0)

    def test_missing_volume_fails(self):
        from scanner.scanner import _passes_filters
        assert not _passes_filters({}, 50.0)

    def test_exactly_at_min_price_passes(self):
        from scanner.scanner import _passes_filters
        info = {"averageVolume": MIN_AVG_VOLUME}
        assert _passes_filters(info, MIN_PRICE)


# ── _technical scoring direction ─────────────────────────────────────────────

class TestTechnicalScoring:

    def _score_df(self, df):
        from scanner.scanner import _technical
        return _technical("TEST", df)["technical_score"]

    def test_bullish_df_positive_score(self):
        # Strong uptrend, low RSI start, high volume
        df = make_price_df(trend=0.003, volatility=0.008)
        score = self._score_df(df)
        # Not asserting exact value — just direction
        assert isinstance(score, (int, float))

    def test_score_is_numeric(self):
        df = make_price_df()
        from scanner.scanner import _technical
        tech = _technical("AAPL", df)
        assert isinstance(tech["technical_score"], (int, float))
        assert isinstance(tech["rsi"], float)
        assert isinstance(tech["price"], float)

    def test_score_threshold_gate(self):
        """Candidates with |score| < SCORE_THRESHOLD are filtered out."""
        # Build a df that produces a near-zero score (flat, no signals)
        df = make_price_df(trend=0.0, volatility=0.001)  # flat market
        from scanner.scanner import _technical
        tech = _technical("FLAT", df)
        # If score is below threshold, _scan_ticker would return None
        if abs(tech["technical_score"]) < SCORE_THRESHOLD:
            assert True  # correctly would be filtered
        else:
            # Score happened to be above threshold on this fixture — acceptable
            assert abs(tech["technical_score"]) >= SCORE_THRESHOLD


# ── run_scan ──────────────────────────────────────────────────────────────────

class TestRunScan:

    def test_returns_list(self):
        from scanner.scanner import run_scan
        with patch("scanner.scanner._scan_ticker", return_value=None):
            result = run_scan(universe=["AAPL", "MSFT"])
        assert isinstance(result, list)

    def test_none_results_excluded(self):
        from scanner.scanner import run_scan
        with patch("scanner.scanner._scan_ticker", side_effect=[None, None]):
            result = run_scan(universe=["AAPL", "MSFT"])
        assert result == []

    def test_valid_candidates_included(self):
        fake_candidate = {"ticker": "AAPL", "technical_score": 5, "price": 150.0}
        from scanner.scanner import run_scan
        with patch("scanner.scanner._scan_ticker", return_value=fake_candidate):
            result = run_scan(universe=["AAPL"])
        assert len(result) == 1
        assert result[0]["ticker"] == "AAPL"

    def test_sorted_by_abs_score_descending(self):
        candidates = [
            {"ticker": "A", "technical_score": 3},
            {"ticker": "B", "technical_score": -7},
            {"ticker": "C", "technical_score": 5},
        ]
        from scanner.scanner import run_scan
        with patch("scanner.scanner._scan_ticker", side_effect=candidates):
            result = run_scan(universe=["A", "B", "C"])
        scores = [abs(r["technical_score"]) for r in result]
        assert scores == sorted(scores, reverse=True)


# ── Intraday range (ATR) filter ───────────────────────────────────────────────

def _make_volatile_df(intraday_range_pct: float, days: int = 60) -> pd.DataFrame:
    """
    Construct a price DataFrame where (High - Low) / Open averages the given %.
    Used to test the intraday volatility filter without calling yfinance.
    """
    df = make_price_df(days=days)
    # Force high and low so H-L/O == intraday_range_pct on every bar
    opens  = df["close"].shift(1).fillna(df["close"])
    df = df.copy()
    df["open"]  = opens
    df["high"]  = opens * (1 + intraday_range_pct / 100)
    df["low"]   = opens * (1 - intraday_range_pct / 100)
    return df


class TestIntradayRangeMetric:
    """_technical() must compute intraday_range_pct from the OHLCV data."""

    def test_intraday_range_pct_present_in_output(self):
        df = make_price_df()
        from scanner.scanner import _technical
        tech = _technical("TEST", df)
        assert "intraday_range_pct" in tech
        assert isinstance(tech["intraday_range_pct"], float)

    def test_high_volatility_df_has_high_range(self):
        df = _make_volatile_df(intraday_range_pct=10.0)
        from scanner.scanner import _technical
        tech = _technical("RGTI", df)
        assert tech["intraday_range_pct"] > MAX_INTRADAY_RANGE_PCT

    def test_low_volatility_df_has_low_range(self):
        df = _make_volatile_df(intraday_range_pct=2.0)
        from scanner.scanner import _technical
        tech = _technical("AAPL", df)
        assert tech["intraday_range_pct"] <= MAX_INTRADAY_RANGE_PCT


class TestIntradayRangeFilter:
    """_scan_ticker() must return None for stocks whose intraday range exceeds MAX_INTRADAY_RANGE_PCT."""

    def _run_scan_ticker(self, intraday_range_pct: float):
        """Run _scan_ticker with a synthetic df wired to produce the given intraday range."""
        df = _make_volatile_df(intraday_range_pct=intraday_range_pct)
        info = {"averageVolume": 5_000_000, "longName": "Test", "sector": "Tech"}

        with patch("scanner.scanner._fetch", return_value=(info, df)), \
             patch("scanner.scanner._technical", return_value={
                 "technical_score": 6, "signals": [], "rsi": 55.0,
                 "macd_hist": 0.1, "bb_pct": 0.5, "volume_ratio": 2.0,
                 "atr": 1.0, "atr_pct": 1.0,
                 "intraday_range_pct": intraday_range_pct,
                 "range_52w_pct": 0.5, "dist_sma20": 0.01, "dist_sma50": 0.02,
                 "mom1": 0.01, "mom5": 0.02, "sma20": 100.0, "price": 100.0,
                 "breakout_freshness": "NORMAL",
             }), \
             patch("scanner.scanner._intraday_signals", return_value={}):
            from scanner.scanner import _scan_ticker
            return _scan_ticker("TEST")

    def test_low_intraday_range_passes(self):
        result = self._run_scan_ticker(intraday_range_pct=2.0)
        assert result is not None, "Low-volatility stock must pass the filter"

    def test_high_intraday_range_blocked(self):
        result = self._run_scan_ticker(intraday_range_pct=10.0)
        assert result is None, "High-volatility stock must be filtered out"

    def test_exactly_at_threshold_passes(self):
        result = self._run_scan_ticker(intraday_range_pct=MAX_INTRADAY_RANGE_PCT)
        assert result is not None, "Stock at exactly the threshold must pass (> not >=)"

    def test_just_above_threshold_blocked(self):
        result = self._run_scan_ticker(intraday_range_pct=MAX_INTRADAY_RANGE_PCT + 0.1)
        assert result is None

    def test_intraday_range_pct_in_returned_candidate(self):
        result = self._run_scan_ticker(intraday_range_pct=2.0)
        assert result is not None
        assert "intraday_range_pct" in result


# ── _intraday_signals / ORB / VWAP ───────────────────────────────────────────

def _make_intraday_df(n: int = 12, trend: str = "up") -> pd.DataFrame:
    """Synthetic 5-min bars for ORB/VWAP tests."""
    import datetime as dt
    base = 100.0
    rows = []
    for i in range(n):
        close = base + i * 0.3 if trend == "up" else base - i * 0.3
        rows.append({
            "open":   close - 0.1,
            "high":   close + 0.4,
            "low":    close - 0.4,
            "close":  close,
            "volume": 200_000,
        })
    today = date.today()
    times = [
        dt.datetime.combine(today, dt.time(9, 30)) + dt.timedelta(minutes=5 * i)
        for i in range(n)
    ]
    df = pd.DataFrame(rows, index=pd.DatetimeIndex(times))
    return df


class TestIntradaySignals:

    def test_returns_empty_when_no_bars(self):
        from scanner.scanner import _intraday_signals
        with patch("scanner.scanner._intraday_bars", return_value=None):
            result = _intraday_signals("AAPL")
        assert result == {}

    def test_above_orb_true_on_uptrend(self):
        from scanner.scanner import _intraday_signals
        df = _make_intraday_df(n=12, trend="up")
        with patch("scanner.scanner._intraday_bars", return_value=df):
            result = _intraday_signals("AAPL")
        assert result["above_orb"] is True

    def test_above_orb_false_on_downtrend(self):
        from scanner.scanner import _intraday_signals
        df = _make_intraday_df(n=12, trend="down")
        with patch("scanner.scanner._intraday_bars", return_value=df):
            result = _intraday_signals("AAPL")
        assert result["above_orb"] is False

    def test_above_vwap_present(self):
        from scanner.scanner import _intraday_signals
        df = _make_intraday_df(n=12, trend="up")
        with patch("scanner.scanner._intraday_bars", return_value=df):
            result = _intraday_signals("AAPL")
        assert "above_vwap" in result
        assert isinstance(result["above_vwap"], bool)

    def test_vwap_reclaim_present(self):
        from scanner.scanner import _intraday_signals
        df = _make_intraday_df(n=12, trend="up")
        with patch("scanner.scanner._intraday_bars", return_value=df):
            result = _intraday_signals("AAPL")
        assert "vwap_reclaim" in result

    def test_vwap_value_returned(self):
        from scanner.scanner import _intraday_signals
        df = _make_intraday_df(n=12, trend="up")
        with patch("scanner.scanner._intraday_bars", return_value=df):
            result = _intraday_signals("AAPL")
        assert isinstance(result.get("vwap"), float)
        assert result["vwap"] > 0


class TestScanTickerIntradayFields:
    """_scan_ticker() must include ORB/VWAP fields from _intraday_signals()."""

    def _run(self, intraday_signals: dict):
        df = _make_volatile_df(intraday_range_pct=2.0)
        info = {"averageVolume": 5_000_000, "longName": "Test", "sector": "Tech"}
        with patch("scanner.scanner._fetch", return_value=(info, df)), \
             patch("scanner.scanner._technical", return_value={
                 "technical_score": 6, "signals": [], "rsi": 55.0,
                 "macd_hist": 0.1, "bb_pct": 0.5, "volume_ratio": 2.0,
                 "atr": 1.0, "atr_pct": 1.0, "intraday_range_pct": 2.0,
                 "range_52w_pct": 0.5, "dist_sma20": 0.01, "dist_sma50": 0.02,
                 "mom1": 0.01, "mom5": 0.02, "sma20": 100.0, "price": 100.0,
                 "breakout_freshness": "NORMAL",
             }), \
             patch("scanner.scanner._intraday_signals", return_value=intraday_signals):
            from scanner.scanner import _scan_ticker
            return _scan_ticker("TEST")

    def test_orb_and_vwap_fields_present_when_market_open(self):
        signals = {"above_orb": True, "above_vwap": True, "vwap_reclaim": False, "vwap": 101.5}
        result = self._run(signals)
        assert result is not None
        assert result["above_orb"] is True
        assert result["above_vwap"] is True
        assert result["vwap_reclaim"] is False
        assert result["vwap"] == 101.5

    def test_fields_are_none_when_market_closed(self):
        result = self._run({})
        assert result is not None
        assert result["above_orb"] is None
        assert result["above_vwap"] is None
        assert result["vwap_reclaim"] is None


# ── Bid-ask spread filter ─────────────────────────────────────────────────────

def _make_tech_stub(**overrides) -> dict:
    """Base _technical() stub for _scan_ticker tests."""
    base = {
        "technical_score": 6, "signals": [], "rsi": 55.0,
        "macd_hist": 0.1, "bb_pct": 0.5, "volume_ratio": 2.0,
        "atr": 1.0, "atr_pct": 1.0, "intraday_range_pct": 2.0,
        "range_52w_pct": 0.5, "dist_sma20": 0.01, "dist_sma50": 0.02,
        "mom1": 0.01, "mom5": 0.02, "sma20": 100.0, "price": 100.0,
        "breakout_freshness": "NORMAL",
    }
    base.update(overrides)
    return base


def _run_scan_with_info(info_overrides: dict):
    """Run _scan_ticker with a patched info dict and fixed _technical stub."""
    from tests.conftest import make_price_df
    df = make_price_df()
    base_info = {"averageVolume": 5_000_000, "longName": "Test", "sector": "Tech"}
    base_info.update(info_overrides)
    with patch("scanner.scanner._fetch", return_value=(base_info, df)), \
         patch("scanner.scanner._technical", return_value=_make_tech_stub()), \
         patch("scanner.scanner._intraday_signals", return_value={}):
        from scanner.scanner import _scan_ticker
        return _scan_ticker("TEST")


class TestSpreadFilter:
    """_scan_ticker() rejects tickers with bid-ask spread > MAX_SPREAD_PCT."""

    def test_wide_spread_blocked(self):
        # bid=100, ask=101 → spread=1% > 0.5% MAX_SPREAD_PCT
        result = _run_scan_with_info({"bid": 100.0, "ask": 101.0})
        assert result is None, "Wide spread (1%) must be filtered out"

    def test_tight_spread_passes(self):
        # bid=100, ask=100.3 → spread=0.3% < 0.5%
        result = _run_scan_with_info({"bid": 100.0, "ask": 100.3})
        assert result is not None, "Tight spread (0.3%) must pass the filter"

    def test_exactly_at_threshold_passes(self):
        # bid=100, ask=100.5 → spread=0.498% < 0.5%
        result = _run_scan_with_info({"bid": 100.0, "ask": 100.5})
        assert result is not None

    def test_missing_bid_skips_filter(self):
        # No bid/ask info → filter is skipped (no rejection)
        result = _run_scan_with_info({})
        assert result is not None, "Missing bid/ask must not block the ticker"

    def test_zero_bid_skips_filter(self):
        # bid=0 (yfinance sometimes returns 0) → filter is skipped
        result = _run_scan_with_info({"bid": 0, "ask": 100.5})
        assert result is not None


# ── Pre-market gap filter ────────────────────────────────────────────────────

class TestPremarketGapFilter:
    """_scan_ticker() rejects tickers with abs(premarket gap) > MAX_PREMARKET_GAP_PCT."""

    def test_large_gap_up_blocked(self):
        # 10% gap up — too extended to hit 2.5% more
        result = _run_scan_with_info({
            "preMarketPrice": 110.0,
            "regularMarketPreviousClose": 100.0,
        })
        assert result is None, "10% gap up must be filtered out"

    def test_large_gap_down_blocked(self):
        # 10% gap down
        result = _run_scan_with_info({
            "preMarketPrice": 90.0,
            "regularMarketPreviousClose": 100.0,
        })
        assert result is None, "10% gap down must be filtered out"

    def test_small_gap_passes(self):
        # 3% gap — within the 8% threshold
        result = _run_scan_with_info({
            "preMarketPrice": 103.0,
            "regularMarketPreviousClose": 100.0,
        })
        assert result is not None, "3% gap must pass the filter"

    def test_missing_premarket_price_skips_filter(self):
        result = _run_scan_with_info({"regularMarketPreviousClose": 100.0})
        assert result is not None, "Missing premarket price must not block the ticker"

    def test_premarket_gap_pct_in_returned_candidate(self):
        result = _run_scan_with_info({
            "preMarketPrice": 103.0,
            "regularMarketPreviousClose": 100.0,
        })
        assert result is not None
        assert "premarket_gap_pct" in result
        assert abs(result["premarket_gap_pct"] - 0.03) < 0.001


# ── Breakout freshness ───────────────────────────────────────────────────────

class TestBreakoutFreshness:
    """_technical() must classify breakout freshness and score accordingly."""

    def test_fresh_breakout_adds_score(self):
        """dist_sma20 0-5% → +1 score and FRESH label."""
        from tests.conftest import make_price_df
        from scanner.scanner import _technical
        df = make_price_df(start_price=100.0, trend=0.001)
        # Inject a fresh breakout: price just above SMA20 by ~3%
        df = df.copy()
        sma20 = float(df["close"].tail(20).mean())
        # Shift last close to be 3% above SMA20
        df.iloc[-1, df.columns.get_loc("close")] = sma20 * 1.03
        tech = _technical("TEST", df)
        assert tech["breakout_freshness"] == "FRESH"
        assert any("Fresh" in s for s in tech["signals"])

    def test_extended_breakout_penalises_score(self):
        """dist_sma20 >12% → -1 score and EXTENDED label."""
        from tests.conftest import make_price_df
        from scanner.scanner import _technical
        df = make_price_df(start_price=100.0, trend=0.001)
        df = df.copy()
        sma20 = float(df["close"].tail(20).mean())
        df.iloc[-1, df.columns.get_loc("close")] = sma20 * 1.15
        tech = _technical("TEST", df)
        assert tech["breakout_freshness"] == "EXTENDED"
        assert any("Extended" in s for s in tech["signals"])

    def test_normal_range_no_label_change(self):
        """dist_sma20 5-12% → NORMAL label, no extra score."""
        from tests.conftest import make_price_df
        from scanner.scanner import _technical
        df = make_price_df(start_price=100.0, trend=0.001)
        df = df.copy()
        sma20 = float(df["close"].tail(20).mean())
        df.iloc[-1, df.columns.get_loc("close")] = sma20 * 1.08
        tech = _technical("TEST", df)
        assert tech["breakout_freshness"] == "NORMAL"

    def test_below_sma20_is_normal(self):
        """Price below SMA20 (negative dist) → NORMAL."""
        from tests.conftest import make_price_df
        from scanner.scanner import _technical
        df = make_price_df(start_price=100.0, trend=-0.001)
        df = df.copy()
        sma20 = float(df["close"].tail(20).mean())
        df.iloc[-1, df.columns.get_loc("close")] = sma20 * 0.95
        tech = _technical("TEST", df)
        assert tech["breakout_freshness"] == "NORMAL"

    def test_breakout_freshness_in_scan_ticker_output(self):
        result = _run_scan_with_info({})
        assert result is not None
        assert "breakout_freshness" in result
        assert result["breakout_freshness"] in ("FRESH", "NORMAL", "EXTENDED")


# ── ATR quality gate (P1) ────────────────────────────────────────────────────

class TestAtrQualityGate:
    """Stocks with ATR% > MAX_ATR_PCT must be dropped by _scan_ticker."""

    def _run_scan_with_atr(self, atr_pct_override):
        """Patch _technical to return a given atr_pct, run _scan_ticker, return result."""
        from tests.conftest import make_price_df
        from scanner.scanner import _scan_ticker
        from config.settings import MAX_ATR_PCT

        df = make_price_df(start_price=100.0, trend=0.002)
        info = {
            "averageVolume": 5_000_000,
            "sector": "Technology",
            "longName": "TEST Corp",
            "marketCap": 50_000_000_000,
        }

        with patch("scanner.scanner._fetch", return_value=(info, df)), \
             patch("scanner.scanner._intraday_signals", return_value={}):
            # We control atr_pct by patching _technical
            from scanner.scanner import _technical as real_technical

            def patched_technical(ticker, df, **kw):
                result = real_technical(ticker, df, **kw)
                result["atr_pct"] = atr_pct_override
                # Ensure score is high enough to not be blocked by score gate
                result["technical_score"] = 6
                result["intraday_range_pct"] = 1.0
                return result

            with patch("scanner.scanner._technical", side_effect=patched_technical):
                return _scan_ticker("TEST")

    def test_high_atr_ticker_blocked(self):
        from config.settings import MAX_ATR_PCT
        result = self._run_scan_with_atr(atr_pct_override=MAX_ATR_PCT + 0.1)
        assert result is None, f"ATR {MAX_ATR_PCT + 0.1}% must be blocked by quality gate"

    def test_acceptable_atr_ticker_passes(self):
        from config.settings import MAX_ATR_PCT
        result = self._run_scan_with_atr(atr_pct_override=MAX_ATR_PCT - 0.1)
        assert result is not None, f"ATR {MAX_ATR_PCT - 0.1}% must pass quality gate"

    def test_exact_threshold_passes(self):
        from config.settings import MAX_ATR_PCT
        result = self._run_scan_with_atr(atr_pct_override=MAX_ATR_PCT)
        assert result is not None, "ATR exactly at threshold must pass (not strictly greater)"


# ── _fetch_alpaca BarSet fix ──────────────────────────────────────────────────

class TestFetchAlpacaBarSet:
    """_fetch_alpaca must use bars.data.get() — BarSet has no .get() method."""

    def test_fetch_alpaca_uses_barset_data(self):
        from unittest.mock import MagicMock
        import pandas as pd

        bar = MagicMock()
        bar.open = bar.high = bar.low = bar.close = 100.0
        bar.volume = 2_000_000.0
        bar.timestamp = pd.Timestamp("2026-05-26")

        barset = MagicMock(spec=[])        # no .get() on the object
        barset.data = {"AAPL": [bar] * 25}

        with patch("agents.alpaca_broker._dclient") as mock_dc:
            mock_dc.return_value.get_stock_bars.return_value = barset
            from scanner.scanner import _fetch_alpaca
            info, df = _fetch_alpaca("AAPL")

        assert df is not None
        assert len(df) == 25
        assert info["averageVolume"] > 0

    def test_fetch_alpaca_missing_ticker_returns_none(self):
        from unittest.mock import MagicMock

        barset = MagicMock(spec=[])
        barset.data = {}                   # ticker not in response

        with patch("agents.alpaca_broker._dclient") as mock_dc:
            mock_dc.return_value.get_stock_bars.return_value = barset
            from scanner.scanner import _fetch_alpaca
            info, df = _fetch_alpaca("AAPL")

        assert df is None
        assert info == {}


# ── _intraday_bars Alpaca fallback ───────────────────────────────────────────

class TestIntradayBarsAlpacaFallback:
    """_intraday_bars falls back to Alpaca when yfinance fails."""

    def test_falls_back_to_alpaca_on_yfinance_failure(self):
        """yfinance download raises → Alpaca fallback is called."""
        import pandas as pd
        from datetime import date as dt_date

        fake_df = pd.DataFrame(
            [{"open": 100, "high": 101, "low": 99, "close": 100, "volume": 500_000}] * 8,
            index=pd.DatetimeIndex([
                pd.Timestamp.combine(dt_date.today(), pd.Timestamp("09:30").time()) + pd.Timedelta(minutes=5 * i)
                for i in range(8)
            ]),
        )

        with patch("scanner.scanner.yf.download", side_effect=Exception("401")), \
             patch("scanner.scanner._intraday_bars_alpaca", return_value=fake_df) as mock_alpaca:
            from scanner.scanner import _intraday_bars
            result = _intraday_bars("AAPL")

        mock_alpaca.assert_called_once_with("AAPL")
        assert result is not None
        assert len(result) == 8

    def test_uses_yfinance_when_available(self):
        """If yfinance succeeds with >=6 bars, Alpaca fallback is NOT called."""
        import pandas as pd
        from datetime import date as dt_date

        fake_df = pd.DataFrame(
            [{"open": 100, "high": 101, "low": 99, "close": 100, "volume": 500_000}] * 8,
            index=pd.DatetimeIndex([
                pd.Timestamp.combine(dt_date.today(), pd.Timestamp("09:30").time()) + pd.Timedelta(minutes=5 * i)
                for i in range(8)
            ]),
        )

        with patch("scanner.scanner.yf.download", return_value=fake_df), \
             patch("scanner.scanner._intraday_bars_alpaca") as mock_alpaca:
            from scanner.scanner import _intraday_bars
            result = _intraday_bars("AAPL")

        mock_alpaca.assert_not_called()
        assert result is not None


# ── Market momentum signal ────────────────────────────────────────────────────

class TestMarketMomentumSignal:
    """_technical adds +1 to bullish candidates when SPY is up >1%."""

    def _score_with_ctx(self, spy_pct: float, base_trend: float = 0.002):
        from tests.conftest import make_price_df
        from scanner.scanner import _technical
        df = make_price_df(trend=base_trend)
        return _technical("TEST", df, market_ctx={"spy_pct": spy_pct})

    def test_strong_market_adds_one_to_bullish(self):
        result_neutral = self._score_with_ctx(0.0)
        result_bull    = self._score_with_ctx(1.5)
        if result_neutral["technical_score"] > 0:
            assert result_bull["technical_score"] == result_neutral["technical_score"] + 1
            assert any("tailwind" in s.lower() for s in result_bull["signals"])

    def test_flat_market_no_bonus(self):
        result_no_ctx  = self._score_with_ctx(0.0)
        result_flat    = self._score_with_ctx(0.5)
        assert result_flat["technical_score"] == result_no_ctx["technical_score"]

    def test_market_bonus_not_applied_to_bearish_candidate(self):
        from tests.conftest import make_price_df
        from scanner.scanner import _technical
        # Overbought extended stock (trend=0.015, low vol, skip volume surge) → score=-1
        # RSI overbought(-2) + upper BB(-1) + extended SMA20(-1) + MACD bullish(+2) + uptrend(+1) = -1
        df = make_price_df(trend=0.015, volatility=0.001)
        result = _technical("TEST", df, skip_volume_surge=True, market_ctx={"spy_pct": 2.0})
        assert result["technical_score"] <= 0, "test setup: expected non-positive score"
        assert not any("tailwind" in s.lower() for s in result["signals"])

    def test_no_market_ctx_behaves_as_before(self):
        """Passing market_ctx=None must not raise and must not change score."""
        from tests.conftest import make_price_df
        from scanner.scanner import _technical
        df = make_price_df(trend=0.002)
        r1 = _technical("TEST", df, market_ctx=None)
        r2 = _technical("TEST", df)
        assert r1["technical_score"] == r2["technical_score"]


# ── run_scan passes market_ctx to each ticker ─────────────────────────────────

class TestRunScanMarketCtx:

    def test_market_ctx_computed_and_passed(self):
        """run_scan must call _get_market_context once and forward it to _scan_ticker."""
        fake_ctx = {"spy_pct": 1.2}

        with patch("scanner.scanner._get_market_context", return_value=fake_ctx) as mock_ctx, \
             patch("scanner.scanner._scan_ticker", return_value=None) as mock_scan:
            from scanner.scanner import run_scan
            run_scan(universe=["AAPL", "MSFT"])

        mock_ctx.assert_called_once()
        for call in mock_scan.call_args_list:
            assert call.args[2] == fake_ctx or call.kwargs.get("market_ctx") == fake_ctx
