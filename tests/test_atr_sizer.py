"""Tests for agents/atr_sizer.py — ATR-based stop sizing and ORB choppiness gate."""
from unittest.mock import patch, MagicMock
import pytest
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from agents.atr_sizer import apply, _fetch_orb_pct, _orb_cache
from config.settings import ATR_STOP_MULTIPLIER, ATR_STOP_FLOOR, MAX_LOSS_DOLLARS, POSITION_SIZE_BY_CONFIDENCE


# ── helpers for Alpaca bar mocks ─────────────────────────────────────────────

def _make_bar(high: float, low: float, close: float = 0.0) -> MagicMock:
    b = MagicMock()
    b.high  = high
    b.low   = low
    b.close = close
    return b


def _make_dclient_mock(bars: list) -> MagicMock:
    """Return a callable mock for _dclient — calling it returns a data client."""
    resp = MagicMock()
    resp.data = MagicMock()
    resp.data.get = MagicMock(return_value=bars)
    client = MagicMock()
    client.get_stock_bars.return_value = resp
    # _dclient() is called as a function, so wrap in a callable mock
    dclient_fn = MagicMock(return_value=client)
    return dclient_fn


def _trade(**overrides) -> dict:
    base = {
        "ticker":        "AAPL",
        "action":        "BUY",
        "entry_price":   150.0,
        "target_price":  156.0,   # 4% above entry
        "stop_loss":     148.99,  # formula stop (0.67%)
        "shares":        23,
        "position_size": 3500.0,
        "estimated_profit": 138.0,
        "max_loss":      23.0,
        "reward_risk":   6.0,
        "confidence":    "MEDIUM",
    }
    base.update(overrides)
    return base


class TestAtrSizerBasic:
    def test_no_atr_data_passes_through(self):
        """Trades with no ATR in candidates_atr dict are returned unchanged."""
        trades = [_trade()]
        adjusted, dropped = apply(trades, {})
        assert len(adjusted) == 1
        assert len(dropped) == 0
        assert adjusted[0]["stop_loss"] == 148.99

    def test_none_atr_passes_through(self):
        trades = [_trade()]
        adjusted, dropped = apply(trades, {"AAPL": None})
        assert adjusted[0]["stop_loss"] == 148.99

    def test_zero_atr_passes_through(self):
        trades = [_trade()]
        adjusted, dropped = apply(trades, {"AAPL": 0.0})
        assert adjusted[0]["stop_loss"] == 148.99

    def test_atr_stop_wider_than_formula(self):
        """ATR 2% → stop 2.4% (1.2×) — wider than 0.67% formula."""
        trades = [_trade()]
        adjusted, dropped = apply(trades, {"AAPL": 2.0})
        assert len(adjusted) == 1
        assert len(dropped) == 0
        new_stop = adjusted[0]["stop_loss"]
        expected_stop_pct = max(0.02 * ATR_STOP_MULTIPLIER, ATR_STOP_FLOOR)
        expected_stop = round(150.0 * (1 - expected_stop_pct), 2)
        assert new_stop == expected_stop

    def test_atr_stop_pct_field_added(self):
        """atr_stop_pct field must be present on adjusted trade."""
        trades = [_trade()]
        adjusted, _ = apply(trades, {"AAPL": 1.5})
        assert "atr_stop_pct" in adjusted[0]
        assert adjusted[0]["atr_stop_pct"] > 0

    def test_shares_from_constant_risk(self):
        """Shares = MAX_LOSS_DOLLARS / (entry × stop_pct), capped by position limit."""
        entry = 150.0
        atr_pct = 1.5
        stop_pct = max((atr_pct / 100) * ATR_STOP_MULTIPLIER, ATR_STOP_FLOOR)
        trades = [_trade(entry_price=entry)]
        adjusted, _ = apply(trades, {"AAPL": atr_pct})
        shares = adjusted[0]["shares"]
        shares_by_risk = int(MAX_LOSS_DOLLARS / (entry * stop_pct))
        shares_by_size = int(POSITION_SIZE_BY_CONFIDENCE["MEDIUM"] / entry)
        assert shares == min(shares_by_risk, shares_by_size)

    def test_position_size_recalculated(self):
        trades = [_trade()]
        adjusted, _ = apply(trades, {"AAPL": 1.5})
        t = adjusted[0]
        assert abs(t["position_size"] - t["shares"] * t["entry_price"]) < 0.02

    def test_estimated_profit_recalculated(self):
        trades = [_trade()]
        adjusted, _ = apply(trades, {"AAPL": 1.5})
        t = adjusted[0]
        expected = round(t["shares"] * (t["target_price"] - t["entry_price"]), 2)
        assert t["estimated_profit"] == expected

    def test_max_loss_recalculated(self):
        trades = [_trade()]
        adjusted, _ = apply(trades, {"AAPL": 1.5})
        t = adjusted[0]
        expected = round(t["shares"] * (t["entry_price"] - t["stop_loss"]), 2)
        assert t["max_loss"] == expected


class TestAtrSizerDrops:
    def test_drop_when_stop_exceeds_target(self):
        """ATR 5% → stop 6% > target 4% → R:R < 1 → drop."""
        trades = [_trade()]
        adjusted, dropped = apply(trades, {"AAPL": 5.0})
        assert len(adjusted) == 0
        assert len(dropped) == 1
        assert "AAPL" in dropped[0]

    def test_drop_when_atrstop_equals_target(self):
        """Exactly equal stop and target → drop (boundary)."""
        # entry=100, target=104 (4%). Need ATR where 0.8×ATR/100 = 0.04 → ATR = 5.0%
        trades = [_trade(entry_price=100.0, target_price=104.0, stop_loss=99.33)]
        adjusted, dropped = apply(trades, {"AAPL": 5.001})  # 5.001% × 0.8 = 4.0008% > 4% target
        assert len(adjusted) == 0
        assert len(dropped) == 1

    def test_multiple_trades_some_dropped(self):
        trades = [_trade(ticker="AAPL"), _trade(ticker="IONQ")]
        adjusted, dropped = apply(trades, {"AAPL": 1.5, "IONQ": 10.0})
        tickers_adj = {t["ticker"] for t in adjusted}
        assert "AAPL" in tickers_adj
        assert "IONQ" not in tickers_adj
        assert any("IONQ" in d for d in dropped)


class TestAtrFloor:
    def test_floor_applied_when_atr_tiny(self):
        """ATR 0.1% → stop should be floored at ATR_STOP_FLOOR (0.5%)."""
        trades = [_trade()]
        adjusted, _ = apply(trades, {"AAPL": 0.1})
        t = adjusted[0]
        stop_pct = (t["entry_price"] - t["stop_loss"]) / t["entry_price"]
        assert abs(stop_pct - ATR_STOP_FLOOR) < 0.001


class TestOrbGate:
    def test_choppy_open_halves_shares(self):
        """If ORB < 0.5 × ATR, shares should be halved."""
        entry = 150.0
        atr_pct = 2.0
        stop_pct = max((atr_pct / 100) * ATR_STOP_MULTIPLIER, ATR_STOP_FLOOR)
        shares_by_risk = int(MAX_LOSS_DOLLARS / (entry * stop_pct))
        shares_by_size = int(POSITION_SIZE_BY_CONFIDENCE["MEDIUM"] / entry)
        expected_base = min(shares_by_risk, shares_by_size)

        # ORB = 0.2% of entry — less than 0.5 × 2% ATR = 1%
        orb_fraction = 0.002  # 0.2%

        with patch("agents.atr_sizer._fetch_orb_pct", return_value=orb_fraction):
            adjusted, _ = apply([_trade(entry_price=entry)], {"AAPL": atr_pct})

        assert adjusted[0]["shares"] == max(1, expected_base // 2)
        assert adjusted[0]["orb_choppy"] is True

    def test_normal_open_full_shares(self):
        """If ORB ≥ 0.5 × ATR, shares are not halved."""
        entry = 150.0
        atr_pct = 2.0
        stop_pct = max((atr_pct / 100) * ATR_STOP_MULTIPLIER, ATR_STOP_FLOOR)
        shares_by_risk = int(MAX_LOSS_DOLLARS / (entry * stop_pct))
        shares_by_size = int(POSITION_SIZE_BY_CONFIDENCE["MEDIUM"] / entry)
        expected_base = min(shares_by_risk, shares_by_size)

        # ORB = 1.5% — comfortably above 0.5 × 2% = 1%
        orb_fraction = 0.015

        with patch("agents.atr_sizer._fetch_orb_pct", return_value=orb_fraction):
            adjusted, _ = apply([_trade(entry_price=entry)], {"AAPL": atr_pct})

        assert adjusted[0]["shares"] == expected_base
        assert adjusted[0]["orb_choppy"] is False

    def test_missing_orb_no_halving(self):
        """If ORB fetch fails (None), treat as normal — no halving."""
        with patch("agents.atr_sizer._fetch_orb_pct", return_value=None):
            adjusted, _ = apply([_trade()], {"AAPL": 1.5})
        assert adjusted[0]["orb_choppy"] is False

    def test_empty_input(self):
        adjusted, dropped = apply([], {})
        assert adjusted == []
        assert dropped == []


# ── _fetch_orb_pct (Alpaca implementation) ────────────────────────────────────

class TestFetchOrbPctAlpaca:

    def setup_method(self):
        # Clear the module-level cache before each test
        _orb_cache.clear()

    def test_orb_computed_from_alpaca_bars(self):
        """ORB = (high - low) / entry from Alpaca 1-min bars."""
        bars = [_make_bar(high=101.0, low=99.0), _make_bar(high=102.0, low=100.0)]
        dclient_fn = _make_dclient_mock(bars)
        with patch("agents.alpaca_broker._dclient", dclient_fn):
            result = _fetch_orb_pct("AAPL", entry=100.0)
        # high = max(101, 102) = 102, low = min(99, 100) = 99 -> range = 3 -> 3/100 = 0.03
        assert result == pytest.approx(0.03, abs=0.001)

    def test_orb_returns_none_on_insufficient_bars(self):
        """Fewer than 2 bars returns None."""
        bars = [_make_bar(high=101.0, low=99.0)]  # only 1 bar
        dclient_fn = _make_dclient_mock(bars)
        with patch("agents.alpaca_broker._dclient", dclient_fn):
            result = _fetch_orb_pct("MSFT", entry=100.0)
        assert result is None

    def test_orb_returns_none_on_empty_bars(self):
        """Empty bar list returns None."""
        dclient_fn = _make_dclient_mock([])
        with patch("agents.alpaca_broker._dclient", dclient_fn):
            result = _fetch_orb_pct("TSLA", entry=250.0)
        assert result is None

    def test_orb_cached_after_first_call(self):
        """Second call for same ticker reads from cache, not Alpaca."""
        bars = [_make_bar(high=105.0, low=95.0), _make_bar(high=106.0, low=96.0)]
        dclient_fn = _make_dclient_mock(bars)
        with patch("agents.alpaca_broker._dclient", dclient_fn):
            r1 = _fetch_orb_pct("NVDA", entry=100.0)
            r2 = _fetch_orb_pct("NVDA", entry=100.0)
        assert r1 == r2
        # _dclient() should only be called once (second call hits cache)
        assert dclient_fn.call_count == 1

    def test_orb_returns_none_on_exception(self):
        """Exception during Alpaca call returns None gracefully."""
        with patch("agents.alpaca_broker._dclient", side_effect=Exception("api down")):
            result = _fetch_orb_pct("FAIL", entry=100.0)
        assert result is None

    def test_orb_zero_entry_returns_zero(self):
        """Zero entry price returns 0.0, not a ZeroDivisionError."""
        bars = [_make_bar(high=101.0, low=99.0), _make_bar(high=102.0, low=100.0)]
        dclient_fn = _make_dclient_mock(bars)
        with patch("agents.alpaca_broker._dclient", dclient_fn):
            result = _fetch_orb_pct("ZERO", entry=0.0)
        assert result == 0.0
