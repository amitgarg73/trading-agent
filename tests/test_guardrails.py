"""
Tests for agents/guardrails.py
Covers: daily loss limit, action whitelist, ticker whitelist, duplicate guard,
price sanity (Alpaca primary / fail-closed, no yfinance fallback), capital check.
"""
import pytest
from unittest.mock import patch, MagicMock
from datetime import date
from tests.conftest import make_trade, make_position, FakeDB
from agents.guardrails import filter_trades, _current_price
from config.settings import DAILY_LOSS_LIMIT, PRICE_SANITY_PCT, TOTAL_CAPITAL


# ── Helpers ─────────────────────────────────────────────────────────────────

def _make_alpaca_bars(closes: list[float]) -> list:
    """Return a list of mock Alpaca bar objects with .close attributes."""
    bars = []
    for c in closes:
        b = MagicMock()
        b.close = c
        bars.append(b)
    return bars


def _make_alpaca_dclient(bars: list) -> MagicMock:
    """Return a mock that, when called as _dclient(), returns a client whose
    get_stock_bars().data.get(ticker) returns `bars`."""
    resp = MagicMock()
    resp.data = MagicMock()
    resp.data.get = MagicMock(return_value=bars)
    client = MagicMock()
    client.get_stock_bars.return_value = resp
    # _dclient is called as a function: _dclient() → client
    dclient_fn = MagicMock(return_value=client)
    return dclient_fn


def _run(trades, *, today_pnl=0.0, open_pos=None, closed_today=None,
         market_price=100.0, buying_power=None, universe=None,
         hist_bars=None):
    """Patch all external calls and run filter_trades."""
    open_pos     = open_pos or []
    closed_today = closed_today or []

    all_pos = open_pos + closed_today
    # Default: 30d Alpaca bars with stable prices (won't trigger >25% deviation)
    if hist_bars is None:
        hist_bars = _make_alpaca_bars([market_price or 100.0] * 10)
    mock_dclient = _make_alpaca_dclient(hist_bars)

    # _dclient is imported locally inside guardrails via `from agents.alpaca_broker import _dclient`
    # Patch the source so the local import gets the mock
    with patch("agents.guardrails._today_realized_pnl", return_value=today_pnl), \
         patch("agents.guardrails._current_price", return_value=market_price), \
         patch("agents.alpaca_broker._dclient", mock_dclient), \
         patch("core.db.select", side_effect=lambda table, **kw: (
             open_pos  if table == "positions" and kw.get("filters", {}).get("status") == "OPEN"
             else all_pos if table == "positions" and not kw.get("filters")
             else closed_today
         )):
        return filter_trades(
            trades,
            broker="simulation",
            universe=universe or [t["ticker"] for t in trades],
        )


# ── Empty input ─────────────────────────────────────────────────────────────

def test_empty_trades_returns_empty():
    result = filter_trades([], broker="simulation")
    assert result == {"approved_trades": [], "guardrail_blocked": []}


# ── Daily loss limit ────────────────────────────────────────────────────────

class TestDailyLossLimit:

    def test_above_limit_passes(self):
        result = _run([make_trade()], today_pnl=0.0)
        assert len(result["approved_trades"]) == 1

    def test_at_limit_passes(self):
        result = _run([make_trade()], today_pnl=DAILY_LOSS_LIMIT)
        assert len(result["approved_trades"]) == 1

    def test_below_limit_blocks_all(self):
        trades = [make_trade("AAPL"), make_trade("MSFT")]
        result = _run(trades, today_pnl=DAILY_LOSS_LIMIT - 1)
        assert len(result["approved_trades"]) == 0
        assert len(result["guardrail_blocked"]) == 2

    def test_below_limit_reason_mentions_limit(self):
        result = _run([make_trade()], today_pnl=DAILY_LOSS_LIMIT - 100)
        reason = result["guardrail_blocked"][0]["reason"]
        assert "loss limit" in reason.lower()


# ── Action whitelist ─────────────────────────────────────────────────────────

class TestActionWhitelist:

    def test_buy_passes(self):
        result = _run([make_trade(action="BUY")])
        assert len(result["approved_trades"]) == 1

    def test_sell_blocked(self):
        trade = make_trade(); trade["action"] = "SELL"
        result = _run([trade])
        assert len(result["approved_trades"]) == 0
        assert "not allowed" in result["guardrail_blocked"][0]["reason"]

    def test_sell_short_blocked(self):
        trade = make_trade(); trade["action"] = "SELL_SHORT"
        result = _run([trade])
        assert len(result["approved_trades"]) == 0


# ── Ticker whitelist ─────────────────────────────────────────────────────────

class TestTickerWhitelist:

    def test_ticker_in_universe_passes(self):
        result = _run([make_trade("AAPL")], universe=["AAPL", "MSFT"])
        assert len(result["approved_trades"]) == 1

    def test_ticker_not_in_universe_blocked(self):
        result = _run([make_trade("UNKNOWN")], universe=["AAPL", "MSFT"])
        assert len(result["approved_trades"]) == 0
        assert "universe" in result["guardrail_blocked"][0]["reason"].lower()

    def test_empty_universe_skips_check(self):
        # universe=None means no whitelist check applied
        result = _run([make_trade("ANYTHING")], universe=None)
        assert len(result["approved_trades"]) == 1


# ── Duplicate guard ──────────────────────────────────────────────────────────

class TestDuplicateGuard:

    def test_no_duplicate_passes(self):
        result = _run([make_trade("AAPL")], open_pos=[])
        assert len(result["approved_trades"]) == 1

    def test_already_open_blocked(self):
        open_pos = [make_position("AAPL")]
        result = _run([make_trade("AAPL")], open_pos=open_pos)
        assert len(result["approved_trades"]) == 0
        assert "Duplicate" in result["guardrail_blocked"][0]["reason"]

    def test_already_traded_today_blocked(self):
        closed = [make_position("AAPL", status="CLOSED")]
        result = _run([make_trade("AAPL")], closed_today=closed)
        assert len(result["approved_trades"]) == 0

    def test_different_ticker_not_blocked(self):
        open_pos = [make_position("AAPL")]
        result = _run([make_trade("MSFT")], open_pos=open_pos)
        assert len(result["approved_trades"]) == 1

    def test_stopped_out_and_reopened_today_blocked(self):
        # Position was opened AND closed today (stop hit) — re-entry must be blocked
        # even if it no longer appears in open_pos (parallel-run / same-day re-entry guard)
        closed = [make_position("AAPL", status="CLOSED")]  # opened_at = today
        result = _run([make_trade("AAPL")], open_pos=[], closed_today=closed)
        assert len(result["approved_trades"]) == 0
        assert "Duplicate" in result["guardrail_blocked"][0]["reason"]


# ── Price sanity ─────────────────────────────────────────────────────────────

class TestPriceSanity:

    def test_price_within_tolerance_passes(self):
        # entry=100, market=100 → 0% deviation
        trade = make_trade(entry=100.0)
        result = _run([trade], market_price=100.0)
        assert len(result["approved_trades"]) == 1

    def test_price_at_edge_of_tolerance_passes(self):
        # entry=100, market=102.9 → ~2.8% deviation < PRICE_SANITY_PCT (3%)
        trade = make_trade(entry=100.0)
        result = _run([trade], market_price=102.9)
        assert len(result["approved_trades"]) == 1

    def test_price_over_tolerance_blocked(self):
        # entry=100, market=104 → ~3.8% deviation > PRICE_SANITY_PCT (3%)
        trade = make_trade(entry=100.0)
        result = _run([trade], market_price=104.0)
        assert len(result["approved_trades"]) == 0
        assert "Price sanity" in result["guardrail_blocked"][0]["reason"]

    def test_price_fetch_fails_blocks_trade(self):
        # market_price=None → fail-closed: trade blocked
        trade = make_trade(entry=100.0)
        result = _run([trade], market_price=None)
        assert len(result["approved_trades"]) == 0
        reason = result["guardrail_blocked"][0]["reason"]
        assert "could not fetch" in reason.lower()

    def test_alpaca_used_as_primary_price_source(self):
        """_current_price should call Alpaca first."""
        with patch("agents.guardrails._current_price") as mock_price:
            mock_price.return_value = 100.0
            with patch("agents.guardrails._today_realized_pnl", return_value=0.0), \
                 patch("core.db.select", return_value=[]):
                filter_trades([make_trade("AAPL")], broker="simulation",
                              universe=["AAPL"])
            mock_price.assert_called_once_with("AAPL")

    def test_historical_avg_sanity_blocks_corrupt_price(self):
        """Entry 6x above 30d avg (e.g. MU $907 vs $115) must be blocked."""
        trade = make_trade(entry=900.0)  # corrupt scanner price
        # 30d Alpaca bars all at ~115 avg → entry at 900 is 682% above → blocked
        hist_bars = _make_alpaca_bars([115.0] * 10)
        mock_dclient = _make_alpaca_dclient(hist_bars)
        with patch("agents.guardrails._today_realized_pnl", return_value=0.0), \
             patch("agents.guardrails._current_price", return_value=905.0), \
             patch("agents.alpaca_broker._dclient", mock_dclient), \
             patch("core.db.select", return_value=[]):
            result = filter_trades([trade], broker="simulation", universe=[trade["ticker"]])
        assert len(result["approved_trades"]) == 0
        assert "30d avg" in result["guardrail_blocked"][0]["reason"]

    def test_historical_avg_sanity_passes_normal_price(self):
        """Normal entry within 25% of 30d avg passes secondary check."""
        trade = make_trade(entry=102.0)
        hist_bars = _make_alpaca_bars([100.0] * 10)
        mock_dclient = _make_alpaca_dclient(hist_bars)
        with patch("agents.guardrails._today_realized_pnl", return_value=0.0), \
             patch("agents.guardrails._current_price", return_value=102.0), \
             patch("agents.alpaca_broker._dclient", mock_dclient), \
             patch("core.db.select", return_value=[]):
            result = filter_trades([trade], broker="simulation", universe=[trade["ticker"]])
        assert len(result["approved_trades"]) == 1

    def test_current_price_returns_none_when_alpaca_fails(self):
        """_current_price returns None when Alpaca fails — no yfinance fallback."""
        with patch("agents.alpaca_broker.get_live_prices", side_effect=Exception("alpaca down")):
            result = _current_price("AAPL")
        assert result is None

    def test_current_price_returns_none_when_alpaca_returns_empty(self):
        """_current_price returns None when Alpaca returns no price for ticker."""
        with patch("agents.alpaca_broker.get_live_prices", return_value={}):
            result = _current_price("AAPL")
        assert result is None


# ── Capital check ────────────────────────────────────────────────────────────

class TestCapitalCheck:

    def test_sufficient_capital_passes(self):
        result = _run([make_trade(size=6000)], buying_power=None)  # simulation uses TOTAL_CAPITAL
        assert len(result["approved_trades"]) == 1

    def test_multiple_trades_cumulative_capital(self):
        """Two trades together should both fit within TOTAL_CAPITAL."""
        trades = [make_trade("AAPL", size=6000), make_trade("MSFT", size=6000)]
        result = _run(trades)
        assert len(result["approved_trades"]) == 2
