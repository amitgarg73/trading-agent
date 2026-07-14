"""Tests for health_check.check_universe() — reads from universe_cache.json."""
import json
import pytest
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch, mock_open


def _make_cache(days_ago: int, ticker_count: int = 100) -> str:
    cache_date = (date.today() - timedelta(days=days_ago)).isoformat()
    return json.dumps({"date": cache_date, "tickers": [f"T{i}" for i in range(ticker_count)]})


class TestCheckUniverse:
    def _run(self):
        from health_check import check_universe
        return check_universe()

    def test_fresh_cache_passes(self, tmp_path):
        cache = tmp_path / "universe_cache.json"
        cache.write_text(_make_cache(days_ago=10, ticker_count=518))
        with patch("pathlib.Path.exists", return_value=True), \
             patch("pathlib.Path.read_text", return_value=cache.read_text()):
            ok, msg = self._run()
        assert ok
        assert "518 tickers" in msg

    def test_stale_cache_fails(self):
        stale = _make_cache(days_ago=35)
        with patch("pathlib.Path.exists", return_value=True), \
             patch("pathlib.Path.read_text", return_value=stale):
            ok, msg = self._run()
        assert not ok
        assert ">30 days ago" in msg

    def test_exactly_30_days_old_passes(self):
        # cutoff = today - 30d; cache date == cutoff → not strictly less than → passes
        on_boundary = _make_cache(days_ago=30)
        with patch("pathlib.Path.exists", return_value=True), \
             patch("pathlib.Path.read_text", return_value=on_boundary):
            ok, msg = self._run()
        assert ok

    def test_31_days_old_fails(self):
        stale = _make_cache(days_ago=31)
        with patch("pathlib.Path.exists", return_value=True), \
             patch("pathlib.Path.read_text", return_value=stale):
            ok, msg = self._run()
        assert not ok

    def test_29_days_old_passes(self):
        # inside the normal cadence gap between mid-month refreshes — must not alert
        fresh = _make_cache(days_ago=29)
        with patch("pathlib.Path.exists", return_value=True), \
             patch("pathlib.Path.read_text", return_value=fresh):
            ok, msg = self._run()
        assert ok

    def test_missing_cache_fails(self):
        with patch("pathlib.Path.exists", return_value=False):
            ok, msg = self._run()
        assert not ok
        assert "No universe cache found" in msg

    def test_corrupt_cache_fails(self):
        with patch("pathlib.Path.exists", return_value=True), \
             patch("pathlib.Path.read_text", return_value="not-json"):
            ok, msg = self._run()
        assert not ok
        assert "Universe check failed" in msg
