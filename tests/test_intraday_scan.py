"""
Tests for _maybe_run_intraday_scan in agents/intraday.py.
All external calls (DB, scanner, strategy, risk, portfolio, market_context) are mocked.
"""
import pytest
from unittest.mock import patch, MagicMock
from datetime import datetime
from config.settings import (
    MAX_POSITIONS, DAILY_LOSS_LIMIT, DAILY_BONUS_TARGET,
    INTRADAY_SCAN_UTC_START, INTRADAY_SCAN_UTC_END,
)

TODAY = "2026-05-20"
WINDOW_HOUR   = INTRADAY_SCAN_UTC_START   # 15 — first hour inside window (11 AM ET)
OUTSIDE_HOUR  = INTRADAY_SCAN_UTC_END     # 17 — first hour outside window (1 PM ET)


def _utc_now(hour: int) -> datetime:
    return datetime(2026, 5, 20, hour, 30, 0)


def _make_closed_row(realized: float) -> dict:
    return {"realized_pnl": realized, "closed_at": "2026-05-20T10:00:00", "close_reason": "TARGET"}


def _make_open_row(unrealized: float = 0.0) -> dict:
    return {"unrealized_pnl": unrealized, "status": "OPEN"}


def _run_scan(hour=WINDOW_HOUR, prior_scans=None, open_rows=None, closed_rows=None,
              candidates=None, trades=None, approved=None, opened_count=1):
    """
    Run _maybe_run_intraday_scan with full pipeline mocked.
    Returns the function result and the mock_open_positions call args.
    """
    prior_scans  = prior_scans  or []
    open_rows    = open_rows    if open_rows    is not None else []
    closed_rows  = closed_rows  if closed_rows  is not None else []
    candidates   = candidates   if candidates   is not None else [
        {"ticker": "AAPL", "technical_score": 5, "action": "BUY"}
    ]
    trades = trades if trades is not None else [
        {"ticker": "AAPL", "action": "BUY", "entry_price": 180.0,
         "target_price": 183.6, "stop_loss": 178.8, "shares": 33,
         "position_size": 5940.0, "confidence": "MEDIUM",
         "reasoning": "test", "estimated_profit": 118.8}
    ]
    approved = approved if approved is not None else trades

    def db_select(table, **kw):
        f = kw.get("filters", {})
        if f.get("scan_type") == "intraday_scan":
            return prior_scans
        if f.get("status") == "OPEN":
            return open_rows
        if f.get("status") == "CLOSED":
            return closed_rows
        return []

    mock_open_positions = MagicMock(return_value=[{"id": f"p{i}"} for i in range(opened_count)])

    with patch("agents.intraday.datetime") as mock_dt, \
         patch("core.db.select",   side_effect=db_select), \
         patch("core.db.insert",   return_value={"id": "scan-001"}), \
         patch("core.db.update"), \
         patch("agents.market_context.run",     return_value={"quiet_day": False, "summary": "flat"}), \
         patch("scanner.scanner.run_scan",      return_value=candidates), \
         patch("agents.strategy.run",           return_value={"trades": trades, "market_context": ""}), \
         patch("agents.risk.run",               return_value={"approved_trades": approved, "rejected_trades": []}), \
         patch("agents.portfolio.open_positions", mock_open_positions):
        mock_dt.utcnow.return_value = _utc_now(hour)
        from agents.intraday import _maybe_run_intraday_scan
        result = _maybe_run_intraday_scan(broker="simulation")

    return result, mock_open_positions


# ── Guard tests ──────────────────────────────────────────────────────────────

class TestIntradayScanGuards:
    """Each guard should short-circuit and return None without touching the pipeline."""

    def test_skips_before_window(self):
        result, _ = _run_scan(hour=INTRADAY_SCAN_UTC_START - 1)
        assert result is None

    def test_skips_at_or_after_window(self):
        result, _ = _run_scan(hour=OUTSIDE_HOUR)
        assert result is None

    def test_skips_if_already_scanned_today(self):
        prior = [{"scan_type": "intraday_scan", "date": TODAY}]
        result, _ = _run_scan(prior_scans=prior)
        assert result is None

    def test_skips_if_all_slots_full(self):
        open_rows = [_make_open_row() for _ in range(MAX_POSITIONS)]
        result, _ = _run_scan(open_rows=open_rows)
        assert result is None

    def test_skips_if_realized_at_loss_limit(self):
        closed_rows = [_make_closed_row(float(DAILY_LOSS_LIMIT))]
        result, _ = _run_scan(closed_rows=closed_rows)
        assert result is None

    def test_skips_if_realized_below_loss_limit(self):
        closed_rows = [_make_closed_row(float(DAILY_LOSS_LIMIT) - 50)]
        result, _ = _run_scan(closed_rows=closed_rows)
        assert result is None

    def test_skips_if_total_at_bonus_target(self):
        closed_rows = [_make_closed_row(float(DAILY_BONUS_TARGET))]
        result, _ = _run_scan(closed_rows=closed_rows)
        assert result is None


# ── Pipeline bail-out tests ───────────────────────────────────────────────────

class TestIntradayScanPipeline:
    """Guard passes; test each pipeline exit point."""

    def _good_closed_rows(self):
        return [_make_closed_row(100.0)]  # realized $100 — above loss limit, below bonus

    def test_returns_none_when_no_candidates(self):
        result, mock_open = _run_scan(candidates=[], closed_rows=self._good_closed_rows())
        assert result is None
        mock_open.assert_not_called()

    def test_returns_none_when_no_trades_selected(self):
        result, mock_open = _run_scan(trades=[], closed_rows=self._good_closed_rows())
        assert result is None
        mock_open.assert_not_called()

    def test_returns_none_when_all_rejected(self):
        result, mock_open = _run_scan(approved=[], closed_rows=self._good_closed_rows())
        assert result is None
        mock_open.assert_not_called()

    def test_happy_path_returns_result_dict(self):
        result, mock_open = _run_scan(closed_rows=self._good_closed_rows(), opened_count=2)
        assert result is not None
        assert result["opened"] == 2
        assert result["approved"] >= 1
        assert result["candidates"] >= 1
        mock_open.assert_called_once()

    def test_opens_positions_with_correct_broker(self):
        result, mock_open = _run_scan(closed_rows=self._good_closed_rows())
        call_kwargs = mock_open.call_args
        assert call_kwargs[1].get("broker") == "simulation" or call_kwargs[0][-1] == "simulation"

    def test_respects_available_slots_cap(self):
        """strategy.run should receive max_positions = available_slots, not MAX_POSITIONS."""
        open_rows = [_make_open_row() for _ in range(MAX_POSITIONS - 2)]  # 2 slots left
        # Candidates have technical_score to pass the pre-filter
        many_candidates = [
            {"ticker": f"T{i}", "technical_score": 5, "action": "BUY"}
            for i in range(5)
        ]
        many_trades = [
            {"ticker": f"T{i}", "action": "BUY", "entry_price": 100.0,
             "target_price": 102.0, "stop_loss": 99.33, "shares": 60,
             "position_size": 6000.0, "confidence": "MEDIUM",
             "reasoning": "test", "estimated_profit": 120.0}
            for i in range(2)
        ]

        def db_select(table, **kw):
            f = kw.get("filters", {})
            if f.get("scan_type") == "intraday_scan":
                return []
            if f.get("status") == "OPEN":
                return open_rows
            if f.get("status") == "CLOSED":
                return [_make_closed_row(100.0)]
            return []

        mock_strategy_run = MagicMock(return_value={"trades": many_trades, "market_context": ""})

        with patch("agents.intraday.datetime") as mock_dt, \
             patch("core.db.select",   side_effect=db_select), \
             patch("core.db.insert",   return_value={"id": "x"}), \
             patch("core.db.update"), \
             patch("agents.market_context.run", return_value={"quiet_day": False, "summary": ""}), \
             patch("scanner.scanner.run_scan",  return_value=many_candidates), \
             patch("agents.strategy.run",       mock_strategy_run), \
             patch("agents.risk.run",           return_value={"approved_trades": many_trades, "rejected_trades": []}), \
             patch("agents.portfolio.open_positions", return_value=[{"id": "p1"}, {"id": "p2"}]):
            mock_dt.utcnow.return_value = _utc_now(WINDOW_HOUR)
            from agents.intraday import _maybe_run_intraday_scan
            _maybe_run_intraday_scan(broker="simulation")

        call_kwargs = mock_strategy_run.call_args[1]
        assert call_kwargs["max_positions"] == 2

    def test_handles_scanner_exception_gracefully(self):
        """If scanner raises, scan must return None without crashing run()."""
        def db_select(table, **kw):
            f = kw.get("filters", {})
            if f.get("scan_type") == "intraday_scan":
                return []
            if f.get("status") == "OPEN":
                return []
            if f.get("status") == "CLOSED":
                return [_make_closed_row(100.0)]
            return []

        with patch("agents.intraday.datetime") as mock_dt, \
             patch("core.db.select",   side_effect=db_select), \
             patch("core.db.insert",   return_value={"id": "x"}), \
             patch("core.db.update"), \
             patch("agents.market_context.run", return_value={"quiet_day": False, "summary": ""}), \
             patch("scanner.scanner.run_scan",  side_effect=Exception("yfinance down")):
            mock_dt.utcnow.return_value = _utc_now(WINDOW_HOUR)
            from agents.intraday import _maybe_run_intraday_scan
            result = _maybe_run_intraday_scan(broker="simulation")

        assert result is None
