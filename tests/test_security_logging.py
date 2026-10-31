"""Tests for security policies, credential isolation, and audit logging.

Ensures no plaintext passwords, tokens, or secret credentials are ever stored,
logged, exposed via CLI outputs, or leaked in model string representations.
"""

import json
import logging
import os
import pytest
from io import StringIO

from flow_account.cli import main as cli_main
from flow_account.importer import SecurityImportError
from flow_account.models import Account, AccountStatus, UsageRecord


def test_account_model_has_no_secret_or_password_fields():
    """Verify Account dataclass has no raw password or secret fields."""
    acc = Account(
        id=1,
        email="test@example.com",
        credential_ref="ENV:FLOW_PASS_01",
        daily_limit=85,
        notes="Security test account",
    )
    acc_dict = acc.to_dict()

    forbidden_keys = {"password", "pass", "senha", "secret", "pwd", "token", "api_key"}
    for key in forbidden_keys:
        assert not hasattr(acc, key), f"Account model must not have field '{key}'"
        assert key not in acc_dict, f"Account.to_dict() must not contain key '{key}'"


def test_account_str_and_repr_do_not_expose_credentials(monkeypatch):
    """Verify str(account) and repr(account) only show the reference tag, never resolved secrets."""
    secret_value = "P@ssw0rd_Super_Secret_Value_12345!"
    env_var_name = "FLOW_SECRET_TEST_VAR"
    monkeypatch.setenv(env_var_name, secret_value)

    acc = Account(
        id=42,
        email="agent@example.com",
        credential_ref=f"ENV:{env_var_name}",
        daily_limit=85,
        notes="Regular note without secret",
    )

    acc_str = str(acc)
    acc_repr = repr(acc)

    # Must contain the reference tag
    assert f"ENV:{env_var_name}" in acc_str
    assert f"ENV:{env_var_name}" in acc_repr

    # Must NEVER contain the actual secret value
    assert secret_value not in acc_str
    assert secret_value not in acc_repr


def test_cli_outputs_do_not_leak_secrets(temp_db_path, monkeypatch, capsys):
    """CLI commands (list, status, current) must never print raw secrets to stdout or stderr."""
    secret_value = "ULTRA_CONFIDENTIAL_TOKEN_9999"
    monkeypatch.setenv("FLOW_SECRET_01", secret_value)

    # Initialize account via CLI add
    rc = cli_main([
        "--db", temp_db_path,
        "add",
        "secure.user@example.com",
        "--credential-ref", "ENV:FLOW_SECRET_01",
        "--daily", "85",
        "--notes", "Production secured account",
    ])
    assert rc == 0
    captured = capsys.readouterr()
    assert secret_value not in captured.out
    assert secret_value not in captured.err

    # Test 'list' command
    rc = cli_main(["--db", temp_db_path, "list"])
    assert rc == 0
    captured = capsys.readouterr()
    assert "ENV:FLOW_SECRET_01" in captured.out
    assert secret_value not in captured.out
    assert secret_value not in captured.err

    # Test 'list --json' command
    rc = cli_main(["--db", temp_db_path, "--json", "list"])
    assert rc == 0
    captured = capsys.readouterr()
    assert secret_value not in captured.out
    data = json.loads(captured.out)
    assert len(data) == 1
    assert data[0]["credential_ref"] == "ENV:FLOW_SECRET_01"
    assert "password" not in data[0]

    # Test 'status' command
    rc = cli_main(["--db", temp_db_path, "status"])
    assert rc == 0
    captured = capsys.readouterr()
    assert secret_value not in captured.out

    # Test 'current' command
    rc = cli_main(["--db", temp_db_path, "current"])
    assert rc == 0
    captured = capsys.readouterr()
    assert secret_value not in captured.out
    assert "ENV:FLOW_SECRET_01" in captured.out


def test_importer_exception_does_not_leak_row_passwords(manager, tmp_path, caplog):
    """When an import is rejected for containing a forbidden column, passwords from rows must not leak."""
    secret_in_file = "LeakedPasswordInPlaintext99!"
    csv_file = tmp_path / "leaked_passwords.csv"
    csv_file.write_text(
        f"email,password,daily_limit\nvictim@example.com,{secret_in_file},85\n",
        encoding="utf-8",
    )

    with caplog.at_level(logging.DEBUG):
        with pytest.raises(SecurityImportError) as exc_info:
            manager.import_accounts(str(csv_file))

    # The error message should explain the security policy, not dump the row content
    err_text = str(exc_info.value)
    assert secret_in_file not in err_text
    assert "Forbidden column 'password' detected" in err_text

    # Log records should also not contain the plaintext password
    for record in caplog.records:
        assert secret_in_file not in record.getMessage()


def test_usage_history_sanitization(manager):
    """Audit logs only store metadata and notes, without credentials or secrets."""
    manager.add_account(
        email="audit@example.com",
        daily_limit=85,
        credential_ref="ENV:AUDIT_REF",
    )
    acc, record = manager.consume(15, note="Batch job #1042")

    records = manager.history()
    assert len(records) == 1
    hist = records[0]
    hist_dict = hist.to_dict()

    assert hist_dict["amount"] == 15
    assert hist_dict["note"] == "Batch job #1042"
    assert "credential" not in hist_dict
    assert "password" not in hist_dict
    assert "secret" not in hist_dict


def test_safe_credential_ref_formats(manager):
    """Verify various standard credential reference schemes are safely accepted."""
    safe_refs = [
        "ENV:GOOGLE_FLOW_PASS_1",
        "VAULT:secret/data/flow/acc1#password",
        "KEYCHAIN:flow-account-01",
        "AWS_SECRETS_MANAGER:arn:aws:secretsmanager:us-east-1:123456789012:secret:flow-key",
        "GCP_SECRET_MANAGER:projects/123/secrets/flow_key/versions/latest",
    ]

    for idx, ref in enumerate(safe_refs):
        acc = manager.add_account(
            email=f"ref_test_{idx}@example.com",
            credential_ref=ref,
        )
        assert acc.credential_ref == ref
        retrieved = manager.select_account(acc.id)
        assert retrieved.credential_ref == ref
