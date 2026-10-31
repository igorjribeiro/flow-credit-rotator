"""Tests for data persistence across manager restarts and process lifetimes."""

import pytest
from flow_account.models import AccountStatus, Mode, Strategy
from flow_account.manager import FlowAccountManager


def test_persistence_across_manager_restarts(temp_db_path):
    """Instantiating a new FlowAccountManager on an existing SQLite DB restores full state."""
    # Instance 1: Setup configuration, accounts, and usage
    mgr1 = FlowAccountManager(db_path=temp_db_path)
    mgr1.set_mode(Mode.MANUAL)
    mgr1.set_strategy(Strategy.ROUND_ROBIN)
    mgr1.set_default_limit(110)

    acc1 = mgr1.add_account(
        email="persist1@example.com",
        daily_limit=90,
        monthly_limit=2000,
        priority=1,
        credential_ref="ENV:TEST_CRED_1",
        notes="Primary persistent account",
    )
    acc2 = mgr1.add_account(
        email="persist2@example.com",
        daily_limit=90,
        monthly_limit=2000,
        priority=2,
        credential_ref="ENV:TEST_CRED_2",
        notes="Secondary persistent account",
    )

    # Select acc1 and consume credits
    mgr1.select_account(acc1.id)
    acc1_consumed, rec1 = mgr1.consume(35, note="Batch job 101")

    # Change status on acc2 to disabled
    mgr1.disable(acc2.id)

    # Capture state snapshot
    st1 = mgr1.status()
    hist1 = mgr1.history()

    # Close manager 1
    mgr1.db.close()

    # Instance 2: Connect to same DB file
    mgr2 = FlowAccountManager(db_path=temp_db_path)

    try:
        st2 = mgr2.status()

        # Check system configuration persistence
        assert st2["system"]["mode"] == Mode.MANUAL.value
        assert st2["system"]["strategy"] == Strategy.ROUND_ROBIN.value
        assert st2["system"]["default_daily_limit"] == 110
        assert st2["system"]["current_account_id"] == acc1.id

        # Check accounts persistence
        accounts = mgr2.list_accounts()
        assert len(accounts) == 2

        acc1_loaded = next(a for a in accounts if a.id == acc1.id)
        assert acc1_loaded.email == "persist1@example.com"
        assert acc1_loaded.daily_limit == 90
        assert acc1_loaded.monthly_limit == 2000
        assert acc1_loaded.used_today == 35
        assert acc1_loaded.used_month == 35
        assert acc1_loaded.priority == 1
        assert acc1_loaded.status == AccountStatus.ACTIVE
        assert acc1_loaded.credential_ref == "ENV:TEST_CRED_1"
        assert acc1_loaded.notes == "Primary persistent account"
        assert acc1_loaded.last_used_at is not None

        acc2_loaded = next(a for a in accounts if a.id == acc2.id)
        assert acc2_loaded.status == AccountStatus.DISABLED
        assert acc2_loaded.credential_ref == "ENV:TEST_CRED_2"

        # Check history audit persistence
        hist2 = mgr2.history()
        assert len(hist2) == 1
        assert hist2[0].account_id == acc1.id
        assert hist2[0].amount == 35
        assert hist2[0].note == "Batch job 101"
        assert hist2[0].used_today_after == 35

        # Check further mutations with Instance 2
        acc1_again, rec2 = mgr2.consume(15, note="Batch job 102")
        assert acc1_again.used_today == 50
        assert acc1_again.available_credits == 40

    finally:
        mgr2.db.close()

    # Instance 3: Verify mutations made by Instance 2
    mgr3 = FlowAccountManager(db_path=temp_db_path)
    try:
        curr = mgr3.select_account(acc1.id)
        assert curr.used_today == 50
        assert curr.used_month == 50

        hist3 = mgr3.history()
        assert len(hist3) == 2
        assert hist3[0].note == "Batch job 102"
        assert hist3[1].note == "Batch job 101"
    finally:
        mgr3.db.close()
