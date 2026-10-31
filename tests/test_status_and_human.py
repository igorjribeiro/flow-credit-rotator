"""Tests for account status transitions and human-in-the-loop verification flows."""

import pytest
from flow_account.models import AccountStatus, Mode
from flow_account.manager import (
    FlowAccountManager,
    AccountNotFoundError,
    NoAccountsAvailableError,
)


def test_disable_account_removes_from_selection(manager):
    """Disabling an account sets DISABLED status, makes it ineligible, and excludes from selection."""
    acc1 = manager.add_account("dis1@example.com", priority=1)
    acc2 = manager.add_account("dis2@example.com", priority=2)

    manager.set_mode(Mode.AUTO)
    curr = manager.current_account()
    assert curr.id == acc1.id

    # Disable acc1
    disabled_acc = manager.disable(acc1.id)
    assert disabled_acc.status == AccountStatus.DISABLED
    assert disabled_acc.is_eligible is False

    # In AUTO mode, disabling the current account forces rotation to acc2
    new_curr = manager.current_account()
    assert new_curr is not None
    assert new_curr.id == acc2.id


def test_disable_by_email(manager):
    """Accounts can be disabled using their email identifier."""
    acc = manager.add_account("by_email@example.com")
    disabled = manager.disable("by_email@example.com")
    assert disabled.id == acc.id
    assert disabled.status == AccountStatus.DISABLED


def test_enable_account_restores_active(manager):
    """Enabling a DISABLED account restores ACTIVE status and eligibility."""
    acc = manager.add_account("enable_me@example.com")
    manager.disable(acc.id)
    assert manager.select_account(acc.id).status == AccountStatus.DISABLED

    re_enabled = manager.enable(acc.id)
    assert re_enabled.status == AccountStatus.ACTIVE
    assert re_enabled.is_eligible is True


def test_enable_account_with_exhausted_limits_stays_exhausted(manager):
    """Enabling an account that has already used all its quota results in EXHAUSTED status."""
    acc = manager.add_account("exhaust_enable@example.com", daily_limit=50)
    manager.select_account(acc.id)
    manager.consume(50)

    # Disable it
    manager.disable(acc.id)
    assert manager.select_account(acc.id).status == AccountStatus.DISABLED

    # Re-enable: quota is still exhausted, so status must be EXHAUSTED, not ACTIVE
    restored = manager.enable(acc.id)
    assert restored.status == AccountStatus.EXHAUSTED
    assert restored.is_eligible is False


def test_human_required_suspends_automation_and_rotates(manager):
    """human_required() sets NEEDS_HUMAN, appends reason note, and forces rotation away in AUTO mode."""
    acc1 = manager.add_account("human1@example.com", priority=1)
    acc2 = manager.add_account("human2@example.com", priority=2)

    manager.set_mode(Mode.AUTO)
    curr = manager.current_account()
    assert curr.id == acc1.id

    # Trigger human required on current account with reason
    updated = manager.human_required(acc1.id, reason="CAPTCHA triggered during login")
    assert updated.status == AccountStatus.NEEDS_HUMAN
    assert updated.is_eligible is False
    assert "CAPTCHA triggered during login" in updated.notes
    assert "[HUMAN_REQUIRED" in updated.notes

    # Auto-rotation immediately switches current account to acc2
    new_curr = manager.current_account()
    assert new_curr is not None
    assert new_curr.id == acc2.id


def test_human_cleared_returns_account_to_active_pool(manager):
    """human_cleared() restores an account from NEEDS_HUMAN back to ACTIVE pool."""
    acc1 = manager.add_account("clear1@example.com", priority=1)
    acc2 = manager.add_account("clear2@example.com", priority=2)

    manager.set_mode(Mode.AUTO)
    manager.human_required(acc1.id, reason="SMS OTP needed")

    # While in NEEDS_HUMAN, only acc2 is eligible
    st = manager.status()
    assert st["summary"]["needs_human_accounts"] == 1
    assert st["summary"]["eligible_accounts"] == 1

    # Human operator solves verification and clears it
    cleared = manager.human_cleared(acc1.id)
    assert cleared.status == AccountStatus.ACTIVE
    assert cleared.is_eligible is True
    assert "[HUMAN_CLEARED" in cleared.notes

    st2 = manager.status()
    assert st2["summary"]["needs_human_accounts"] == 0
    assert st2["summary"]["eligible_accounts"] == 2


def test_human_cleared_with_exhausted_limits(manager):
    """human_cleared() on an account with exhausted quota results in EXHAUSTED status."""
    acc = manager.add_account("human_exhaust@example.com", daily_limit=30)
    manager.select_account(acc.id)
    manager.consume(30)

    manager.human_required(acc.id, reason="Suspicious activity checkpoint")
    assert manager.select_account(acc.id).status == AccountStatus.NEEDS_HUMAN

    cleared = manager.human_cleared(acc.id)
    assert cleared.status == AccountStatus.EXHAUSTED
    assert cleared.is_eligible is False


def test_status_actions_on_nonexistent_account_raise_error(manager):
    """Operating on an unknown account ID or email raises AccountNotFoundError."""
    with pytest.raises(AccountNotFoundError):
        manager.disable("missing@example.com")

    with pytest.raises(AccountNotFoundError):
        manager.enable(99999)

    with pytest.raises(AccountNotFoundError):
        manager.human_required(99999, reason="test")

    with pytest.raises(AccountNotFoundError):
        manager.human_cleared("missing@example.com")
