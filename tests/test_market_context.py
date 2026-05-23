"""
Tests for agents/market_context.py
Covers: sector rotation fetch, sector rotation in run() output, summary inclusion.
All external calls (yfinance, CNN Fear & Greed, urllib) are mocked.
"""
from __future__ import annotations
import pandas as pd
import pytest
from unittest.mock import patch, MagicMock


# ── _fetch_sector_rotation ────────────────────────────────────────────────────

class TestFetchSectorRotation:

    def _make_multi_df(self, etfs: list[str], chg: float = 0.01) -> pd.DataFrame:
        """Build a fake yf.download multi-ticker DataFrame with predictable returns."""
        cols = pd.MultiIndex.from_tuples(
            [(etf, col) for etf in etfs for col in ["Close", "Volume"]],
        )
        data = {}
        for etf in etfs:
            data[(etf, "Close")] = [100.0, 100.0 * (1 + chg)]
            data[(etf, "Volume")] = [1_000_000, 1_000_000]
        return pd.DataFrame(data, index=pd.date_range("2026-01-01", periods=2))

    def test_returns_sorted_dict(self):
        from agents.market_context import _fetch_sector_rotation
        etfs = ["XLK", "XLF", "XLE"]
        fake_df = self._make_multi_df(etfs, chg=0.01)
        with patch("agents.market_context.yf.download", return_value=fake_df):
            result = _fetch_sector_rotation()
        assert isinstance(result, dict)
        values = list(result.values())
        assert values == sorted(values, reverse=True), "Sector rotation must be sorted best→worst"

    def test_returns_all_etfs_present(self):
        from agents.market_context import _fetch_sector_rotation, _SECTOR_ETFS
        etfs = _SECTOR_ETFS
        fake_df = self._make_multi_df(etfs, chg=0.005)
        with patch("agents.market_context.yf.download", return_value=fake_df):
            result = _fetch_sector_rotation()
        assert len(result) == len(_SECTOR_ETFS)

    def test_returns_empty_on_exception(self):
        from agents.market_context import _fetch_sector_rotation
        with patch("agents.market_context.yf.download", side_effect=Exception("network error")):
            result = _fetch_sector_rotation()
        assert result == {}

    def test_change_pct_is_correct(self):
        from agents.market_context import _fetch_sector_rotation
        fake_df = self._make_multi_df(["XLK"], chg=0.02)
        with patch("agents.market_context.yf.download", return_value=fake_df):
            result = _fetch_sector_rotation()
        assert "XLK" in result
        assert abs(result["XLK"] - 2.0) < 0.01


# ── run() output ─────────────────────────────────────────────────────────────

def _mock_run_patches(sector_rotation=None, vix=18.0, fg=60, futures_chg=0.3):
    """Return a context-manager-friendly set of patches for market_context.run()."""
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
