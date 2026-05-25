"""
Tests for RF-2: _reconcile_with_alpaca() when get_orders() raises an exception.

Pre-fix: silently set filled_buys/pending_buys to empty sets and continued —
         unfilled orders undetected for that cycle.
Post-fix: log to ledger, write to scan_results (DB), send alert, return early.
"""
import pytest
from unittest.mock import patch, MagicMock, call
from datetime import datetime


OPEN_POS = {
    "id":             "pos-rf2",
    "ticker":         "AAPL",
    "status":         "OPEN",
    "entry_price":    180.0,
    "fill_price":     180.0,
    "shares":         20,
    "alpaca_order_id": "order-rf2",
}


def _run_reconcile_with_broken_orders(tmp_path, monkeypatch):
    """Run _reconcile_with_alpaca when Alpaca get_orders() raises."""
    import core.ledger as ledger_mod
    monkeypatch.setattr(ledger_mod, "_DATA_DIR", tmp_path)

    from agents.intraday import _reconcile_with_alpaca

    with patch("agents.alpaca_broker.get_open_tickers", return_value=set()), \
         patch("agents.alpaca_broker._client") as mock_ac, \
         patch("core.db.select",  return_value=[OPEN_POS]) as mock_sel, \
         patch("core.db.update")  as mock_upd, \
         patch("core.db.insert")  as mock_ins, \
         patch("core.alerts.send_alert") as mock_alert, \
         patch("alpaca.trading.requests.GetOrdersRequest"), \
         patch("alpaca.trading.enums.QueryOrderStatus"):

        mock_ac.return_value.get_orders.side_effect = Exception("Alpaca connection timeout")
        _reconcile_with_alpaca()

    return mock_upd, mock_ins, mock_alert, ledger_mod


class TestReconcileFailureRF2:

    def test_returns_early_on_exception(self, monkeypatch, tmp_path):
        """Function must return early — must not continue to process positions."""
        mock_upd, mock_ins, mock_alert, _ = _run_reconcile_with_broken_orders(tmp_path, monkeypatch)
        # db.update would only be called if we tried to mark positions CLOSED/UNFILLED
        mock_upd.assert_not_called()

    def test_logs_reconcile_failed_to_ledger(self, monkeypatch, tmp_path):
        _, _, _, ledger_mod = _run_reconcile_with_broken_orders(tmp_path, monkeypatch)
        events = ledger_mod.read_today()
        assert any(e["event"] == "reconcile_failed" for e in events)
        fail_ev = next(e for e in events if e["event"] == "reconcile_failed")
        assert "Alpaca connection timeout" in fail_ev["data"]["error"]

    def test_writes_reconcile_failed_to_db(self, monkeypatch, tmp_path):
        _, mock_ins, _, _ = _run_reconcile_with_broken_orders(tmp_path, monkeypatch)
        db_calls = [c for c in mock_ins.call_args_list if c[0][0] == "scan_results"]
        assert len(db_calls) == 1
        scan_row = db_calls[0][0][1]
        assert scan_row["scan_type"] == "reconcile_failed"
        assert "Alpaca connection timeout" in scan_row["results"]["error"]

    def test_sends_alert_on_failure(self, monkeypatch, tmp_path):
        _, _, mock_alert, _ = _run_reconcile_with_broken_orders(tmp_path, monkeypatch)
        mock_alert.assert_called_once()
        subject, body = mock_alert.call_args[0]
        assert "Reconciliation" in subject or "reconcil" in subject.lower()
        assert "Alpaca connection timeout" in body

    def test_normal_reconcile_does_not_log_reconcile_failed(self, monkeypatch, tmp_path):
        """Happy path: no exception → no reconcile_failed event in ledger."""
        import core.ledger as ledger_mod
        monkeypatch.setattr(ledger_mod, "_DATA_DIR", tmp_path)

        from agents.intraday import _reconcile_with_alpaca

        with patch("agents.alpaca_broker.get_open_tickers", return_value={"AAPL"}), \
             patch("agents.alpaca_broker._client") as mock_ac, \
             patch("core.db.select", return_value=[OPEN_POS]), \
             patch("core.db.update"), \
             patch("core.db.insert"), \
             patch("alpaca.trading.requests.GetOrdersRequest"), \
             patch("alpaca.trading.enums.QueryOrderStatus"):
            mock_ac.return_value.get_orders.return_value = []
            _reconcile_with_alpaca()

        events = ledger_mod.read_today()
        assert not any(e["event"] == "reconcile_failed" for e in events)
