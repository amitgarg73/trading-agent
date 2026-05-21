"""
Shared fixtures and mock factories for the trading agent test suite.
All external dependencies (yfinance, Alpaca, Supabase, Anthropic) are mocked here.
No API calls. No cost. Runs in ~5 seconds.
"""
from __future__ import annotations
import pytest
import pandas as pd
import numpy as np
from datetime import date, datetime, timedelta
from typing import Optional
from unittest.mock import MagicMock, patch


# ── Price DataFrame factory ─────────────────────────────────────────────────

def make_price_df(
    days: int = 60,
    start_price: float = 100.0,
    trend: float = 0.001,       # daily drift
    volatility: float = 0.015,  # daily std dev
    volume: int = 5_000_000,
    last_date: date | None = None,
) -> pd.DataFrame:
    """Realistic OHLCV DataFrame with controllable trend and volatility."""
    if last_date is None:
        last_date = date.today() - timedelta(days=1)  # yesterday's close (premarket norm)
    dates = pd.bdate_range(end=last_date, periods=days)
    np.random.seed(42)
    returns = np.random.normal(trend, volatility, days)
    closes  = start_price * np.cumprod(1 + returns)
    highs   = closes * (1 + np.abs(np.random.normal(0, 0.005, days)))
    lows    = closes * (1 - np.abs(np.random.normal(0, 0.005, days)))
    opens   = np.roll(closes, 1); opens[0] = start_price
    vols    = np.random.randint(int(volume * 0.8), int(volume * 1.2), days)

    df = pd.DataFrame({
        "open":   opens, "high": highs, "low": lows,
        "close":  closes, "volume": vols,
    }, index=pd.DatetimeIndex(dates, tz="America/New_York"))
    return df


def make_stale_price_df(days_old: int = 10) -> pd.DataFrame:
    """DataFrame whose most recent row is `days_old` calendar days ago — triggers freshness reject."""
    last_date = date.today() - timedelta(days=days_old)
    return make_price_df(last_date=last_date)


# ── Trade / position factories ──────────────────────────────────────────────

def make_trade(
    ticker: str = "AAPL",
    entry: float = 100.0,
    target: float = 102.0,   # 2% up
    stop: float = 99.50,     # 0.50% down → 4:1 R:R
    size: int = 3_000,
    confidence: str = "MEDIUM",
    action: str = "BUY",
) -> dict:
    shares = max(1, int(size / entry))
    return {
        "ticker":           ticker,
        "action":           action,
        "entry_price":      entry,
        "target_price":     target,
        "stop_loss":        stop,
        "position_size":    size,
        "shares":           shares,
        "confidence":       confidence,
        "reasoning":        "Test trade",
        "estimated_profit": round(shares * (target - entry), 2),
        "max_loss":         round(shares * (entry - stop), 2),
    }


def make_position(
    ticker: str = "AAPL",
    entry: float = 100.0,
    target: float = 102.0,
    stop: float = 99.33,
    shares: int = 60,
    status: str = "OPEN",
    high_watermark: float | None = None,
    close_reason: str | None = None,
    realized_pnl: float = 0.0,
    close_date: str | None = None,
) -> dict:
    today = close_date or date.today().isoformat()
    return {
        "id":               "test-pos-001",
        "planned_trade_id": "test-plan-001",
        "ticker":           ticker,
        "action":           "BUY",
        "entry_price":      entry,
        "current_price":    entry,
        "target_price":     target,
        "stop_loss":        stop,
        "shares":           shares,
        "position_size":    shares * entry,
        "unrealized_pnl":   0.0,
        "realized_pnl":     realized_pnl,
        "status":           status,
        "alpaca_order_id":  None,
        "high_watermark":   high_watermark or entry,
        "native_trail_active": False,
        "close_reason":     close_reason,
        "exit_mechanism":   close_reason,
        "opened_at":        f"{today}T09:35:00",
        "closed_at":        f"{today}T15:30:00" if status == "CLOSED" else None,
    }


def make_perf_row(
    date_str: str,
    total_pnl: float = 500.0,
    win_rate: float = 75.0,
    ending_capital: float = 50_500.0,
) -> dict:
    return {
        "date":            date_str,
        "total_pnl":       total_pnl,
        "win_rate":        win_rate,
        "ending_capital":  ending_capital,
        "trades_taken":    4,
    }


# ── DB mock ─────────────────────────────────────────────────────────────────

class FakeDB:
    """In-memory mock for core.db. Supports select/insert/update."""

    def __init__(self):
        self._tables: dict[str, list[dict]] = {}
        self._id_counter = 0

    def _next_id(self) -> str:
        self._id_counter += 1
        return f"fake-id-{self._id_counter:04d}"

    def insert(self, table: str, data: dict) -> dict:
        row = {"id": self._next_id(), **data}
        self._tables.setdefault(table, []).append(row)
        return row

    def select(self, table: str, filters: dict | None = None,
               order: str | None = None, limit: int | None = None,
               desc: bool = True) -> list:
        rows = list(self._tables.get(table, []))
        if filters:
            for k, v in filters.items():
                rows = [r for r in rows if r.get(k) == v]
        if order:
            rows = sorted(rows, key=lambda r: r.get(order, ""), reverse=desc)
        if limit:
            rows = rows[:limit]
        return rows

    def update(self, table: str, match: dict, data: dict) -> None:
        for row in self._tables.get(table, []):
            if all(row.get(k) == v for k, v in match.items()):
                row.update(data)

    def reset(self):
        self._tables.clear()
        self._id_counter = 0


@pytest.fixture
def fake_db():
    return FakeDB()


# ── Shared patches ──────────────────────────────────────────────────────────

@pytest.fixture(autouse=False)
def patch_db(fake_db, monkeypatch):
    """Patch core.db everywhere with FakeDB."""
    import core.db as real_db
    monkeypatch.setattr(real_db, "select", fake_db.select)
    monkeypatch.setattr(real_db, "insert", fake_db.insert)
    monkeypatch.setattr(real_db, "update", fake_db.update)
    return fake_db
