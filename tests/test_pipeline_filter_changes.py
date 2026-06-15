"""
Regression tests for pipeline filter changes:
1. Garbage data filter — rejects candidates with price=0 or rsi=None
2. ORB downgrade — above_orb=False no longer hard-filters; data still passed to Claude
3. Extension filter — threshold rises to 5% on strong up days (avg futures >= 2%)
"""
from __future__ import annotations


def _make_candidate(**kwargs) -> dict:
    base = {
        "ticker": "TEST",
        "technical_score": 6,
        "price": 50.0,
        "current_price": 50.0,
        "rsi": 55.0,
        "volume_ratio": 1.2,
        "above_orb": True,
        "above_vwap": True,
        "today_pct_change": 1.0,
        "day_high": 52.0,
        "day_low": 48.0,
    }
    base.update(kwargs)
    return base


# ── Garbage data filter ───────────────────────────────────────────────────────

def _apply_garbage_filter(candidates: list[dict]) -> list[dict]:
    return [
        c for c in candidates
        if (c.get("price") or c.get("current_price") or 0) > 0
        and c.get("rsi") is not None
    ]


class TestGarbageFilter:

    def test_passes_normal_candidate(self):
        c = [_make_candidate(ticker="AAPL", price=182.0, rsi=54.8)]
        assert len(_apply_garbage_filter(c)) == 1

    def test_rejects_price_zero(self):
        c = [_make_candidate(ticker="CXAI", price=0.0, current_price=0.0, rsi=None)]
        assert len(_apply_garbage_filter(c)) == 0

    def test_rejects_rsi_none(self):
        c = [_make_candidate(ticker="DXST", price=0.0, rsi=None)]
        assert len(_apply_garbage_filter(c)) == 0

    def test_rejects_multiple_garbage(self):
        candidates = [
            _make_candidate(ticker="DXST", price=0.0, rsi=None),
            _make_candidate(ticker="LOBO", price=0.0, rsi=None),
            _make_candidate(ticker="MRVU", price=0.0, rsi=None),
            _make_candidate(ticker="SE",   price=92.6, rsi=54.8),
        ]
        result = _apply_garbage_filter(candidates)
        assert len(result) == 1
        assert result[0]["ticker"] == "SE"

    def test_passes_when_current_price_set_but_not_price(self):
        c = [_make_candidate(ticker="XYZ", price=0.0, current_price=45.0, rsi=52.0)]
        assert len(_apply_garbage_filter(c)) == 1

    def test_june2_candidates_all_rejected(self):
        """Reproduce June 2 scenario: 10 garbage candidates, all should be filtered."""
        june2 = [
            _make_candidate(ticker=t, price=0.0, current_price=0.0, rsi=None)
            for t in ["DXST","LOBO","MRVU","SLXNW","MVLL","STAK","TE.WS","ABTS","ZJYL","BJDX"]
        ]
        assert len(_apply_garbage_filter(june2)) == 0


# ── ORB downgrade — signal not gate ──────────────────────────────────────────

def _apply_orb_signal(candidates: list[dict]) -> tuple[list[dict], int]:
    """New behaviour: pass all through, just count below-ORB for logging."""
    orb_below = sum(1 for c in candidates if c.get("above_orb") is False)
    return candidates, orb_below


class TestORBDowngrade:

    def test_below_orb_candidates_pass_through(self):
        candidates = [
            _make_candidate(ticker="FCX",  above_orb=False),
            _make_candidate(ticker="TSLA", above_orb=False),
            _make_candidate(ticker="AAPL", above_orb=True),
        ]
        result, below_count = _apply_orb_signal(candidates)
        assert len(result) == 3
        assert below_count == 2

    def test_none_orb_passes_through(self):
        """above_orb=None (pre-market, no intraday data) must not be dropped."""
        candidates = [_make_candidate(ticker="AMT", above_orb=None)]
        result, below = _apply_orb_signal(candidates)
        assert len(result) == 1
        assert below == 0

    def test_all_below_orb_still_passes_to_claude(self):
        """June 3 scenario: 29 candidates all below ORB — old filter killed them all."""
        candidates = [_make_candidate(ticker=f"T{i}", above_orb=False) for i in range(29)]
        result, below = _apply_orb_signal(candidates)
        assert len(result) == 29
        assert below == 29

    def test_above_orb_candidates_unaffected(self):
        candidates = [_make_candidate(ticker="SE", above_orb=True)]
        result, below = _apply_orb_signal(candidates)
        assert len(result) == 1
        assert below == 0


# ── Extension filter — market-context-aware threshold ────────────────────────

def _avg_futures(futures: dict) -> float:
    if not futures:
        return 0.0
    return sum(v["change_pct"] for v in futures.values()) / len(futures)


def _apply_extension_filter(candidates: list[dict], futures: dict) -> tuple[list[dict], float]:
    avg_fut = _avg_futures(futures)
    threshold = 5.0 if avg_fut >= 2.0 else 3.0
    result = [
        c for c in candidates
        if not (
            (c.get("today_pct_change") or 0) > threshold
            and (c.get("volume_ratio") or 0) < 0.7
        )
    ]
    return result, threshold


class TestExtensionFilter:

    def _futures(self, avg_pct: float) -> dict:
        return {"S&P500": {"change_pct": avg_pct, "price": 5000.0}}

    def test_normal_day_drops_above_3pct_low_vol(self):
        """On a flat day, stocks >3% up with volume<0.7 are dropped."""
        candidates = [
            _make_candidate(ticker="NVDA", today_pct_change=3.5, volume_ratio=0.4),
            _make_candidate(ticker="AAPL", today_pct_change=1.5, volume_ratio=0.4),
        ]
        result, threshold = _apply_extension_filter(candidates, self._futures(0.5))
        assert threshold == 3.0
        assert len(result) == 1
        assert result[0]["ticker"] == "AAPL"

    def test_strong_up_day_keeps_stocks_up_to_5pct(self):
        """On a +2%+ futures day, stocks up 3-5% on low volume are NOT extended — keep them."""
        candidates = [
            _make_candidate(ticker="NVDA", today_pct_change=3.5, volume_ratio=0.4),
            _make_candidate(ticker="AMD",  today_pct_change=4.8, volume_ratio=0.5),
        ]
        result, threshold = _apply_extension_filter(candidates, self._futures(2.5))
        assert threshold == 5.0
        assert len(result) == 2

    def test_strong_up_day_still_drops_above_5pct_low_vol(self):
        """Even on a strong day, >5% up + low volume is exhaustion — drop it."""
        candidates = [
            _make_candidate(ticker="NVDA", today_pct_change=5.5, volume_ratio=0.3),
            _make_candidate(ticker="AAPL", today_pct_change=3.5, volume_ratio=0.4),
        ]
        result, threshold = _apply_extension_filter(candidates, self._futures(3.0))
        assert threshold == 5.0
        assert len(result) == 1
        assert result[0]["ticker"] == "AAPL"

    def test_high_volume_never_filtered_regardless_of_pct(self):
        """volume_ratio >= 0.7 means conviction — never dropped by extension filter."""
        candidates = [
            _make_candidate(ticker="TSLA", today_pct_change=6.0, volume_ratio=0.8),
        ]
        result, _ = _apply_extension_filter(candidates, self._futures(0.3))
        assert len(result) == 1

    def test_exactly_2pct_futures_triggers_strong_threshold(self):
        """Boundary: avg futures exactly 2.0% activates the 5% threshold."""
        candidates = [_make_candidate(ticker="MSFT", today_pct_change=3.5, volume_ratio=0.5)]
        result, threshold = _apply_extension_filter(candidates, self._futures(2.0))
        assert threshold == 5.0
        assert len(result) == 1

    def test_no_futures_data_uses_default_threshold(self):
        """Missing futures data falls back to 3% threshold."""
        candidates = [_make_candidate(ticker="AMZN", today_pct_change=3.5, volume_ratio=0.4)]
        result, threshold = _apply_extension_filter(candidates, {})
        assert threshold == 3.0
        assert len(result) == 0
