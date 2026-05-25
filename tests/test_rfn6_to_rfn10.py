"""
Tests for RFN-6, RFN-7, RFN-8, RFN-10 in Strategy A.

RFN-6: performance._alpaca_order_pnl uses getattr(o.side/leg.status) not str()
RFN-7: intraday reconcile today_start has tzinfo=timezone.utc
RFN-8: portfolio.close_all_positions orphan sweep skips positions just closed (db_tickers guard)
RFN-10: eod() skips on non-trading days
"""
import inspect
import pytest
from unittest.mock import patch, MagicMock, call


# ──────────────────────────────────────────────────────────────────────────────
# RFN-6: enum-safe comparisons in _alpaca_order_pnl
# ──────────────────────────────────────────────────────────────────────────────

class TestPerformanceEnumComparisons:
    """RFN-6: _alpaca_order_pnl must use getattr not str() for side/status enums."""

    def test_side_comparison_uses_getattr_not_str(self):
        from agents.performance import _alpaca_order_pnl
        source = inspect.getsource(_alpaca_order_pnl)
        assert 'str(o.side).lower() == "buy"' not in source, (
            "RFN-6: use getattr(o.side, 'value', str(o.side)) not str(o.side)"
        )
        assert "getattr(o.side" in source

    def test_leg_status_comparison_uses_getattr_not_str(self):
        from agents.performance import _alpaca_order_pnl
        source = inspect.getsource(_alpaca_order_pnl)
        assert 'str(leg.status).lower()' not in source, (
            "RFN-6: use getattr(leg.status, 'value', str(leg.status)) not str(leg.status)"
        )
        assert "getattr(leg.status" in source

    def test_pnl_computed_correctly_when_side_is_enum_object(self):
        """When Alpaca SDK returns enum objects, P&L still computes correctly."""
        from agents.performance import _alpaca_order_pnl

        class FakeSide:
            value = "buy"
            def __str__(self): return "OrderSide.buy"  # str() would break old code

        class FakeStatus:
            value = "filled"
            def __str__(self): return "OrderStatus.filled"

        fill_leg = MagicMock()
        fill_leg.status = FakeStatus()
        fill_leg.filled_avg_price = 102.0

        order = MagicMock()
        order.client_order_id = "strata_AAPL_20260524"
        order.side = FakeSide()
        order.filled_avg_price = 100.0
        order.filled_qty = 10
        order.legs = [fill_leg]

        mock_client = MagicMock()
        mock_client.get_orders.return_value = [order]

        with patch("agents.alpaca_broker._client", return_value=mock_client):
            pnl, note = _alpaca_order_pnl("strata_", [])

        assert pnl == 20.0, f"Expected $20 P&L (10 shares × $2), got {pnl}"
        assert "1b" in note  # 1 bracket exit


# ──────────────────────────────────────────────────────────────────────────────
# RFN-7: timezone-aware today_start in intraday reconcile
# ──────────────────────────────────────────────────────────────────────────────

class TestIntradayReconcileDateFilter:
    """RFN-7: _reconcile_with_alpaca must pass timezone-aware after= to get_orders."""

    def test_today_start_has_timezone(self):
        from agents.intraday import _reconcile_with_alpaca
        source = inspect.getsource(_reconcile_with_alpaca)
        assert "tzinfo=timezone.utc" in source, (
            "RFN-7: today_start must include tzinfo=timezone.utc"
        )

    def test_reconcile_passes_aware_datetime_to_get_orders(self):
        """get_orders is called with a timezone-aware datetime in after=."""
        from datetime import timezone

        mock_client = MagicMock()
        mock_client.get_orders.return_value = []

        with patch("agents.alpaca_broker._client", return_value=mock_client), \
             patch("agents.alpaca_broker.get_open_tickers", return_value=set()), \
             patch("core.db.select", return_value=[{"ticker": "AAPL", "id": "1",
                                                    "entry_price": 100, "shares": 10,
                                                    "alpaca_order_id": None}]), \
             patch("core.db.update"):
            from agents.intraday import _reconcile_with_alpaca
            _reconcile_with_alpaca()

        mock_client.get_orders.assert_called_once()
        req = mock_client.get_orders.call_args[0][0]
        assert req.after is not None
        assert req.after.tzinfo is not None, (
            "RFN-7: after= datetime must be timezone-aware (tzinfo set)"
        )
        assert req.after.tzinfo == timezone.utc


# ──────────────────────────────────────────────────────────────────────────────
# RFN-8: orphan sweep in portfolio.close_all_positions skips db-tracked positions
# ──────────────────────────────────────────────────────────────────────────────

class TestOrphanSweepDbTickerGuard:
    """RFN-8: orphan sweep must NOT re-close positions already tracked in our DB."""

    def test_orphan_sweep_does_not_close_db_tracked_position(self):
        """Position in DB (just closed) must not appear in orphan sweep close calls."""
        mock_alpaca = MagicMock()
        # Alpaca still shows AAPL (just submitted for close — API lag)
        alpaca_pos = MagicMock()
        alpaca_pos.symbol = "AAPL"
        alpaca_pos.qty = 10
        mock_alpaca.get_all_positions.return_value = [alpaca_pos]

        # order history shows AAPL was ours
        mock_order = MagicMock()
        mock_order.client_order_id = "strata_AAPL_20260524"
        mock_order.symbol = "AAPL"
        mock_alpaca.get_orders.return_value = [mock_order]

        # DB has AAPL as open (was just closed in the first loop)
        open_pos = [{"id": "1", "ticker": "AAPL", "shares": 10,
                     "entry_price": 100.0, "action": "BUY",
                     "target_price": 104.0, "stop_loss": 99.0,
                     "position_size": 1000, "planned_trade_id": "pt1",
                     "current_price": None, "unrealized_pnl": None,
                     "realized_pnl": None, "alpaca_order_id": None}]

        with patch("core.db.select", return_value=open_pos), \
             patch("core.db.update"), \
             patch("agents.alpaca_broker._client", return_value=mock_alpaca), \
             patch("agents.alpaca_broker.cancel_all_orders"), \
             patch("agents.alpaca_broker.close_position", return_value=(True, 100.5)) as mock_close:
            from agents.portfolio import close_all_positions
            close_all_positions(reason="EOD", broker="alpaca")

        # close_position should be called ONCE (from the main loop), NOT a second time from orphan sweep
        assert mock_close.call_count == 1, (
            f"RFN-8: close_position called {mock_close.call_count} times — "
            "orphan sweep must skip positions already in DB"
        )

    def test_orphan_sweep_closes_genuinely_orphaned_position(self):
        """Position in Alpaca but NOT in our DB must be closed by orphan sweep."""
        mock_alpaca = MagicMock()
        orphan_pos = MagicMock()
        orphan_pos.symbol = "MSFT"
        orphan_pos.qty = 5
        mock_alpaca.get_all_positions.return_value = [orphan_pos]

        # MSFT tagged as ours in order history
        mock_order = MagicMock()
        mock_order.client_order_id = "strata_MSFT_20260524"
        mock_order.symbol = "MSFT"
        mock_alpaca.get_orders.return_value = [mock_order]

        # DB has NO open positions (MSFT is truly orphaned)
        with patch("core.db.select", return_value=[]), \
             patch("core.db.update"), \
             patch("agents.alpaca_broker._client", return_value=mock_alpaca), \
             patch("agents.alpaca_broker.cancel_all_orders"), \
             patch("agents.alpaca_broker.close_position", return_value=(True, 420.0)) as mock_close:
            from agents.portfolio import close_all_positions
            close_all_positions(reason="EOD", broker="alpaca")

        mock_close.assert_called_once_with("MSFT")


# ──────────────────────────────────────────────────────────────────────────────
# RFN-10 (Strategy A): eod() skips on non-trading days
# ──────────────────────────────────────────────────────────────────────────────

class TestEodTradingDayGuard:
    """RFN-10: eod() must exit early on non-trading days."""

    def test_eod_skips_on_non_trading_day(self):
        """eod() returns without writing dedup record on non-trading days."""
        with patch("orchestrator._is_trading_day", return_value=False), \
             patch("orchestrator._is_halted") as mock_halt, \
             patch("core.db.select") as mock_select, \
             patch("core.db.insert") as mock_insert:
            from orchestrator import eod
            eod(broker="alpaca")

        mock_halt.assert_not_called()
        mock_insert.assert_not_called(), "No DB writes on non-trading day"

    def test_eod_proceeds_on_trading_day(self):
        """eod() passes the trading day check and reaches the halt check."""
        with patch("orchestrator._is_trading_day", return_value=True), \
             patch("orchestrator._is_halted", return_value=True) as mock_halt, \
             patch("core.db.select", return_value=[]):
            from orchestrator import eod
            eod(broker="alpaca")

        mock_halt.assert_called_once()

    def test_eod_non_trading_day_does_not_consume_dedup_slot(self):
        """Non-trading day exit must NOT write run_eod_started — real EOD stays runnable."""
        inserted_scan_types = []

        def capture_insert(table, payload):
            if table == "scan_results":
                inserted_scan_types.append(payload.get("scan_type", ""))
            return {"id": "fake"}

        with patch("orchestrator._is_trading_day", return_value=False), \
             patch("core.db.insert", side_effect=capture_insert), \
             patch("core.db.select", return_value=[]):
            from orchestrator import eod
            eod(broker="alpaca")

        assert "run_eod_started" not in inserted_scan_types, (
            "RFN-10: non-trading day must not write dedup record"
        )
