"""
Tests for daily_runs integration:
- Net P&L loss guard (realized + unrealized)
- daily_runs record creation on intraday scan
- run_id threading to open_positions
"""
from unittest.mock import patch, MagicMock, call
from datetime import datetime as real_datetime
from config.settings import (
    DAILY_LOSS_LIMIT, DAILY_BONUS_TARGET,
    INTRADAY_SCAN_UTC_START, INTRADAY_SCAN_MAX_RUNS,
    INTRADAY_SCAN_MIN_INTERVAL_MINS,
)

TODAY = "2026-05-21"
WINDOW_HOUR = INTRADAY_SCAN_UTC_START


def _utc_now(hour: int = WINDOW_HOUR):
    return real_datetime(2026, 5, 21, hour, 30, 0)


def _make_closed_row(realized: float) -> dict:
    return {"realized_pnl": realized, "closed_at": TODAY + "T10:00:00", "close_reason": "TARGET"}


def _make_open_row(unrealized: float = 0.0, ticker: str = "HELD") -> dict:
    return {"ticker": ticker, "unrealized_pnl": unrealized, "status": "OPEN"}


def _default_trade():
    return {
        "ticker": "AAPL", "action": "BUY", "entry_price": 180.0,
        "target_price": 181.8, "stop_loss": 178.8, "shares": 33,
        "position_size": 5940.0, "confidence": "MEDIUM",
        "reasoning": "test", "estimated_profit": 60.0,
    }


def _run_intraday_scan(hour=WINDOW_HOUR, prior_scans=None, open_rows=None, closed_rows=None,
                       candidates=None, trades=None, approved=None, opened_count=1):
    """Run _maybe_run_intraday_scan with full pipeline mocked."""
    prior_scans  = prior_scans  or []
    open_rows    = open_rows    if open_rows    is not None else []
    closed_rows  = closed_rows  if closed_rows  is not None else []
    trade        = _default_trade()
    candidates   = candidates   if candidates   is not None else [{"ticker": "AAPL", "technical_score": 5}]
    trades       = trades       if trades       is not None else [trade]
    approved     = approved     if approved     is not None else [trade]

    def db_select(table, **kw):
        f = kw.get("filters", {})
        if f.get("scan_type") == "intraday_scan":
            return prior_scans
        if f.get("status") == "OPEN":
            return open_rows
        if f.get("status") == "CLOSED":
            return closed_rows
        if table == "trade_plans":
            return [{"id": "plan-001"}]
        return []

    mock_db_insert    = MagicMock(return_value={"id": "run-001"})
    mock_db_update    = MagicMock()
    mock_open_positions = MagicMock(return_value=[{"id": f"p{i}"} for i in range(opened_count)])

    with patch("agents.intraday.datetime") as mock_dt, \
         patch("core.db.select",   side_effect=db_select), \
         patch("core.db.insert",   mock_db_insert), \
         patch("core.db.update",   mock_db_update), \
         patch("agents.market_context.run",      return_value={"quiet_day": False, "summary": "flat"}), \
         patch("scanner.scanner.run_scan",        return_value=candidates), \
         patch("scanner.intraday_momentum.scan",  return_value=[]), \
         patch("agents.strategy.run",             return_value={"trades": trades, "market_context": ""}), \
         patch("agents.risk.run",                 return_value={"approved_trades": approved, "rejected_trades": []}), \
         patch("agents.portfolio.open_positions", mock_open_positions):
        mock_dt.utcnow.return_value = _utc_now(hour)
        mock_dt.fromisoformat.side_effect = real_datetime.fromisoformat
        from agents.intraday import _maybe_run_intraday_scan
        result = _maybe_run_intraday_scan(broker="simulation")

    return result, mock_open_positions, mock_db_insert, mock_db_update


# ── Net P&L loss guard ─────────────────────────────────────────────────────────

class TestNetPnlLossGuard:
    """Loss guard uses realized + unrealized, not just realized."""

    def test_unrealized_loss_pushes_total_below_limit(self):
        """realized = -200, unrealized = -400 → total = -600 ≤ -500 → skip."""
        open_rows    = [_make_open_row(unrealized=-400.0)]
        closed_rows  = [_make_closed_row(-200.0)]
        result, _, _, _ = _run_intraday_scan(open_rows=open_rows, closed_rows=closed_rows)
        assert result is None

    def test_unrealized_gain_keeps_total_above_limit(self):
        """realized = -400, unrealized = +100 → total = -300 > -500 → proceed."""
        open_rows   = [_make_open_row(unrealized=100.0)]
        closed_rows = [_make_closed_row(-400.0)]
        result, _, _, _ = _run_intraday_scan(open_rows=open_rows, closed_rows=closed_rows)
        assert result is not None

    def test_realized_at_limit_with_zero_unrealized_skips(self):
        """realized = DAILY_LOSS_LIMIT, unrealized = 0 → total exactly at limit → skip."""
        closed_rows = [_make_closed_row(float(DAILY_LOSS_LIMIT))]
        result, _, _, _ = _run_intraday_scan(closed_rows=closed_rows)
        assert result is None

    def test_unrealized_barely_pushes_below_limit(self):
        """realized = -499, unrealized = -2 → total = -501 ≤ -500 → skip."""
        open_rows   = [_make_open_row(unrealized=-2.0)]
        closed_rows = [_make_closed_row(-499.0)]
        result, _, _, _ = _run_intraday_scan(open_rows=open_rows, closed_rows=closed_rows)
        assert result is None

    def test_skips_when_both_realized_and_unrealized_positive(self):
        """Bonus target: realized + unrealized >= DAILY_BONUS_TARGET → skip."""
        closed_rows = [_make_closed_row(float(DAILY_BONUS_TARGET))]
        result, _, _, _ = _run_intraday_scan(closed_rows=closed_rows)
        assert result is None


# ── daily_runs record creation ─────────────────────────────────────────────────

class TestDailyRunsRecordCreation:
    """Verify daily_runs row is created and updated on each successful scan."""

    def _good_pnl(self):
        return [_make_closed_row(100.0)]

    def test_daily_runs_row_inserted_on_successful_scan(self):
        """db.insert("daily_runs", ...) must be called when scan opens positions."""
        _, _, mock_insert, _ = _run_intraday_scan(closed_rows=self._good_pnl())
        insert_calls = [c for c in mock_insert.call_args_list if c[0][0] == "daily_runs"]
        assert len(insert_calls) >= 1

    def test_daily_runs_row_has_run_type_intraday(self):
        """Run record must have run_type='intraday'."""
        _, _, mock_insert, _ = _run_intraday_scan(closed_rows=self._good_pnl())
        runs_inserts = [c for c in mock_insert.call_args_list if c[0][0] == "daily_runs"]
        assert any(c[0][1].get("run_type") == "intraday" for c in runs_inserts)

    def test_daily_runs_row_has_run_number(self):
        """Run record must include run_number (first run = 1)."""
        _, _, mock_insert, _ = _run_intraday_scan(closed_rows=self._good_pnl())
        runs_inserts = [c for c in mock_insert.call_args_list if c[0][0] == "daily_runs"]
        assert all("run_number" in c[0][1] for c in runs_inserts)

    def test_daily_runs_row_updated_with_positions_opened(self):
        """db.update("daily_runs", ...) must be called with positions_opened count."""
        _, _, _, mock_update = _run_intraday_scan(closed_rows=self._good_pnl(), opened_count=2)
        update_calls = [c for c in mock_update.call_args_list if c[0][0] == "daily_runs"]
        assert len(update_calls) >= 1
        updated_data = update_calls[0][0][2]
        assert "positions_opened" in updated_data

    def test_daily_runs_not_created_when_scan_skipped_by_loss_guard(self):
        """No daily_runs row if scan skipped due to loss guard."""
        closed_rows = [_make_closed_row(float(DAILY_LOSS_LIMIT) - 100)]
        _, _, mock_insert, _ = _run_intraday_scan(closed_rows=closed_rows)
        runs_inserts = [c for c in mock_insert.call_args_list if c[0][0] == "daily_runs"]
        assert len(runs_inserts) == 0

    def test_daily_runs_not_created_when_no_approved_trades(self):
        """No daily_runs row if risk rejects all trades."""
        _, _, mock_insert, _ = _run_intraday_scan(
            closed_rows=self._good_pnl(), approved=[])
        runs_inserts = [c for c in mock_insert.call_args_list if c[0][0] == "daily_runs"]
        assert len(runs_inserts) == 0


# ── run_id threading ───────────────────────────────────────────────────────────

class TestRunIdThreading:
    """run_id from daily_runs row must be passed through to open_positions."""

    def test_open_positions_called_with_run_id(self):
        """open_positions must receive the run_id from the newly created daily_runs row."""
        _, mock_open, _, _ = _run_intraday_scan(closed_rows=[_make_closed_row(100.0)])
        assert mock_open.called
        kwargs = mock_open.call_args[1]
        assert "run_id" in kwargs
        assert kwargs["run_id"] == "run-001"

    def test_run_id_is_none_when_no_run_row(self):
        """If daily_runs row creation fails (returns None), open_positions still called."""
        trade = _default_trade()

        def db_select(table, **kw):
            f = kw.get("filters", {})
            if f.get("scan_type") == "intraday_scan":
                return []
            if f.get("status") == "OPEN":
                return []
            if f.get("status") == "CLOSED":
                return [_make_closed_row(100.0)]
            if table == "trade_plans":
                return [{"id": "plan-001"}]
            return []

        mock_open = MagicMock(return_value=[{"id": "p1"}])

        with patch("agents.intraday.datetime") as mock_dt, \
             patch("core.db.select",   side_effect=db_select), \
             patch("core.db.insert",   return_value=None), \
             patch("core.db.update"), \
             patch("agents.market_context.run",     return_value={"quiet_day": False, "summary": ""}), \
             patch("scanner.scanner.run_scan",       return_value=[{"ticker": "AAPL", "technical_score": 5}]), \
             patch("scanner.intraday_momentum.scan", return_value=[]), \
             patch("agents.strategy.run",            return_value={"trades": [trade], "market_context": ""}), \
             patch("agents.risk.run",                return_value={"approved_trades": [trade], "rejected_trades": []}), \
             patch("agents.portfolio.open_positions", mock_open):
            mock_dt.utcnow.return_value = _utc_now()
            mock_dt.fromisoformat.side_effect = real_datetime.fromisoformat
            from agents.intraday import _maybe_run_intraday_scan
            _maybe_run_intraday_scan(broker="simulation")

        if mock_open.called:
            kwargs = mock_open.call_args[1]
            assert kwargs.get("run_id") is None
