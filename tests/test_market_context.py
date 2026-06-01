"""
Tests for agents/market_context.py
Covers: sector rotation fetch (Alpaca), sector rotation in run() output, summary inclusion.
All external calls (Alpaca, CNN Fear & Greed, urllib) are mocked.
"""
from __future__ import annotations
import pytest
from unittest.mock import patch, MagicMock
from datetime import datetime


# ── helpers ───────────────────────────────────────────────────────────────────

def _make_fake_bar(close: float) -> MagicMock:
    bar = MagicMock()
    bar.close = close
    return bar


def _make_dclient_mock(etf_bars: dict[str, list]) -> MagicMock:
    """Return a callable mock for _dclient — calling it returns a client whose
    get_stock_bars().data returns etf_bars."""
    resp = MagicMock()
    resp.data = etf_bars
    client = MagicMock()
    client.get_stock_bars.return_value = resp
    # _dclient() is called as _dclient().get_stock_bars(...) in the code
    dclient_fn = MagicMock(return_value=client)
    return dclient_fn


# ── _fetch_sector_rotation ────────────────────────────────────────────────────

class TestFetchSectorRotation:

    def _build_bars(self, etfs: list[str], prev: float = 100.0, chg: float = 0.01) -> dict:
        curr = round(prev * (1 + chg), 4)
        return {etf: [_make_fake_bar(prev), _make_fake_bar(curr)] for etf in etfs}

    def test_returns_sorted_dict(self):
        from agents.market_context import _fetch_sector_rotation, _SECTOR_ETFS
        bars = self._build_bars(_SECTOR_ETFS, chg=0.01)
        dclient_fn = _make_dclient_mock(bars)
        with patch("agents.alpaca_broker._dclient", dclient_fn):
            result = _fetch_sector_rotation()
        assert isinstance(result, dict)
        values = list(result.values())
        assert values == sorted(values, reverse=True), "Sector rotation must be sorted best->worst"

    def test_returns_all_etfs_present(self):
        from agents.market_context import _fetch_sector_rotation, _SECTOR_ETFS
        bars = self._build_bars(_SECTOR_ETFS, chg=0.005)
        dclient_fn = _make_dclient_mock(bars)
        with patch("agents.alpaca_broker._dclient", dclient_fn):
            result = _fetch_sector_rotation()
        assert len(result) == len(_SECTOR_ETFS)

    def test_returns_empty_on_exception(self):
        from agents.market_context import _fetch_sector_rotation
        with patch("agents.alpaca_broker._dclient", side_effect=Exception("network error")):
            result = _fetch_sector_rotation()
        assert result == {}

    def test_change_pct_is_correct(self):
        from agents.market_context import _fetch_sector_rotation
        bars = {"XLK": [_make_fake_bar(100.0), _make_fake_bar(102.0)]}
        dclient_fn = _make_dclient_mock(bars)
        with patch("agents.alpaca_broker._dclient", dclient_fn):
            result = _fetch_sector_rotation()
        assert "XLK" in result
        assert abs(result["XLK"] - 2.0) < 0.01

    def test_etf_with_fewer_than_2_bars_skipped(self):
        from agents.market_context import _fetch_sector_rotation
        bars = {
            "XLK": [_make_fake_bar(100.0), _make_fake_bar(102.0)],
            "XLF": [_make_fake_bar(50.0)],  # only 1 bar — should be skipped
        }
        dclient_fn = _make_dclient_mock(bars)
        with patch("agents.alpaca_broker._dclient", dclient_fn):
            result = _fetch_sector_rotation()
        assert "XLK" in result
        assert "XLF" not in result

    def test_returns_empty_dict_when_no_bars(self):
        from agents.market_context import _fetch_sector_rotation, _SECTOR_ETFS
        bars = {etf: [] for etf in _SECTOR_ETFS}
        dclient_fn = _make_dclient_mock(bars)
        with patch("agents.alpaca_broker._dclient", dclient_fn):
            result = _fetch_sector_rotation()
        assert result == {}


# ── run() output ─────────────────────────────────────────────────────────────

def _mock_run_patches(sector_rotation=None, vix=18.0, fg=60, futures_chg=0.3):
    if sector_rotation is None:
        sector_rotation = {"XLK": 1.2, "XLF": 0.5, "XLE": -0.3}
    return {
        "_fetch_market_data": lambda: {
            "vix": vix,
            "futures": {"S&P500": {"price": 5000.0, "change_pct": futures_chg}},
            "intl": {"Nikkei (Japan)": {"change_pct": 0.5}},
        },
        "_fetch_fear_greed": lambda: {"value": fg, "classification": "Neutral"},
        "_check_economic_calendar": lambda: [],
        "_fetch_sector_rotation": lambda: sector_rotation,
    }


class TestMarketContextRun:

    def _run_with_mocks(self, sector_rotation=None):
        mocks = _mock_run_patches(sector_rotation=sector_rotation)
        with patch("agents.market_context._fetch_market_data", side_effect=mocks["_fetch_market_data"]), \
             patch("agents.market_context._fetch_fear_greed", side_effect=mocks["_fetch_fear_greed"]), \
             patch("agents.market_context._check_economic_calendar", side_effect=mocks["_check_economic_calendar"]), \
             patch("agents.market_context._fetch_sector_rotation", side_effect=mocks["_fetch_sector_rotation"]):
            from agents.market_context import run
            return run()

    def test_sector_rotation_key_present(self):
        result = self._run_with_mocks()
        assert "sector_rotation" in result

    def test_sector_rotation_is_dict(self):
        result = self._run_with_mocks(sector_rotation={"XLK": 1.5, "XLF": -0.3})
        assert isinstance(result["sector_rotation"], dict)

    def test_sector_rotation_values_in_summary(self):
        result = self._run_with_mocks(sector_rotation={"XLK": 1.5, "XLF": 0.8, "XLE": -0.3,
                                                        "XLV": -0.5, "XLI": -0.7, "XLC": -0.9})
        assert "XLK" in result["summary"], "Top sector must appear in summary"

    def test_empty_sector_rotation_does_not_crash(self):
        result = self._run_with_mocks(sector_rotation={})
        assert result["sector_rotation"] == {}
        assert isinstance(result["summary"], str)
