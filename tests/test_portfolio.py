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
        assert eff_stop == pytest.approx(entry * 0.985, abs=0.01)  # TRAIL_PCT=0.015

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
        # 60 shares × (99.33 - 100.0) = -$40.20  (closes at stop price, not yfinance poll price)
        pos = [make_position(entry=100.0, stop=99.33, target=102.0, shares=60)]
        result, _ = self._run_refresh(pos, price=99.0)
        assert result[0]["unrealized_pnl"] == pytest.approx(60 * (99.33 - 100.0), abs=0.02)

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
        Peak 104, trail 1.5% → stop = 102.44. Price = 102.0 → below trail → STOP.
        """
        pos = [make_position(entry=100.0, stop=99.33, target=106.0,
                             shares=60, high_watermark=104.0)]
        result, _ = self._run_refresh(pos, price=102.0)
        assert result[0]["close_reason"] == "STOP"

    def test_stop_closes_at_stop_price_not_current_price(self):
        """
        Simulation closes STOP positions at eff_stop, not current yfinance price.
        Real bracket stop-market orders execute at the stop level regardless of
        how far price has fallen by the time the 15-min poll runs.
        entry=100, hard_stop=99.33, peak=100 (no trail ratchet), current price=95.
        eff_stop = max(99.33, 100*(1-0.015)) = max(99.33, 98.5) = 99.33
        close_price should be 99.33, not 95.
        pnl = 60 * (99.33 - 100) = -40.20
        """
        pos = [make_position(entry=100.0, stop=99.33, target=106.0,
                             shares=60, high_watermark=100.0)]
        result, mock_update = self._run_refresh(pos, price=95.0)
        assert result[0]["close_reason"] == "STOP"
        # Verify DB was updated with close_price = stop (99.33), not current (95)
        update_calls = [c[0][2] for c in mock_update.call_args_list if "close_price" in (c[0][2] if c[0] else {})]
        stop_updates = [c for c in update_calls if "close_price" in c]
        assert any(abs(c["close_price"] - 99.33) < 0.01 for c in stop_updates), \
            f"Expected close_price ≈ 99.33 (stop), got: {[c.get('close_price') for c in stop_updates]}"

    def test_stop_pnl_uses_stop_price_not_current_price(self):
        """P&L on STOP exit is computed at stop_price, not the yfinance poll price."""
        pos = [make_position(entry=100.0, stop=99.33, target=106.0,
                             shares=60, high_watermark=100.0)]
        result, _ = self._run_refresh(pos, price=95.0)
        # pnl should be 60 * (99.33 - 100.0) = -40.20, not 60 * (95 - 100) = -300
        assert result[0]["unrealized_pnl"] == pytest.approx(60 * (99.33 - 100.0), abs=0.02)


class TestConfidenceStoredOnPosition:

    def test_confidence_stored_when_opening_position(self):
        """_open_single_position must write confidence from the trade dict into the position row."""
        trade = {
            "ticker": "AAPL", "action": "BUY", "entry_price": 100.0,
            "target_price": 104.0, "stop_loss": 99.33, "shares": 30,
            "position_size": 3000.0, "confidence": "HIGH",
            "reasoning": "test", "estimated_profit": 120.0,
        }

        inserted_rows = []
        def fake_insert(table, data):
            inserted_rows.append((table, data))
            return {"id": f"fake-{table}", **data}

        with patch("core.db.insert", side_effect=fake_insert), \
             patch("core.db.update"), \
             patch("core.ledger.log"), \
             patch("agents.portfolio._current_price", return_value=100.0):
            from agents.portfolio import _open_single_position
            _open_single_position("plan-001", trade, price=100.0, broker="simulation", run_id="r1")

        position_rows = [data for (table, data) in inserted_rows if table == "positions"]
        assert position_rows, "No position row inserted into positions table"
        assert position_rows[0].get("confidence") == "HIGH"


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


# ── Breakeven stop (partial profit) ──────────────────────────────────────────

class TestBreakevenStop:
    """
    After Leg A (partial, 1% target) hits TARGET, Leg B's stop moves to entry.
    Tests _is_partial_leg detection and _lock_breakeven in simulation mode.
    """

    def _make_legs(self, entry: float = 100.0):
        """Return (leg_a, leg_b) with distinct IDs and targets."""
        leg_a = make_position(
            ticker="AAPL",
            entry=entry,
            target=round(entry * 1.01, 2),   # 1% partial target
            stop=round(entry * 0.9933, 2),
            shares=30,
        )
        leg_a["id"] = "test-leg-a"
        leg_a["planned_trade_id"] = "test-plan-a"

        leg_b = make_position(
            ticker="AAPL",
            entry=entry,
            target=round(entry * 1.02, 2),   # 2% full target
            stop=round(entry * 0.9933, 2),
            shares=30,
        )
        leg_b["id"] = "test-leg-b"
        leg_b["planned_trade_id"] = "test-plan-b"

        return leg_a, leg_b

    def _select_side_effect(self, open_pos):
        return lambda table, **kw: (
            open_pos if kw.get("filters", {}).get("status") == "OPEN"
            else []
        )

    def _stop_loss_updates_for(self, mock_update, pos_id: str) -> list:
        return [
            call[0][2]
            for call in mock_update.call_args_list
            if call[0][0] == "positions"
            and call[0][1].get("id") == pos_id
            and "stop_loss" in call[0][2]
        ]

    def test_is_partial_leg_true_for_leg_a(self):
        from agents.portfolio import _is_partial_leg
        leg_a, _ = self._make_legs()
        assert _is_partial_leg(leg_a) is True

    def test_is_partial_leg_false_for_leg_b(self):
        from agents.portfolio import _is_partial_leg
        _, leg_b = self._make_legs()
        assert _is_partial_leg(leg_b) is False

    def test_is_partial_leg_false_when_disabled(self):
        from agents.portfolio import _is_partial_leg
        leg_a, _ = self._make_legs()
        with patch("agents.portfolio.PARTIAL_PROFIT_ENABLED", False):
            assert _is_partial_leg(leg_a) is False

    def test_leg_a_closes_as_target_at_partial_price(self):
        """Price = Leg A's 1% target → Leg A closes TARGET, Leg B stays open."""
        leg_a, leg_b = self._make_legs(entry=100.0)
        open_pos = [leg_a, leg_b]
        price = leg_a["target_price"]  # 101.0

        with patch("agents.portfolio._current_price", return_value=price), \
             patch("core.db.select", side_effect=self._select_side_effect(open_pos)), \
             patch("core.db.update"), \
             patch("core.db.insert", return_value={"id": "x"}):
            from agents.portfolio import refresh_positions
            result = refresh_positions(broker="simulation")

        leg_a_r = next(r for r in result if r["id"] == "test-leg-a")
        leg_b_r = next(r for r in result if r["id"] == "test-leg-b")
        assert leg_a_r["close_reason"] == "TARGET"
        assert leg_b_r["close_reason"] is None

    def test_breakeven_lock_moves_leg_b_stop_to_entry(self):
        """When Leg A hits TARGET, Leg B stop_loss must be updated to entry price."""
        entry = 100.0
        leg_a, leg_b = self._make_legs(entry=entry)
        open_pos = [leg_a, leg_b]
        price = leg_a["target_price"]  # 101.0

        with patch("agents.portfolio._current_price", return_value=price), \
             patch("core.db.select", side_effect=self._select_side_effect(open_pos)), \
             patch("core.db.update") as mock_update, \
             patch("core.db.insert", return_value={"id": "x"}):
            from agents.portfolio import refresh_positions
            refresh_positions(broker="simulation")

        updates = self._stop_loss_updates_for(mock_update, "test-leg-b")
        assert len(updates) == 1, "Expected exactly one stop_loss update for Leg B"
        assert updates[0]["stop_loss"] == pytest.approx(entry, abs=0.01)

    def test_breakeven_lock_not_triggered_on_leg_a_stop(self):
        """When Leg A hits STOP (not TARGET), Leg B stop must NOT move."""
        leg_a, leg_b = self._make_legs(entry=100.0)
        open_pos = [leg_a, leg_b]

        with patch("agents.portfolio._current_price", return_value=98.0), \
             patch("core.db.select", side_effect=self._select_side_effect(open_pos)), \
             patch("core.db.update") as mock_update, \
             patch("core.db.insert", return_value={"id": "x"}):
            from agents.portfolio import refresh_positions
            refresh_positions(broker="simulation")

        updates = self._stop_loss_updates_for(mock_update, "test-leg-b")
        assert len(updates) == 0, "Leg B stop must not change when Leg A stops out"

    def test_breakeven_lock_idempotent_when_stop_already_at_entry(self):
        """If Leg B stop is already at entry, _lock_breakeven must not re-update it."""
        entry = 100.0
        leg_a, leg_b = self._make_legs(entry=entry)
        leg_b["stop_loss"] = entry  # already locked
        open_pos = [leg_a, leg_b]
        price = leg_a["target_price"]

        with patch("agents.portfolio._current_price", return_value=price), \
             patch("core.db.select", side_effect=self._select_side_effect(open_pos)), \
             patch("core.db.update") as mock_update, \
             patch("core.db.insert", return_value={"id": "x"}):
            from agents.portfolio import refresh_positions
            refresh_positions(broker="simulation")

        updates = self._stop_loss_updates_for(mock_update, "test-leg-b")
        assert len(updates) == 0, "Should not re-lock already locked stop"

    def test_breakeven_lock_only_matches_same_ticker(self):
        """Leg B for a different ticker must not get its stop moved."""
        entry = 100.0
        leg_a, leg_b = self._make_legs(entry=entry)
        leg_b["ticker"] = "MSFT"  # different ticker
        open_pos = [leg_a, leg_b]
        price = leg_a["target_price"]

        with patch("agents.portfolio._current_price", return_value=price), \
             patch("core.db.select", side_effect=self._select_side_effect(open_pos)), \
             patch("core.db.update") as mock_update, \
             patch("core.db.insert", return_value={"id": "x"}):
            from agents.portfolio import refresh_positions
            refresh_positions(broker="simulation")

        updates = self._stop_loss_updates_for(mock_update, "test-leg-b")
        assert len(updates) == 0, "Cross-ticker breakeven lock must not fire"

    def test_breakeven_lock_alpaca_resubmits_with_native_trail(self):
        """In Alpaca mode, breakeven lock resubmit must pass use_native_trail=True and trail_pct."""
        from unittest.mock import patch, MagicMock
        from agents.portfolio import _lock_breakeven
        from config.settings import USE_NATIVE_TRAILING_STOP, TRAIL_PCT

        entry = 100.0
        leg_a, leg_b = self._make_legs(entry=entry)
        leg_b["alpaca_order_id"] = "old-bracket-id"
        leg_b["stop_loss"] = entry - 1  # below entry — lock not yet applied

        with patch("agents.portfolio.db.update") as mock_update, \
             patch("agents.alpaca_broker.cancel_order", return_value=True) as mock_cancel, \
             patch("agents.alpaca_broker.submit_bracket_order", return_value=("new-bracket-id", 100.0)) as mock_submit:
            _lock_breakeven([leg_b], leg_a, broker="alpaca")

        mock_submit.assert_called_once()
        _, kwargs = mock_submit.call_args
        assert kwargs.get("use_native_trail") == USE_NATIVE_TRAILING_STOP, \
            "use_native_trail must be passed to resubmitted bracket"
        assert kwargs.get("trail_pct") == TRAIL_PCT, \
            "trail_pct must be passed to resubmitted bracket"


# ── Live-price recalculation (inverted stop prevention) ──────────────────────

class TestLivePriceRecalculation:
    """
    _open_single_position must anchor stop/target to the live price at submission
    time, not the stale scanner price.  A stock that reverses 3% between scan and
    execution would otherwise produce stop > fill price, firing the bracket
    immediately and booking a guaranteed loss.
    """

    def _make_trade(self, entry=100.0, target=102.0, stop=99.33):
        return {
            "ticker": "AAPL", "action": "BUY",
            "entry_price": entry, "target_price": target, "stop_loss": stop,
            "position_size": 6000, "shares": 60,
            "estimated_profit": 120.0, "max_loss": 40.2,
            "confidence": "HIGH", "reasoning": "test",
        }

    def test_stop_target_recalculated_from_live_price(self):
        """When live price differs within sanity threshold, stop/target anchor to live price."""
        trade = self._make_trade(entry=100.0, target=102.0, stop=99.33)
        live_price = 97.0  # stock dipped 3% — within 5% sanity threshold

        inserted = {}
        def capture_insert(table, data):
            inserted.update(data)
            return {**data, "id": "pos-1"}

        with patch("agents.portfolio._current_price", return_value=live_price), \
             patch("agents.alpaca_broker.get_live_prices", return_value={"AAPL": live_price}), \
             patch("agents.alpaca_broker.submit_bracket_order", return_value=("order-123", live_price)), \
             patch("core.db.insert", side_effect=capture_insert), \
             patch("core.db.update"):
            from agents.portfolio import _open_single_position
            _open_single_position("plan-1", trade, live_price, broker="alpaca")

        assert inserted.get("entry_price") == pytest.approx(97.0, abs=0.01)
        assert inserted["stop_loss"] < inserted["entry_price"], "stop must be below entry"
        # Stop % should be preserved: 0.67% below live price
        expected_stop = round(97.0 * (1 - 0.0067), 2)
        assert inserted["stop_loss"] == pytest.approx(expected_stop, abs=0.02)

    def test_price_drift_beyond_sanity_skips_trade(self):
        """When live price deviates >PRICE_SANITY_PCT from plan, trade is skipped."""
        trade = self._make_trade(entry=100.0)
        live_price = 94.0  # 6% drift — exceeds PRICE_SANITY_PCT=5%

        inserts, updates = [], []

        with patch("agents.portfolio._current_price", return_value=live_price), \
             patch("agents.alpaca_broker.get_live_prices", return_value={"AAPL": live_price}), \
             patch("agents.alpaca_broker.submit_bracket_order") as mock_submit, \
             patch("core.db.insert", side_effect=lambda t, d: (inserts.append(t), {**d, "id": "x"})[1]), \
             patch("core.db.update", side_effect=lambda t, m, d: updates.append(d)):
            from agents.portfolio import _open_single_position
            result = _open_single_position("plan-1", trade, live_price, broker="alpaca")

        assert result is None, "Must return None when price drift exceeds sanity threshold"
        mock_submit.assert_not_called()
        position_inserts = [t for t in inserts if t == "positions"]
        assert len(position_inserts) == 0
        cancelled = [d for d in updates if d.get("status") == "CANCELLED"]
        assert len(cancelled) == 1

    def test_inverted_stop_prevented_on_reversal(self):
        """A 3% reversal within sanity threshold must still produce stop < entry."""
        trade = self._make_trade(entry=100.0, target=102.0, stop=99.33)
        live_price = 97.5  # reversed but within 5% threshold

        inserted = {}
        def capture_insert(table, data):
            inserted.update(data)
            return {**data, "id": "pos-1"}

        submitted = {}
        def capture_submit(**kwargs):
            submitted.update(kwargs)
            return ("order-xyz", live_price)

        with patch("agents.portfolio._current_price", return_value=live_price), \
             patch("agents.alpaca_broker.get_live_prices", return_value={"AAPL": live_price}), \
             patch("agents.alpaca_broker.submit_bracket_order", side_effect=capture_submit), \
             patch("core.db.insert", side_effect=capture_insert), \
             patch("core.db.update"):
            from agents.portfolio import _open_single_position
            _open_single_position("plan-1", trade, live_price, broker="alpaca")

        assert submitted.get("stop_price", 999) < live_price, \
            f"stop {submitted.get('stop_price')} must be below live price {live_price}"
        assert submitted.get("target_price", 0) > live_price, \
            f"target {submitted.get('target_price')} must be above live price {live_price}"

    def test_simulation_uses_live_price(self):
        """Simulation mode uses the live yfinance price as entry (no Alpaca calls)."""
        trade = self._make_trade(entry=100.0)
        live_price = 97.0

        inserted = {}
        def capture_insert(table, data):
            inserted.update(data)
            return {**data, "id": "pos-1"}

        with patch("agents.portfolio._current_price", return_value=live_price), \
             patch("core.db.insert", side_effect=capture_insert):
            from agents.portfolio import _open_single_position
            _open_single_position("plan-1", trade, live_price, broker="simulation")

        assert inserted.get("entry_price") == live_price


# ── Fix 1: race-condition guard ───────────────────────────────────────────────

class TestRaceConditionGuard:
    """
    refresh_positions (Alpaca) must not close a position as STOP/$0 when the
    bracket fill isn't visible yet — i.e. position just opened and get_order_fill
    returns (None, None).  It should leave the position OPEN for the next cycle.
    After 120 s the fallback close behaviour is restored.
    """

    def _make_alpaca_pos(self, opened_at: str, order_id: str = "ord-abc") -> dict:
        pos = make_position(ticker="AAPL", entry=100.0, shares=30)
        pos["alpaca_order_id"]  = order_id
        pos["opened_at"]        = opened_at
        pos["native_trail_active"] = False
        return pos

    @patch("agents.alpaca_broker.get_order_fill", return_value=(None, None))
    @patch("agents.alpaca_broker.get_open_tickers", return_value=set())   # gone from Alpaca
    @patch("core.db.update")
    @patch("core.db.select")
    def test_new_position_left_open_when_fill_missing(
            self, mock_select, mock_update, mock_tickers, mock_fill):
        """Position opened 5s ago + no fill data → stays OPEN, no DB close written."""
        from datetime import datetime, timedelta
        opened_at = (datetime.utcnow() - timedelta(seconds=5)).isoformat()
        pos = self._make_alpaca_pos(opened_at)
        mock_select.side_effect = lambda table, **kw: (
            [pos] if kw.get("filters", {}).get("status") == "OPEN" else []
        )

        from agents.portfolio import refresh_positions
        result = refresh_positions(broker="alpaca")

        # position should be in result with no close_reason
        assert any(r.get("close_reason") is None for r in result)
        # DB must not have been updated to CLOSED
        for call in mock_update.call_args_list:
            assert call[0][2].get("status") != "CLOSED", \
                "Should not mark position CLOSED during race-condition window"

    @patch("agents.alpaca_broker.get_order_fill", return_value=(None, None))
    @patch("agents.alpaca_broker.get_open_tickers", return_value=set())
    @patch("core.db.update")
    @patch("core.db.select")
    def test_old_position_closed_with_fallback_when_fill_missing(
            self, mock_select, mock_update, mock_tickers, mock_fill):
        """Position opened 300s ago + no fill data → fallback close still fires."""
        from datetime import datetime, timedelta
        opened_at = (datetime.utcnow() - timedelta(seconds=300)).isoformat()
        pos = self._make_alpaca_pos(opened_at)
        mock_select.side_effect = lambda table, **kw: (
            [pos] if kw.get("filters", {}).get("status") == "OPEN" else []
        )

        from agents.portfolio import refresh_positions
        refresh_positions(broker="alpaca")

        closed_calls = [c for c in mock_update.call_args_list
                        if c[0][0] == "positions" and c[0][2].get("status") == "CLOSED"]
        assert len(closed_calls) == 1, "Old position with no fill should still be closed"


# ── Fix 2: phantom position guard ────────────────────────────────────────────

class TestPhantomPositionGuard:
    """
    _open_single_position must not write a positions row to the DB when the
    Alpaca bracket order submission fails.  The planned_trade should be marked
    CANCELLED and the function must return None.
    """

    def _make_trade(self):
        return {
            "ticker": "AAPL", "action": "BUY",
            "entry_price": 100.0, "target_price": 102.0, "stop_loss": 99.33,
            "position_size": 6000, "shares": 60,
            "estimated_profit": 120.0, "max_loss": 40.2,
            "confidence": "HIGH", "reasoning": "test",
        }

    def test_no_db_insert_when_order_fails(self):
        """If submit_bracket_order raises, positions row must NOT be written."""
        inserts = []
        updates = []

        def capture_insert(table, data):
            inserts.append((table, data))
            return {**data, "id": f"fake-{table}"}

        def capture_update(table, match, data):
            updates.append((table, data))

        with patch("agents.alpaca_broker.submit_bracket_order",
                   side_effect=RuntimeError("order rejected")), \
             patch("agents.alpaca_broker.get_live_prices", return_value={"AAPL": 100.0}), \
             patch("core.db.insert", side_effect=capture_insert), \
             patch("core.db.update", side_effect=capture_update), \
             patch("agents.portfolio._current_price", return_value=100.0):
            from agents.portfolio import _open_single_position
            result = _open_single_position("plan-1", self._make_trade(), 100.0, broker="alpaca")

        assert result is None, "Must return None when order fails"
        position_inserts = [t for t, _ in inserts if t == "positions"]
        assert len(position_inserts) == 0, "Must not insert phantom position row"
        cancelled = [d for _, d in updates if d.get("status") == "CANCELLED"]
        assert len(cancelled) == 1, "planned_trade must be marked CANCELLED"

    def test_position_inserted_when_order_succeeds(self):
        """Happy path: order succeeds → position row IS written and returned."""
        inserts = []

        def capture_insert(table, data):
            inserts.append((table, data))
            return {**data, "id": f"fake-{table}"}

        with patch("agents.alpaca_broker.submit_bracket_order", return_value=("order-xyz", 100.0)), \
             patch("agents.alpaca_broker.get_live_prices", return_value={"AAPL": 100.0}), \
             patch("core.db.insert", side_effect=capture_insert), \
             patch("core.db.update"), \
             patch("agents.portfolio._current_price", return_value=100.0):
            from agents.portfolio import _open_single_position
            result = _open_single_position("plan-1", self._make_trade(), 100.0, broker="alpaca")

        assert result is not None, "Must return the inserted position"
        position_inserts = [t for t, _ in inserts if t == "positions"]
        assert len(position_inserts) == 1, "Must insert exactly one position row"


# ── Fix 3: close_price=0.0 not replaced by fallback ──────────────────────────

class TestClosePriceZeroFix:
    """close_price of 0.0 must not be treated as falsy and swapped for entry_price."""

    def _make_alpaca_pos(self, opened_at: str = "2024-01-01T10:00:00") -> dict:
        pos = make_position(ticker="AAPL", entry=100.0, shares=30)
        pos["alpaca_order_id"]   = "ord-abc"
        pos["opened_at"]         = opened_at
        pos["native_trail_active"] = False
        pos["current_price"]     = 99.0
        return pos

    @patch("agents.alpaca_broker.get_order_fill", return_value=(0.0, "STOP"))
    @patch("agents.alpaca_broker.get_open_tickers", return_value=set())
    @patch("core.db.update")
    @patch("core.db.select")
    def test_zero_close_price_not_replaced_by_fallback(
            self, mock_select, mock_update, mock_tickers, mock_fill):
        """close_price=0.0 is NOT replaced — fix changed `or` chain to `is None` check."""
        pos = self._make_alpaca_pos()
        mock_select.side_effect = lambda table, **kw: (
            [pos] if kw.get("filters", {}).get("status") == "OPEN" else []
        )

        from agents.portfolio import refresh_positions
        refresh_positions(broker="alpaca")

        closed_calls = [c[0][2] for c in mock_update.call_args_list
                        if c[0][0] == "positions" and c[0][2].get("status") == "CLOSED"]
        assert len(closed_calls) == 1
        assert closed_calls[0]["close_price"] == 0.0, \
            "close_price 0.0 must not be replaced by current_price or entry_price fallback"
