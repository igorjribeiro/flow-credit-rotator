"""Test suite for v1.2.0 public experience and documentation requirements."""

import os
from pathlib import Path
import openpyxl
import pytest

from flow_account import FlowAccountManager, AccountStatus
from flow_account.importer import read_central_config_xlsx

REPO_ROOT = Path(__file__).resolve().parent.parent


def test_template_xlsx_structure_and_sheets():
    """Verify templates/flow_accounts_template.xlsx has exactly two sheets: ACCOUNTS and SETTINGS."""
    template_path = REPO_ROOT / "templates" / "flow_accounts_template.xlsx"
    assert template_path.exists(), "Template flow_accounts_template.xlsx must exist"

    wb = openpyxl.load_workbook(filename=str(template_path), read_only=True)
    sheet_names = wb.sheetnames
    assert len(sheet_names) == 2, f"Expected exactly 2 sheets, found {sheet_names}"
    assert "ACCOUNTS" in sheet_names, "ACCOUNTS sheet must exist"
    assert "SETTINGS" in sheet_names, "SETTINGS sheet must exist"
    wb.close()


def test_template_xlsx_accounts_sheet_columns_and_data():
    """Verify ACCOUNTS sheet contains the required columns and only mock example.com emails."""
    template_path = REPO_ROOT / "templates" / "flow_accounts_template.xlsx"
    wb = openpyxl.load_workbook(filename=str(template_path), read_only=True, data_only=True)
    sheet = wb["ACCOUNTS"]
    rows = list(sheet.iter_rows(values_only=True))
    wb.close()

    assert len(rows) >= 4, "Expected header + at least 3 account rows"
    headers = [str(h).strip().lower() for h in rows[0]]
    expected_headers = ["order", "email", "daily_limit", "monthly_limit", "enabled", "credential_ref", "notes"]
    assert headers == expected_headers

    for r in rows[1:]:
        order, email, daily_limit, monthly_limit, enabled, cred_ref, notes = r
        assert isinstance(order, (int, float))
        assert str(email).endswith("@example.com"), f"Email must be @example.com: {email}"
        assert int(float(str(daily_limit))) > 0
        assert str(enabled).upper() in ("YES", "TRUE", "NO", "FALSE")
        assert "password" not in str(cred_ref).lower()
        assert str(cred_ref).startswith("KEYRING:")


def test_template_xlsx_settings_sheet_columns_and_data():
    """Verify SETTINGS sheet contains required columns and default setting rows."""
    template_path = REPO_ROOT / "templates" / "flow_accounts_template.xlsx"
    wb = openpyxl.load_workbook(filename=str(template_path), read_only=True, data_only=True)
    sheet = wb["SETTINGS"]
    rows = list(sheet.iter_rows(values_only=True))
    wb.close()

    headers = [str(h).strip() for h in rows[0]]
    assert headers == ["Setting", "Value", "Description"]

    settings_map = {str(r[0]).strip(): r[1] for r in rows[1:] if r and len(r) >= 2}
    assert settings_map["rotation_strategy"] == "sequential"
    assert int(float(str(settings_map["default_daily_limit"]))) == 85
    assert int(float(str(settings_map["default_monthly_limit"]))) == 1500
    assert settings_map["timezone"] == "America/Bahia"
    assert str(settings_map["auto_rotate"]).upper() == "YES"


def test_accounts_limits_override_settings_default(temp_db_path, tmp_path):
    """Verify account-level daily_limit in ACCOUNTS sheet overrides default_daily_limit in SETTINGS sheet."""
    custom_xlsx = str(tmp_path / "custom_override.xlsx")
    wb = openpyxl.Workbook()

    ws_acc = wb.active
    assert ws_acc is not None
    ws_acc.title = "ACCOUNTS"
    ws_acc.append(["order", "email", "daily_limit", "monthly_limit", "enabled", "credential_ref", "notes"])
    ws_acc.append([1, "override_test@example.com", 70, 1200, "YES", "KEYRING:test", ""])

    ws_set = wb.create_sheet(title="SETTINGS")
    ws_set.append(["Setting", "Value", "Description"])
    ws_set.append(["default_daily_limit", 85, ""])
    ws_set.append(["rotation_strategy", "sequential", ""])
    wb.save(custom_xlsx)

    manager = FlowAccountManager(db_path=temp_db_path)
    manager.sync(custom_xlsx)

    acc = manager.get_account("override_test@example.com")
    assert acc is not None
    assert acc.daily_limit == 70, "Account daily_limit 70 must override default 85"
    assert acc.monthly_limit == 1200


def test_config_directory_is_gitignored():
    """Verify that config/ is ignored by .gitignore to prevent accidental commit of real user accounts."""
    gitignore_path = REPO_ROOT / ".gitignore"
    content = gitignore_path.read_text(encoding="utf-8")
    assert "config/" in content, "config/ directory must be present in .gitignore"


def test_readme_covers_all_onboarding_sections():
    """Verify README.md covers installation, credentials, rotation modes, sync, and reservation workflow."""
    readme_path = REPO_ROOT / "README.md"
    assert readme_path.exists()
    content = readme_path.read_text(encoding="utf-8")

    # Linux install
    assert "./install.sh" in content
    assert "flow-account --help" in content
    # Windows install
    assert ".\\install.ps1" in content or "install.ps1" in content
    # Credentials
    assert "flow-account credential set" in content
    assert "flow-account credential test" in content
    # Sync
    assert "flow-account sync" in content
    # Rotation modes
    assert "sequential" in content
    assert "random" in content
    assert "round-robin" in content
    assert "lowest-usage" in content
    # Reservation
    assert "flow-account reserve" in content
    assert "flow-account reserve-confirm" in content or "commit" in content
    assert "flow-account reserve-cancel" in content or "cancel" in content


def test_only_single_official_xlsx_template_in_templates():
    """Verify templates/flow_accounts_template.xlsx is the only official XLSX template, and duplicates are gone."""
    templates_dir = REPO_ROOT / "templates"
    xlsx_files = list(templates_dir.glob("*.xlsx"))
    assert len(xlsx_files) == 1, f"Expected exactly 1 XLSX template in templates/, found: {[f.name for f in xlsx_files]}"
    assert xlsx_files[0].name == "flow_accounts_template.xlsx"

    # Redundant templates must not exist
    assert not (templates_dir / "accounts_template.xlsx").exists()
    assert not (templates_dir / "modelo_de_contas_de_fluxo.xlsx").exists()

    # CSV template is retained as alternative
    assert (templates_dir / "accounts_template.csv").exists()


def test_notes_column_documented_as_optional_and_safe_usage():
    """Verify notes column is documented as optional in README.md and advice against editing template directly."""
    readme_path = REPO_ROOT / "README.md"
    content = readme_path.read_text(encoding="utf-8").lower()

    assert "notes" in content
    assert "optional" in content
    assert "never edit the original template" in content or "never edit the template directly" in content or "never edit the template original" in content
