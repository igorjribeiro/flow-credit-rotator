"""Tests for initial selection and rotation by strategy."""

import pytest
from flow_account.models import AccountStatus, Strategy, Mode
from flow_account.manager import FlowAccountManager


def test_initial_selection_priority_strategy(populated_manager):
    """In AUTO mode with priority strategy, highest priority (1) account is selected."""
    curr = populated_manager.current_account()
    assert curr is not None
    assert curr.priority == 1
    assert curr.email in ("prio1@example.com", "prio1_b@example.com")
    assert curr.is_eligible is True


def test_priority_tie_breaking_by_used_today(manager):
    """When priority is equal, account with lowest used_today is preferred."""
    # Both priority 1, but one has used 10 credits already
    acc1 = manager.add_account("prio1_used@example.com", daily_limit=85, priority=1)
    acc2 = manager.add_account("prio1_clean@example.com", daily_limit=85, priority=1)

    # Simulate usage on acc1
    manager.select_account(acc1.id)
    manager.consume(10, note="Initial use")

    # Now verify current_account in AUTO selects acc2 (0 usage vs 10 usage)
    # We first clear current_account_id or call current_account with strategy
    manager.set_mode(Mode.AUTO)
    # If acc1 is still current, calling next_account() advances to acc2
    next_acc = manager.next_account()
    assert next_acc.id == acc2.id


def test_auto_rotation_on_exhaustion(populated_manager):
    """When an account is exhausted in AUTO mode, it automatically rotates to the next."""
    populated_manager.set_mode(Mode.AUTO)
    curr = populated_manager.current_account()
    assert curr is not None
    first_id = curr.id

    # Consume all remaining credits
    rem = curr.available_credits
    updated, _ = populated_manager.consume(rem, note="Exhausting first account")
    assert updated.status == AccountStatus.EXHAUSTED
    assert updated.is_eligible is False

    # Current account should have automatically rotated to a different eligible account
    new_curr = populated_manager.current_account()
    assert new_curr is not None
    assert new_curr.id != first_id
    assert new_curr.is_eligible is True
    assert new_curr.status == AccountStatus.ACTIVE


def test_round_robin_rotation(manager):
    """Round-robin rotates sequentially through all eligible accounts."""
    a1 = manager.add_account("rr1@example.com", daily_limit=50, priority=1)
    a2 = manager.add_account("rr2@example.com", daily_limit=50, priority=1)
    a3 = manager.add_account("rr3@example.com", daily_limit=50, priority=1)

    manager.set_strategy(Strategy.ROUND_ROBIN)
    manager.select_account(a1.id)

    # Advance 1 -> 2
    next1 = manager.next_account()
    assert next1.id == a2.id

    # Advance 2 -> 3
    next2 = manager.next_account()
    assert next2.id == a3.id

    # Advance 3 -> 1 (circular)
    next3 = manager.next_account()
    assert next3.id == a1.id


def test_round_robin_skips_ineligible(manager):
    """Round-robin skips accounts that are exhausted, disabled, or need human attention."""
    a1 = manager.add_account("rr_active1@example.com", daily_limit=50)
    a2 = manager.add_account("rr_disabled@example.com", daily_limit=50)
    a3 = manager.add_account("rr_active2@example.com", daily_limit=50)

    manager.disable(a2.id)
    manager.set_strategy(Strategy.ROUND_ROBIN)
    manager.select_account(a1.id)

    # Next from a1 should skip a2 and land on a3
    next_acc = manager.next_account()
    assert next_acc.id == a3.id

    # Next from a3 should cycle back to a1
    cycle_acc = manager.next_account()
    assert cycle_acc.id == a1.id


def test_lowest_usage_strategy(manager):
    """Lowest-usage strategy picks the account with the minimum used_today."""
    a1 = manager.add_account("low1@example.com", daily_limit=100, priority=1)
    a2 = manager.add_account("low2@example.com", daily_limit=100, priority=1)
    a3 = manager.add_account("low3@example.com", daily_limit=100, priority=1)

    manager.set_strategy(Strategy.LOWEST_USAGE)

    # Add usage: a1=20, a2=5, a3=40
    manager.select_account(a1.id)
    manager.consume(20)

    manager.select_account(a2.id)
    manager.consume(5)

    manager.select_account(a3.id)
    manager.consume(40)

    # Reset current account selection in state
    manager.set_mode(Mode.AUTO)
    # The next account by lowest usage should be a2 (5 used)
    # Let's force advance or check lowest
    eligible = manager._get_eligible_accounts(manager.db._create_connection())
    strat = manager.set_strategy(Strategy.LOWEST_USAGE)
    from flow_account.strategies import LowestUsageStrategy
    chosen = LowestUsageStrategy().select(eligible, current_account_id=None)
    assert chosen is not None
    assert chosen.id == a2.id
    assert chosen.used_today == 5
