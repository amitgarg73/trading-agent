"""
Tests for Gap 2 fix: _reconcile_with_alpaca() in agents/intraday.py

Pre-fix: positions in filled_buys (entry filled, bracket exited) were skipped
         with a bare `continue` — position stayed OPEN in DB forever.
Post-fix: get_order_fill() is called, position is marked CLOSED with real P&L.

Patch targets:
  - agents.alpaca_broker  (imported inside the function via `from agents import`)
  - core.db               (module-level import in intraday.py)
  - alpaca.trading.requests / enums  (imported inside the function)
"""
import pytest
from unittest.mock import patch, MagicMock, call
from datetime import datetime, timezone


# Use live UTC date so order timestamps match what the function checks
TODAY = datetime.utcnow().date().isoformat()

OPEN_POS = {
    "id": "pos-1",
    "ticker": "AAPL",
    "status": "OPEN",
    "entry_price": 180.0,
    "fill_price": 180.0,
    "shares": 30,
    "alpaca_order_id": "order-abc",
}


def _make_order(symbol: str, side: str, status: str) -> MagicMock:
    o = MagicMock()
    o.symbol = symbol
    o.side = MagicMock()
    o.side.value = side
    o.status = MagicMock()
    o.status.value = status
    o.filled_at = datetime.utcnow().isoformat() if status == "filled" else None
    o.submitted_at = datetime.utcnow().isoformat()
    return o


def _run_reconcile(
    alpaca_tickers: set,
    open_positions: list,
    buy_orders: list,
    get_order_fill_return=(185.0, "TARGET"),
):
    """Run _reconcile_with_alpaca with all external calls mocked."""
    from agents.intraday import _reconcile_with_alpaca

    with patch("agents.alpaca_broker.get_open_tickers", return_value=alpaca_tickers), \
         patch("agents.alpaca_broker.get_order_fill", return_value=get_order_fill_return) as mock_fill, \
         patch("agents.alpaca_broker._client") as mock_ac, \
         patch("core.db.select", return_value=open_positions) as mock_select, \
         patch("core.db.update") as mock_update, \
         patch("alpaca.trading.requests.GetOrdersRequest"), \
         patch("alpaca.trading.enums.QueryOrderStatus"):

        mock_ac.return_value.get_orders.return_value = buy_orders
        _reconcile_with_alpaca()

    return mock_fill, mock_update


class TestReconcileBracketExit:

    def test_bracket_target_exit_writes_closed(self):
        """Entry filled + gone from Alpaca (TARGET) → position marked CLOSED with P&L."""
        buy_orders = [_make_order("AAPL", "buy", "filled")]
        mock_fill, mock_update = _run_reconcile(
            alpaca_tickers=set(),
            open_positions=[OPEN_POS],
            buy_orders=buy_orders,
            get_order_fill_return=(185.0, "TARGET"),
        )
        mock_fill.assert_called_once_with("order-abc")
        assert mock_update.call_count == 1
        update_data = mock_update.call_args[0][2]
        assert update_data["status"] == "CLOSED"
        assert update_data["close_reason"] == "TARGET"
        assert update_data["exit_mechanism"] == "TARGET"
        assert update_data["close_price"] == 185.0
        assert update_data["realized_pnl"] == pytest.approx(30 * (185.0 - 180.0))

    def test_bracket_stop_exit_writes_closed(self):
        """Entry filled + gone from Alpaca (STOP) → position marked CLOSED with loss P&L."""
        buy_orders = [_make_order("AAPL", "buy", "filled")]
        mock_fill, mock_update = _run_reconcile(
            alpaca_tickers=set(),
            open_positions=[OPEN_POS],
            buy_orders=buy_orders,
            get_order_fill_return=(178.8, "STOP"),
        )
        update_data = mock_update.call_args[0][2]
        assert update_data["status"] == "CLOSED"
        assert update_data["close_reason"] == "STOP"
        assert update_data["realized_pnl"] == pytest.approx(30 * (178.8 - 180.0))

    def test_bracket_native_trail_exit_writes_closed(self):
        """Entry filled + gone from Alpaca (NATIVE_TRAIL) → CLOSED with correct mechanism."""
        buy_orders = [_make_order("AAPL", "buy", "filled")]
        mock_fill, mock_update = _run_reconcile(
            alpaca_tickers=set(),
            open_positions=[OPEN_POS],
            buy_orders=buy_orders,
            get_order_fill_return=(183.0, "NATIVE_TRAIL"),
        )
        update_data = mock_update.call_args[0][2]
        assert update_data["close_reason"] == "NATIVE_TRAIL"
        assert update_data["exit_mechanism"] == "NATIVE_TRAIL"

    def test_get_order_fill_returns_none_leaves_open(self):
        """get_order_fill can't resolve price yet → no DB write, retry next cycle."""
        buy_orders = [_make_order("AAPL", "buy", "filled")]
        mock_fill, mock_update = _run_reconcile(
            alpaca_tickers=set(),
            open_positions=[OPEN_POS],
            buy_orders=buy_orders,
            get_order_fill_return=(None, None),
        )
        mock_fill.assert_called_once()
        mock_update.assert_not_called()

    def test_pending_buy_leaves_open(self):
        """Entry order in flight (< 5 min old) → no DB write, wait for fill."""
        buy_orders = [_make_order("AAPL", "buy", "new")]
        buy_orders[0].submitted_at = datetime.utcnow().isoformat()  # fresh — not yet stale
        mock_fill, mock_update = _run_reconcile(
            alpaca_tickers=set(),
            open_positions=[OPEN_POS],
            buy_orders=buy_orders,
        )
        mock_update.assert_not_called()
        mock_fill.assert_not_called()

    def test_still_in_alpaca_untouched(self):
        """Position still open in Alpaca → no DB write."""
        mock_fill, mock_update = _run_reconcile(
            alpaca_tickers={"AAPL"},
            open_positions=[OPEN_POS],
            buy_orders=[],
        )
        mock_update.assert_not_called()
        mock_fill.assert_not_called()

    def test_unfilled_no_buy_order_marks_unfilled(self):
        """Not in Alpaca, no filled or pending buy → mark UNFILLED at $0."""
        mock_fill, mock_update = _run_reconcile(
            alpaca_tickers=set(),
            open_positions=[OPEN_POS],
            buy_orders=[],
        )
        update_data = mock_update.call_args[0][2]
        assert update_data["status"] == "CLOSED"
        assert update_data["close_reason"] == "UNFILLED"
        assert update_data["realized_pnl"] == 0
        mock_fill.assert_not_called()

    def test_no_open_positions_skips_early(self):
        """No OPEN rows in DB → nothing happens."""
        mock_fill, mock_update = _run_reconcile(
            alpaca_tickers=set(),
            open_positions=[],
            buy_orders=[],
        )
        mock_update.assert_not_called()
        mock_fill.assert_not_called()
