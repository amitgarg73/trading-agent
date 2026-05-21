"""
One-shot backfill: fixes zero-P&L STOP exits from 2026-05-21 caused by the
race-condition bug. Fetches actual exit prices from Alpaca and updates
realized_pnl, close_price, and exit_mechanism for affected rows.

Run once: python3 backfill_pnl_20260521.py
"""
from dotenv import load_dotenv
load_dotenv()

from core import db
from agents.alpaca_broker import get_order_fill

TARGET_DATE = "2026-05-21"

positions = db.select("positions", filters={"status": "CLOSED"})
bad = [
    p for p in positions
    if (p.get("closed_at") or "")[:10] == TARGET_DATE
    and p.get("close_reason") == "STOP"
    and (p.get("realized_pnl") or 0) == 0
    and p.get("alpaca_order_id")
]

print(f"Found {len(bad)} zero-P&L STOP exits to backfill on {TARGET_DATE}\n")
fixed = skipped = 0

for pos in bad:
    order_id   = pos["alpaca_order_id"]
    close_price, mechanism = get_order_fill(order_id)

    if close_price is None:
        print(f"  ⚠️  {pos['ticker']:6s}  order={order_id[:8]}… — no fill data, skipping")
        skipped += 1
        continue

    entry  = float(pos["entry_price"])
    shares = int(pos["shares"])
    pnl    = round(shares * (close_price - entry), 2)

    db.update("positions", {"id": pos["id"]}, {
        "close_price":    close_price,
        "realized_pnl":   pnl,
        "exit_mechanism": mechanism,
    })
    print(f"  ✅ {pos['ticker']:6s}  entry=${entry:.2f}  close=${close_price:.4f}"
          f"  shares={shares}  pnl=${pnl:.2f}  ({mechanism})")
    fixed += 1

print(f"\nDone — fixed {fixed}, skipped {skipped} of {len(bad)} records")
