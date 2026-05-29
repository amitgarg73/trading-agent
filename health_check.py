"""
Pre-market health check — runs at 8:45 AM ET Mon–Fri via GitHub Actions.
Checks 5 systems and sends an email alert if anything is broken.
Exits with code 1 on failure so the GitHub Actions run is marked red.
"""
from __future__ import annotations
import os
import sys
import smtplib
from email.mime.text import MIMEText
from datetime import date, timedelta


def check_supabase() -> tuple:
    try:
        from core import db
        db.select("daily_performance", limit=1)
        return True, "Supabase connection OK"
    except Exception as e:
        return False, f"Supabase unreachable: {e}"


def check_alpaca() -> tuple:
    try:
        from agents import alpaca_broker
        buying_power = alpaca_broker.get_buying_power()
        if buying_power is None:
            return False, "Alpaca API unreachable — could not fetch buying power"
        if buying_power < 10000:
            return False, f"Buying power critically low: ${buying_power:,.0f} (need ≥ $10K to trade)"
        return True, f"Alpaca OK — buying power: ${buying_power:,.0f}"
    except Exception as e:
        return False, f"Alpaca check failed: {e}"


def check_anthropic() -> tuple:
    try:
        import anthropic
        client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
        client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=1,
            messages=[{"role": "user", "content": "ping"}],
        )
        return True, "Anthropic API OK — key valid, credit available"
    except anthropic.AuthenticationError:
        return False, "Anthropic API key invalid or expired"
    except anthropic.PermissionDeniedError:
        return False, "Anthropic API credit exhausted — top up at console.anthropic.com"
    except Exception as e:
        return False, f"Anthropic API check failed: {e}"


def check_universe() -> tuple:
    try:
        from core import db
        cutoff = (date.today() - timedelta(days=25)).isoformat()
        rows = db.select("scan_results", filters={"scan_type": "universe_refresh"},
                         order="created_at", limit=1)
        if not rows:
            return False, "No universe refresh found — scanner will use static fallback list"
        last_refresh = rows[0]["date"]
        if last_refresh < cutoff:
            return False, f"Universe refresh stale: last run {last_refresh} (>25 days ago)"
        results = rows[0].get("results") or {}
        count = results.get("count") or len(results.get("tickers", []))
        return True, f"Universe OK — last refresh {last_refresh}, {count or '?'} tickers"
    except Exception as e:
        return False, f"Universe check failed: {e}"


def check_stale_positions() -> tuple:
    try:
        from core import db
        open_pos = db.select("positions", filters={"status": "OPEN"})
        today = date.today().isoformat()
        stale = [p for p in open_pos
                 if not (p.get("opened_at") or "").startswith(today)]
        if stale:
            tickers = ", ".join(p["ticker"] for p in stale)
            return False, (f"Stale open positions from a prior day: {tickers}. "
                           f"EOD may not have closed them — check Alpaca manually.")
        return True, f"No stale positions ({len(open_pos)} open today, expected 0 pre-market)"
    except Exception as e:
        return False, f"Stale position check failed: {e}"


def send_email(subject: str, body: str):
    gmail_user     = os.getenv("GMAIL_USER")
    gmail_password = os.getenv("GMAIL_APP_PASSWORD")
    to_email       = os.getenv("ALERT_EMAIL", gmail_user)

    if not gmail_user or not gmail_password:
        print("  ⚠️  Email not configured — set GMAIL_USER and GMAIL_APP_PASSWORD in GitHub Secrets")
        return

    msg = MIMEText(body)
    msg["Subject"] = subject
    msg["From"]    = gmail_user
    msg["To"]      = to_email

    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as smtp:
            smtp.login(gmail_user, gmail_password)
            smtp.send_message(msg)
        print(f"  ✉️  Alert sent to {to_email}")
    except Exception as e:
        print(f"  ⚠️  Email send failed: {e}")


def run():
    today = date.today().isoformat()
    print(f"\n{'='*55}")
    print(f"  PRE-MARKET HEALTH CHECK — {today}  (8:45 AM ET)")
    print(f"{'='*55}\n")

    checks = [
        ("Supabase",          check_supabase),
        ("Alpaca",            check_alpaca),
        ("Anthropic API",     check_anthropic),
        ("Universe Refresh",  check_universe),
        ("Stale Positions",   check_stale_positions),
    ]

    failures = []
    for name, fn in checks:
        ok, msg = fn()
        icon = "✅" if ok else "❌"
        print(f"  {icon}  {name}: {msg}")
        if not ok:
            failures.append((name, msg))

    print(f"\n{'='*55}\n")

    if failures:
        print(f"  🚨 {len(failures)} check(s) FAILED — sending alert email\n")
        lines = [
            f"Pre-market health check failed — {today}\n\n",
            f"{len(failures)} issue(s) found:\n\n",
        ]
        for name, msg in failures:
            lines.append(f"  ❌ {name}\n     {msg}\n\n")
        lines += [
            "Action required before 9:00 AM ET.\n\n",
            "Useful links:\n",
            "  Dashboard:       https://trading-agent-q39gepfhtsg3ianyezywl7.streamlit.app\n",
            "  GitHub Actions:  https://github.com/amitgarg73/trading-agent/actions\n",
            "  Anthropic:       https://console.anthropic.com\n",
            "  Alpaca paper:    https://app.alpaca.markets/paper/dashboard/overview\n",
        ]
        send_email(
            subject=f"🚨 Trading Agent: {len(failures)} pre-market issue(s) — {today}",
            body="".join(lines),
        )
        sys.exit(1)
    else:
        print("  ✅ All systems healthy — ready for 9:00 AM ET premarket run\n")


if __name__ == "__main__":
    run()
