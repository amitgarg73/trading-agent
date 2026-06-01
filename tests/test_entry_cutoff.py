"""
Tests for RF-5: INTRADAY_ENTRY_CUTOFF_UTC gate in _maybe_run_intraday_scan().

Entries after 12:00 PM ET (UTC 16) are negative EV — 14-day data shows 0 targets
and 12-25% win rates from noon onward. Cutoff moved from 3 PM (UTC 19) to noon (UTC 16).
"""
import pytest
from unittest.mock import patch, MagicMock
from datetime import datetime


def _run_scan_at_hour(utc_hour: int) -> bool:
    """
    Call _maybe_run_intraday_scan with the clock set to a specific UTC hour.
    Returns True if a scan was attempted (db.insert for scan_results called),
    False if it returned early.
    """
    from agents.intraday import _maybe_run_intraday_scan

    fake_now = datetime(2026, 5, 26, utc_hour, 30, 0)

    with patch("agents.intraday.datetime") as mock_dt, \
         patch("core.db.select",  return_value=[]) as mock_sel, \
         patch("core.db.insert")  as mock_ins:

        mock_dt.utcnow.return_value = fake_now
        mock_dt.fromisoformat = datetime.fromisoformat
        mock_dt.combine      = datetime.combine

        _maybe_run_intraday_scan(broker="simulation")

    # A scan attempt would call db.select for scan_results with scan_type=intraday_scan
    scan_result_selects = [
        c for c in mock_sel.call_args_list
        if len(c[0]) >= 1 and c[0][0] == "scan_results"
    ]
    return len(scan_result_selects) > 0


class TestEntryCutoff:

    def test_scan_allowed_at_hour_14(self):
        """10:00 AM ET — well within window, scan should proceed to guard checks."""
        assert _run_scan_at_hour(14) is True

    def test_scan_blocked_at_hour_16(self):
        """12:00 PM ET — exactly at new cutoff, must return early."""
        assert _run_scan_at_hour(16) is False

    def test_scan_blocked_at_hour_18(self):
        """2:00 PM ET — past cutoff, must return early."""
        assert _run_scan_at_hour(18) is False

    def test_scan_blocked_at_hour_19(self):
        """3:00 PM ET — well past cutoff."""
        assert _run_scan_at_hour(19) is False

    def test_scan_blocked_at_hour_20(self):
        """4:00 PM ET — well past cutoff."""
        assert _run_scan_at_hour(20) is False

    def test_cutoff_constant_is_16(self):
        from config.settings import INTRADAY_ENTRY_CUTOFF_UTC
        assert INTRADAY_ENTRY_CUTOFF_UTC == 16
