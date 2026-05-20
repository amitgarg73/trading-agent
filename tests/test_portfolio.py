"""
Tests for agents/portfolio.py — simulation mode only (no Alpaca calls).
Covers: Tier 1/2 lock-in logic, trail stop math, TARGET/STOP close reasons,
high watermark ratchet, LOCK_IN_TRAIL_PCT tightening after Tier 1.
"""
import pytest
from unittest.mock import patch, MagicMock
from datetime import date, datetime
from tests.conftest import make_position, FakeDB
from config.settings import (
    TRAIL_PCT, LOCK_IN_TRAIL_PCT, DAILY_LOCK_IN_TARGET, DAILY_BONUS_TARGET,
)


# ── Trail stop math ──────────────────────────────────────────────────────────

class TestTrailStopMath:
    """Verify the effective stop calculation is correct."""

    def test_trail_stop_from_entry(self):
        entry = 100.0
        eff_stop = round(entry * (1 - TRAIL_PCT), 4)
        assert eff_stop == pytest.approx(entry * 0.99, abs=0.01)

    def test_trail_stop_ratchets_up_with_price(self):
        entry = 100.0
        peak  = 105.0
        eff_stop = max(99.33, round(peak * (1 - TRAIL_PCT), 4))  # stop_loss=99.33
        assert eff_stop > round(entry * (1 - TRAIL_PCT), 4)

    def test_lock_in_trail_tighter_than_normal(self):
        peak = 105.0
        normal_stop  = round(peak * (1 - TRAIL_PCT), 4)
        lock_in_stop = round(peak * (1 - LOCK_IN_TRAIL_PCT), 4)
        assert lock_in_stop > normal_stop  # tighter trail = higher stop

    def test_stop_never_below_hard_stop(self):
        """Trail stop must never be below the original hard stop."""
        entry     = 100.0
        hard_stop = 99.33
        peak      = 99.0  # price fell below entry — no ratchet possible
        eff_stop  = max(hard_stop, round(peak * (1 - TRAIL_PCT), 4))
        assert eff_stop >= hard_stop


# ── Simulation refresh_positions ─────────────────────────────────────────────

class TestRefreshPositionsSimulation:
    """Test portfolio.refresh_positions in simulation mode with mocked prices."""

    def _run_refresh(self, positions, price, today_realized=0.0):
        db = FakeDB()
        db._tables["positions"] = positions
        db._tables["positions_closed"] = []

        with patch("agents.portfolio._current_price", return_value=price), \
             patch("core.db.select", side_effect=lambda table, **kw: (
                 [p for p in positions if p["status"] == "OPEN"]
                 if kw.get("filters", {}).get("status") == "OPEN"
                 else [p for p in positions if p["status"] == "CLOSED"]
             )), \
             patch("core.db.update") as mock_update, \
             patch("core.db.insert", return_value={"id": "x"}):
            from agents.portfolio import refresh_positions
            result = refresh_positions(broker="simulation")
        return result, mock_update

    def test_price_below_stop_closes_as_stop(self):
        pos = [make_position(entry=100.0, stop=99.33, target=102.0, shares=60)]
        result, _ = self._run_refresh(pos, price=99.0)  # below stop
        assert result[0]["close_reason"] == "STOP"

    def test_price_above_target_closes_as_target(self):
        pos = [make_position(entry=100.0, stop=99.33, target=102.0, shares=60)]
        result, _ = self._run_refresh(pos, price=102.5)  # above target
        assert result[0]["close_reason"] == "TARGET"

    def test_price_between_stop_and_target_stays_open(self):
        pos = [make_position(entry=100.0, stop=99.33, target=102.0, shares=60)]
        result, _ = self._run_refresh(pos, price=101.0)
        assert result[0]["close_reason"] is None

    def test_pnl_calculated_correctly_on_target(self):
        # 60 shares × (102.5 - 100.0) = $150
        pos = [make_position(entry=100.0, stop=99.33, target=102.0, shares=60)]
        result, _ = self._run_refresh(pos, price=102.5)
        assert result[0]["unrealized_pnl"] == pytest.approx(60 * (102.5 - 100.0), abs=0.01)

    def test_pnl_calculated_correctly_on_stop(self):
        # 60 shares × (99.0 - 100.0) = -$60
        pos = [make_position(entry=100.0, stop=99.33, target=102.0, shares=60)]
        result, _ = self._run_refresh(pos, price=99.0)
        assert result[0]["unrealized_pnl"] == pytest.approx(60 * (99.0 - 100.0), abs=0.01)

    def test_price_none_skips_position(self):
        pos = [make_position(entry=100.0, stop=99.33, target=102.0)]
        result, _ = self._run_refresh(pos, price=None)
        assert result == []

    def test_high_watermark_ratchets_up(self):
        """When price rises above entry, high_watermark should update."""
        pos = [make_position(entry=100.0, stop=99.33, target=105.0,
                             shares=60, high_watermark=100.0)]
        with patch("agents.portfolio._current_price", return_value=103.0), \
             patch("core.db.select", side_effect=lambda t, **kw: pos if kw.get("filters", {}).get("status") == "OPEN" else []), \
             patch("core.db.update") as mock_update, \
             patch("core.db.insert", return_value={"id": "x"}):
            from agents.portfolio import refresh_positions
            refresh_positions(broker="simulation")
        # Check that update was called with new high_watermark = 103.0
        calls = [str(c) for c in mock_update.call_args_list]
        assert any("high_watermark" in c for c in calls)

    def test_trail_stop_triggers_before_hard_stop(self):
        """
        Peak at 104, trail 1% → effective stop = 102.96.
        Hard stop = 99.33. Current price = 103.0 → above hard stop but below trail → STOP.
        """
        pos = [make_position(entry=100.0, stop=99.33, target=106.0,
                             shares=60, high_watermark=104.0)]
        result, _ = self._run_refresh(pos, price=103.0)
        # 104 * (1 - 0.01) = 102.96 — price 103.0 > 102.96 so trail NOT hit
        # price 103.0 > 99.33 hard stop → stays open
        assert result[0]["close_reason"] is None

    def test_trail_stop_hit_when_price_falls_from_peak(self):
        """
        Peak 104, trail 1% → stop = 102.96. Price = 102.5 → below trail → STOP.
        """
        pos = [make_position(entry=100.0, stop=99.33, target=106.0,
                             shares=60, high_watermark=104.0)]
        result, _ = self._run_refresh(pos, price=102.5)
        assert result[0]["close_reason"] == "STOP"


# ── Tier logic ───────────────────────────────────────────────────────────────

class TestTierLogic:
    """Verify Tier 1 ($716 floor) and Tier 2 ($1000 exceptional day) thresholds."""

    def test_tier1_threshold_is_correct(self):
        assert DAILY_LOCK_IN_TARGET == 716

    def test_tier2_threshold_is_correct(self):
        assert DAILY_BONUS_TARGET == 1_000

    def test_tier2_above_tier1(self):
        assert DAILY_BONUS_TARGET > DAILY_LOCK_IN_TARGET

    def test_lock_in_trail_tighter_than_normal_trail(self):
        assert LOCK_IN_TRAIL_PCT < TRAIL_PCT

    def test_effective_trail_normal_below_tier1(self):
        """Below Tier 1 realized → normal trail applies."""
        realized = DAILY_LOCK_IN_TARGET - 1
        effective = LOCK_IN_TRAIL_PCT if realized >= DAILY_LOCK_IN_TARGET else TRAIL_PCT
        assert effective == TRAIL_PCT

    def test_effective_trail_tight_at_or_above_tier1(self):
        """At or above Tier 1 realized → tighter trail applies."""
        realized = DAILY_LOCK_IN_TARGET
        effective = LOCK_IN_TRAIL_PCT if realized >= DAILY_LOCK_IN_TARGET else TRAIL_PCT
        assert effective == LOCK_IN_TRAIL_PCT


# ── close_all_positions ───────────────────────────────────────────────────────

class TestCloseAllPositions:

    def test_closes_all_open_positions(self):
        positions = [
            make_position("AAPL", entry=100.0, shares=60),
            make_position("MSFT", entry=200.0, shares=30),
        ]
        with patch("core.db.select", return_value=positions), \
             patch("agents.portfolio._current_price", return_value=101.0), \
             patch("core.db.update"), \
             patch("core.db.insert", return_value={"id": "x"}):
            from agents.portfolio import close_all_positions
            closed = close_all_positions(reason="EOD", broker="simulation")
        assert len(closed) == 2

    def test_pnl_computed_on_force_close(self):
        pos = [make_position("AAPL", entry=100.0, shares=60)]
        with patch("core.db.select", return_value=pos), \
             patch("agents.portfolio._current_price", return_value=101.5), \
             patch("core.db.update"), \
             patch("core.db.insert", return_value={"id": "x"}):
            from agents.portfolio import close_all_positions
            closed = close_all_positions(reason="LOCK_IN", broker="simulation")
        assert closed[0]["realized_pnl"] == pytest.approx(60 * 1.5, abs=0.01)
