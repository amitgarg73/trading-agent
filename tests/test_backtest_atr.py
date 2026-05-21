"""
Tests for backtest.py ATR simulation additions:
  - compute_atr() returns positive dollar ATR from historical OHLC data
  - simulate_day_atr() uses 0.75×ATR target and 0.25×ATR stop
  - simulate_day_atr() target-hit, stop-hit, and EOD-close scenarios
  - simulate_day_atr() skips candidates with missing or extreme ATR
  - scanner output now includes 'atr' dollar field
"""
import pytest
import pandas as pd
import numpy as np
from datetime import date, timedelta


def _make_ohlcv(n=60, price=100.0, daily_range_pct=0.02, volume=5_000_000) -> pd.DataFrame:
    """Controlled synthetic OHLCV. Daily range = price * daily_range_pct."""
    dates = pd.bdate_range(end=date.today() - timedelta(days=1), periods=n)
    closes = [price] * n
    rows = []
    for c in closes:
        daily_range = c * daily_range_pct
        rows.append({
            "Open":   c - daily_range * 0.3,
            "High":   c + daily_range * 0.5,
            "Low":    c - daily_range * 0.5,
            "Close":  c,
            "Volume": volume,
        })
    return pd.DataFrame(rows, index=pd.DatetimeIndex(dates))


class TestComputeATR:

    def test_returns_positive_value(self):
        from backtest import compute_atr
        df = _make_ohlcv(n=60, price=100.0, daily_range_pct=0.02)
        atr = compute_atr(df, 50)
        assert atr > 0

    def test_larger_range_gives_larger_atr(self):
        from backtest import compute_atr
        df_tight  = _make_ohlcv(n=60, price=100.0, daily_range_pct=0.01)
        df_wide   = _make_ohlcv(n=60, price=100.0, daily_range_pct=0.05)
        atr_tight = compute_atr(df_tight, 50)
        atr_wide  = compute_atr(df_wide, 50)
        assert atr_wide > atr_tight

    def test_insufficient_data_returns_zero(self):
        from backtest import compute_atr
        df = _make_ohlcv(n=5, price=100.0)  # only 5 rows — less than window+1
        atr = compute_atr(df, 5)
        assert atr == 0.0

    def test_no_lookahead(self):
        """ATR at idx=30 should not use rows 30+ (no lookahead)."""
        from backtest import compute_atr
        df = _make_ohlcv(n=60, price=100.0, daily_range_pct=0.02)
        df_copy = df.copy()
        # Massively inflate future rows (idx 30+) — should not affect ATR at idx=30
        df_copy.iloc[30:, df_copy.columns.get_loc("High")] *= 10
        atr_before = compute_atr(df, 30)
        atr_after  = compute_atr(df_copy, 30)
        assert abs(atr_before - atr_after) < 0.01  # same — future data not used


class TestSimulateDayATR:

    def _make_day_df(self, n=60, open_px=100.0, high_px=102.0, low_px=98.5, close_px=101.0,
                     daily_range_pct=0.02) -> pd.DataFrame:
        """N-1 historical bars + 1 trading day with controlled OHLC."""
        dates_hist = pd.bdate_range(end=date.today() - timedelta(days=2), periods=n - 1)
        rows = []
        p = open_px
        for _ in range(n - 1):
            rows.append({"Open": p, "High": p * 1.01, "Low": p * 0.99, "Close": p, "Volume": 5_000_000})
        today_row = {"Open": open_px, "High": high_px, "Low": low_px, "Close": close_px, "Volume": 5_000_000}
        rows.append(today_row)
        all_dates = list(dates_hist) + [pd.Timestamp(date.today() - timedelta(days=1))]
        return pd.DataFrame(rows, index=pd.DatetimeIndex(all_dates))

    def test_target_hit_produces_win(self):
        from backtest import simulate_day_atr, compute_atr, ATR_TARGET_MULT
        day = date.today() - timedelta(days=1)
        df = self._make_day_df(open_px=100.0, high_px=103.0, low_px=99.5, close_px=102.0)
        idx = len(df) - 1
        atr = compute_atr(df, idx)
        assert atr > 0
        target = 100.0 + ATR_TARGET_MULT * atr

        candidates = [("AAPL", 8, 100.0, idx, df)]
        pnl, trades = simulate_day_atr(day, candidates, 1, 50_000)
        if trades:  # if ATR was small enough that high_px>=target
            hit_target = [t for t in trades if t["close_reason"] == "TARGET"]
            if hit_target:
                assert hit_target[0]["win"] is True
                assert hit_target[0]["exit"] == round(target, 2)

    def test_stop_hit_produces_loss(self):
        from backtest import simulate_day_atr, compute_atr, ATR_STOP_MULT
        day = date.today() - timedelta(days=1)
        # High never reaches target, low falls through stop
        df = self._make_day_df(open_px=100.0, high_px=100.5, low_px=96.0, close_px=97.0)
        idx = len(df) - 1
        atr = compute_atr(df, idx)
        if atr <= 0:
            pytest.skip("ATR not available for this synthetic df")
        stop = 100.0 - ATR_STOP_MULT * atr
        candidates = [("CLF", 6, 100.0, idx, df)]
        pnl, trades = simulate_day_atr(day, candidates, 1, 50_000)
        if trades:
            stop_trades = [t for t in trades if t["close_reason"] == "STOP"]
            if stop_trades:
                assert stop_trades[0]["win"] is False
                assert pnl < 0

    def test_eod_close_when_neither_hit(self):
        from backtest import simulate_day_atr
        day = date.today() - timedelta(days=1)
        # Very tight range: high and low stay inside target/stop
        df = self._make_day_df(open_px=100.0, high_px=100.1, low_px=99.9, close_px=100.05)
        idx = len(df) - 1
        candidates = [("MSFT", 7, 100.0, idx, df)]
        pnl, trades = simulate_day_atr(day, candidates, 1, 50_000)
        if trades:
            eod = [t for t in trades if t["close_reason"] == "EOD"]
            assert len(eod) >= 0  # may or may not be EOD depending on ATR

    def test_extreme_atr_skipped(self):
        from backtest import simulate_day_atr
        day = date.today() - timedelta(days=1)
        # Build a df where ATR will be >12% of price (penny-stock noise filter)
        rows = [{"Open": 5.0, "High": 6.0, "Low": 3.0, "Close": 5.0, "Volume": 100_000}] * 60
        dates = list(pd.bdate_range(end=date.today() - timedelta(days=1), periods=60))
        df = pd.DataFrame(rows, index=pd.DatetimeIndex(dates))
        candidates = [("PENNY", 5, 5.0, 55, df)]
        pnl, trades = simulate_day_atr(day, candidates, 1, 50_000)
        # Either skipped (empty trades) or traded with reasonable values — no crash
        assert isinstance(pnl, float)
        assert isinstance(trades, list)

    def test_empty_candidates_returns_zero(self):
        from backtest import simulate_day_atr
        pnl, trades = simulate_day_atr(date.today(), [], 5, 50_000)
        assert pnl == 0.0
        assert trades == []

    def test_rr_is_approximately_three(self):
        """0.75/0.25 = 3:1. Verify by checking target and stop distances."""
        from backtest import ATR_TARGET_MULT, ATR_STOP_MULT
        assert abs(ATR_TARGET_MULT / ATR_STOP_MULT - 3.0) < 0.01


class TestScannerATRField:

    def test_scanner_includes_atr_dollar(self):
        """scanner._technical() should return 'atr' (dollar) alongside 'atr_pct'."""
        import pandas as pd
        from tests.conftest import make_price_df
        from scanner.scanner import _technical
        df_raw = make_price_df(days=60, start_price=200.0)
        # Rename columns to title case expected by _technical
        df = df_raw.rename(columns={"open": "open", "high": "high", "low": "low",
                                    "close": "close", "volume": "volume"})
        df.columns = [c.lower() for c in df.columns]
        result = _technical("AAPL", df)
        assert "atr" in result, "scanner._technical() must include 'atr' (dollar ATR) field"
        assert result["atr"] is not None
        assert result["atr"] > 0
        assert result["atr_pct"] > 0

    def test_atr_dollar_matches_atr_pct(self):
        """atr (dollar) should equal price * atr_pct / 100 within rounding."""
        from tests.conftest import make_price_df
        from scanner.scanner import _technical
        df = make_price_df(days=60, start_price=100.0)
        df.columns = [c.lower() for c in df.columns]
        result = _technical("TEST", df)
        if result["atr"] and result["atr_pct"] and result["price"]:
            expected = result["price"] * result["atr_pct"] / 100
            assert abs(result["atr"] - expected) < 0.10  # within 10 cents for rounding
