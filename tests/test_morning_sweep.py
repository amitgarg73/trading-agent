"""
Tests for RF-4 in Strategy A: morning sweep with one retry and halt-on-failure.

_sweep_and_verify() closes overnight Alpaca positions before trading begins.
Returns True if cleared within 2 attempts. Returns False if positions remain —
halt flag written to DB, ledger event logged, and alert sent with recovery steps.

premarket() returns early (no market context run) when sweep fails.
"""
import pytest
from unittest.mock import patch, MagicMock, call


class TestMorningSweepA:

    def test_no_overnight_positions(self):
        """Empty Alpaca account → returns True immediately, no close or cancel calls."""
        with patch("agents.alpaca_broker.get_open_tickers", return_value=[]), \
             patch("agents.alpaca_broker.cancel_all_orders") as mock_cancel, \
             patch("agents.alpaca_broker.close_all_positions") as mock_close, \
             patch("time.sleep"):
            from orchestrator import _sweep_and_verify
            result = _sweep_and_verify()

        assert result is True
        mock_cancel.assert_not_called()
        mock_close.assert_not_called()

    def test_clears_on_first_attempt(self):
        """Positions present, cleared after first close+sleep → returns True, no alert."""
        side_effects = [["AAPL"], []]
        with patch("agents.alpaca_broker.get_open_tickers", side_effect=side_effects), \
             patch("agents.alpaca_broker.cancel_all_orders"), \
             patch("agents.alpaca_broker.close_all_positions") as mock_close, \
             patch("orchestrator.send_alert") as mock_alert, \
             patch("time.sleep"):
            from orchestrator import _sweep_and_verify
            result = _sweep_and_verify()

        assert result is True
        mock_close.assert_called_once()
        mock_alert.assert_not_called()

    def test_clears_on_second_attempt(self):
        """Still dirty after first close, clear after second → returns True, no alert."""
        side_effects = [["AAPL"], ["AAPL"], []]
        with patch("agents.alpaca_broker.get_open_tickers", side_effect=side_effects), \
             patch("agents.alpaca_broker.cancel_all_orders"), \
             patch("agents.alpaca_broker.close_all_positions") as mock_close, \
             patch("orchestrator.send_alert") as mock_alert, \
             patch("time.sleep"):
            from orchestrator import _sweep_and_verify
            result = _sweep_and_verify()

        assert result is True
        assert mock_close.call_count == 2
        mock_alert.assert_not_called()

    def test_fails_after_two_attempts_returns_false(self):
        """Dirty after both attempts → returns False."""
        side_effects = [["AAPL", "TSLA"], ["AAPL", "TSLA"], ["AAPL", "TSLA"]]
        with patch("agents.alpaca_broker.get_open_tickers", side_effect=side_effects), \
             patch("agents.alpaca_broker.cancel_all_orders"), \
             patch("agents.alpaca_broker.close_all_positions"), \
             patch("core.ledger.log"), \
             patch("core.db.insert"), \
             patch("orchestrator.send_alert"), \
             patch("time.sleep"):
            from orchestrator import _sweep_and_verify
            result = _sweep_and_verify()

        assert result is False

    def test_fails_after_two_attempts_sends_alert_with_details(self):
        """Alert includes ticker list, Alpaca dashboard URL, and restart workflow URL."""
        side_effects = [["AAPL"], ["AAPL"], ["AAPL"]]
        with patch("agents.alpaca_broker.get_open_tickers", side_effect=side_effects), \
             patch("agents.alpaca_broker.cancel_all_orders"), \
             patch("agents.alpaca_broker.close_all_positions"), \
             patch("core.ledger.log"), \
             patch("core.db.insert"), \
             patch("orchestrator.send_alert") as mock_alert, \
             patch("time.sleep"):
            from orchestrator import _sweep_and_verify
            _sweep_and_verify()

        mock_alert.assert_called_once()
        subject, body = mock_alert.call_args[0]
        assert "HALTED" in subject
        assert "AAPL" in body
        assert "app.alpaca.markets" in body
        assert "restart.yml" in body
        assert "STEP 1" in body
        assert "STEP 2" in body

    def test_fails_after_two_attempts_sets_halt_flag(self):
        """Sweep failure inserts scan_type='halt_flag' row into scan_results."""
        side_effects = [["MSFT"], ["MSFT"], ["MSFT"]]
        with patch("agents.alpaca_broker.get_open_tickers", side_effect=side_effects), \
             patch("agents.alpaca_broker.cancel_all_orders"), \
             patch("agents.alpaca_broker.close_all_positions"), \
             patch("core.ledger.log"), \
             patch("core.db.insert") as mock_insert, \
             patch("orchestrator.send_alert"), \
             patch("time.sleep"):
            from orchestrator import _sweep_and_verify
            _sweep_and_verify()

        halt_call = next(
            (c for c in mock_insert.call_args_list
             if c[0][0] == "scan_results" and c[0][1].get("scan_type") == "halt_flag"),
            None,
        )
        assert halt_call is not None, "Expected halt_flag insert into scan_results"

    def test_fails_after_two_attempts_logs_to_ledger(self):
        """Sweep failure writes event_type='sweep_failed' to local ledger."""
        side_effects = [["NVDA"], ["NVDA"], ["NVDA"]]
        with patch("agents.alpaca_broker.get_open_tickers", side_effect=side_effects), \
             patch("agents.alpaca_broker.cancel_all_orders"), \
             patch("agents.alpaca_broker.close_all_positions"), \
             patch("core.ledger.log") as mock_ledger, \
             patch("core.db.insert"), \
             patch("orchestrator.send_alert"), \
             patch("time.sleep"):
            from orchestrator import _sweep_and_verify
            _sweep_and_verify()

        mock_ledger.assert_called_once()
        assert mock_ledger.call_args[0][0] == "sweep_failed"

    def test_premarket_returns_early_when_sweep_fails(self):
        """premarket(broker='alpaca') returns before market_context.run() when sweep fails."""
        with patch("orchestrator._is_trading_day", return_value=True), \
             patch("orchestrator._is_halted", return_value=False), \
             patch("core.db.select", return_value=[]), \
             patch("orchestrator._sweep_and_verify", return_value=False), \
             patch("agents.market_context.run") as mock_mkt:
            from orchestrator import premarket
            premarket(broker="alpaca")

        mock_mkt.assert_not_called()
