"""Tests for dynamic account pools and rotation strategies.

Covers dynamic pool scalability (1, 2, 5, 20, 50 accounts), sequential rotation
ordering (1 -> 2 -> 3), skipping unavailable accounts (DISABLED, NEEDS_HUMAN, EXHAUSTED),
loop-back to first eligible account, 100% eligible random selection, batch imports
of variable quantities, and verification of zero hardcoded account limits.
"""

import re
from pathlib import Path
import pytest

from flow_account.manager import FlowAccountManager, NoAccountsAvailableError
from flow_account.models import AccountStatus, Strategy, Mode
from flow_account.strategies import RandomStrategy, SequentialStrategy


def test_single_account_pool(manager):
    """Test operations on a dynamic pool containing exactly one account."""
    acc = manager.add_account(
        email="single@example.com",
        daily_limit=50,
        monthly_limit=1000,
        priority=1,
        credential_ref="ENV:SINGLE_01",
    )

    accounts = manager.list_accounts()
    assert len(accounts) == 1
    assert accounts[0].id == acc.id

    # AUTO mode should immediately select the single account
    curr = manager.current_account()
    assert curr is not None
    assert curr.id == acc.id
    assert curr.email == "single@example.com"
    assert curr.available_credits == 50

    # Consume credits within limit
    updated, record = manager.consume(20, note="Single pool consume")
    assert updated.used_today == 20
    assert updated.available_credits == 30

    # Calling next_account on a single eligible account retains that account
    next_acc = manager.next_account()
    assert next_acc.id == acc.id

    # Exhaust remaining credits
    updated_exh, _ = manager.consume(30, note="Exhausting single account")
    assert updated_exh.status == AccountStatus.EXHAUSTED
    assert updated_exh.available_credits == 0

    # After exhaustion, no accounts are available
    assert manager.current_account() is None
    with pytest.raises(NoAccountsAvailableError):
        manager.next_account()


def test_two_accounts_pool(manager):
    """Test operations and rotation on a dynamic pool of two accounts."""
    acc1 = manager.add_account("two_1@example.com", daily_limit=40, priority=1)
    acc2 = manager.add_account("two_2@example.com", daily_limit=60, priority=2)

    accounts = manager.list_accounts()
    assert len(accounts) == 2

    # Initial selection should pick highest priority (Account 1)
    curr = manager.current_account()
    assert curr is not None
    assert curr.id == acc1.id

    # Explicit advance: 1 -> 2
    next_acc = manager.next_account()
    assert next_acc.id == acc2.id

    # Explicit advance: 2 -> 1 (loop back)
    next_back = manager.next_account()
    assert next_back.id == acc1.id

    # Auto-rotation on exhaustion: consume all 40 credits on acc1
    manager.select_account(acc1.id)
    manager.consume(40, note="Exhausting acc1")
    new_curr = manager.current_account()
    assert new_curr is not None
    assert new_curr.id == acc2.id
    assert new_curr.is_eligible is True

    # acc2 can consume normally
    updated2, _ = manager.consume(25, note="Consume acc2")
    assert updated2.id == acc2.id
    assert updated2.used_today == 25
    assert updated2.available_credits == 35


def test_variable_quantity_accounts(manager):
    """Test dynamic pools supporting arbitrary quantities: 5, 20, and 50 accounts."""
    # 1. Pool of 5 accounts
    for i in range(1, 6):
        manager.add_account(f"pool5_{i}@example.com", daily_limit=50, priority=i)

    pool5 = manager.list_accounts()
    assert len(pool5) == 5
    status5 = manager.status()
    assert status5["summary"]["total_accounts"] == 5
    assert status5["summary"]["eligible_accounts"] == 5
    curr5 = manager.current_account()
    assert curr5 is not None
    assert curr5.priority == 1
    manager.consume(10, note="Test pool 5")

    # 2. Expand pool to 20 accounts (add 15 more)
    for i in range(6, 21):
        manager.add_account(f"pool20_{i}@example.com", daily_limit=60, priority=i)

    pool20 = manager.list_accounts()
    assert len(pool20) == 20
    status20 = manager.status()
    assert status20["summary"]["total_accounts"] == 20
    assert status20["summary"]["eligible_accounts"] == 20
    # Advance across multiple accounts in the expanded pool
    for _ in range(5):
        adv = manager.next_account()
        assert adv is not None
        assert adv.is_eligible is True

    # 3. Expand pool to 50 accounts (add 30 more)
    for i in range(21, 51):
        manager.add_account(f"pool50_{i}@example.com", daily_limit=80, priority=i)

    pool50 = manager.list_accounts()
    assert len(pool50) == 50
    status50 = manager.status()
    assert status50["summary"]["total_accounts"] == 50
    assert status50["summary"]["eligible_accounts"] == 50
    assert status50["summary"]["total_available_credits"] > 0
    curr50 = manager.current_account()
    assert curr50 is not None
    assert curr50.is_eligible is True


def test_sequential_rotation_order(manager):
    """Test strict sequential rotation order: 1 -> 2 -> 3."""
    manager.set_strategy(Strategy.SEQUENTIAL)

    acc1 = manager.add_account("seq1@example.com", daily_limit=50, priority=1)
    acc2 = manager.add_account("seq2@example.com", daily_limit=50, priority=2)
    acc3 = manager.add_account("seq3@example.com", daily_limit=50, priority=3)

    # Starts on Account 1
    assert manager.current_account().id == acc1.id

    # Deterministic sequence: 1 -> 2 -> 3
    assert manager.next_account().id == acc2.id
    assert manager.next_account().id == acc3.id

    # Auto-rotation sequence on exhaustion: 1 exhausts -> 2, 2 exhausts -> 3
    manager.select_account(acc1.id)
    assert manager.current_account().id == acc1.id

    # Exhaust Account 1 -> rotates to Account 2
    manager.consume(50, note="Exhausting account 1")
    curr_after_1 = manager.current_account()
    assert curr_after_1 is not None
    assert curr_after_1.id == acc2.id

    # Exhaust Account 2 -> rotates to Account 3
    manager.consume(50, note="Exhausting account 2")
    curr_after_2 = manager.current_account()
    assert curr_after_2 is not None
    assert curr_after_2.id == acc3.id


def test_skip_unavailable_accounts(manager):
    """Test sequential strategy skips DISABLED, NEEDS_HUMAN, and EXHAUSTED accounts."""
    manager.set_strategy(Strategy.SEQUENTIAL)

    acc1 = manager.add_account("skip1@example.com", daily_limit=50, priority=1)
    acc2 = manager.add_account("skip_disabled@example.com", daily_limit=50, priority=2)
    acc3 = manager.add_account("skip_human@example.com", daily_limit=50, priority=3)
    acc4 = manager.add_account("skip_exhausted@example.com", daily_limit=50, priority=4)
    acc5 = manager.add_account("skip5@example.com", daily_limit=50, priority=5)

    # Set states for acc2, acc3, acc4
    manager.disable(acc2.id)
    manager.human_required(acc3.id, reason="CAPTCHA challenge")
    manager.select_account(acc4.id)
    manager.consume(50, note="Exhausting account 4")

    # Start at Account 1
    manager.select_account(acc1.id)
    assert manager.current_account().id == acc1.id

    # When Account 1 exhausts, auto-rotation must skip acc2, acc3, and acc4, landing directly on acc5
    manager.consume(50, note="Exhausting account 1")
    curr = manager.current_account()
    assert curr is not None
    assert curr.id == acc5.id
    assert curr.status == AccountStatus.ACTIVE
    assert curr.is_eligible is True


def test_loop_back_to_first_eligible(manager):
    """Test rotation loops back to the first eligible account when sequence ends."""
    manager.set_strategy(Strategy.SEQUENTIAL)

    acc1 = manager.add_account("loop1@example.com", daily_limit=50, priority=1)
    acc2 = manager.add_account("loop2@example.com", daily_limit=50, priority=2)
    acc3 = manager.add_account("loop3@example.com", daily_limit=50, priority=3)

    # When at Account 3 and advancing explicitly, loops back to Account 1
    manager.select_account(acc3.id)
    assert manager.current_account().id == acc3.id
    next_acc = manager.next_account()
    assert next_acc.id == acc1.id

    # When Account 1 is disabled, advancing from Account 3 loops back to Account 2
    manager.disable(acc1.id)
    manager.select_account(acc3.id)
    next_eligible = manager.next_account()
    assert next_eligible.id == acc2.id

    # Auto-rotation on exhaustion loops back to first eligible account
    manager.select_account(acc3.id)
    manager.consume(50, note="Exhausting account 3")
    curr = manager.current_account()
    assert curr is not None
    assert curr.id == acc2.id


def test_random_strategy_selects_only_eligible(manager):
    """Test random strategy selects 100% eligible accounts over 100 iterations."""
    manager.set_strategy(Strategy.RANDOM)

    active_ids = set()
    for i in range(1, 6):
        acc = manager.add_account(
            f"rand_active_{i}@example.com",
            daily_limit=1000,
            monthly_limit=50000,
            priority=i,
        )
        active_ids.add(acc.id)

    # Add various ineligible accounts
    acc_disabled = manager.add_account("rand_dis@example.com", daily_limit=100)
    manager.disable(acc_disabled.id)

    acc_human = manager.add_account("rand_hum@example.com", daily_limit=100)
    manager.human_required(acc_human.id, reason="CAPTCHA")

    acc_exh_daily = manager.add_account("rand_exh_d@example.com", daily_limit=50)
    manager.select_account(acc_exh_daily.id)
    manager.consume(50, note="Exhausting daily limit")

    acc_exh_monthly = manager.add_account("rand_exh_m@example.com", daily_limit=100, monthly_limit=40)
    manager.select_account(acc_exh_monthly.id)
    manager.consume(40, note="Exhausting monthly limit")

    acc_zero_limit = manager.add_account("rand_zero@example.com", daily_limit=10); manager.select_account(acc_zero_limit.id); manager.consume(10, note="zero limit")

    ineligible_ids = {
        acc_disabled.id,
        acc_human.id,
        acc_exh_daily.id,
        acc_exh_monthly.id,
        acc_zero_limit.id,
    }

    # Execute 100 iterations: all selections must be strictly eligible and active
    for _ in range(100):
        selected = manager.next_account()
        assert selected is not None
        assert selected.id in active_ids
        assert selected.id not in ineligible_ids
        assert selected.status == AccountStatus.ACTIVE
        assert selected.is_eligible is True
        assert selected.available_credits > 0


def test_import_variable_quantities(manager, tmp_path):
    """Test batch importing variable quantities of accounts (3 and then 15 accounts)."""
    # 1. Import initial batch of 3 accounts
    file_3 = tmp_path / "accounts_batch_3.csv"
    csv_3_lines = [
        "email,daily_limit,monthly_limit,priority,status,credential_ref,notes",
        "batch3_1@example.com,85,2500,1,ACTIVE,ENV:B3_01,Initial 1",
        "batch3_2@example.com,85,2500,2,ACTIVE,ENV:B3_02,Initial 2",
        "batch3_3@example.com,85,2500,3,ACTIVE,ENV:B3_03,Initial 3",
    ]
    file_3.write_text("\n".join(csv_3_lines), encoding="utf-8")

    res_3 = manager.import_accounts(str(file_3))
    assert res_3["imported"] == 3
    assert res_3["errors"] == []
    assert len(manager.list_accounts()) == 3

    # 2. Import second batch of 15 accounts
    file_15 = tmp_path / "accounts_batch_15.csv"
    csv_15_lines = ["email,daily_limit,monthly_limit,priority,status,credential_ref,notes"]
    for i in range(1, 16):
        csv_15_lines.append(
            f"batch15_{i}@example.com,90,2700,{i},ACTIVE,ENV:B15_{i:02d},Batch 15 - {i}"
        )
    file_15.write_text("\n".join(csv_15_lines), encoding="utf-8")

    res_15 = manager.import_accounts(str(file_15))
    assert res_15["imported"] == 15
    assert res_15["errors"] == []

    # Combined total is now 18 accounts
    all_accounts = manager.list_accounts()
    assert len(all_accounts) == 18

    # Verify status report reflects the variable quantity
    status = manager.status()
    assert status["summary"]["total_accounts"] == 18
    assert status["summary"]["eligible_accounts"] == 18

    # Verify rotation works smoothly across the 18 accounts
    curr = manager.current_account()
    assert curr is not None
    next_acc = manager.next_account()
    assert next_acc is not None
    assert next_acc.id != curr.id


def test_no_fixed_twelve_account_reference():
    """Verify that flow_account/ contains no MAX_ACCOUNTS or hardcoded 12-account limit."""
    pkg_dir = Path(__file__).resolve().parent.parent / "flow_account"
    assert pkg_dir.is_dir()

    py_files = list(pkg_dir.glob("**/*.py"))
    assert len(py_files) > 0

    for py_file in py_files:
        content = py_file.read_text(encoding="utf-8")

        # Must not define MAX_ACCOUNTS or MAX_POOL_SIZE
        assert "MAX_ACCOUNTS" not in content, f"Found MAX_ACCOUNTS in {py_file}"
        assert "MAX_POOL_SIZE" not in content, f"Found MAX_POOL_SIZE in {py_file}"

        # Check for hardcoded 12-account limit comparisons or assignments
        match = re.search(r"\b(?:len\(.*?\)\s*(?:>=|>|==)\s*12|accounts\s*=\s*12)\b", content)
        assert match is None, f"Found potential hardcoded 12-account limit in {py_file}: {match}"
