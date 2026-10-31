"""Regression tests for issues raised during the independent technical audit.

Verifies fixes for:
1. History querying with --account does not mutate the active account selection.
2. Account imports preserve existing NEEDS_HUMAN and DISABLED statuses when omitted/empty.
3. Updating daily limits via set_limit preserves existing monthly limits.
4. Validation rejects arbitrary plaintext passwords and only accepts valid indirect references.
"""

import json
from pathlib import Path
import openpyxl
import pytest

from flow_account.cli import main as cli_main
from flow_account.models import (
    Account,
    AccountStatus,
    Mode,
    validate_credential_ref,
)


def test_history_account_filter_does_not_mutate_selection(manager, temp_db_path, capsys):
    """Querying history for a specific account must not mutate the active account."""
    acc1 = manager.add_account("first@example.com", daily_limit=85, priority=1)
    acc2 = manager.add_account("second@example.com", daily_limit=85, priority=2)

    manager.set_mode(Mode.MANUAL)
    manager.select_account(acc1.id)

    # Consume credits to populate history
    manager.consume(10, note="Job on acc1")
    manager.select_account(acc2.id)
    manager.consume(20, note="Job on acc2")

    # Set active account back to acc1
    manager.select_account(acc1.id)
    assert manager.status()["system"]["current_account_id"] == acc1.id

    # Verify get_account is read-only
    looked_up = manager.get_account("second@example.com")
    assert looked_up is not None
    assert looked_up.id == acc2.id
    assert manager.status()["system"]["current_account_id"] == acc1.id

    # CLI history command filtered by acc2
    rc = cli_main(["--db", temp_db_path, "history", "--account", "second@example.com"])
    assert rc == 0
    _ = capsys.readouterr()

    st_after = manager.status()
    assert st_after["system"]["current_account_id"] == acc1.id
    current_acc = manager.current_account()
    assert current_acc is not None
    assert current_acc.id == acc1.id

    # Also check JSON mode output
    rc_json = cli_main(
        ["--db", temp_db_path, "--json", "history", "--account", str(acc2.id)]
    )
    assert rc_json == 0
    captured = capsys.readouterr()
    records = json.loads(captured.out)
    assert len(records) == 1
    assert records[0]["account_id"] == acc2.id

    # Active account must still remain acc1
    assert manager.status()["system"]["current_account_id"] == acc1.id


def test_import_preserves_needs_human_and_disabled_status(manager, tmp_path):
    """Re-importing CSV/XLSX without status or with empty status must preserve NEEDS_HUMAN and DISABLED."""
    acc_human = manager.add_account(
        "human_check@example.com",
        daily_limit=85,
        priority=1,
        credential_ref="ENV:REF_01",
    )
    acc_disabled = manager.add_account(
        "disabled_check@example.com",
        daily_limit=85,
        priority=2,
        credential_ref="ENV:REF_02",
    )

    manager.human_required(acc_human.id, reason="CAPTCHA triggered")
    manager.disable(acc_disabled.id)

    assert manager.get_account(acc_human.id).status == AccountStatus.NEEDS_HUMAN
    assert manager.get_account(acc_disabled.id).status == AccountStatus.DISABLED

    # 1. Re-import CSV that has no 'status' column at all
    csv_no_status = tmp_path / "accounts_no_status.csv"
    csv_no_status.write_text(
        "email,daily_limit,priority,notes\n"
        "human_check@example.com,100,1,Updated via CSV without status column\n"
        "disabled_check@example.com,100,2,Updated via CSV without status column\n",
        encoding="utf-8",
    )
    res1 = manager.import_accounts(str(csv_no_status))
    assert res1["updated"] == 2
    assert res1["errors"] == []

    acc_h1 = manager.get_account(acc_human.id)
    acc_d1 = manager.get_account(acc_disabled.id)
    assert acc_h1.daily_limit == 100
    assert acc_h1.status == AccountStatus.NEEDS_HUMAN
    assert acc_d1.daily_limit == 100
    assert acc_d1.status == AccountStatus.DISABLED

    # 2. Re-import CSV that has empty values in the status column
    csv_empty_status = tmp_path / "accounts_empty_status.csv"
    csv_empty_status.write_text(
        "email,daily_limit,status,notes\n"
        "human_check@example.com,120,,Empty status cell\n"
        "disabled_check@example.com,120,,Empty status cell\n",
        encoding="utf-8",
    )
    res2 = manager.import_accounts(str(csv_empty_status))
    assert res2["updated"] == 2
    assert res2["errors"] == []

    acc_h2 = manager.get_account(acc_human.id)
    acc_d2 = manager.get_account(acc_disabled.id)
    assert acc_h2.daily_limit == 120
    assert acc_h2.status == AccountStatus.NEEDS_HUMAN
    assert acc_d2.daily_limit == 120
    assert acc_d2.status == AccountStatus.DISABLED

    # 3. Re-import via XLSX spreadsheet with None in status cells
    xlsx_file = tmp_path / "accounts_preserve_status.xlsx"
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["email", "daily_limit", "status", "notes"])
    ws.append(["human_check@example.com", 150, None, "XLSX empty status"])
    ws.append(["disabled_check@example.com", 150, None, "XLSX empty status"])
    wb.save(str(xlsx_file))
    wb.close()

    res3 = manager.import_accounts(str(xlsx_file))
    assert res3["updated"] == 2
    assert res3["errors"] == []

    acc_h3 = manager.get_account(acc_human.id)
    acc_d3 = manager.get_account(acc_disabled.id)
    assert acc_h3.daily_limit == 150
    assert acc_h3.status == AccountStatus.NEEDS_HUMAN
    assert acc_d3.daily_limit == 150
    assert acc_d3.status == AccountStatus.DISABLED


def test_set_limit_daily_only_preserves_existing_monthly_limit(manager):
    """Calling set_limit with only daily_limit must not erase an existing monthly_limit."""
    acc = manager.add_account(
        "monthly_user@example.com",
        daily_limit=100,
        monthly_limit=2000,
        priority=1,
    )
    assert acc.monthly_limit == 2000

    # Increase daily limit without passing monthly_limit
    updated = manager.set_limit(acc.id, daily_limit=150)
    assert updated.daily_limit == 150
    assert updated.monthly_limit == 2000

    # Ensure persistence in DB
    refetched = manager.get_account(acc.id)
    assert refetched.daily_limit == 150
    assert refetched.monthly_limit == 2000

    # Exhaust monthly quota: consume 2000 credits over multiple calls
    manager.select_account(acc.id)
    for _ in range(13):
        # consume daily quota of 150
        manager.consume(150)
        manager.reset_daily()

    # Remaining 50 credits to reach 2000
    manager.consume(50)
    exhausted_acc = manager.get_account(acc.id)
    assert exhausted_acc.used_month >= 2000
    assert exhausted_acc.status == AccountStatus.EXHAUSTED

    # Modifying daily limit must NOT reactivate an account exhausted by monthly limit
    updated_daily = manager.set_limit(acc.id, daily_limit=200)
    assert updated_daily.daily_limit == 200
    assert updated_daily.monthly_limit == 2000
    assert updated_daily.status == AccountStatus.EXHAUSTED
    assert updated_daily.is_eligible is False

    # Explicit removal of monthly limit works via clear_monthly=True or monthly_limit=0
    cleared = manager.set_limit(acc.id, daily_limit=200, clear_monthly=True)
    assert cleared.monthly_limit is None
    # Now that monthly limit is cleared and used_today is 50 < 200, account reactivates
    assert cleared.status == AccountStatus.ACTIVE
    assert cleared.is_eligible is True


def test_credential_ref_validation_rejects_arbitrary_plaintext(manager, tmp_path, capsys):
    """Only approved indirect reference schemes or empty string are accepted for credential_ref."""
    # Unit level validation helper
    valid_samples = [
        "",
        "ENV:FLOW_ACCOUNT_01",
        "ENV:GOOGLE_CRED_PROD_99",
        "FILE:/etc/secrets/flow_key.json",
        "VAULT:secret/data/flow/acc1#password",
        "KEYRING:google-flow-01",
        "OP:op://vault/flow/password",
        "BWS:bws-secret-uuid-12345",
        "KEYCHAIN:flow-account",
        "AWS_SECRETS_MANAGER:arn:aws:secretsmanager:us-east-1:123456789012:secret:flow-key",
        "GCP_SECRET_MANAGER:projects/123/secrets/flow_key/versions/latest",
    ]
    for ref in valid_samples:
        assert validate_credential_ref(ref) is True, f"Expected {ref} to be valid"

    invalid_samples = [
        "PlaintextPassword123!",
        "admin:password",
        "my-secret-key-without-prefix",
        "password",
        "12345678",
        "ENV:",  # empty locator
        "INVALID:something",
        "ENV:INVALID VAR WITH SPACES",
    ]
    for ref in invalid_samples:
        assert validate_credential_ref(ref) is False, f"Expected {ref} to be rejected"

    # Manager add_account rejection
    with pytest.raises(ValueError, match="Invalid credential_ref"):
        manager.add_account("unsafe@example.com", credential_ref="MySecretPassword99!")

    # Importer rejection of rows with invalid credential_ref
    csv_file = tmp_path / "invalid_cred_ref.csv"
    csv_file.write_text(
        "email,daily_limit,credential_ref\n"
        "safe@example.com,85,ENV:SAFE_CRED_01\n"
        "unsafe@example.com,85,RawSecretPassword12345\n",
        encoding="utf-8",
    )
    import_res = manager.import_accounts(str(csv_file))
    assert import_res["imported"] == 1
    assert len(import_res["errors"]) == 1
    assert "Invalid credential_ref" in import_res["errors"][0]

    # CLI add rejection
    rc = cli_main([
        "--db", manager.db.db_path,
        "add", "cli_unsafe@example.com",
        "--credential-ref", "SuperSecretPlainTextPassword!",
    ])
    assert rc == 1
    captured = capsys.readouterr()
    assert "Error: Invalid credential_ref" in captured.err
    assert "SuperSecretPlainTextPassword!" not in captured.out

    # Model to_dict masking fallback test
    acc_dummy = Account(
        id=999,
        email="dummy@example.com",
        credential_ref="UnexpectedRawPassword123",
        daily_limit=85,
    )
    d = acc_dummy.to_dict()
    assert "UnexpectedRawPassword123" not in str(d)
    assert d["credential_ref"] == "[INVALID CREDENTIAL REF]"
