"""Tests for corrupted database states, malformed inputs, and resilient error handling.

Verifies that the manager and CLI gracefully handle invalid arguments, corrupt storage,
and malformed records with user-friendly error messages and without raw tracebacks.
"""

import json
import sqlite3
import pytest

from flow_account.cli import main as cli_main
from flow_account.manager import (
    FlowAccountManager,
    FlowAccountError,
    AccountNotFoundError,
    NoAccountsAvailableError,
)


def test_corrupted_database_file_manager_init(tmp_path):
    """Instantiating manager on a corrupted/non-SQLite file raises FlowAccountError."""
    corrupt_db = tmp_path / "corrupt.db"
    corrupt_db.write_bytes(b"THIS_IS_NOT_AN_ACTUAL_SQLITE_DATABASE_HEADER_000000")

    with pytest.raises(FlowAccountError) as exc_info:
        FlowAccountManager(db_path=str(corrupt_db))

    assert "Database error" in str(exc_info.value)


def test_corrupted_database_cli_resilience(tmp_path, capsys):
    """CLI execution against a corrupted DB file exits with code 1 and no raw traceback."""
    corrupt_db = tmp_path / "corrupt_cli.db"
    corrupt_db.write_bytes(b"TRASH_CORRUPTED_BYTES_12345")

    rc = cli_main(["--db", str(corrupt_db), "status"])
    assert rc == 1

    captured = capsys.readouterr()
    assert "Error:" in captured.err or "Unexpected error:" in captured.err
    assert "Traceback (most recent call last)" not in captured.err
    assert "Traceback (most recent call last)" not in captured.out

    # Test with --json flag
    rc_json = cli_main(["--db", str(corrupt_db), "--json", "status"])
    assert rc_json == 1
    captured_json = capsys.readouterr()
    data = json.loads(captured_json.err)
    assert "error" in data
    assert "message" in data


def test_corrupted_system_state_missing_row(temp_db_path, capsys):
    """Corrupted DB missing system_state row raises FlowAccountError and CLI handles cleanly."""
    mgr = FlowAccountManager(db_path=temp_db_path)
    mgr.db.close()

    # Delete system_state row
    conn = sqlite3.connect(temp_db_path)
    conn.execute("DELETE FROM system_state WHERE id = 1;")
    conn.commit()
    conn.close()

    mgr = FlowAccountManager(db_path=temp_db_path)
    with pytest.raises(FlowAccountError, match="Corrupted database state"):
        mgr.status()
    mgr.db.close()

    # CLI check
    rc = cli_main(["--db", temp_db_path, "status"])
    assert rc == 1
    captured = capsys.readouterr()
    assert "Error: Corrupted database state: system_state row missing." in captured.err
    assert "Traceback" not in captured.err


def test_invalid_inputs_consume(manager, capsys):
    """Negative, zero, or non-numeric credit values are cleanly rejected."""
    manager.add_account("user@example.com", daily_limit=85)

    with pytest.raises(ValueError, match="positive integer"):
        manager.consume(-10)

    with pytest.raises(ValueError, match="positive integer"):
        manager.consume(0)

    with pytest.raises(ValueError, match="positive integer"):
        manager.consume("abc")  # type: ignore

    # CLI rejection
    rc = cli_main(["--db", manager.db.db_path, "consume", "0"])
    assert rc == 1
    captured = capsys.readouterr()
    assert "Error: Credit amount to consume must be a positive integer." in captured.err
    assert "Traceback" not in captured.err


def test_invalid_inputs_strategy(manager, capsys):
    """Invalid strategy names raise ValueError and CLI rejects with clear message."""
    with pytest.raises(ValueError, match="Invalid strategy 'unknown_strat'"):
        manager.set_strategy("unknown_strat")

    # CLI argparse choice validation
    rc = cli_main(["--db", manager.db.db_path, "set-strategy", "bad_strategy"])
    # Argparse exit code 2
    assert rc == 2
    captured = capsys.readouterr()
    assert "invalid choice" in captured.err
    assert "Traceback" not in captured.err


def test_invalid_inputs_mode(manager, capsys):
    """Invalid mode values raise ValueError and CLI rejects cleanly."""
    with pytest.raises(ValueError, match="Invalid mode 'SUPER_AUTO'"):
        manager.set_mode("SUPER_AUTO")

    rc = cli_main(["--db", manager.db.db_path, "mode", "invalid_mode"])
    assert rc == 2
    captured = capsys.readouterr()
    assert "invalid choice" in captured.err
    assert "Traceback" not in captured.err


def test_invalid_inputs_limits(manager):
    """Negative and non-positive limits raise ValueError."""
    with pytest.raises(ValueError, match="positive integer"):
        manager.set_default_limit(-5)

    with pytest.raises(ValueError, match="positive integer"):
        manager.set_default_limit(0)

    acc = manager.add_account("limits_test@example.com", daily_limit=85)

    with pytest.raises(ValueError, match="positive integer"):
        manager.set_limit(acc.id, -10)

    with pytest.raises(ValueError, match="positive integer"):
        manager.set_limit(acc.id, 0)


def test_invalid_inputs_email(manager):
    """Malformed or empty emails raise ValueError."""
    with pytest.raises(ValueError, match="Invalid email address"):
        manager.add_account("not_an_email")

    with pytest.raises(ValueError, match="Invalid email address"):
        manager.add_account("")

    with pytest.raises(ValueError, match="Invalid email address"):
        manager.add_account("   ")

    # Duplicate email rejection
    manager.add_account("duplicate@example.com", daily_limit=85)
    with pytest.raises(ValueError, match="already exists"):
        manager.add_account("duplicate@example.com", daily_limit=100)


def test_nonexistent_account_operations(manager, capsys):
    """Operating on nonexistent account IDs or emails raises AccountNotFoundError without traceback in CLI."""
    nonexistent = "ghost@example.com"

    with pytest.raises(AccountNotFoundError):
        manager.select_account(nonexistent)

    with pytest.raises(AccountNotFoundError):
        manager.enable(nonexistent)

    with pytest.raises(AccountNotFoundError):
        manager.disable(nonexistent)

    with pytest.raises(AccountNotFoundError):
        manager.human_required(nonexistent)

    with pytest.raises(AccountNotFoundError):
        manager.human_cleared(nonexistent)

    # Check CLI handles AccountNotFoundError cleanly
    rc = cli_main(["--db", manager.db.db_path, "select", nonexistent])
    assert rc == 1
    captured = capsys.readouterr()
    assert "Error: Account 'ghost@example.com' not found." in captured.err
    assert "Traceback" not in captured.err

    # Test with --json
    rc_json = cli_main(["--db", manager.db.db_path, "--json", "enable", nonexistent])
    assert rc_json == 1
    captured_json = capsys.readouterr()
    err_json = json.loads(captured_json.err)
    assert err_json["error"] == "AccountNotFoundError"
