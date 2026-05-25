"""
Tests for Gap 5 (EOD dedup) and Gap 6 (run observability + alerts) in
trading-agent/orchestrator.py and core/alerts.py.
"""
import pytest
from unittest.mock import patch, MagicMock
from datetime import date

TODAY = date.today().isoformat()


# ── Gap 5: EOD dedup ──────────────────────────────────────────────────────────

class TestEODDedup:

    def test_eod_skips_when_already_ran(self):
        """EOD bails early when run_eod_started record exists for today."""
        with patch("orchestrator.db") as mock_db, \
             patch("orchestrator._is_trading_day", return_value=True), \
             patch("orchestrator._is_halted", return_value=False):
            def _sel(table, **kw):
                f = kw.get("filters", {})
                if table == "scan_results" and f.get("scan_type") == "run_eod_started":
                    return [{"id": 1}]
                return []
            mock_db.select.side_effect = _sel
            from orchestrator import eod
            eod(broker="simulation")
            # _log_run (insert) must NOT have been called
            mock_db.insert.assert_not_called()

    def test_eod_proceeds_when_no_prior_run(self):
        """EOD runs and inserts run_eod_started when no prior run exists."""
        with patch("orchestrator.db") as mock_db, \
             patch("orchestrator._is_trading_day", return_value=True), \
             patch("orchestrator._is_halted", return_value=False), \
             patch("orchestrator.performance") as mock_perf, \
             patch("orchestrator.daily_summary"):
            mock_db.select.return_value = []
            mock_db.insert.return_value = {}
            mock_perf.run.return_value = None   # no trades today
            from orchestrator import eod
            eod(broker="simulation")
            assert mock_db.insert.called
            scan_types = [
                c[0][1].get("scan_type", "") for c in mock_db.insert.call_args_list
                if len(c[0]) >= 2 and isinstance(c[0][1], dict)
            ]
            assert any("run_eod_started" in s for s in scan_types)


# ── Gap 6: Run logging ────────────────────────────────────────────────────────

class TestRunLogging:

    def test_log_run_inserts_correct_fields(self):
        """_log_run writes a scan_results record with correct scan_type and results."""
        with patch("orchestrator.db") as mock_db:
            mock_db.insert.return_value = {}
            from orchestrator import _log_run
            _log_run("eod", "started", {"info": "test"})
            mock_db.insert.assert_called_once()
            table, payload = mock_db.insert.call_args[0]
            assert table == "scan_results"
            assert payload["scan_type"] == "run_eod_started"
            assert payload["results"]["mode"] == "eod"
            assert payload["results"]["status"] == "started"
            assert payload["results"]["info"] == "test"

    def test_log_run_swallows_db_error(self):
        """_log_run doesn't raise when db.insert fails."""
        with patch("orchestrator.db") as mock_db:
            mock_db.insert.side_effect = Exception("DB down")
            from orchestrator import _log_run
            _log_run("eod", "failed")   # must not raise

    def test_eod_logs_failed_and_reraises(self):
        """EOD inserts run_eod_failed and re-raises on unexpected exception."""
        with patch("orchestrator.db") as mock_db, \
             patch("orchestrator._is_trading_day", return_value=True), \
             patch("orchestrator._is_halted", return_value=False), \
             patch("orchestrator.performance") as mock_perf, \
             patch("orchestrator.send_alert"):
            mock_db.select.return_value = []
            mock_db.insert.return_value = {}
            mock_perf.run.side_effect = RuntimeError("crash")
            from orchestrator import eod
            with pytest.raises(RuntimeError, match="crash"):
                eod(broker="simulation")
            scan_types = [
                c[0][1].get("scan_type", "") for c in mock_db.insert.call_args_list
                if len(c[0]) >= 2 and isinstance(c[0][1], dict)
            ]
            assert any("run_eod_failed" in s for s in scan_types)

    def test_eod_logs_completed_on_success(self):
        """EOD inserts run_eod_completed after a successful no-trades-today run."""
        with patch("orchestrator.db") as mock_db, \
             patch("orchestrator._is_trading_day", return_value=True), \
             patch("orchestrator._is_halted", return_value=False), \
             patch("orchestrator.performance") as mock_perf, \
             patch("orchestrator.daily_summary"):
            mock_db.select.return_value = []
            mock_db.insert.return_value = {}
            mock_perf.run.return_value = None  # no trades today
            from orchestrator import eod
            eod(broker="simulation")
            scan_types = [
                c[0][1].get("scan_type", "") for c in mock_db.insert.call_args_list
                if len(c[0]) >= 2 and isinstance(c[0][1], dict)
            ]
            assert any("run_eod_completed" in s for s in scan_types)


# ── Gap 6: EOD alerts ─────────────────────────────────────────────────────────

class TestEODAlerts:

    def test_alert_sent_when_positions_still_open_after_eod(self):
        """Alert fires when Alpaca positions remain open after EOD performance.run()."""
        open_pos  = [{"ticker": "MSFT", "id": 42}]
        still_pos = [{"ticker": "MSFT", "id": 42}]  # same position still open

        def _sel(table, **kw):
            f = kw.get("filters", {})
            if table == "positions" and f.get("status") == "OPEN":
                return still_pos
            return []

        _perf_record = {
            "total_pnl": 50.0, "ending_capital": 50050.0,
            "total_trades": 1, "win_rate": 100,
            "best_trade_ticker": "MSFT", "best_trade_pnl": 50.0,
            "worst_trade_ticker": "MSFT", "worst_trade_pnl": 50.0,
        }
        with patch("orchestrator.db") as mock_db, \
             patch("orchestrator._is_trading_day", return_value=True), \
             patch("orchestrator._is_halted", return_value=False), \
             patch("orchestrator.performance") as mock_perf, \
             patch("orchestrator.daily_summary"), \
             patch("orchestrator.send_alert") as mock_alert:
            mock_db.select.side_effect = _sel
            mock_db.insert.return_value = {}
            mock_perf.run.return_value = _perf_record
            from orchestrator import eod
            eod(broker="alpaca")
            mock_alert.assert_called_once()
            assert "FAILED" in mock_alert.call_args[0][0] or "still open" in mock_alert.call_args[0][0].lower()

    def test_alert_sent_on_eod_crash(self):
        """send_alert is called when the EOD run crashes with an unexpected exception."""
        with patch("orchestrator.db") as mock_db, \
             patch("orchestrator._is_trading_day", return_value=True), \
             patch("orchestrator._is_halted", return_value=False), \
             patch("orchestrator.performance") as mock_perf, \
             patch("orchestrator.send_alert") as mock_alert:
            mock_db.select.return_value = []
            mock_db.insert.return_value = {}
            mock_perf.run.side_effect = ValueError("unexpected crash")
            from orchestrator import eod
            with pytest.raises(ValueError):
                eod(broker="simulation")
            mock_alert.assert_called_once()
            assert "FAILED" in mock_alert.call_args[0][0]
