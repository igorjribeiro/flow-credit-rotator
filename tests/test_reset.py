"""Tests for manual and automatic daily/monthly quota resets and rollover."""

import pytest
from datetime import datetime
from zoneinfo import ZoneInfo
from unittest.mock import patch

from flow_account.models import AccountStatus, Mode
from flow_account.manager import FlowAccountManager
from flow_account.reset import get_current_date_and_month


def test_manual_daily_reset(manager):
    """reset_daily() resets used_today to 0, reactivates EXHAUSTED accounts, preserves history and other statuses."""
    a1 = manager.add_account("daily1@example.com", daily_limit=50)
    a2 = manager.add_account("daily2@example.com", daily_limit=50)
    a3 = manager.add_account("disabled_acc@example.com", daily_limit=50)
    a4 = manager.add_account("human_acc@example.com", daily_limit=50)

    manager.select_account(a1.id)
    manager.consume(50, note="Exhausting a1")

    manager.select_account(a2.id)
    manager.consume(20, note="Partial use a2")

    manager.disable(a3.id)
    manager.human_required(a4.id, reason="CAPTCHA required")

    # Verify pre-reset state
    assert manager.select_account(a1.id).status == AccountStatus.EXHAUSTED
    assert manager.select_account(a1.id).used_today == 50
    assert manager.select_account(a2.id).used_today == 20
    assert len(manager.history()) == 2

    # Execute manual daily reset
    result = manager.reset_daily()
    assert result["status"] == "ok"
    assert result["reset_type"] == "daily"
    assert result["accounts_affected"] == 4

    # Post-reset verification
    a1_after = manager.select_account(a1.id)
    assert a1_after.used_today == 0
    assert a1_after.status == AccountStatus.ACTIVE
    assert a1_after.is_eligible is True

    a2_after = manager.select_account(a2.id)
    assert a2_after.used_today == 0
    assert a2_after.status == AccountStatus.ACTIVE

    # DISABLED and NEEDS_HUMAN should NOT be reactivated
    a3_after = manager.select_account(a3.id)
    assert a3_after.status == AccountStatus.DISABLED

    a4_after = manager.select_account(a4.id)
    assert a4_after.status == AccountStatus.NEEDS_HUMAN

    # Audit history is preserved completely
    hist = manager.history()
    assert len(hist) == 2
    assert hist[0].note == "Partial use a2"
    assert hist[1].note == "Exhausting a1"


def test_daily_reset_does_not_reactivate_monthly_exhausted_account(manager):
    """An account that exceeded its monthly limit remains EXHAUSTED after daily reset."""
    a = manager.add_account("month_block@example.com", daily_limit=100, monthly_limit=150)
    manager.select_account(a.id)

    # Use 100 on day 1
    manager.consume(100)
    manager.reset_daily()

    # Use 50 on day 2 -> hits monthly limit (150/150)
    manager.consume(50)
    assert manager.select_account(a.id).status == AccountStatus.EXHAUSTED
    assert manager.select_account(a.id).used_month == 150

    # Daily reset occurs: used_today resets to 0, but status remains EXHAUSTED because used_month >= monthly_limit
    manager.reset_daily()
    acc_after = manager.select_account(a.id)
    assert acc_after.used_today == 0
    assert acc_after.used_month == 150
    assert acc_after.status == AccountStatus.EXHAUSTED
    assert acc_after.is_eligible is False


def test_manual_monthly_reset(manager):
    """reset_monthly() resets used_month to 0 and reactivates accounts blocked by monthly limits."""
    a = manager.add_account("monthly_reset_test@example.com", daily_limit=100, monthly_limit=200)
    manager.select_account(a.id)

    # Day 1: 100
    manager.consume(100)
    manager.reset_daily()

    # Day 2: 100 (monthly limit 200 reached)
    manager.consume(100)
    assert manager.select_account(a.id).status == AccountStatus.EXHAUSTED

    # Daily reset: clears daily, but account remains EXHAUSTED due to monthly limit
    manager.reset_daily()
    assert manager.select_account(a.id).status == AccountStatus.EXHAUSTED

    # Monthly reset executed
    res = manager.reset_monthly()
    assert res["status"] == "ok"
    assert res["reset_type"] == "monthly"

    acc_after = manager.select_account(a.id)
    assert acc_after.used_month == 0
    assert acc_after.status == AccountStatus.ACTIVE
    assert acc_after.is_eligible is True


def test_automatic_daily_reset_on_date_transition(manager):
    """Transitioning to a new date automatically triggers daily reset on subsequent manager call."""
    a = manager.add_account("auto_date@example.com", daily_limit=50)
    manager.select_account(a.id)
    manager.consume(50)
    assert manager.select_account(a.id).status == AccountStatus.EXHAUSTED

    # Simulate that yesterday was recorded in system_state
    with manager.db.transaction() as conn:
        conn.execute("UPDATE system_state SET last_reset_date = '2026-10-01' WHERE id = 1;")

    # When next manager operation runs, check_and_apply_reset runs automatically
    st = manager.status()
    today_str, _, _ = get_current_date_and_month(st["system"]["timezone"])

    # Account should have been reset automatically
    acc = manager.select_account(a.id)
    assert acc.used_today == 0
    assert acc.status == AccountStatus.ACTIVE
    assert acc.is_eligible is True

    # last_reset_date in system_state is updated to today
    st_after = manager.status()
    assert st_after["system"]["last_reset_date"] == today_str


def test_automatic_monthly_reset_on_month_transition(manager):
    """Transitioning to a new calendar month automatically triggers monthly reset."""
    # Account where daily limit is 200, monthly limit is 100
    a = manager.add_account("auto_month@example.com", daily_limit=200, monthly_limit=100)
    manager.select_account(a.id)
    manager.consume(100)  # used_today=100 < daily_limit(200), used_month=100 == monthly_limit
    assert manager.select_account(a.id).status == AccountStatus.EXHAUSTED

    # Simulate last reset was in a previous month
    with manager.db.transaction() as conn:
        conn.execute("UPDATE system_state SET last_reset_month = '2026-08' WHERE id = 1;")

    # Next call to current_account() triggers reset check
    curr = manager.current_account()
    assert curr is not None
    assert curr.id == a.id
    assert curr.used_month == 0
    assert curr.status == AccountStatus.ACTIVE


def test_automatic_combined_month_and_day_rollover(manager):
    """When both month and day rollover occur together (e.g. at 1st of month), both resets execute."""
    a = manager.add_account("combined_rollover@example.com", daily_limit=50, monthly_limit=50)
    manager.select_account(a.id)
    manager.consume(50)  # Exhausts both daily and monthly
    assert manager.select_account(a.id).status == AccountStatus.EXHAUSTED

    # Simulate last reset was last month and last day
    with manager.db.transaction() as conn:
        conn.execute(
            "UPDATE system_state SET last_reset_month = '2026-08', last_reset_date = '2026-08-31' WHERE id = 1;"
        )

    # Next call to current_account() triggers both resets
    curr = manager.current_account()
    assert curr is not None
    assert curr.id == a.id
    assert curr.used_today == 0
    assert curr.used_month == 0
    assert curr.status == AccountStatus.ACTIVE


def test_timezone_america_bahia_evaluation():
    """Verify timezone America/Bahia date/month resolution."""
    date_str, month_str, dt = get_current_date_and_month("America/Bahia")
    assert isinstance(date_str, str)
    assert len(date_str) == 10  # YYYY-MM-DD
    assert len(month_str) == 7  # YYYY-MM
    assert dt.tzinfo.key == "America/Bahia"
