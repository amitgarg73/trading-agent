"""
Tests for agents/sector_guard.py
Covers: sector cap enforcement, ETF classification, confidence-based tiebreaking,
Unknown sector pass-through, under-cap sectors untouched.
_get_sector no longer makes network calls — unknown tickers always return "Unknown".
"""
import pytest
from unittest.mock import patch
from tests.conftest import make_trade
from agents.sector_guard import run, _get_sector
from config.settings import MAX_PER_SECTOR


def _risk_output(trades):
    return {
        "approved_trades": trades,
        "rejected_trades": [],
        "market_context":  "test",
        "risk_note":       "",
        "total_estimated_profit": sum(t.get("estimated_profit", 10) for t in trades),
        "total_max_loss":         sum(t.get("max_loss", 3) for t in trades),
    }


def _with_sector(trades_sectors: list[tuple]) -> list:
    """Build trades pre-tagged with sector (skips yfinance call)."""
    trades = []
    for ticker, sector, confidence in trades_sectors:
        t = make_trade(ticker=ticker, confidence=confidence)
        t["sector"] = sector
        t["estimated_profit"] = 100.0
        t["max_loss"] = 33.0
        trades.append(t)
    return trades


# ── _get_sector ──────────────────────────────────────────────────────────────

class TestGetSector:

    def test_etf_returns_etf(self):
        from config.settings import ETF_UNIVERSE
        etf = ETF_UNIVERSE[0]
        assert _get_sector(etf) == "ETF"

    def test_unknown_ticker_returns_unknown_without_network_call(self):
        """_get_sector must return Unknown for any non-ETF ticker — no network call."""
        # No patching needed — the function has no network call anymore
        result = _get_sector("AAPL")
        assert result == "Unknown"

    def test_unknown_ticker_no_import_of_yfinance(self):
        """sector_guard must not import yfinance."""
        import agents.sector_guard as sg
        src = open(sg.__file__).read()
        assert "import yfinance" not in src, "sector_guard.py must not import yfinance"

    def test_multiple_unknown_tickers_all_return_unknown(self):
        for ticker in ["NVDA", "MSFT", "TSLA", "IONQ"]:
            assert _get_sector(ticker) == "Unknown"


# ── run ──────────────────────────────────────────────────────────────────────

class TestSectorGuardRun:

    def _run_with_sectors(self, trades_sectors):
        trades = _with_sector(trades_sectors)
        with patch("agents.sector_guard._get_sector", side_effect=lambda t: next(
            tr["sector"] for tr in trades if tr["ticker"] == t
        )):
            return run(_risk_output(trades))

    def test_empty_trades_passthrough(self):
        result = run(_risk_output([]))
        assert result["approved_trades"] == []
        assert result["sector_blocked"] == []

    def test_under_cap_all_pass(self):
        # 2 tech trades, MAX_PER_SECTOR=3 → all pass
        result = self._run_with_sectors([
            ("AAPL", "Technology", "HIGH"),
            ("MSFT", "Technology", "MEDIUM"),
        ])
        assert len(result["approved_trades"]) == 2
        assert len(result["sector_blocked"]) == 0

    def test_exactly_at_cap_all_pass(self):
        trades = [("AAPL", "Technology", "HIGH"),
                  ("MSFT", "Technology", "MEDIUM"),
                  ("NVDA", "Technology", "LOW")][:MAX_PER_SECTOR]
        result = self._run_with_sectors(trades)
        assert len(result["approved_trades"]) == MAX_PER_SECTOR
        assert len(result["sector_blocked"]) == 0

    def test_over_cap_blocks_lowest_confidence(self):
        trades = [
            ("AAPL", "Technology", "HIGH"),
            ("MSFT", "Technology", "HIGH"),
            ("NVDA", "Technology", "MEDIUM"),
            ("AMD",  "Technology", "LOW"),   # should be blocked
        ]
        result = self._run_with_sectors(trades)
        kept_tickers    = {t["ticker"] for t in result["approved_trades"]}
        blocked_tickers = {b["ticker"] for b in result["sector_blocked"]}
        assert len(result["approved_trades"]) == MAX_PER_SECTOR
        assert "AMD" in blocked_tickers
        assert "AMD" not in kept_tickers

    def test_over_cap_keeps_highest_confidence(self):
        trades = [
            ("AAPL", "Technology", "HIGH"),
            ("MSFT", "Technology", "HIGH"),
            ("NVDA", "Technology", "HIGH"),
            ("AMD",  "Technology", "LOW"),
        ]
        result = self._run_with_sectors(trades)
        kept = {t["ticker"] for t in result["approved_trades"]}
        assert "AAPL" in kept
        assert "MSFT" in kept
        assert "NVDA" in kept
        assert "AMD" not in kept

    def test_unknown_sector_never_capped(self):
        # 5 Unknown-sector trades — no cap applied
        trades = [(f"T{i}", "Unknown", "MEDIUM") for i in range(5)]
        result = self._run_with_sectors(trades)
        assert len(result["approved_trades"]) == 5
        assert len(result["sector_blocked"]) == 0

    def test_different_sectors_independent_caps(self):
        # 2 Tech + 2 Health → both sectors under cap → all pass
        trades = [
            ("AAPL", "Technology", "HIGH"),
            ("MSFT", "Technology", "HIGH"),
            ("LLY",  "Healthcare", "HIGH"),
            ("ABBV", "Healthcare", "HIGH"),
        ]
        result = self._run_with_sectors(trades)
        assert len(result["approved_trades"]) == 4
        assert len(result["sector_blocked"]) == 0

    def test_etf_trades_not_sector_capped(self):
        # ETFs all go into "ETF" sector — treated as Unknown (no cap if ≤ MAX)
        trades = [(t, "ETF", "HIGH") for t in ["SPY", "QQQ", "XLK", "XLF"]]
        result = self._run_with_sectors(trades)
        # ETF is a real sector — cap applies if > MAX_PER_SECTOR
        assert len(result["approved_trades"]) == MAX_PER_SECTOR

    def test_blocked_reason_mentions_sector(self):
        trades = [("AAPL", "Technology", "HIGH"),
                  ("MSFT", "Technology", "HIGH"),
                  ("NVDA", "Technology", "HIGH"),
                  ("AMD",  "Technology", "LOW")]
        result = self._run_with_sectors(trades)
        reason = result["sector_blocked"][0]["reason"]
        assert "Technology" in reason or "cap" in reason.lower()

    def test_total_profit_updated_after_cap(self):
        trades = [
            ("AAPL", "Technology", "HIGH"),
            ("MSFT", "Technology", "HIGH"),
            ("NVDA", "Technology", "HIGH"),
            ("AMD",  "Technology", "LOW"),
        ]
        result = self._run_with_sectors(trades)
        expected = sum(t["estimated_profit"] for t in result["approved_trades"])
        assert result["total_estimated_profit"] == pytest.approx(expected)
