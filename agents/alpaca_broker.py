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


def get_intraday_signals(tickers: list[str]) -> dict[str, dict]:
    """
    Fetch intraday signals via Alpaca snapshot API: VWAP position, relative strength vs SPY.
    Returns {ticker: {above_vwap, vwap, today_pct_change, rs_vs_spy}} for available tickers.
    SPY is fetched as the RS baseline — stocks outperforming SPY are market leaders.
    Falls back gracefully on failure: missing tickers absent from result dict.
    """
    if not tickers:
        return {}
    # Alpaca rejects symbols with dashes (e.g. BRK-B) — filter before batch call
    clean = [t for t in tickers if "-" not in t]
    all_tickers = list(set(clean + ["SPY"]))
    try:
        from alpaca.data.requests import StockSnapshotRequest
        req       = StockSnapshotRequest(symbol_or_symbols=all_tickers)
        snapshots = _dclient().get_stock_snapshot(req)

        spy_snap = snapshots.get("SPY")
        spy_pct  = None
        if spy_snap and spy_snap.daily_bar:
            spy_open  = getattr(spy_snap.daily_bar, "open", None)
            spy_price = getattr(spy_snap.latest_trade, "price", None) or getattr(spy_snap.daily_bar, "close", None)
            if spy_open and spy_price and float(spy_open) > 0:
                spy_pct = (float(spy_price) - float(spy_open)) / float(spy_open)

        signals = {}
        for ticker in tickers:
            snap = snapshots.get(ticker)
            if not snap or not snap.daily_bar:
                continue
            vwap    = getattr(snap.daily_bar, "vwap",  None)
            open_px = getattr(snap.daily_bar, "open",  None)
            price   = getattr(snap.latest_trade, "price", None) or getattr(snap.daily_bar, "close", None)
            if not (vwap and open_px and price):
                continue
            vwap, open_px, price = float(vwap), float(open_px), float(price)
            today_pct  = (price - open_px) / open_px if open_px > 0 else 0.0
            rs_vs_spy  = round(today_pct / spy_pct, 2) if spy_pct and abs(spy_pct) > 0.003 else None
            today_vol = int(getattr(snap.daily_bar, "volume", 0) or 0)
            signals[ticker] = {
                "above_vwap":       price > vwap,
                "vwap":             round(vwap, 2),
                "today_pct_change": round(today_pct * 100, 2),
                "rs_vs_spy":        rs_vs_spy,
                "today_volume":     today_vol,
            }
        return signals
    except Exception as e:
        print(f"        ⚠️  Intraday signals fetch failed: {e}")
        return {}


def get_avg_daily_volumes(tickers: list[str], days: int = 20) -> dict[str, float]:
    """Return {ticker: avg_daily_volume} over the past N trading days. Used for vol_ratio filtering."""
    if not tickers:
        return {}
    try:
        from alpaca.data.requests import StockBarsRequest
        from alpaca.data.timeframe import TimeFrame
        from datetime import date, timedelta
        req = StockBarsRequest(
            symbol_or_symbols=tickers,
            timeframe=TimeFrame.Day,
            start=date.today() - timedelta(days=days + 14),  # buffer for weekends/holidays
            end=date.today() - timedelta(days=1),
        )
        bars = _dclient().get_stock_bars(req)
        result = {}
        for ticker in tickers:
            ticker_bars = bars.get(ticker) or []
            recent = list(ticker_bars)[-days:]
            if recent:
                result[ticker] = sum(float(b.volume) for b in recent) / len(recent)
        return result
    except Exception as e:
        print(f"        ⚠️  Avg volume fetch failed: {e}")
        return {}


def submit_bracket_order(
    ticker: str,
    shares: int,
    entry_price: float,
    target_price: float,
    stop_price: float,
    action: str = "BUY",
    use_native_trail: bool = False,
    trail_pct: float = 0.01,
) -> tuple[str | None, float | None]:
    """
    Submit a bracket order: market entry + take-profit + stop-loss.
    Polls up to 15 s for fill confirmation before returning.

    Returns (order_id, fill_price) on confirmed fill.
    Returns (None, None) when the order is rejected, cancelled, or unconfirmed —
    callers must not write a position row in this case.

    use_native_trail=True: trailing stop leg at trail_pct% — Alpaca tracks peak
    in real-time, fires on reversal without the 15-min polling gap.
    """
    import time
    from alpaca.trading.requests import MarketOrderRequest, TakeProfitRequest, StopLossRequest
    from alpaca.trading.enums import OrderSide, TimeInForce, OrderClass

    side = OrderSide.BUY if action == "BUY" else OrderSide.SELL

    if use_native_trail:
        stop_loss_req = StopLossRequest(trail_percent=round(trail_pct * 100, 4))
        print(f"        📍 Native trail: {ticker} @ {trail_pct*100:.1f}% real-time Alpaca trail")
    else:
        stop_loss_req = StopLossRequest(stop_price=round(stop_price, 2))

    req = MarketOrderRequest(
        symbol=ticker,
        qty=shares,
        side=side,
        time_in_force=TimeInForce.DAY,
        order_class=OrderClass.BRACKET,
        take_profit=TakeProfitRequest(limit_price=round(target_price, 2)),
        stop_loss=stop_loss_req,
    )
    order = _client().submit_order(req)
    print(f"        Market order: {ticker} {shares} shares → {order.id}")

    for _ in range(15):
        time.sleep(1)
        try:
            o = _client().get_order_by_id(str(order.id))
            status = str(o.status).lower()
            if status in ("filled", "partially_filled"):
                fill_price = float(o.filled_avg_price) if o.filled_avg_price else None
                return str(order.id), fill_price
            if status in ("cancelled", "rejected", "expired"):
                print(f"        ⚠️ {ticker} order {status} — blocking DB write")
                return None, None
        except Exception:
            pass

    print(f"        ⚠️ {ticker} — could not confirm fill after 15s — blocking DB write")
    return None, None


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
    For a completed bracket order, return (close_price, exit_mechanism).
    exit_mechanism: TARGET, NATIVE_TRAIL, or STOP.
    Returns (None, None) if fill data isn't available.

    Classification order matters: check "trailing" before "stop" before "limit"
    so "stop_limit" is correctly classified as STOP (not TARGET).
    """
    try:
        order = _client().get_order_by_id(order_id)
        legs = order.legs or []
        for leg in legs:
            status_str = str(leg.status).lower()
            type_str   = str(leg.order_type).lower()
            if "filled" in status_str and leg.filled_avg_price:
                if "trailing" in type_str:
                    mechanism = "NATIVE_TRAIL"
                elif "stop" in type_str:
                    mechanism = "STOP"
                else:
                    mechanism = "TARGET"  # "limit" leg = take-profit
                return float(leg.filled_avg_price), mechanism
        if legs:
            statuses = [(str(l.order_type), str(l.status)) for l in legs]
            print(f"        ℹ️  get_order_fill({order_id[:8]}…): no filled leg found — {statuses}")
    except Exception as e:
        print(f"        ⚠️  get_order_fill({order_id}): {e}")
    return None, None


def close_position(ticker: str) -> tuple[bool, Optional[float]]:
    """Market-close an open position. Returns (success, fill_price)."""
    try:
        order = _client().close_position(ticker)
        fill_price = float(order.filled_avg_price) if order.filled_avg_price else None
        return True, fill_price
    except Exception:
        return False, None


def cancel_order(order_id: str) -> bool:
    """Cancel a single open order by ID. Returns True if cancelled successfully."""
    try:
        _client().cancel_order_by_id(order_id)
        return True
    except Exception:
        return False


def cancel_all_orders() -> None:
    """Cancel all open orders — call before EOD close to clear pending bracket legs."""
    try:
        _client().cancel_orders()
    except Exception:
        pass


def close_all_positions() -> list[dict]:
    """Market-close every open position on Alpaca. Returns list of {ticker, success, fill_price}."""
    results = []
    try:
        positions = _client().get_all_positions()
    except Exception as e:
        print(f"  ⚠️  Could not fetch Alpaca positions: {e}")
        return results
    for pos in positions:
        ticker = pos.symbol
        ok, fill = close_position(ticker)
        results.append({"ticker": ticker, "success": ok, "fill_price": fill})
        status = f"${fill:.2f}" if fill else "pending"
        icon = "✅" if ok else "❌"
        print(f"  {icon} {ticker}: market close submitted — fill {status}")
    return results


def get_buying_power() -> float | None:
    """Return current buying power from Alpaca account."""
    try:
        account = _client().get_account()
        return float(account.buying_power)
    except Exception:
        return None
