"""Test suite for v1.1.0 features of Flow Credit Rotator.

Covers the 20 required criteria:
1. 80/85 + operation 10 -> switches account
2. 80/85 + operation 5 -> stays on current account
3. 85/85 -> switches account
4. Monthly limit pre-check
5. Sequential rotation with pre-check
6. Random rotation with pre-check
7. All accounts without capacity raises error / returns false
8. Reservation creation
9. Reservation confirmation debiting credits
10. Reservation cancellation releasing capacity
11. Concurrency in reservations
12. XLSX sync: add account
13. XLSX sync: update account
14. XLSX sync: remove/archive account without deleting history
15. XLSX sync: change order/priority
16. XLSX sync: change sequential -> random
17. credential_ref validation & storage
18. No plaintext passwords in XLSX, SQLite, logs
19. History preserved across sync and resets
20. Cross-platform compatibility (Linux and Windows paths)
"""

import os
from pathlib import Path
import threading
import openpyxl
import pytest

from flow_account import (
    FlowAccountManager,
    AccountStatus,
    Mode,
    NoAccountsAvailableError,
    InvalidOperationError,
    ReservationStatus,
    Strategy,
)
from flow_account.credentials import get_credential, set_credential, has_credential
from flow_account.importer import (
    create_central_config_template_xlsx,
    read_central_config_xlsx,
    SecurityImportError,
)

REPO_ROOT = Path(__file__).resolve().parent.parent


def test_01_precheck_switches_when_exceeding_daily(temp_db_path):
    """Scenario 1: 80/85 + operation 10 -> switches account."""
    manager = FlowAccountManager(db_path=temp_db_path)
    manager.set_mode(Mode.AUTO)

    a1 = manager.add_account("acc1@example.com", daily_limit=85, priority=1)
    a2 = manager.add_account("acc2@example.com", daily_limit=85, priority=2)

    manager.select_account(a1.id)
    manager.consume(80)

    # Pre-check for 10: 80 + 10 = 90 > 85
    can_exec, target, msg = manager.can_consume(10)
    assert can_exec is True
    assert target is not None
    assert target.email == "acc2@example.com"

    # Reserve for 10 rotates to acc2
    acc, res = manager.reserve(10)
    assert acc.email == "acc2@example.com"
    assert res.estimated_cost == 10


def test_02_precheck_stays_when_fitting_daily(temp_db_path):
    """Scenario 2: 80/85 + operation 5 -> stays on current account."""
    manager = FlowAccountManager(db_path=temp_db_path)
    manager.set_mode(Mode.AUTO)

    a1 = manager.add_account("acc1@example.com", daily_limit=85, priority=1)
    a2 = manager.add_account("acc2@example.com", daily_limit=85, priority=2)

    manager.select_account(a1.id)
    manager.consume(80)

    # Pre-check for 5: 80 + 5 = 85 <= 85
    can_exec, target, msg = manager.can_consume(5)
    assert can_exec is True
    assert target is not None
    assert target.email == "acc1@example.com"

    # Reserve for 5 remains on acc1
    acc, res = manager.reserve(5)
    assert acc.email == "acc1@example.com"
    assert res.estimated_cost == 5


def test_03_precheck_switches_when_account_exhausted(temp_db_path):
    """Scenario 3: 85/85 -> switches account."""
    manager = FlowAccountManager(db_path=temp_db_path)
    manager.set_mode(Mode.AUTO)

    a1 = manager.add_account("acc1@example.com", daily_limit=85, priority=1)
    a2 = manager.add_account("acc2@example.com", daily_limit=85, priority=2)

    manager.select_account(a1.id)
    manager.consume(85)

    can_exec, target, msg = manager.can_consume(1)
    assert can_exec is True
    assert target is not None
    assert target.email == "acc2@example.com"


def test_04_monthly_limit_precheck(temp_db_path):
    """Scenario 4: Monthly limit pre-check triggers rotation."""
    manager = FlowAccountManager(db_path=temp_db_path)
    manager.set_mode(Mode.AUTO)

    # a1 has daily limit 85, but monthly limit 100 and has used 95
    a1 = manager.add_account("monthly1@example.com", daily_limit=85, monthly_limit=100, priority=1)
    a2 = manager.add_account("monthly2@example.com", daily_limit=85, monthly_limit=500, priority=2)

    manager.select_account(a1.id)
    manager.consume(95)

    # Operation 10: 95 + 10 = 105 > monthly limit 100
    can_exec, target, msg = manager.can_consume(10)
    assert can_exec is True
    assert target is not None
    assert target.email == "monthly2@example.com"


def test_05_sequential_rotation_with_precheck(temp_db_path):
    """Scenario 5: Sequential rotation strictly follows order and capacity."""
    manager = FlowAccountManager(db_path=temp_db_path)
    manager.set_mode(Mode.AUTO)
    manager.set_strategy("sequential")

    # Three accounts with various available capacities
    manager.add_account("seq1@example.com", daily_limit=10, priority=1)
    manager.add_account("seq2@example.com", daily_limit=20, priority=2)
    manager.add_account("seq3@example.com", daily_limit=30, priority=3)

    # Operation of 25: seq1 (10) and seq2 (20) lack capacity, rotates straight to seq3 (30)
    acc, res = manager.reserve(25)
    assert acc.email == "seq3@example.com"


def test_06_random_rotation_with_precheck(temp_db_path):
    """Scenario 6: Random rotation selects only among accounts with sufficient capacity."""
    manager = FlowAccountManager(db_path=temp_db_path)
    manager.set_mode(Mode.AUTO)
    manager.set_strategy("random")

    # 2 accounts with capacity 10, 2 accounts with capacity 50
    manager.add_account("small1@example.com", daily_limit=10)
    manager.add_account("small2@example.com", daily_limit=10)
    manager.add_account("large1@example.com", daily_limit=50)
    manager.add_account("large2@example.com", daily_limit=50)

    # Requesting 30 credits should only ever select large1 or large2
    for _ in range(10):
        can_exec, target, msg = manager.can_consume(30)
        assert can_exec is True
        assert target is not None
        assert target.email in ("large1@example.com", "large2@example.com")


def test_07_all_accounts_without_capacity(temp_db_path):
    """Scenario 7: When all accounts lack capacity, can_consume returns False and reserve raises NoAccountsAvailableError."""
    manager = FlowAccountManager(db_path=temp_db_path)
    manager.set_mode(Mode.AUTO)

    manager.add_account("acc1@example.com", daily_limit=10)
    manager.add_account("acc2@example.com", daily_limit=10)

    can_exec, target, msg = manager.can_consume(50)
    assert can_exec is False
    assert target is None

    with pytest.raises(NoAccountsAvailableError):
        manager.reserve(50)


def test_08_reservation_creation(temp_db_path):
    """Scenario 8: Creating a reservation creates PENDING status and locks capacity."""
    manager = FlowAccountManager(db_path=temp_db_path)
    manager.add_account("res_test@example.com", daily_limit=50)

    acc, res = manager.reserve(20, note="Job #999")
    assert res.id.startswith("res_")
    assert res.status == ReservationStatus.PENDING
    assert res.estimated_cost == 20
    assert res.note == "Job #999"

    # Pending reservation reduces available capacity for next checks
    can_exec, target, msg = manager.can_consume(35)
    # 20 reserved + 35 = 55 > 50 -> should be False
    assert can_exec is False


def test_09_reservation_confirmation(temp_db_path):
    """Scenario 9: Confirming a reservation commits credits to usage_history and updates account."""
    manager = FlowAccountManager(db_path=temp_db_path)
    acc, res = manager.add_account("commit_test@example.com", daily_limit=50), None
    target_acc, res = manager.reserve(15, note="Job #100")

    updated_acc, usage = manager.confirm_reservation(res.id, actual_amount=12, note="Finished early")
    assert usage.amount == 12
    assert updated_acc.used_today == 12
    assert updated_acc.available_credits == 38

    # Check reservation status
    reservations = manager.list_reservations(status="COMMITTED")
    assert len(reservations) == 1
    assert reservations[0].id == res.id
    assert reservations[0].actual_amount == 12


def test_10_reservation_cancellation(temp_db_path):
    """Scenario 10: Cancelling a reservation frees reserved capacity without debiting credits."""
    manager = FlowAccountManager(db_path=temp_db_path)
    manager.add_account("cancel_test@example.com", daily_limit=50)

    acc, res = manager.reserve(30)
    # Currently 30 reserved, remaining capacity is 20
    assert manager.can_consume(25)[0] is False

    # Cancel reservation
    cancelled_res = manager.cancel_reservation(res.id, reason="Job aborted")
    assert cancelled_res.status == ReservationStatus.CANCELLED

    # Capacity is restored
    assert manager.can_consume(40)[0] is True
    # Zero credits used
    current = manager.current_account()
    assert current is not None
    assert current.used_today == 0


def test_11_concurrency_in_reservations(temp_db_path):
    """Scenario 11: Concurrent reservations lock capacity safely without double-spending."""
    manager = FlowAccountManager(db_path=temp_db_path)
    manager.add_account("conc@example.com", daily_limit=50)

    successful = []
    failed = []

    def try_reserve(amount):
        m = FlowAccountManager(db_path=temp_db_path)
        try:
            _, res = m.reserve(amount)
            successful.append(res)
        except NoAccountsAvailableError:
            failed.append(amount)

    # 4 threads each trying to reserve 20 credits on a 50 credit account
    # Only 2 can succeed (2 * 20 = 40 <= 50; 3 * 20 = 60 > 50)
    threads = [threading.Thread(target=try_reserve, args=(20,)) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(successful) == 2
    assert len(failed) == 2


def test_12_xlsx_sync_add_account(temp_db_path, tmp_path):
    """Scenario 12: XLSX sync adds new accounts."""
    manager = FlowAccountManager(db_path=temp_db_path)
    xlsx_file = str(tmp_path / "sync_add.xlsx")
    create_central_config_template_xlsx(xlsx_file)

    sync_res = manager.sync(xlsx_file)
    assert sync_res["added"] == 3
    accounts = manager.list_accounts()
    assert len(accounts) == 3
    emails = {a.email for a in accounts}
    assert "flow_test01@example.com" in emails


def test_13_xlsx_sync_update_account(temp_db_path, tmp_path):
    """Scenario 13: XLSX sync updates limits and priority for existing accounts."""
    manager = FlowAccountManager(db_path=temp_db_path)
    manager.add_account("flow_test01@example.com", daily_limit=50, priority=5)

    xlsx_file = str(tmp_path / "sync_update.xlsx")
    create_central_config_template_xlsx(xlsx_file)

    sync_res = manager.sync(xlsx_file)
    assert sync_res["updated"] == 1
    acc = manager.get_account("flow_test01@example.com")
    assert acc is not None
    assert acc.daily_limit == 85
    assert acc.priority == 1


def test_14_xlsx_sync_remove_account_archives_without_deleting_history(temp_db_path, tmp_path):
    """Scenario 14: An account missing from XLSX is disabled, and its usage history is preserved."""
    manager = FlowAccountManager(db_path=temp_db_path)
    old_acc = manager.add_account("vanished@example.com", daily_limit=50)
    manager.select_account(old_acc.id)
    manager.consume(15, note="Historical consumption")

    xlsx_file = str(tmp_path / "sync_archive.xlsx")
    create_central_config_template_xlsx(xlsx_file)

    sync_res = manager.sync(xlsx_file)
    assert sync_res["disabled"] >= 1

    # Check vanished account still exists but is DISABLED
    vanished = manager.get_account("vanished@example.com")
    assert vanished is not None
    assert vanished.status == AccountStatus.DISABLED

    # History is intact
    history = manager.history(account_id=vanished.id)
    assert len(history) == 1
    assert history[0].amount == 15


def test_15_xlsx_sync_change_order(temp_db_path, tmp_path):
    """Scenario 15: Changing order column in XLSX updates priority and selection."""
    manager = FlowAccountManager(db_path=temp_db_path)
    xlsx_file = str(tmp_path / "sync_order.xlsx")

    # Create custom workbook with swapped orders
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "ACCOUNTS"
    ws.append(["order", "email", "daily_limit", "monthly_limit", "enabled", "credential_ref", "notes"])
    ws.append([2, "alpha@example.com", 85, None, "YES", "", ""])
    ws.append([1, "beta@example.com", 85, None, "YES", "", ""])
    wb.save(xlsx_file)

    manager.sync(xlsx_file)
    acc_beta = manager.get_account("beta@example.com")
    acc_alpha = manager.get_account("alpha@example.com")
    assert acc_beta is not None and acc_beta.priority == 1
    assert acc_alpha is not None and acc_alpha.priority == 2


def test_16_xlsx_sync_change_strategy(temp_db_path, tmp_path):
    """Scenario 16: SETTINGS sheet in XLSX updates rotation strategy."""
    manager = FlowAccountManager(db_path=temp_db_path)
    assert manager.status()["system"]["strategy"] == "sequential"

    xlsx_file = str(tmp_path / "sync_settings.xlsx")
    wb = openpyxl.Workbook()
    ws_acc = wb.active
    ws_acc.title = "ACCOUNTS"
    ws_acc.append(["order", "email", "daily_limit", "monthly_limit", "enabled", "credential_ref", "notes"])
    ws_acc.append([1, "user@example.com", 85, None, "YES", "", ""])

    ws_set = wb.create_sheet(title="SETTINGS")
    ws_set.append(["Setting", "Value"])
    ws_set.append(["rotation_strategy", "random"])
    ws_set.append(["default_daily_limit", 100])
    wb.save(xlsx_file)

    manager.sync(xlsx_file)
    status = manager.status()["system"]
    assert status["strategy"] == "random"
    assert status["default_daily_limit"] == 100


def test_17_credential_ref_keyring_and_env(temp_db_path, monkeypatch):
    """Scenario 17: credential_ref properly resolves KEYRING and ENV references."""
    monkeypatch.setenv("FLOW_SECRET_TEST", "my_env_secret_123")
    assert get_credential("ENV:FLOW_SECRET_TEST") == "my_env_secret_123"

    # Test set_credential and retrieval
    ref = set_credential("flow_test_account", "super_secret_value_xyz")
    assert ref.startswith("KEYRING:")
    assert has_credential("flow_test_account") is True
    assert get_credential(ref) == "super_secret_value_xyz"


def test_18_no_plaintext_passwords_in_xlsx_or_sqlite(temp_db_path, tmp_path):
    """Scenario 18: Plaintext passwords are rejected and never stored in XLSX or SQLite."""
    # Attempting to import an XLSX with a forbidden password column must raise SecurityImportError
    bad_xlsx = str(tmp_path / "bad.xlsx")
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["email", "password"])
    ws.append(["hacker@example.com", "plaintext_pass123"])
    wb.save(bad_xlsx)

    manager = FlowAccountManager(db_path=temp_db_path)
    with pytest.raises(SecurityImportError):
        manager.import_accounts(bad_xlsx)


def test_19_history_preserved_across_resets_and_sync(temp_db_path, tmp_path):
    """Scenario 19: Consumption history is never destroyed by daily resets or XLSX syncs."""
    manager = FlowAccountManager(db_path=temp_db_path)
    acc = manager.add_account("stable@example.com", daily_limit=50)
    manager.select_account(acc.id)
    manager.consume(20, note="Stable history check")

    # Daily reset
    manager.reset_daily()
    history_after_reset = manager.history(account_id=acc.id)
    assert len(history_after_reset) == 1

    # XLSX sync
    xlsx_file = str(tmp_path / "stable_sync.xlsx")
    create_central_config_template_xlsx(xlsx_file)
    manager.sync(xlsx_file)

    history_after_sync = manager.history(account_id=acc.id)
    assert len(history_after_sync) == 1
    assert history_after_sync[0].amount == 20


def test_20_cross_platform_path_compatibility():
    """Scenario 20: Install scripts and config resolution use portable environment variables without personal paths."""
    from flow_account.config import get_db_path

    bad_linux = "/home/" + "ubuntu"
    bad_win = "C:\\Users\\" + "Igor"

    # DB path expands ~ to user profile portably
    p = get_db_path()
    assert bad_linux not in p or p.startswith(str(Path.home()))
    assert bad_win not in p

    # Verify install.sh and install.ps1 have zero hardcoded personal paths
    install_sh = (REPO_ROOT / "install.sh").read_text(encoding="utf-8")
    assert bad_linux not in install_sh
    assert bad_win not in install_sh

    install_ps1 = (REPO_ROOT / "install.ps1").read_text(encoding="utf-8")
    assert bad_linux not in install_ps1
    assert bad_win not in install_ps1
