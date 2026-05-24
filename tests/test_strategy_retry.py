"""
Tests for Gap 1 fix: Anthropic API retry logic in agents/strategy.py

Verifies:
  - Transient API errors are retried up to 3 times
  - Successful response on second attempt returns trades normally
  - All 3 attempts failing returns empty trades (no crash)
  - Non-retryable errors (e.g. AuthenticationError) are NOT caught/retried
"""
import pytest
from unittest.mock import patch, MagicMock, call
import anthropic


def _make_response(trades: list) -> MagicMock:
    resp = MagicMock()
    import json
    resp.content = [MagicMock(text=json.dumps({
        "trades": trades,
        "market_context": "test",
        "total_estimated_profit": 0,
    }))]
    resp.usage = MagicMock(
        cache_creation_input_tokens=0,
        cache_read_input_tokens=0,
    )
    return resp


def _make_candidate() -> dict:
    return {
        "ticker": "AAPL", "action": "BUY", "technical_score": 7,
        "current_price": 180.0, "volume_ratio": 1.5,
    }


class TestStrategyRetry:

    def test_success_on_first_attempt(self):
        """Happy path — no retry needed."""
        from agents.strategy import run
        with patch("agents.strategy.client") as mock_client:
            mock_client.messages.create.return_value = _make_response([
                {"ticker": "AAPL", "action": "BUY", "entry_price": 180.0,
                 "target_price": 183.6, "stop_loss": 178.8,
                 "shares": 30, "confidence": "MEDIUM",
                 "position_size": 5400, "estimated_profit": 108}
            ])
            result = run([_make_candidate()])

        assert len(result["trades"]) == 1
        assert mock_client.messages.create.call_count == 1

    def test_retry_on_connection_error_then_success(self):
        """First call raises APIConnectionError; second succeeds — returns trades."""
        from agents.strategy import run
        with patch("agents.strategy.client") as mock_client, \
             patch("agents.strategy.time.sleep"):
            mock_client.messages.create.side_effect = [
                anthropic.APIConnectionError(request=MagicMock()),
                _make_response([{"ticker": "AAPL", "action": "BUY",
                                 "entry_price": 180.0, "target_price": 183.6,
                                 "stop_loss": 178.8, "shares": 30,
                                 "confidence": "MEDIUM", "position_size": 5400,
                                 "estimated_profit": 108}]),
            ]
            result = run([_make_candidate()])

        assert len(result["trades"]) == 1
        assert mock_client.messages.create.call_count == 2

    def test_retry_on_timeout_error(self):
        """APITimeoutError triggers retry."""
        from agents.strategy import run
        with patch("agents.strategy.client") as mock_client, \
             patch("agents.strategy.time.sleep"):
            mock_client.messages.create.side_effect = [
                anthropic.APITimeoutError(request=MagicMock()),
                _make_response([]),
            ]
            result = run([_make_candidate()])

        assert mock_client.messages.create.call_count == 2

    def test_retry_on_rate_limit_error(self):
        """RateLimitError triggers retry."""
        from agents.strategy import run
        with patch("agents.strategy.client") as mock_client, \
             patch("agents.strategy.time.sleep"):
            mock_client.messages.create.side_effect = [
                anthropic.RateLimitError(
                    message="rate limit", response=MagicMock(status_code=429),
                    body={},
                ),
                _make_response([]),
            ]
            result = run([_make_candidate()])

        assert mock_client.messages.create.call_count == 2

    def test_all_attempts_fail_returns_empty_no_crash(self):
        """3 consecutive APIConnectionErrors → returns empty trades, does not raise."""
        from agents.strategy import run
        with patch("agents.strategy.client") as mock_client, \
             patch("agents.strategy.time.sleep"):
            mock_client.messages.create.side_effect = anthropic.APIConnectionError(
                request=MagicMock()
            )
            result = run([_make_candidate()])

        assert result["trades"] == []
        assert mock_client.messages.create.call_count == 3

    def test_all_attempts_fail_returns_empty_no_crash_internal_server(self):
        """3 consecutive InternalServerErrors → returns empty trades, does not raise."""
        from agents.strategy import run
        with patch("agents.strategy.client") as mock_client, \
             patch("agents.strategy.time.sleep"):
            mock_client.messages.create.side_effect = anthropic.InternalServerError(
                message="500", response=MagicMock(status_code=500), body={},
            )
            result = run([_make_candidate()])

        assert result["trades"] == []
        assert mock_client.messages.create.call_count == 3

    def test_sleep_backoff_on_retry(self):
        """Sleep durations increase: 15s, 30s between attempts."""
        from agents.strategy import run
        with patch("agents.strategy.client") as mock_client, \
             patch("agents.strategy.time.sleep") as mock_sleep:
            mock_client.messages.create.side_effect = [
                anthropic.APIConnectionError(request=MagicMock()),
                anthropic.APIConnectionError(request=MagicMock()),
                _make_response([]),
            ]
            run([_make_candidate()])

        sleep_calls = [c.args[0] for c in mock_sleep.call_args_list]
        assert sleep_calls == [15, 30], f"Expected [15, 30], got {sleep_calls}"

    def test_empty_candidates_skips_api(self):
        """No candidates → returns immediately, API never called."""
        from agents.strategy import run
        with patch("agents.strategy.client") as mock_client:
            result = run([])

        mock_client.messages.create.assert_not_called()
        assert result["trades"] == []
