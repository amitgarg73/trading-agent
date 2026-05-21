"""
Tests for agents/universe_refresh.py
Covers: fetch_sp500 happy path, fetch_sp500 fallback on error, no leveraged ETFs
in NON_LEVERAGED_ETFS, fallback list is non-empty and has no duplicates.
"""
import pytest
import pandas as pd
from unittest.mock import patch, MagicMock
from agents.universe_refresh import fetch_sp500, NON_LEVERAGED_ETFS, _SP500_FALLBACK

LEVERAGED = {"TQQQ", "SQQQ", "UPRO", "SPXU", "SOXL", "SOXS", "UVXY", "SVXY", "LABU", "LABD"}


class TestFetchSp500:
    def _make_wiki_df(self, symbols: list[str]) -> pd.DataFrame:
        return pd.DataFrame({"Symbol": symbols, "Security": ["Fake"] * len(symbols)})

    def test_returns_tickers_from_wikipedia(self):
        symbols = [f"T{i:03d}" for i in range(503)]
        fake_df = self._make_wiki_df(symbols)
        with patch("agents.universe_refresh.pd.read_html", return_value=[fake_df]):
            result = fetch_sp500()
        assert result == symbols
        assert len(result) == 503

    def test_normalises_dot_to_dash(self):
        symbols = ["BRK.B", "BF.B"] + [f"T{i:03d}" for i in range(499)]
        fake_df = self._make_wiki_df(symbols)
        with patch("agents.universe_refresh.pd.read_html", return_value=[fake_df]):
            result = fetch_sp500()
        assert "BRK-B" in result
        assert "BF-B" in result
        assert "BRK.B" not in result

    def test_falls_back_when_wikipedia_raises(self):
        with patch("agents.universe_refresh.pd.read_html", side_effect=Exception("timeout")):
            result = fetch_sp500()
        assert result == list(_SP500_FALLBACK)
        assert len(result) >= 40

    def test_falls_back_when_result_is_truncated(self):
        # Fewer than 400 tickers → looks wrong → use fallback
        symbols = [f"T{i:03d}" for i in range(50)]
        fake_df = self._make_wiki_df(symbols)
        with patch("agents.universe_refresh.pd.read_html", return_value=[fake_df]):
            result = fetch_sp500()
        assert result == list(_SP500_FALLBACK)


class TestNonLeveragedEtfs:
    def test_no_leveraged_etfs_in_list(self):
        found = LEVERAGED & set(NON_LEVERAGED_ETFS)
        assert found == set(), f"Leveraged ETFs found in NON_LEVERAGED_ETFS: {found}"

    def test_core_etfs_present(self):
        for ticker in ("SPY", "QQQ", "XLK", "XLF", "GLD", "TLT"):
            assert ticker in NON_LEVERAGED_ETFS, f"{ticker} missing from NON_LEVERAGED_ETFS"

    def test_no_duplicates(self):
        assert len(NON_LEVERAGED_ETFS) == len(set(NON_LEVERAGED_ETFS))


class TestFallbackList:
    def test_fallback_is_non_empty(self):
        assert len(_SP500_FALLBACK) >= 40

    def test_fallback_has_no_leveraged_etfs(self):
        found = LEVERAGED & set(_SP500_FALLBACK)
        assert found == set(), f"Leveraged ETFs in fallback: {found}"

    def test_fallback_has_no_duplicates(self):
        assert len(_SP500_FALLBACK) == len(set(_SP500_FALLBACK))
