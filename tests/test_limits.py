"""Tests for credit limits: daily limits, monthly limits, and limit adjustments."""

import pytest
from flow_account.models import AccountStatus, Mode
from flow_account.manager import (
    FlowAccountManager,
    AccountNotFoundError,
    NoAccountsAvailableError,
)


def test_daily_limit_exactly_reached(manager):
    """When used_today == daily_limit, account becomes EXHAUSTED, ineligible, and rotates in AUTO."""
    acc1 = manager.add_account("lim1@example.com", daily_limit=85, priority=1)
    acc2 = manager.add_account("lim2@example.com", daily_limit=85, priority=2)

    manager.set_mode(Mode.AUTO)
    curr = manager.current_account()
    assert curr.id == acc1.id

    # Consume exactly 85
    updated, record = manager.consume(85, note="Exact limit consumption")
    assert updated.id == acc1.id
    assert updated.used_today == 85
    assert updated.status == AccountStatus.EXHAUSTED
    assert updated.is_eligible is False
    assert updated.available_credits == 0

    # Auto-rotation should have selected acc2
    new_curr = manager.current_account()
    assert new_curr is not None
    assert new_curr.id == acc2.id
    assert new_curr.is_eligible is True


def test_daily_limit_exceeded(manager):
    """When consumption causes used_today > daily_limit, account is marked EXHAUSTED with 0 available."""
    acc1 = manager.add_account("over1@example.com", daily_limit=50, priority=1)
    acc2 = manager.add_account("over2@example.com", daily_limit=50, priority=2)

    manager.set_mode(Mode.AUTO)
    curr = manager.current_account()
    assert curr.id == acc1.id

    # Consume 60 credits on 50 limit account
    updated, record = manager.consume(60, note="Over limit consumption")
    assert updated.used_today == 60
    assert updated.status == AccountStatus.EXHAUSTED
    assert updated.is_eligible is False
    assert updated.available_credits == 0

    # Next current account is acc2
    new_curr = manager.current_account()
    assert new_curr.id == acc2.id


def test_set_default_limit(manager):
    """Updating system-wide default daily limit affects subsequent accounts created without limit."""
    # Default is 85 initially
    initial_st = manager.status()
    assert initial_st["system"]["default_daily_limit"] == 85

    # Change default limit to 120
    new_limit = manager.set_default_limit(120)
    assert new_limit == 120

    st = manager.status()
    assert st["system"]["default_daily_limit"] == 120

    # Create account without explicit daily_limit
    acc = manager.add_account("default_test@example.com")
    assert acc.daily_limit == 120

    # Check validation for invalid default limit
    with pytest.raises(ValueError, match="positive integer"):
        manager.set_default_limit(0)

    with pytest.raises(ValueError, match="positive integer"):
        manager.set_default_limit(-10)


def test_set_individual_limit(manager):
    """Changing individual account limit dynamically updates headroom and reactivates EXHAUSTED."""
    acc = manager.add_account("indiv@example.com", daily_limit=50, priority=1)
    manager.select_account(acc.id)

    # Consume 50 credits to exhaust it
    updated, _ = manager.consume(50)
    assert updated.status == AccountStatus.EXHAUSTED
    assert updated.is_eligible is False

    # Increase daily limit to 100
    raised = manager.set_limit(acc.id, daily_limit=100)
    assert raised.daily_limit == 100
    assert raised.status == AccountStatus.ACTIVE
    assert raised.is_eligible is True
    assert raised.available_credits == 50

    # Find by email works as well
    raised_by_email = manager.set_limit("indiv@example.com", daily_limit=150)
    assert raised_by_email.daily_limit == 150
    assert raised_by_email.available_credits == 100

    # Non-existent account raises AccountNotFoundError
    with pytest.raises(AccountNotFoundError):
        manager.set_limit("nonexistent@example.com", daily_limit=100)

    # Invalid daily limits raise ValueError
    with pytest.raises(ValueError, match="positive integer"):
        manager.set_limit(acc.id, daily_limit=0)

    with pytest.raises(ValueError, match="positive integer"):
        manager.set_limit(acc.id, daily_limit=-5)


def test_monthly_limit_reached(manager):
    """When used_month reaches monthly_limit, account becomes EXHAUSTED and ineligible."""
    acc1 = manager.add_account("month1@example.com", daily_limit=100, monthly_limit=200, priority=1)
    acc2 = manager.add_account("month2@example.com", daily_limit=100, monthly_limit=200, priority=2)

    manager.set_mode(Mode.AUTO)
    curr = manager.current_account()
    assert curr.id == acc1.id

    # Consume 100 credits on day 1
    manager.consume(100, note="Day 1 usage")

    # Reset daily usage to simulate next day in the same month
    manager.reset_daily()

    # Re-check acc1: used_today is 0, used_month is 100. Still eligible.
    acc1_after_reset = manager.select_account(acc1.id)
    assert acc1_after_reset.used_today == 0
    assert acc1_after_reset.used_month == 100
    assert acc1_after_reset.status == AccountStatus.ACTIVE
    assert acc1_after_reset.is_eligible is True
    assert acc1_after_reset.available_credits == 100  # min(100 - 0, 200 - 100) = 100

    # Consume another 100 credits -> hits monthly limit (200 / 200)
    exhausted_acc, _ = manager.consume(100, note="Day 2 usage hits monthly limit")
    assert exhausted_acc.used_month == 200
    assert exhausted_acc.status == AccountStatus.EXHAUSTED
    assert exhausted_acc.is_eligible is False
    assert exhausted_acc.available_credits == 0

    # In AUTO mode, it should rotate to acc2
    curr_after = manager.current_account()
    assert curr_after.id == acc2.id


def test_monthly_limit_exceeded(manager):
    """When consumption exceeds monthly_limit, account is EXHAUSTED with 0 available."""
    acc = manager.add_account("over_month@example.com", daily_limit=100, monthly_limit=50)
    manager.select_account(acc.id)

    updated, _ = manager.consume(75, note="Exceeding monthly limit")
    assert updated.used_month == 75
    assert updated.status == AccountStatus.EXHAUSTED
    assert updated.is_eligible is False
    assert updated.available_credits == 0


def test_monthly_limit_restored_via_set_limit(manager):
    """Increasing monthly limit on an account exhausted by monthly limit reactivates it."""
    acc = manager.add_account("reactivate_month@example.com", daily_limit=100, monthly_limit=50)
    manager.select_account(acc.id)
    manager.consume(50)

    # Now daily usage is 50/100, monthly usage is 50/50 -> EXHAUSTED
    exhausted = manager.select_account(acc.id)
    assert exhausted.status == AccountStatus.EXHAUSTED

    # Raising monthly limit to 100 restores ACTIVE status because used_today (50) < daily_limit (100)
    # and used_month (50) < monthly_limit (100)
    restored = manager.set_limit(acc.id, daily_limit=100, monthly_limit=100)
    assert restored.status == AccountStatus.ACTIVE
    assert restored.is_eligible is True
    assert restored.available_credits == 50
