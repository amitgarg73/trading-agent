"""
Tests for agents/risk.py
Covers: position sizing, R:R validation, confidence sizing, share calculation, MAX_POSITIONS cap.
"""
import pytest
from tests.conftest import make_trade
from agents.risk import _validate_trade, _apply_confidence_sizing, _compute_shares, run
from config.settings import (
    TOTAL_CAPITAL, MAX_POSITION_PCT, MIN_POSITION_PCT,
    MAX_LOSS_PER_TRADE, MIN_REWARD_RISK, MAX_POSITIONS,
    POSITION_SIZE_BY_CONFIDENCE,
)


# ── _validate_trade ─────────────────────────────────────────────────────────

class TestValidateTrade:

    def test_valid_trade_passes(self):
        ok, msg = _validate_trade(make_trade())
        assert ok, msg

    def test_missing_fields_rejected(self):
        ok, msg = _validate_trade({"ticker": "AAPL"})
        assert not ok
        assert "Missing" in msg

    def test_target_below_entry_rejected(self):
        ok, msg = _validate_trade(make_trade(target=99.0))  # target < entry=100
        assert not ok
        assert "Target" in msg

    def test_stop_above_entry_rejected(self):
        ok, msg = _validate_trade(make_trade(stop=101.0))  # stop > entry=100
        assert not ok
        assert "Stop" in msg

    def test_position_too_large_rejected(self):
        max_size = TOTAL_CAPITAL * MAX_POSITION_PCT
        ok, msg = _validate_trade(make_trade(size=int(max_size + 1000)))
        assert not ok
        assert "exceeds max" in msg

    def test_position_too_small_rejected(self):
        min_size = TOTAL_CAPITAL * MIN_POSITION_PCT
        ok, msg = _validate_trade(make_trade(size=int(min_size - 500)))
        assert not ok
        assert "below min" in msg

    def test_stop_too_wide_rejected(self):
        # stop at 5% below entry → loss > MAX_LOSS_PER_TRADE (0.67%)
        ok, msg = _validate_trade(make_trade(entry=100, target=110, stop=95.0))
        assert not ok
        assert "Stop too wide" in msg

    def test_rr_below_minimum_rejected(self):
        # entry=100, target=100.5 (0.5% gain), stop=99.5 (0.5% loss) → 1:1 R:R < 3.0
        ok, msg = _validate_trade(make_trade(entry=100, target=100.5, stop=99.5, size=2_500))
        assert not ok
        assert "Reward:risk" in msg

    def test_exact_min_rr_passes(self):
        # entry=100, target=102 (2%), stop=99.34 (0.66%) → rr=3.03, just above 3:1
        ok, msg = _validate_trade(make_trade(entry=100, target=102.0, stop=99.34))
        assert ok, msg

    def test_zero_entry_rejected(self):
        # Construct dict directly — make_trade() would divide by zero before reaching validation
        trade = {"ticker": "AAPL", "action": "BUY", "entry_price": 0,
                 "target_price": 102.0, "stop_loss": 99.50,
                 "position_size": 6000, "confidence": "MEDIUM"}
        ok, msg = _validate_trade(trade)
        assert not ok


# ── _apply_confidence_sizing ────────────────────────────────────────────────

class TestConfidenceSizing:

    def test_high_confidence_gets_max_size(self):
        trade = make_trade(confidence="HIGH", size=5000)
        result = _apply_confidence_sizing(trade)
        assert result["position_size"] == POSITION_SIZE_BY_CONFIDENCE["HIGH"]

    def test_medium_confidence_sizing(self):
        trade = make_trade(confidence="MEDIUM", size=9999)
        result = _apply_confidence_sizing(trade)
        assert result["position_size"] == POSITION_SIZE_BY_CONFIDENCE["MEDIUM"]

    def test_low_confidence_gets_min_size(self):
        trade = make_trade(confidence="LOW", size=9999)
        result = _apply_confidence_sizing(trade)
        assert result["position_size"] == POSITION_SIZE_BY_CONFIDENCE["LOW"]

    def test_unknown_confidence_defaults_to_medium(self):
        trade = make_trade(confidence="UNKNOWN", size=9999)
        result = _apply_confidence_sizing(trade)
        assert result["position_size"] == POSITION_SIZE_BY_CONFIDENCE["MEDIUM"]

    def test_case_insensitive(self):
        trade = make_trade(confidence="high", size=1000)
        result = _apply_confidence_sizing(trade)
        assert result["position_size"] == POSITION_SIZE_BY_CONFIDENCE["HIGH"]


# ── _compute_shares ─────────────────────────────────────────────────────────

class TestComputeShares:

    def test_basic_share_calculation(self):
        trade = make_trade(size=6000, entry=100.0)
        assert _compute_shares(trade) == 60

    def test_always_at_least_1_share(self):
        trade = make_trade(size=1, entry=10000.0)
        assert _compute_shares(trade) >= 1

    def test_fractional_truncates_down(self):
        trade = make_trade(size=6000, entry=133.0)
        assert _compute_shares(trade) == 45  # 6000/133 = 45.11


# ── run (full pipeline) ─────────────────────────────────────────────────────

class TestRiskRun:

    def _make_strategy_output(self, trades):
        return {"trades": trades, "market_context": "test", "risk_note": ""}

    def test_valid_trade_approved(self):
        out = run(self._make_strategy_output([make_trade()]))
        assert len(out["approved_trades"]) == 1
        assert len(out["rejected_trades"]) == 0

    def test_invalid_trade_rejected(self):
        bad = make_trade(target=99.0)  # target below entry
        out = run(self._make_strategy_output([bad]))
        assert len(out["approved_trades"]) == 0
        assert len(out["rejected_trades"]) == 1

    def test_shares_populated_on_approval(self):
        out = run(self._make_strategy_output([make_trade(entry=100, size=3_000)]))
        trade = out["approved_trades"][0]
        assert trade["shares"] == 30
        assert trade["status"] == "PLANNED"

    def test_estimated_profit_correct(self):
        # entry=100, target=102, shares=30 → profit = 30*(102-100) = $60
        out = run(self._make_strategy_output([make_trade(entry=100, target=102, size=3_000)]))
        trade = out["approved_trades"][0]
        assert trade["estimated_profit"] == pytest.approx(60.0, abs=1.0)

    def test_max_loss_correct(self):
        # entry=100, stop=99.50, shares=30 → loss = 30*(100-99.50) = $15.00
        out = run(self._make_strategy_output([make_trade(entry=100, stop=99.50, size=3_000)]))
        trade = out["approved_trades"][0]
        assert trade["max_loss"] == pytest.approx(15.0, abs=1.0)

    def test_max_positions_cap(self):
        trades = [make_trade(ticker=f"T{i}") for i in range(MAX_POSITIONS + 5)]
        out = run(self._make_strategy_output(trades))
        assert len(out["approved_trades"]) <= MAX_POSITIONS

    def test_reward_risk_computed(self):
        out = run(self._make_strategy_output([make_trade()]))
        trade = out["approved_trades"][0]
        assert trade["reward_risk"] >= MIN_REWARD_RISK

    def test_total_estimated_profit_sum(self):
        trades = [make_trade(ticker="A"), make_trade(ticker="B")]
        out = run(self._make_strategy_output(trades))
        expected = sum(t["estimated_profit"] for t in out["approved_trades"])
        assert out["total_estimated_profit"] == pytest.approx(expected)
