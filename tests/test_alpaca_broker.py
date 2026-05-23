"""Tests for agents/alpaca_broker.py — get_order_fill classification and fill poll."""
import pytest
from unittest.mock import MagicMock, patch


def _make_leg(order_type: str, status: str, filled_avg_price=None):
    leg = MagicMock()
    leg.order_type = order_type
    leg.status = status
    leg.filled_avg_price = filled_avg_price
    return leg


def _make_order(legs):
    order = MagicMock()
    order.legs = legs
    order.filled_avg_price = None
    return order


@patch("agents.alpaca_broker._client")
def test_limit_leg_classified_as_target(mock_client):
    leg = _make_leg("limit", "filled", filled_avg_price=185.50)
    mock_client.return_value.get_order_by_id.return_value = _make_order([leg])

    from agents.alpaca_broker import get_order_fill
    price, mech = get_order_fill("order-123")
    assert price == 185.50
    assert mech == "TARGET"


@patch("agents.alpaca_broker._client")
def test_stop_limit_leg_classified_as_stop_not_target(mock_client):
    """stop_limit contains 'limit' — must be classified as STOP, not TARGET."""
    leg = _make_leg("stop_limit", "filled", filled_avg_price=178.00)
    mock_client.return_value.get_order_by_id.return_value = _make_order([leg])

    from agents.alpaca_broker import get_order_fill
    price, mech = get_order_fill("order-456")
    assert price == 178.00
    assert mech == "STOP"


@patch("agents.alpaca_broker._client")
def test_stop_leg_classified_as_stop(mock_client):
    leg = _make_leg("stop", "filled", filled_avg_price=177.50)
    mock_client.return_value.get_order_by_id.return_value = _make_order([leg])

    from agents.alpaca_broker import get_order_fill
    price, mech = get_order_fill("order-789")
    assert price == 177.50
    assert mech == "STOP"


@patch("agents.alpaca_broker._client")
def test_trailing_stop_classified_as_native_trail(mock_client):
    leg = _make_leg("trailing_stop", "filled", filled_avg_price=180.00)
    mock_client.return_value.get_order_by_id.return_value = _make_order([leg])

    from agents.alpaca_broker import get_order_fill
    price, mech = get_order_fill("order-abc")
    assert price == 180.00
    assert mech == "NATIVE_TRAIL"


@patch("agents.alpaca_broker._client")
def test_no_filled_leg_returns_none_tuple(mock_client):
    """When no leg is filled, returns (None, None) and prints diagnostic."""
    leg = _make_leg("limit", "canceled", filled_avg_price=None)
    mock_client.return_value.get_order_by_id.return_value = _make_order([leg])

    from agents.alpaca_broker import get_order_fill
    price, mech = get_order_fill("order-xyz")
    assert price is None
    assert mech is None


@patch("agents.alpaca_broker._client")
def test_exception_returns_none_tuple(mock_client):
    """API exception is printed and returns (None, None)."""
    mock_client.return_value.get_order_by_id.side_effect = RuntimeError("timeout")

    from agents.alpaca_broker import get_order_fill
    price, mech = get_order_fill("order-err")
    assert price is None
    assert mech is None


# ── submit_bracket_order fill poll (P1) ──────────────────────────────────────

@patch("agents.alpaca_broker._client")
def test_submit_bracket_order_returns_tuple_on_fill(mock_client):
    """Confirmed fill returns (order_id, fill_price) tuple."""
    order = MagicMock()
    order.id = "ord-123"
    mock_client.return_value.submit_order.return_value = order

    filled = MagicMock()
    filled.status = "filled"
    filled.filled_avg_price = 101.5
    mock_client.return_value.get_order_by_id.return_value = filled

    from agents.alpaca_broker import submit_bracket_order
    with patch("time.sleep"):
        order_id, fill_price = submit_bracket_order("AAPL", 10, 100.0, 104.0, 99.33)

    assert order_id == "ord-123"
    assert fill_price == 101.5


@patch("agents.alpaca_broker._client")
def test_submit_bracket_order_returns_none_on_rejection(mock_client):
    """Rejected order returns (None, None) — caller must not write to DB."""
    order = MagicMock()
    order.id = "ord-456"
    mock_client.return_value.submit_order.return_value = order

    rejected = MagicMock()
    rejected.status = "rejected"
    rejected.filled_avg_price = None
    mock_client.return_value.get_order_by_id.return_value = rejected

    from agents.alpaca_broker import submit_bracket_order
    with patch("time.sleep"):
        order_id, fill_price = submit_bracket_order("TSLA", 5, 200.0, 208.0, 198.66)

    assert order_id is None
    assert fill_price is None


@patch("agents.alpaca_broker._client")
def test_submit_bracket_order_returns_none_on_timeout(mock_client):
    """Unconfirmed fill after 15 polls returns (None, None)."""
    order = MagicMock()
    order.id = "ord-789"
    mock_client.return_value.submit_order.return_value = order

    pending = MagicMock()
    pending.status = "pending_new"
    pending.filled_avg_price = None
    mock_client.return_value.get_order_by_id.return_value = pending

    from agents.alpaca_broker import submit_bracket_order
    with patch("time.sleep"):
        order_id, fill_price = submit_bracket_order("MSFT", 8, 300.0, 312.0, 298.0)

    assert order_id is None
    assert fill_price is None
