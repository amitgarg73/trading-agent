"""
Tests for RF-4 + RFN-1 + RFN-2 in Strategy A.

_sweep_and_verify():
  - Only acts on positions in our DB as OPEN (skips Strategy B's positions)
  - Uses tag_prefix on close calls so B's positions are never accidentally closed
  - Returns False after 2 failed attempts, sets halt flag, logs ledger, sends alert

premarket():
  - Returns early when sweep fails
  - No NameError from undefined 'mode' variable (RFN-2)
"""
import pytest
from unittest.mock import patch, MagicMock, call


OUR_POS = [{"ticker": "AAPL"}]


class TestMorningSweepA:

    def test_no_overnight_positions(self):
        """Empty Alpaca account → True immediately, no DB query, no close calls."""
        with patch("agents.alpaca_broker.get_open_tickers", return_value=set()), \
             patch("agents.alpaca_broker.cancel_all_orders") as mock_cancel, \
             patch("agents.alpaca_broker.close_all_positions") as mock_close, \
             patch("time.sleep"):
            from orchestrator import _sweep_and_verify
            result = _sweep_and_verify()

        assert result is True
        mock_cancel.assert_not_called()
        mock_close.assert_not_called()

    def test_only_other_strategy_positions(self):
        """Alpaca has tickers but none are in our DB — other strategy's positions, skip."""
        with patch("agents.alpaca_broker.get_open_tickers", return_value={"MSFT"}), \
             patch("core.db.select", return_value=[]), \
             patch("agents.alpaca_broker.cancel_all_orders") as mock_cancel, \
             patch("agents.alpaca_broker.close_all_positions") as mock_close, \
             patch("time.sleep"):
            from orchestrator import _sweep_and_verify
            result = _sweep_and_verify()

        assert result is True
        mock_cancel.assert_not_called()
        mock_close.assert_not_called()

    def test_clears_on_first_attempt(self):
        """Our position cleared after first close → True, no alert."""
        side_effects = [{"AAPL"}, set()]
        with patch("agents.alpaca_broker.get_open_tickers", side_effect=side_effects), \
             patch("core.db.select", return_value=OUR_POS), \
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
        """Still dirty after first close, clear after second → True, no alert."""
        side_effects = [{"AAPL"}, {"AAPL"}, set()]
        with patch("agents.alpaca_broker.get_open_tickers", side_effect=side_effects), \
             patch("core.db.select", return_value=OUR_POS), \
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
        side_effects = [{"AAPL"}, {"AAPL"}, {"AAPL"}]
        with patch("agents.alpaca_broker.get_open_tickers", side_effect=side_effects), \
             patch("core.db.select", return_value=OUR_POS), \
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
        """Alert includes ticker, Alpaca URL, and restart workflow URL."""
        side_effects = [{"AAPL"}, {"AAPL"}, {"AAPL"}]
        with patch("agents.alpaca_broker.get_open_tickers", side_effect=side_effects), \
             patch("core.db.select", return_value=OUR_POS), \
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
        """Sweep failure inserts scan_type='halt_flag' into scan_results."""
        side_effects = [{"MSFT"}, {"MSFT"}, {"MSFT"}]
        with patch("agents.alpaca_broker.get_open_tickers", side_effect=side_effects), \
             patch("core.db.select", return_value=[{"ticker": "MSFT"}]), \
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
        side_effects = [{"NVDA"}, {"NVDA"}, {"NVDA"}]
        with patch("agents.alpaca_broker.get_open_tickers", side_effect=side_effects), \
             patch("core.db.select", return_value=[{"ticker": "NVDA"}]), \
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

    def test_close_uses_strategy_tag_prefix(self):
        """close_all_positions is called with tag_prefix='strata_' — never closes B's positions."""
        side_effects = [{"AAPL"}, set()]
        with patch("agents.alpaca_broker.get_open_tickers", side_effect=side_effects), \
             patch("core.db.select", return_value=OUR_POS), \
             patch("agents.alpaca_broker.cancel_all_orders"), \
             patch("agents.alpaca_broker.close_all_positions") as mock_close, \
             patch("orchestrator.send_alert"), \
             patch("time.sleep"):
            from orchestrator import _sweep_and_verify
            _sweep_and_verify()

        mock_close.assert_called_once()
        kwargs = mock_close.call_args[1]
        assert kwargs.get("tag_prefix") == "strata_"

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

    def test_no_mode_nameerror(self):
        """premarket() doesn't reference undefined 'mode' — skip_volume_surge uses literal True."""
        import inspect
        from orchestrator import premarket
        source = inspect.getsource(premarket)
        assert "skip_volume_surge=(mode ==" not in source, (
            "RFN-2: 'mode' is not in scope of premarket() — "
            "use skip_volume_surge=True instead"
        )
        assert "skip_volume_surge=True" in source
