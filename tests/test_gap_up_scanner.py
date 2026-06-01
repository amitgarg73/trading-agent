"""
Tests for _get_gap_up_tickers in orchestrator.py.
Covers: returns filtered list of gainers, empty on exception,
        percent_change threshold filtering.
"""
from __future__ import annotations
import pytest
from unittest.mock import patch, MagicMock


def _make_mover(symbol: str, percent_change: float) -> MagicMock:
    m = MagicMock()
    m.symbol          = symbol
    m.percent_change  = percent_change
    return m


def _make_screener_client(gainers: list, losers: list | None = None) -> MagicMock:
    movers = MagicMock()
    movers.gainers = gainers
    movers.losers  = losers or []
    client = MagicMock()
    client.get_market_movers.return_value = movers
    return client


class TestGetGapUpTickers:

    def test_returns_tickers_above_threshold(self):
        from orchestrator import _get_gap_up_tickers
        gainers = [
            _make_mover("AAPL", 5.0),
            _make_mover("MSFT", 2.5),
            _make_mover("TSLA", 1.0),  # below 2.0 threshold — excluded
        ]
        mock_client = _make_screener_client(gainers)
        with patch("alpaca.data.historical.screener.ScreenerClient", return_value=mock_client):
            result = _get_gap_up_tickers(min_gap_pct=2.0, top_n=20)
        assert "AAPL" in result
        assert "MSFT" in result
        assert "TSLA" not in result

    def test_returns_empty_list_on_exception(self):
        from orchestrator import _get_gap_up_tickers
        with patch("alpaca.data.historical.screener.ScreenerClient",
                   side_effect=Exception("api error")):
            result = _get_gap_up_tickers()
        assert result == []

    def test_returns_empty_when_no_gainers_above_threshold(self):
        from orchestrator import _get_gap_up_tickers
        gainers = [_make_mover("AAPL", 0.5), _make_mover("MSFT", 1.0)]
        mock_client = _make_screener_client(gainers)
        with patch("alpaca.data.historical.screener.ScreenerClient", return_value=mock_client):
            result = _get_gap_up_tickers(min_gap_pct=2.0)
        assert result == []

    def test_exactly_at_threshold_included(self):
        from orchestrator import _get_gap_up_tickers
        gainers = [_make_mover("NVDA", 2.0)]
        mock_client = _make_screener_client(gainers)
        with patch("alpaca.data.historical.screener.ScreenerClient", return_value=mock_client):
            result = _get_gap_up_tickers(min_gap_pct=2.0)
        assert "NVDA" in result

    def test_custom_min_gap_pct(self):
        from orchestrator import _get_gap_up_tickers
        gainers = [
            _make_mover("IONQ", 3.5),
            _make_mover("ASTS", 5.1),
        ]
        mock_client = _make_screener_client(gainers)
        with patch("alpaca.data.historical.screener.ScreenerClient", return_value=mock_client):
            result = _get_gap_up_tickers(min_gap_pct=4.0)
        assert "ASTS" in result
        assert "IONQ" not in result

    def test_import_error_returns_empty(self):
        """If Alpaca screener module is unavailable, returns empty list gracefully."""
        from orchestrator import _get_gap_up_tickers
        with patch("builtins.__import__", side_effect=ImportError("no module")):
            result = _get_gap_up_tickers()
        assert result == []
