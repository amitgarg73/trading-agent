"""
Regression test for halt_reasons TypeError in orchestrator.premarket().

Pre-fix: guardrail_blocked is a list of dicts {"ticker": .., "reason": ..}.
         halt_reasons was built by concatenating that list directly, then
         "; ".join(halt_reasons) raised TypeError because dicts aren't strings.
Post-fix: halt_reasons extracts .get("reason") from each dict before joining.

Tests call premarket() with all pipeline dependencies mocked so the function
reaches the halt_reasons assembly block and join without raising.
"""
from unittest.mock import patch, MagicMock, call
from datetime import date


def _premarket_mocks(guardrail_blocked_dicts):
    """
    Run orchestrator.premarket(broker='simulation') with the minimum set of
    mocks needed to reach the halt_reasons assembly. guardrails.filter_trades
    is set to block all trades with the given list of dicts so the
    "; ".join(halt_reasons) line is exercised.
    """
    candidate = {
        "ticker": "AAPL", "technical_score": 6, "action": "BUY",
        "price": 180.0, "current_price": 180.0, "rsi": 55.0,
    }
    trade = {
        "ticker": "AAPL", "action": "BUY",
        "entry_price": 180.0, "target_price": 187.2, "stop_loss": 176.5,
        "shares": 16, "position_size": 2880.0, "confidence": "MEDIUM",
        "reward_risk": 2.5, "estimated_profit": 115.2, "reasoning": "test",
    }
    mkt = {
        "decision": "GO", "max_positions": 5, "quiet_day": False,
        "vix": 20.0, "fear_greed": 50, "economic_events": [],
        "futures": {}, "intl_markets": {}, "futures_bias": "neutral",
        "summary": "flat",
    }

    def db_select(table, **kw):
        f = kw.get("filters", {})
        if table == "scan_results":
            return []
        if table == "positions" and f.get("status") == "OPEN":
            return []
        return []

    mock_db_update = MagicMock(return_value=[])

    with patch("orchestrator._is_trading_day", return_value=True), \
         patch("orchestrator._is_halted", return_value=False), \
         patch("orchestrator._log_run"), \
         patch("core.db.select", side_effect=db_select), \
         patch("core.db.insert", return_value={"id": "scan-001", "results": {}}), \
         patch("core.db.update", mock_db_update), \
         patch("agents.market_context.run", return_value=mkt), \
         patch("orchestrator.load_universe", return_value=["AAPL"]), \
         patch("orchestrator.run_scan", return_value=[candidate]), \
         patch("agents.news_intel.run", return_value={
             "filtered_candidates": [candidate], "blackout_tickers": [], "news_context": ""
         }), \
         patch("orchestrator.ml_available", return_value=False), \
         patch("agents.strategy.run", return_value={"trades": [trade], "market_context": ""}), \
         patch("agents.risk.run", return_value={"approved_trades": [trade], "rejected_trades": []}), \
         patch("agents.sector_guard.run", return_value={"approved_trades": [trade], "sector_blocked": []}), \
         patch("agents.atr_sizer.apply", return_value=([trade], [])), \
         patch("agents.guardrails.filter_trades", return_value={
             "approved_trades": [],
             "guardrail_blocked": guardrail_blocked_dicts,
         }):
        from orchestrator import premarket
        premarket(broker="simulation")

    return mock_db_update


def test_halt_reasons_no_type_error_with_dict_guardrail_blocked():
    """"; ".join(halt_reasons) must not raise TypeError when guardrail_blocked has dicts."""
    blocked = [
        {"ticker": "AAPL", "reason": "daily loss limit reached"},
        {"ticker": "MSFT", "reason": "max positions open"},
    ]
    mock_db_update = _premarket_mocks(blocked)
    # If we reach here, no TypeError was raised — the fix works.
    assert mock_db_update.called


def test_halt_reasons_are_strings_when_guardrails_block():
    """halt_reasons stored in scan_results must all be strings, not dicts."""
    blocked = [
        {"ticker": "AAPL", "reason": "daily loss limit reached"},
        {"ticker": "MSFT", "reason": "max positions open"},
    ]
    mock_db_update = _premarket_mocks(blocked)

    scan_result_updates = [
        c for c in mock_db_update.call_args_list
        if c[0][0] == "scan_results"
    ]
    assert scan_result_updates, "db.update('scan_results', ...) must be called"

    result_payload = scan_result_updates[0][0][2]["results"]
    halt_reasons = result_payload.get("halt_reasons", [])

    assert isinstance(halt_reasons, list)
    assert all(isinstance(r, str) for r in halt_reasons), (
        f"halt_reasons contains non-strings: {halt_reasons}"
    )
    assert any("daily loss limit" in r for r in halt_reasons)


def test_halt_reasons_reason_field_extracted_not_full_dict():
    """guardrail_blocked dicts must have .get('reason') extracted — not str(dict)."""
    blocked = [{"ticker": "AAPL", "reason": "capital cap exceeded"}]
    mock_db_update = _premarket_mocks(blocked)

    scan_result_updates = [
        c for c in mock_db_update.call_args_list
        if c[0][0] == "scan_results"
    ]
    result_payload = scan_result_updates[0][0][2]["results"]
    halt_reasons = result_payload.get("halt_reasons", [])

    assert not any("ticker" in r for r in halt_reasons), (
        "halt_reasons must not contain raw dict repr with 'ticker' key"
    )
    assert any("capital cap exceeded" in r for r in halt_reasons)
