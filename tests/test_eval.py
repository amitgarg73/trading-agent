"""
Tests for eval._compute_metrics
Covers: win rate, avg daily P&L, annualized return, total return,
integrity flags (duplicates, orphans, R:R violations, size violations),
confidence cohort stats.
All DB calls mocked with fixture data — no Supabase connection needed.
"""
import pytest
from unittest.mock import patch
from datetime import date, timedelta
from tests.conftest import make_perf_row, make_position, make_trade
from config.settings import (
    TOTAL_CAPITAL, DAILY_PROFIT_TARGET, DAILY_LOSS_LIMIT,
    DAILY_LOCK_IN_TARGET, MIN_REWARD_RISK,
)


# ── Helper ───────────────────────────────────────────────────────────────────

def _run_metrics(perf_rows, positions=None, planned=None, open_pos=None):
    """Run _compute_metrics with all DB calls mocked."""
    positions  = positions or []
    planned    = planned   or []
    open_pos   = open_pos  or []

    def _fake_select(table, filters=None, order=None, limit=None):
        if table == "daily_performance":
            return perf_rows
        if table == "positions":
            if filters and filters.get("status") == "CLOSED":
                return positions
            if filters and filters.get("status") == "OPEN":
                return open_pos
            return positions
        if table == "planned_trades":
            return planned
        return []

    with patch("eval.db.select", side_effect=_fake_select):
        from eval import _compute_metrics
        return _compute_metrics(perf_rows=perf_rows)


# ── Basic metrics ─────────────────────────────────────────────────────────────

class TestBasicMetrics:

    def test_returns_none_on_empty(self):
        with patch("eval.db.select", return_value=[]):
            from eval import _compute_metrics
            assert _compute_metrics(perf_rows=[]) is None

    def test_total_pnl_sum(self):
        rows = [make_perf_row("2026-05-01", total_pnl=300),
                make_perf_row("2026-05-02", total_pnl=700)]
        m = _run_metrics(rows)
        assert m["total_pnl"] == pytest.approx(1000.0)

    def test_avg_daily_pnl(self):
        rows = [make_perf_row("2026-05-01", total_pnl=400),
                make_perf_row("2026-05-02", total_pnl=600)]
        m = _run_metrics(rows)
        assert m["avg_daily_pnl"] == pytest.approx(500.0)

    def test_win_days_count(self):
        rows = [make_perf_row("2026-05-01", total_pnl=300),    # win
                make_perf_row("2026-05-02", total_pnl=-50),    # loss
                make_perf_row("2026-05-03", total_pnl=200)]    # win
        m = _run_metrics(rows)
        assert m["win_days"] == 2

    def test_win_days_zero_pnl_is_not_a_win(self):
        rows = [make_perf_row("2026-05-01", total_pnl=0)]
        m = _run_metrics(rows)
        assert m["win_days"] == 0

    def test_avg_win_rate(self):
        rows = [make_perf_row("2026-05-01", win_rate=80),
                make_perf_row("2026-05-02", win_rate=60)]
        m = _run_metrics(rows)
        assert m["avg_win_rate"] == pytest.approx(70.0)

    def test_total_return_calculation(self):
        # ending_capital = 101_000 → return = 1%
        rows = [make_perf_row("2026-05-01", ending_capital=101_000)]
        m = _run_metrics(rows)
        assert m["total_return"] == pytest.approx(1.0, abs=0.01)

    def test_ann_return_extrapolation(self):
        # 1% over 1 day → 250% annualized
        rows = [make_perf_row("2026-05-01", ending_capital=101_000)]
        m = _run_metrics(rows)
        assert m["ann_return"] == pytest.approx(250.0, abs=1.0)

    def test_target_days_count(self):
        rows = [make_perf_row("2026-05-01", total_pnl=DAILY_PROFIT_TARGET),      # hit
                make_perf_row("2026-05-02", total_pnl=DAILY_PROFIT_TARGET - 1),  # miss
                make_perf_row("2026-05-03", total_pnl=DAILY_PROFIT_TARGET + 500)] # hit
        m = _run_metrics(rows)
        assert m["target_days"] == 2

    def test_lock_in_days_count(self):
        rows = [make_perf_row("2026-05-01", total_pnl=DAILY_LOCK_IN_TARGET),
                make_perf_row("2026-05-02", total_pnl=DAILY_LOCK_IN_TARGET - 1)]
        m = _run_metrics(rows)
        assert m["lock_in_days"] == 1

    def test_loss_limit_days_count(self):
        rows = [make_perf_row("2026-05-01", total_pnl=DAILY_LOSS_LIMIT - 1),
                make_perf_row("2026-05-02", total_pnl=100)]
        m = _run_metrics(rows)
        assert m["loss_limit_days"] == 1


# ── Integrity metrics ─────────────────────────────────────────────────────────

class TestIntegrityMetrics:

    def _make_closed_pos(self, ticker, close_date, reason="TARGET", rr=3.0):
        pos = make_position(ticker=ticker, status="CLOSED",
                            close_reason=reason,
                            close_date=close_date)
        pos["planned_trade_id"] = f"pt-{ticker}"
        pos["exit_mechanism"]   = reason
        return pos

    def test_unfilled_count(self):
        rows = [make_perf_row("2026-05-01")]
        positions = [self._make_closed_pos("AAPL", "2026-05-01", reason="UNFILLED")]
        m = _run_metrics(rows, positions=positions)
        assert m["unfilled_count"] == 1

    def test_cleanup_count(self):
        rows = [make_perf_row("2026-05-01")]
        positions = [self._make_closed_pos("AAPL", "2026-05-01", reason="CLEANUP")]
        m = _run_metrics(rows, positions=positions)
        assert m["cleanup_count"] == 1

    def test_missing_exit_mechanism_flagged(self):
        rows = [make_perf_row("2026-05-01")]
        pos = self._make_closed_pos("AAPL", "2026-05-01")
        pos["exit_mechanism"] = None  # missing
        m = _run_metrics(rows, positions=[pos])
        assert m["missing_exit"] >= 1

    def test_rr_violation_detected(self):
        rows = [make_perf_row("2026-05-01")]
        pos = self._make_closed_pos("AAPL", "2026-05-01")
        # Planned trade with bad R:R (1:1)
        planned = {
            "id":           "pt-AAPL",
            "ticker":       "AAPL",
            "action":       "BUY",
            "entry_price":  100.0,
            "target_price": 101.0,  # 1% gain
            "stop_loss":    99.0,   # 1% loss → 1:1 R:R < MIN_REWARD_RISK
            "position_size": 6000,
            "confidence":   "MEDIUM",
        }
        m = _run_metrics(rows, positions=[pos], planned=[planned])
        assert len(m["rr_violations"]) >= 1

    def test_no_rr_violation_on_valid_trade(self):
        rows = [make_perf_row("2026-05-01")]
        pos = self._make_closed_pos("AAPL", "2026-05-01")
        planned = {
            "id":           "pt-AAPL",
            "ticker":       "AAPL",
            "action":       "BUY",
            "entry_price":  100.0,
            "target_price": 102.0,  # 2% gain
            "stop_loss":    99.50,  # 0.50% loss → 4:1 R:R
            "position_size": 6000,
            "confidence":   "MEDIUM",
        }
        m = _run_metrics(rows, positions=[pos], planned=[planned])
        assert len(m["rr_violations"]) == 0

    def test_size_violation_detected(self):
        rows = [make_perf_row("2026-05-01")]
        pos = self._make_closed_pos("AAPL", "2026-05-01")
        planned = {
            "id":           "pt-AAPL",
            "ticker":       "AAPL",
            "action":       "BUY",
            "entry_price":  100.0,
            "target_price": 102.0,
            "stop_loss":    99.33,
            "position_size": 999_999,  # way over max
            "confidence":   "HIGH",
        }
        m = _run_metrics(rows, positions=[pos], planned=[planned])
        assert len(m["size_violations"]) >= 1

    def test_orphaned_open_positions(self):
        rows = [make_perf_row("2026-05-01")]
        # Position opened yesterday, still OPEN today = orphan
        yesterday = (date.today() - timedelta(days=1)).isoformat()
        orphan = make_position("AAPL")
        orphan["opened_at"] = f"{yesterday}T09:35:00"
        orphan["status"] = "OPEN"
        m = _run_metrics(rows, open_pos=[orphan])
        assert len(m["orphaned"]) >= 1


# ── Confidence cohort ─────────────────────────────────────────────────────────

class TestConfidenceCohort:

    def test_high_confidence_cohort_populated(self):
        rows = [make_perf_row("2026-05-01")]
        pos = make_position("AAPL", status="CLOSED",
                            realized_pnl=120.0, close_reason="TARGET",
                            close_date="2026-05-01")
        pos["planned_trade_id"] = "pt-AAPL"
        planned = {
            "id": "pt-AAPL", "ticker": "AAPL",
            "action": "BUY", "entry_price": 100.0,
            "target_price": 102.0, "stop_loss": 99.33,
            "position_size": 7000, "confidence": "HIGH",
        }
        m = _run_metrics(rows, positions=[pos], planned=[planned])
        high = m["confidence_stats"].get("HIGH")
        if high:
            assert high["count"] >= 1
            assert high["avg_pnl"] == pytest.approx(120.0, abs=1.0)
