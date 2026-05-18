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
_data_client    = None


def _client():
    global _trading_client
    if _trading_client is None:
        from alpaca.trading.client import TradingClient
        _trading_client = TradingClient(_API_KEY, _SECRET, paper=_PAPER)
    return _trading_client


def _dclient():
    global _data_client
    if _data_client is None:
        from alpaca.data import StockHistoricalDataClient
        _data_client = StockHistoricalDataClient(_API_KEY, _SECRET)
    return _data_client


def get_live_prices(tickers: list[str]) -> dict[str, float]:
    """
    Fetch real-time ask prices for a list of tickers via Alpaca market data.
    Returns {ticker: ask_price} for tickers where data is available.
    Uses ask price (what you pay to BUY) — more realistic than mid or yfinance close.
    Falls back gracefully: missing tickers are simply absent from the result dict.
    """
    if not tickers:
        return {}
    try:
        from alpaca.data.requests import StockLatestQuoteRequest
        req    = StockLatestQuoteRequest(symbol_or_symbols=tickers)
        quotes = _dclient().get_stock_latest_quote(req)
        prices = {}
        for ticker, quote in quotes.items():
            ask = getattr(quote, "ask_price", None)
            bid = getattr(quote, "bid_price", None)
            if ask and float(ask) > 0:
                prices[ticker] = round(float(ask), 4)
            elif bid and float(bid) > 0:
                prices[ticker] = round(float(bid), 4)
        return prices
    except Exception as e:
        print(f"        ⚠️  Live price fetch failed: {e}")
        return {}


def submit_bracket_order(
    ticker: str,
    shares: int,
    entry_price: float,
    target_price: float,
    stop_price: float,
    action: str = "BUY",
) -> str:
    """
    Submit a bracket order: limit entry + limit take-profit + stop-loss.
    Uses limit order (not market) to avoid paying the spread on entry.
    Limit set at entry_price + 0.1% buffer to ensure fill on liquid stocks
    while avoiding chasing stocks that gap far above our signal price.
    Returns the Alpaca parent order ID.
    """
    from alpaca.trading.requests import LimitOrderRequest, TakeProfitRequest, StopLossRequest
    from alpaca.trading.enums import OrderSide, TimeInForce, OrderClass

    side        = OrderSide.BUY if action == "BUY" else OrderSide.SELL
    limit_price = round(entry_price * 1.001, 2)  # 0.1% buffer for fill probability
    req = LimitOrderRequest(
        symbol=ticker,
        qty=shares,
        side=side,
        time_in_force=TimeInForce.DAY,
        limit_price=limit_price,
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


def get_buying_power() -> float | None:
    """Return current buying power from Alpaca account."""
    try:
        account = _client().get_account()
        return float(account.buying_power)
    except Exception:
        return None
