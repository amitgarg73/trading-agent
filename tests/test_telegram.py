"""
Tests for core/telegram.py.

Covers: configured/unconfigured env, successful send, HTTP failure,
network exception, ledger logging on failure, never raises.
"""
import pytest
from unittest.mock import patch, MagicMock
from pathlib import Path


# ── Helpers ───────────────────────────────────────────────────────

def _with_env(monkeypatch, token="fake-token", chat_id="123456"):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", token)
    monkeypatch.setenv("TELEGRAM_CHAT_ID",   chat_id)


def _mock_http_success():
    resp = MagicMock()
    resp.status = 200
    resp.__enter__ = lambda s: s
    resp.__exit__  = MagicMock(return_value=False)
    return resp


def _mock_http_failure(status=400):
    resp = MagicMock()
    resp.status = status
    resp.__enter__ = lambda s: s
    resp.__exit__  = MagicMock(return_value=False)
    return resp


# ── Unconfigured ──────────────────────────────────────────────────

class TestUnconfigured:
    def test_returns_false_when_no_token(self, monkeypatch):
        monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
        monkeypatch.delenv("TELEGRAM_CHAT_ID",   raising=False)
        from core import telegram
        assert telegram.send_alert("Test", "body") is False

    def test_returns_false_when_missing_chat_id(self, monkeypatch):
        monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "tok")
        monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
        from core import telegram
        assert telegram.send_alert("Test", "body") is False


# ── Successful send ───────────────────────────────────────────────

class TestSuccess:
    def test_returns_true_on_200(self, monkeypatch):
        _with_env(monkeypatch)
        from core import telegram
        with patch("urllib.request.urlopen", return_value=_mock_http_success()):
            result = telegram.send_alert("Subject", "Body text")
        assert result is True

    def test_payload_contains_subject_and_body(self, monkeypatch):
        _with_env(monkeypatch)
        import json
        from core import telegram
        captured = {}

        def fake_urlopen(req, timeout=None):
            captured["data"] = json.loads(req.data.decode())
            return _mock_http_success()

        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            telegram.send_alert("Alert Subject", "Alert body text")

        assert "Alert Subject" in captured["data"]["text"]
        assert "Alert body text" in captured["data"]["text"]
        assert captured["data"]["chat_id"] == "123456"


# ── Failure paths ──────────────────────────────────────────────────

class TestFailure:
    def test_returns_false_on_non_200(self, monkeypatch):
        _with_env(monkeypatch)
        from core import telegram
        with patch("urllib.request.urlopen", return_value=_mock_http_failure(400)):
            result = telegram.send_alert("Fail", "body")
        assert result is False

    def test_returns_false_on_network_exception(self, monkeypatch):
        _with_env(monkeypatch)
        from core import telegram
        with patch("urllib.request.urlopen", side_effect=OSError("network error")):
            result = telegram.send_alert("Fail", "body")
        assert result is False

    def test_logs_to_ledger_on_exception(self, monkeypatch, tmp_path):
        _with_env(monkeypatch)
        import core.ledger as ledger_mod
        monkeypatch.setattr(ledger_mod, "_DATA_DIR", tmp_path)
        from core import telegram
        with patch("urllib.request.urlopen", side_effect=OSError("timeout")):
            telegram.send_alert("Fail", "body")

        events = ledger_mod.read_today()
        assert any(e["event"] == "alert_delivery_failed" for e in events)
        fail_ev = next(e for e in events if e["event"] == "alert_delivery_failed")
        assert fail_ev["data"]["channel"] == "telegram"

    def test_never_raises(self, monkeypatch):
        _with_env(monkeypatch)
        from core import telegram
        # even with completely broken urlopen, must not raise
        with patch("urllib.request.urlopen", side_effect=Exception("unexpected")):
            telegram.send_alert("Crash test", "body")  # must not propagate


# ── GitHub repo footer ────────────────────────────────────────────

class TestFooter:
    def test_footer_included_when_github_repo_set(self, monkeypatch):
        _with_env(monkeypatch)
        monkeypatch.setenv("GITHUB_REPO", "https://github.com/user/repo")
        import importlib, core.telegram as tg_mod
        importlib.reload(tg_mod)   # pick up new env var
        import json
        captured = {}

        def fake_urlopen(req, timeout=None):
            captured["data"] = json.loads(req.data.decode())
            return _mock_http_success()

        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            tg_mod.send_alert("Test", "body")

        assert "github.com/user/repo/actions" in captured["data"]["text"]
        monkeypatch.delenv("GITHUB_REPO", raising=False)
        importlib.reload(tg_mod)
