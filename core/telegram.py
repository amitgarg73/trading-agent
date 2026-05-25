"""
Telegram alert channel. Requires TELEGRAM_BOT_TOKEN + TELEGRAM_CHAT_ID env vars.
Mirrors the send_alert(subject, body) interface from alerts.py.
Uses urllib only — no extra dependencies.
"""
from __future__ import annotations
import json
import os
import urllib.request
import urllib.error


_GITHUB_REPO = os.getenv("GITHUB_REPO", "")


def send_alert(subject: str, body: str) -> bool:
    """Send a Telegram message. Returns True on delivery, False on any failure.
    Logs alert_delivery_failed to the local ledger on exception; never raises."""
    token   = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")

    if not token or not chat_id:
        print(f"  ⚠️  Telegram not configured: {subject}")
        return False

    footer = f"\n\nActions: {_GITHUB_REPO}/actions" if _GITHUB_REPO else ""
    text   = f"{subject}\n\n{body}{footer}"

    try:
        payload = json.dumps({
            "chat_id": chat_id,
            "text":    text,
        }).encode("utf-8")
        req = urllib.request.Request(
            f"https://api.telegram.org/bot{token}/sendMessage",
            data=payload,
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            if resp.status == 200:
                print(f"  📱 Telegram sent: {subject}")
                return True
            return False
    except Exception as e:
        print(f"  ⚠️  Telegram failed ({subject}): {e}")
        try:
            from core import ledger
            ledger.log("alert_delivery_failed", {
                "channel": "telegram",
                "subject": subject,
                "error":   str(e),
            })
        except Exception:
            pass
        return False
