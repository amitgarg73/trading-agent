"""
Validate that migrations/001_daily_runs.sql has been applied correctly.
Run AFTER executing the SQL in the Supabase dashboard.

Usage: python3 validate_daily_runs.py
"""
from dotenv import load_dotenv
load_dotenv()

from core import db

CHECKS = []
ERRORS = []

def check(name: str, ok: bool, detail: str = "") -> None:
    status = "✅" if ok else "❌"
    CHECKS.append((name, ok, detail))
    print(f"  {status}  {name}" + (f" — {detail}" if detail else ""))
    if not ok:
        ERRORS.append(name)

print("\n[ validate_daily_runs.py ] Strategy A schema validation\n")

# 1. daily_runs table exists and is queryable
try:
    rows = db.select("daily_runs", limit=1)
    check("daily_runs table exists", True, f"{len(rows)} existing rows")
except Exception as e:
    check("daily_runs table exists", False, str(e))

# 2. daily_runs has expected columns
try:
    db.insert("daily_runs", {
        "date":               "1970-01-01",
        "run_type":           "premarket",
        "run_number":         99,
        "started_at":         "1970-01-01T00:00:00Z",
        "positions_opened":   0,
        "loss_guard_active":  False,
    })
    probe = db.select("daily_runs", filters={"date": "1970-01-01"})
    db.delete("daily_runs", {"date": "1970-01-01"})
    check("daily_runs columns correct", bool(probe),
          f"probe row inserted and deleted cleanly")
except Exception as e:
    check("daily_runs columns correct", False, str(e))

# 3. UNIQUE(date, run_number) constraint
try:
    db.insert("daily_runs", {"date": "1970-01-02", "run_type": "premarket",
                              "run_number": 0, "started_at": "1970-01-02T00:00:00Z"})
    duplicate_blocked = False
    try:
        db.insert("daily_runs", {"date": "1970-01-02", "run_type": "premarket",
                                  "run_number": 0, "started_at": "1970-01-02T00:00:00Z"})
    except Exception:
        duplicate_blocked = True
    db.delete("daily_runs", {"date": "1970-01-02"})
    check("UNIQUE(date, run_number) enforced", duplicate_blocked)
except Exception as e:
    check("UNIQUE(date, run_number) enforced", False, str(e))

# 4. positions.run_id column exists
try:
    positions = db.select("positions", limit=1)
    if positions:
        has_col = "run_id" in positions[0]
        check("positions.run_id column exists", has_col,
              "found in first row" if has_col else "column missing from row")
    else:
        # No rows — try selecting with explicit run_id filter to force schema check
        db.select("positions", filters={"run_id": None}, limit=1)
        check("positions.run_id column exists", True, "no rows but filter accepted")
except Exception as e:
    check("positions.run_id column exists", False, str(e))

# 5. Existing positions have run_id = NULL (not broken)
try:
    all_pos = db.select("positions", limit=200)
    with_run_id = [p for p in all_pos if p.get("run_id") is not None]
    without = len(all_pos) - len(with_run_id)
    check("existing positions unaffected (run_id=NULL)",
          all(p.get("run_id") is None for p in all_pos),
          f"{without} rows with NULL run_id (legacy), {len(with_run_id)} with run_id")
except Exception as e:
    check("existing positions unaffected", False, str(e))

# Summary
print(f"\n{'─'*50}")
if ERRORS:
    print(f"  ❌  {len(ERRORS)} check(s) failed: {', '.join(ERRORS)}")
    print("\n  Run migrations/001_daily_runs.sql in the Supabase SQL editor first.\n")
    raise SystemExit(1)
else:
    total = len(CHECKS)
    print(f"  ✅  All {total} checks passed — migration is live and correct.\n")
