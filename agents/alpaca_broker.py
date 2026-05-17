"""
Alpaca paper trading broker.
Submits bracket orders (entry + take-profit + stop-loss in one call).
Alpaca handles exit execution — intraday agent just syncs state.

Env vars: ALPACA_API_KEY, ALPACA_SECRET_KEY
ALPACA_PAPER defaults to "true" — set to "false" only for live trading.
"""
from __future__ import annotations
import os
from typing import Optional
from dotenv import load_dotenv

load_dotenv()

_API_KEY = os.getenv("ALPACA_API_KEY", "")
_SECRET  = os.getenv("ALPACA_SECRET_KEY", "")
_PAPER   = os.getenv("ALPACA_PAPER", "true").lower() != "false"

_trading_client = None


def _client():
    global _trading_client
    if _trading_client is None:
        from alpaca.trading.client import TradingClient
        _trading_client = TradingClient(_API_KEY, _SECRET, paper=_PAPER)
    return _trading_client


def submit_bracket_order(
    ticker: str,
    shares: int,
    target_price: float,
    stop_price: float,
    action: str = "BUY",
) -> str:
    """
    Submit a bracket order: market entry + limit take-profit + stop-loss.
    Returns the Alpaca parent order ID (store in DB to track exit fills).
    """
    from alpaca.trading.requests import MarketOrderRequest, TakeProfitRequest, StopLossRequest
    from alpaca.trading.enums import OrderSide, TimeInForce, OrderClass

    side = OrderSide.BUY if action == "BUY" else OrderSide.SELL
    req = MarketOrderRequest(
        symbol=ticker,
        qty=shares,
        side=side,
        time_in_force=TimeInForce.DAY,
        order_class=OrderClass.BRACKET,
        take_profit=TakeProfitRequest(limit_price=round(target_price, 2)),
        stop_loss=StopLossRequest(stop_price=round(stop_price, 2)),
    )
    order = _client().submit_order(req)
    return str(order.id)


def get_open_tickers() -> set:
    """Return set of ticker symbols currently held in Alpaca."""
    positions = _client().get_all_positions()
    return {p.symbol for p in positions}


def get_position_data(ticker: str) -> Optional[dict]:
    """Return current_price and unrealized_pnl for an open position from Alpaca."""
    try:
        p = _client().get_open_position(ticker)
        return {
            "current_price":  float(p.current_price),
            "unrealized_pnl": float(p.unrealized_pl),
        }
    except Exception:
        return None


def get_order_fill(order_id: str) -> tuple:
    """
    For a completed bracket order, return (close_price, close_reason).
    close_reason: TARGET (limit leg filled) or STOP (stop leg filled).
    Returns (None, None) if fill data isn't available.
    """
    try:
        order = _client().get_order_by_id(order_id)
        for leg in (order.legs or []):
            status_str = str(leg.status).lower()
            type_str   = str(leg.order_type).lower()
            if "filled" in status_str and leg.filled_avg_price:
                reason = "TARGET" if "limit" in type_str else "STOP"
                return float(leg.filled_avg_price), reason
    except Exception:
        pass
    return None, None


def close_position(ticker: str) -> tuple[bool, Optional[float]]:
    """Market-close an open position. Returns (success, fill_price)."""
    try:
        order = _client().close_position(ticker)
        fill_price = float(order.filled_avg_price) if order.filled_avg_price else None
        return True, fill_price
    except Exception:
        return False, None


def cancel_all_orders() -> None:
    """Cancel all open orders — call before EOD close to clear pending bracket legs."""
    try:
        _client().cancel_orders()
    except Exception:
        pass
