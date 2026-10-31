"""Tests for safe and deterministic behavior when no accounts are available."""

import pytest
from flow_account.models import AccountStatus, Mode
from flow_account.manager import (
    FlowAccountManager,
    NoAccountsAvailableError,
    InvalidOperationError,
)


def test_empty_database_behavior(manager):
    """When database has zero accounts registered, operations behave safely without crashing."""
    st = manager.status()
    assert st["current_account"] is None
    assert st["summary"]["total_accounts"] == 0
    assert st["summary"]["eligible_accounts"] == 0
    assert st["summary"]["total_available_credits"] == 0

    assert manager.current_account() is None
    assert manager.list_accounts() == []

    with pytest.raises(NoAccountsAvailableError, match="NO_ACCOUNTS_AVAILABLE"):
        manager.consume(10)

    with pytest.raises(NoAccountsAvailableError, match="NO_ACCOUNTS_AVAILABLE"):
        manager.next_account()


def test_all_accounts_exhausted(manager):
    """When all accounts have depleted their daily limit, manager reports zero eligible and raises NoAccountsAvailableError on consume."""
    a1 = manager.add_account("ex1@example.com", daily_limit=20)
    a2 = manager.add_account("ex2@example.com", daily_limit=20)

    manager.set_mode(Mode.AUTO)

    # Exhaust both accounts
    manager.consume(20)
    manager.consume(20)

    # Check status
    st = manager.status()
    assert st["summary"]["total_accounts"] == 2
    assert st["summary"]["active_accounts"] == 0
    assert st["summary"]["exhausted_accounts"] == 2
    assert st["summary"]["eligible_accounts"] == 0
    assert st["summary"]["total_available_credits"] == 0
    assert st["current_account"] is None

    # current_account returns None cleanly
    assert manager.current_account() is None

    # Next attempt to consume raises NoAccountsAvailableError
    with pytest.raises(NoAccountsAvailableError, match="NO_ACCOUNTS_AVAILABLE"):
        manager.consume(5)

    # next_account raises NoAccountsAvailableError
    with pytest.raises(NoAccountsAvailableError, match="NO_ACCOUNTS_AVAILABLE"):
        manager.next_account()


def test_all_accounts_disabled(manager):
    """When all accounts are DISABLED, no accounts are eligible."""
    a1 = manager.add_account("dis_all1@example.com")
    a2 = manager.add_account("dis_all2@example.com")

    manager.disable(a1.id)
    manager.disable(a2.id)

    st = manager.status()
    assert st["summary"]["disabled_accounts"] == 2
    assert st["summary"]["eligible_accounts"] == 0
    assert st["current_account"] is None

    assert manager.current_account() is None

    with pytest.raises(NoAccountsAvailableError, match="NO_ACCOUNTS_AVAILABLE"):
        manager.consume(1)

    with pytest.raises(NoAccountsAvailableError, match="NO_ACCOUNTS_AVAILABLE"):
        manager.next_account()


def test_all_accounts_needs_human(manager):
    """When all accounts are in NEEDS_HUMAN status, automation is safely halted."""
    a1 = manager.add_account("nh1@example.com")
    a2 = manager.add_account("nh2@example.com")

    manager.human_required(a1.id, reason="CAPTCHA")
    manager.human_required(a2.id, reason="SMS OTP")

    st = manager.status()
    assert st["summary"]["needs_human_accounts"] == 2
    assert st["summary"]["eligible_accounts"] == 0
    assert st["current_account"] is None

    assert manager.current_account() is None

    with pytest.raises(NoAccountsAvailableError, match="NO_ACCOUNTS_AVAILABLE"):
        manager.consume(1)


def test_mixed_ineligible_accounts(manager):
    """Pool with accounts split across EXHAUSTED, DISABLED, and NEEDS_HUMAN."""
    a1 = manager.add_account("mix_exhaust@example.com", daily_limit=10)
    a2 = manager.add_account("mix_disabled@example.com")
    a3 = manager.add_account("mix_human@example.com")

    manager.select_account(a1.id)
    manager.consume(10)  # Exhausted

    manager.disable(a2.id)  # Disabled
    manager.human_required(a3.id, reason="Security review")  # Needs human

    st = manager.status()
    assert st["summary"]["exhausted_accounts"] == 1
    assert st["summary"]["disabled_accounts"] == 1
    assert st["summary"]["needs_human_accounts"] == 1
    assert st["summary"]["eligible_accounts"] == 0
    assert manager.current_account() is None

    with pytest.raises(NoAccountsAvailableError, match="NO_ACCOUNTS_AVAILABLE"):
        manager.consume(1)

    with pytest.raises(NoAccountsAvailableError, match="NO_ACCOUNTS_AVAILABLE"):
        manager.next_account()
