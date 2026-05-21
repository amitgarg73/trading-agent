"""
Tests for agents/universe_refresh.py
Covers: fetch_sp500 happy path, dot-to-dash normalisation, fallback-file write on
success, fallback-file read on Wikipedia failure, hardcoded fallback of last resort,
truncated result guard, NON_LEVERAGED_ETFS composition, _SP500_FALLBACK composition.
"""
import json
import pytest
import pandas as pd
from unittest.mock import patch, MagicMock
from agents.universe_refresh import fetch_sp500, NON_LEVERAGED_ETFS, _SP500_FALLBACK

LEVERAGED = {"TQQQ", "SQQQ", "UPRO", "SPXU", "SOXL", "SOXS", "UVXY", "SVXY", "LABU", "LABD"}


def _make_wiki_df(symbols: list[str]) -> pd.DataFrame:
    return pd.DataFrame({"Symbol": symbols, "Security": ["Fake"] * len(symbols)})


def _mock_requests_get(html_text: str):
    resp = MagicMock()
    resp.text = html_text
    resp.raise_for_status.return_value = None
    return resp


class TestFetchSp500:
    def _html_for(self, symbols: list[str]) -> str:
        rows = "".join(f"<tr><td>{s}</td><td>Fake</td></tr>" for s in symbols)
        return f"<table><tr><th>Symbol</th><th>Security</th></tr>{rows}</table>"

    def test_returns_tickers_from_wikipedia(self):
        symbols = [f"T{i:03d}" for i in range(503)]
        fake_df = _make_wiki_df(symbols)
        with patch("agents.universe_refresh.requests.get", return_value=_mock_requests_get("html")), \
             patch("agents.universe_refresh.pd.read_html", return_value=[fake_df]), \
             patch("agents.universe_refresh._FALLBACK_FILE") as mock_file:
            mock_file.write_text = MagicMock()
            result = fetch_sp500()
        assert result == symbols
        assert len(result) == 503

    def test_normalises_dot_to_dash(self):
        symbols = ["BRK.B", "BF.B"] + [f"T{i:03d}" for i in range(499)]
        fake_df = _make_wiki_df(symbols)
        with patch("agents.universe_refresh.requests.get", return_value=_mock_requests_get("html")), \
             patch("agents.universe_refresh.pd.read_html", return_value=[fake_df]), \
             patch("agents.universe_refresh._FALLBACK_FILE") as mock_file:
            mock_file.write_text = MagicMock()
            result = fetch_sp500()
        assert "BRK-B" in result
        assert "BF-B" in result
        assert "BRK.B" not in result

    def test_writes_to_fallback_file_on_success(self, tmp_path):
        symbols = [f"T{i:03d}" for i in range(503)]
        fake_df = _make_wiki_df(symbols)
        fallback = tmp_path / "sp500_tickers.json"
        with patch("agents.universe_refresh.requests.get", return_value=_mock_requests_get("html")), \
             patch("agents.universe_refresh.pd.read_html", return_value=[fake_df]), \
             patch("agents.universe_refresh._FALLBACK_FILE", fallback):
            result = fetch_sp500()
        assert fallback.exists()
        saved = json.loads(fallback.read_text())
        assert saved == symbols

    def test_loads_fallback_file_when_wikipedia_fails(self, tmp_path):
        saved_tickers = [f"REAL{i:03d}" for i in range(503)]
        fallback = tmp_path / "sp500_tickers.json"
        fallback.write_text(json.dumps(saved_tickers))
        with patch("agents.universe_refresh.requests.get", side_effect=Exception("timeout")), \
             patch("agents.universe_refresh._FALLBACK_FILE", fallback):
            result = fetch_sp500()
        assert result == saved_tickers
        assert len(result) == 503

    def test_uses_hardcoded_fallback_when_file_missing(self, tmp_path):
        missing = tmp_path / "no_such_file.json"
        with patch("agents.universe_refresh.requests.get", side_effect=Exception("timeout")), \
             patch("agents.universe_refresh._FALLBACK_FILE", missing):
            result = fetch_sp500()
        assert result == list(_SP500_FALLBACK)

    def test_falls_back_when_result_is_truncated(self, tmp_path):
        symbols = [f"T{i:03d}" for i in range(50)]
        fake_df = _make_wiki_df(symbols)
        missing = tmp_path / "no_such_file.json"
        with patch("agents.universe_refresh.requests.get", return_value=_mock_requests_get("html")), \
             patch("agents.universe_refresh.pd.read_html", return_value=[fake_df]), \
             patch("agents.universe_refresh._FALLBACK_FILE", missing):
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
