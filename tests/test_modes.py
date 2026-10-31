"""Tests for operation modes: AUTO vs MANUAL, mode switching, and rotation behavior."""

import pytest
from flow_account.models import AccountStatus, Mode, Strategy
from flow_account.manager import (
    FlowAccountManager,
    InvalidOperationError,
    NoAccountsAvailableError,
)


def test_set_mode_transitions_and_validation(manager):
    """Switching modes between AUTO and MANUAL, case-insensitivity, and validation."""
    # Default is AUTO
    st = manager.status()
    assert st["system"]["mode"] == Mode.AUTO.value

    # Switch to MANUAL with string
    res = manager.set_mode("MANUAL")
    assert res == Mode.MANUAL
    assert manager.status()["system"]["mode"] == Mode.MANUAL.value

    # Switch to AUTO with lowercase string
    res2 = manager.set_mode("auto")
    assert res2 == Mode.AUTO
    assert manager.status()["system"]["mode"] == Mode.AUTO.value

    # Switch with Enum
    res3 = manager.set_mode(Mode.MANUAL)
    assert res3 == Mode.MANUAL

    # Invalid mode raises ValueError
    with pytest.raises(ValueError, match="Invalid mode"):
        manager.set_mode("SEMI_AUTO")

    with pytest.raises(ValueError, match="Invalid mode"):
        manager.set_mode("")


def test_manual_mode_no_auto_rotation_on_exhaustion(manager):
    """In MANUAL mode, exhausting an account does NOT rotate to another account automatically."""
    a1 = manager.add_account("manual1@example.com", daily_limit=50, priority=1)
    a2 = manager.add_account("manual2@example.com", daily_limit=50, priority=2)

    manager.set_mode(Mode.MANUAL)
    manager.select_account(a1.id)

    # Confirm current account is a1
    curr = manager.current_account()
    assert curr.id == a1.id

    # Exhaust a1
    updated, _ = manager.consume(50, note="Exhausting in manual mode")
    assert updated.status == AccountStatus.EXHAUSTED
    assert updated.used_today == 50

    # In MANUAL mode, current_account is still a1 (no auto-rotation!)
    curr_after = manager.current_account()
    assert curr_after.id == a1.id
    assert curr_after.status == AccountStatus.EXHAUSTED

    # Subsequent consume in MANUAL mode without selecting a new account fails
    with pytest.raises(InvalidOperationError, match="Selected account .* is not eligible"):
        manager.consume(1)


def test_manual_mode_operator_explicit_selection(manager):
    """In MANUAL mode, operator can explicitly select and switch accounts."""
    a1 = manager.add_account("op1@example.com", daily_limit=50, priority=1)
    a2 = manager.add_account("op2@example.com", daily_limit=50, priority=2)

    manager.set_mode(Mode.MANUAL)

    # Select a2 directly
    selected = manager.select_account(a2.id)
    assert selected.id == a2.id
    assert manager.current_account().id == a2.id

    # Consume on a2
    updated, _ = manager.consume(20)
    assert updated.id == a2.id
    assert updated.used_today == 20

    # Switch to a1 by email
    selected_a1 = manager.select_account("op1@example.com")
    assert selected_a1.id == a1.id
    assert manager.current_account().id == a1.id

    updated_a1, _ = manager.consume(15)
    assert updated_a1.id == a1.id
    assert updated_a1.used_today == 15


def test_manual_mode_consume_without_selection_raises_error(manager):
    """In MANUAL mode, attempting to consume when no account is selected raises InvalidOperationError."""
    manager.add_account("dormant@example.com", daily_limit=50)
    manager.set_mode(Mode.MANUAL)

    # Ensure no account is selected
    with manager.db.transaction() as conn:
        state = manager._get_system_state(conn)
        state.current_account_id = None
        manager._save_system_state(conn, state)

    with pytest.raises(InvalidOperationError, match="No account currently selected"):
        manager.consume(10)


def test_switch_from_manual_to_auto_resumes_rotation(manager):
    """Switching from MANUAL to AUTO immediately resumes automatic selection and rotation."""
    a1 = manager.add_account("switch1@example.com", daily_limit=30, priority=1)
    a2 = manager.add_account("switch2@example.com", daily_limit=50, priority=2)

    manager.set_mode(Mode.MANUAL)
    manager.select_account(a1.id)
    manager.consume(30)  # a1 is now exhausted

    # In MANUAL, current account is still a1
    assert manager.current_account().id == a1.id

    # Switch to AUTO mode
    manager.set_mode(Mode.AUTO)

    # Current account should now automatically resolve to eligible a2
    curr = manager.current_account()
    assert curr is not None
    assert curr.id == a2.id
    assert curr.is_eligible is True

    # Consuming should succeed on a2
    updated, _ = manager.consume(10)
    assert updated.id == a2.id
    assert updated.used_today == 10
