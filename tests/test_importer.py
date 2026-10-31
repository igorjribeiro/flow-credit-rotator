"""Tests for account importer (CSV and XLSX) with security enforcement."""

import csv
import openpyxl
import pytest
from pathlib import Path

from flow_account.importer import (
    SecurityImportError,
    read_accounts_from_file,
    validate_column_names,
)
from flow_account.models import AccountStatus


def test_import_valid_csv(manager, tmp_path):
    """Import valid CSV containing all supported columns."""
    csv_file = tmp_path / "valid_accounts.csv"
    csv_content = """email,daily_limit,monthly_limit,priority,status,credential_ref,notes
acc1@example.com,85,2500,1,ACTIVE,ENV:ACC_01,Primary account
acc2@example.com,100,3000,2,ACTIVE,ENV:ACC_02,Secondary account
acc3@example.com,70,,3,DISABLED,ENV:ACC_03,Standby account
"""
    csv_file.write_text(csv_content, encoding="utf-8")

    result = manager.import_accounts(str(csv_file))
    assert result["imported"] == 3
    assert result["updated"] == 0
    assert result["errors"] == []

    accounts = manager.list_accounts()
    assert len(accounts) == 3

    acc1 = next(a for a in accounts if a.email == "acc1@example.com")
    assert acc1.daily_limit == 85
    assert acc1.monthly_limit == 2500
    assert acc1.priority == 1
    assert acc1.status == AccountStatus.ACTIVE
    assert acc1.credential_ref == "ENV:ACC_01"
    assert acc1.notes == "Primary account"
    assert acc1.used_today == 0

    acc3 = next(a for a in accounts if a.email == "acc3@example.com")
    assert acc3.daily_limit == 70
    assert acc3.monthly_limit is None
    assert acc3.priority == 3
    assert acc3.status == AccountStatus.DISABLED


def test_import_valid_xlsx(manager, tmp_path):
    """Import valid XLSX spreadsheet created with openpyxl."""
    xlsx_file = tmp_path / "valid_accounts.xlsx"
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Accounts"

    headers = ["email", "daily_limit", "monthly_limit", "priority", "status", "credential_ref", "notes"]
    ws.append(headers)
    ws.append(["excel1@example.com", 90, 2700, 1, "ACTIVE", "ENV:XL_01", "Excel note 1"])
    ws.append(["excel2@example.com", 60, None, 2, "ACTIVE", "ENV:XL_02", "Excel note 2"])
    wb.save(str(xlsx_file))
    wb.close()

    result = manager.import_accounts(str(xlsx_file))
    assert result["imported"] == 2
    assert result["updated"] == 0
    assert result["errors"] == []

    accounts = manager.list_accounts()
    assert len(accounts) == 2
    a1 = next(a for a in accounts if a.email == "excel1@example.com")
    assert a1.daily_limit == 90
    assert a1.monthly_limit == 2700
    assert a1.credential_ref == "ENV:XL_01"


@pytest.mark.parametrize("forbidden_col", [
    "password",
    "pass",
    "senha",
    "secret",
    "pwd",
    "key",
    "token",
    "api_key",
    "user_password",
    "senha_google",
    "client_secret",
])
def test_import_security_rejection_forbidden_columns_csv(manager, tmp_path, forbidden_col):
    """Security check: CSV containing forbidden password or secret columns must be rejected."""
    csv_file = tmp_path / f"forbidden_{forbidden_col}.csv"
    csv_file.write_text(f"email,{forbidden_col},daily_limit\nhack@example.com,secret123,85\n", encoding="utf-8")

    with pytest.raises(SecurityImportError) as exc_info:
        manager.import_accounts(str(csv_file))

    err_msg = str(exc_info.value)
    assert "Security rejection" in err_msg
    assert forbidden_col in err_msg or "password" in err_msg or "senha" in err_msg or "secret" in err_msg
    assert "credential_ref" in err_msg

    # Verify no accounts were inserted into DB
    assert len(manager.list_accounts()) == 0


def test_import_security_rejection_forbidden_columns_xlsx(manager, tmp_path):
    """Security check: XLSX containing password or secret column must be rejected."""
    xlsx_file = tmp_path / "forbidden_secrets.xlsx"
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["email", "password", "daily_limit"])
    ws.append(["leaked@example.com", "PlaintextPassword!", 85])
    wb.save(str(xlsx_file))
    wb.close()

    with pytest.raises(SecurityImportError) as exc_info:
        manager.import_accounts(str(xlsx_file))

    assert "Security rejection" in str(exc_info.value)
    assert len(manager.list_accounts()) == 0


def test_import_corrupted_and_invalid_rows_handling(manager, tmp_path):
    """Importer should skip corrupted/invalid rows, collect errors, and import valid rows."""
    csv_file = tmp_path / "corrupted_rows.csv"
    csv_content = """email,daily_limit,monthly_limit,priority,status,notes
valid@example.com,85,2500,1,ACTIVE,Good row
,85,2500,1,ACTIVE,Empty email
not-an-email,85,2500,1,ACTIVE,Malformed email
negative_limit@example.com,-10,2500,1,ACTIVE,Negative daily limit
bad_number@example.com,notanumber,2500,1,ACTIVE,String daily limit
bad_status@example.com,85,2500,1,INVALID_STATUS,Unknown status
another_valid@example.com,50,,2,ACTIVE,Another good row
"""
    csv_file.write_text(csv_content, encoding="utf-8")

    result = manager.import_accounts(str(csv_file))
    assert result["imported"] == 2
    assert result["updated"] == 0
    assert len(result["errors"]) >= 5

    # Check specific error reports
    errors_str = " ".join(result["errors"])
    assert "Empty email address" in errors_str
    assert "Invalid email format" in errors_str
    assert "daily_limit cannot be negative" in errors_str
    assert "Invalid daily_limit" in errors_str
    assert "Invalid status" in errors_str

    # Valid rows successfully imported
    accounts = manager.list_accounts()
    assert len(accounts) == 2
    emails = {a.email for a in accounts}
    assert emails == {"valid@example.com", "another_valid@example.com"}


def test_import_upsert_existing_accounts(manager, tmp_path):
    """Re-importing existing accounts updates their limits and metadata while preserving usage."""
    # First import
    csv_initial = tmp_path / "initial.csv"
    csv_initial.write_text("""email,daily_limit,monthly_limit,priority,status,notes
update_me@example.com,85,2000,1,ACTIVE,Initial notes
keep_me@example.com,85,2000,2,ACTIVE,Keep
""", encoding="utf-8")

    res1 = manager.import_accounts(str(csv_initial))
    assert res1["imported"] == 2
    assert res1["updated"] == 0

    # Consume credits from update_me account to verify counters are preserved
    manager.select_account("update_me@example.com")
    manager.consume(30, note="Initial consumption")

    acc_before = manager.current_account()
    assert acc_before.used_today == 30
    assert acc_before.used_month == 30

    # Re-import with updated limits, priority, and notes
    csv_updated = tmp_path / "updated.csv"
    csv_updated.write_text("""email,daily_limit,monthly_limit,priority,status,credential_ref,notes
update_me@example.com,120,4000,3,ACTIVE,ENV:UPDATED_VAR,Updated notes and limits
new_acc@example.com,90,3000,1,ACTIVE,ENV:NEW_01,Brand new account
""", encoding="utf-8")

    res2 = manager.import_accounts(str(csv_updated))
    assert res2["imported"] == 1
    assert res2["updated"] == 1
    assert res2["errors"] == []

    accounts = manager.list_accounts()
    assert len(accounts) == 3

    updated_acc = next(a for a in accounts if a.email == "update_me@example.com")
    assert updated_acc.daily_limit == 120
    assert updated_acc.monthly_limit == 4000
    assert updated_acc.priority == 3
    assert updated_acc.credential_ref == "ENV:UPDATED_VAR"
    assert updated_acc.notes == "Updated notes and limits"
    # Preserved usage counters
    assert updated_acc.used_today == 30
    assert updated_acc.used_month == 30


def test_import_missing_email_header(manager, tmp_path):
    """CSV missing required 'email' column raises ValueError."""
    bad_csv = tmp_path / "missing_email.csv"
    bad_csv.write_text("daily_limit,monthly_limit\n85,2500\n", encoding="utf-8")

    with pytest.raises(ValueError, match="Missing required column 'email'"):
        manager.import_accounts(str(bad_csv))


def test_import_nonexistent_file(manager):
    """Attempting to import nonexistent file raises FileNotFoundError."""
    with pytest.raises(FileNotFoundError):
        manager.import_accounts("/nonexistent/path/to/accounts.csv")


def test_import_unsupported_file_extension(manager, tmp_path):
    """Attempting to import unsupported file formats raises ValueError."""
    bad_ext = tmp_path / "accounts.json"
    bad_ext.write_text("[]", encoding="utf-8")

    with pytest.raises(ValueError, match="Unsupported file format"):
        manager.import_accounts(str(bad_ext))


def test_import_empty_files(manager, tmp_path):
    """Empty CSV and XLSX raise ValueError."""
    empty_csv = tmp_path / "empty.csv"
    empty_csv.write_text("", encoding="utf-8")

    with pytest.raises(ValueError, match="Empty CSV file"):
        manager.import_accounts(str(empty_csv))

    empty_xlsx = tmp_path / "empty.xlsx"
    wb = openpyxl.Workbook()
    ws = wb.active
    # Save workbook without any rows
    wb.save(str(empty_xlsx))
    wb.close()

    # Note: openpyxl creates an empty sheet, so first_row will be empty tuple or StopIteration
    # which raises ValueError
    with pytest.raises(ValueError):
        manager.import_accounts(str(empty_xlsx))
