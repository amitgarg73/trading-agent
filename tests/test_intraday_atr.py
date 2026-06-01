"""
Tests for _fetch_atr_for_tickers in agents/intraday.py (Alpaca implementation).
Covers: correct ATR computation, insufficient bars, batch failure, empty input.
"""
from __future__ import annotations
import pytest
from unittest.mock import patch, MagicMock


# ── helpers ───────────────────────────────────────────────────────────────────

def _make_bar(high: float, low: float, close: float) -> MagicMock:
    b = MagicMock()
    b.high  = high
    b.low   = low
    b.close = close
    return b


def _make_dclient_fn(bars_by_ticker: dict) -> MagicMock:
    """Return a callable mock for _dclient — calling it returns a data client."""
    resp = MagicMock()
    resp.data = bars_by_ticker
    client = MagicMock()
    client.get_stock_bars.return_value = resp
    # _dclient() is called as a function in the code, so mock it as a callable
    dclient_fn = MagicMock(return_value=client)
    return dclient_fn


def _stable_bars(n: int = 20, price: float = 100.0, daily_range: float = 2.0) -> list:
    """n daily bars with a stable price and constant high-low range."""
    return [_make_bar(high=price + daily_range / 2,
                      low=price  - daily_range / 2,
                      close=price) for _ in range(n)]


# ── tests ─────────────────────────────────────────────────────────────────────

class TestFetchAtrForTickers:

    def test_empty_tickers_returns_empty(self):
        from agents.intraday import _fetch_atr_for_tickers
        result = _fetch_atr_for_tickers([])
        assert result == {}

    def test_atr_computed_correctly(self):
        """ATR for stable bars: TR = daily_range = 2.0, price = 100 -> atr_pct = 2.0."""
        from agents.intraday import _fetch_atr_for_tickers
        bars = _stable_bars(n=20, price=100.0, daily_range=2.0)
        dclient_fn = _make_dclient_fn({"AAPL": bars})
        with patch("agents.alpaca_broker._dclient", dclient_fn):
            result = _fetch_atr_for_tickers(["AAPL"])
        assert "AAPL" in result
        assert result["AAPL"] is not None
        assert abs(result["AAPL"] - 2.0) < 0.1

    def test_insufficient_bars_returns_none(self):
        """Fewer than 10 bars -> ATR is None for that ticker."""
        from agents.intraday import _fetch_atr_for_tickers
        bars = _stable_bars(n=5)
        dclient_fn = _make_dclient_fn({"MSFT": bars})
        with patch("agents.alpaca_broker._dclient", dclient_fn):
            result = _fetch_atr_for_tickers(["MSFT"])
        assert result["MSFT"] is None

    def test_missing_ticker_in_response_returns_none(self):
        """If Alpaca returns no bars for a ticker, result is None."""
        from agents.intraday import _fetch_atr_for_tickers
        dclient_fn = _make_dclient_fn({})  # no data for TSLA
        with patch("agents.alpaca_broker._dclient", dclient_fn):
            result = _fetch_atr_for_tickers(["TSLA"])
        assert result["TSLA"] is None

    def test_batch_failure_returns_all_none(self):
        """If Alpaca batch call raises, all tickers return None."""
        from agents.intraday import _fetch_atr_for_tickers
        client = MagicMock()
        client.get_stock_bars.side_effect = Exception("alpaca down")
        dclient_fn = MagicMock(return_value=client)
        with patch("agents.alpaca_broker._dclient", dclient_fn):
            result = _fetch_atr_for_tickers(["AAPL", "MSFT"])
        assert result["AAPL"] is None
        assert result["MSFT"] is None

    def test_multiple_tickers_batch(self):
        """Multiple tickers processed in single batch."""
        from agents.intraday import _fetch_atr_for_tickers
        bars_a = _stable_bars(n=20, price=100.0, daily_range=2.0)
        bars_b = _stable_bars(n=20, price=50.0,  daily_range=1.0)
        dclient_fn = _make_dclient_fn({"AAPL": bars_a, "MSFT": bars_b})
        with patch("agents.alpaca_broker._dclient", dclient_fn):
            result = _fetch_atr_for_tickers(["AAPL", "MSFT"])
        assert result["AAPL"] is not None
        assert result["MSFT"] is not None
        assert abs(result["AAPL"] - 2.0) < 0.1
        assert abs(result["MSFT"] - 2.0) < 0.1  # 1.0/50 * 100 = 2.0%

    def test_atr_pct_uses_last_close_as_denominator(self):
        """ATR% = ATR / last_close * 100."""
        from agents.intraday import _fetch_atr_for_tickers
        # Price at 200, daily range 2 -> atr_pct ~= 1.0%
        bars = _stable_bars(n=20, price=200.0, daily_range=2.0)
        dclient_fn = _make_dclient_fn({"NVDA": bars})
        with patch("agents.alpaca_broker._dclient", dclient_fn):
            result = _fetch_atr_for_tickers(["NVDA"])
        assert result["NVDA"] is not None
        assert abs(result["NVDA"] - 1.0) < 0.1
