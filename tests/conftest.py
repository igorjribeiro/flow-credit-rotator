"""Pytest configuration and shared fixtures for Google Flow Account Manager tests."""

import os
import tempfile
import pytest
from pathlib import Path

from flow_account.manager import FlowAccountManager
from flow_account.models import AccountStatus, Strategy, Mode


@pytest.fixture
def temp_db_path(tmp_path):
    """Provide a path to a temporary SQLite database file."""
    db_file = tmp_path / "test_flow_accounts.db"
    yield str(db_file)
    if db_file.exists():
        try:
            db_file.unlink()
        except OSError:
            pass


@pytest.fixture
def manager(temp_db_path):
    """Provide a clean FlowAccountManager instance on a temporary database."""
    mgr = FlowAccountManager(db_path=temp_db_path)
    yield mgr
    mgr.db.close()


@pytest.fixture
def populated_manager(manager):
    """Provide a FlowAccountManager populated with representative accounts."""
    # Account 1: Priority 1, 85 daily limit
    manager.add_account(
        email="prio1@example.com",
        daily_limit=85,
        monthly_limit=2500,
        priority=1,
        credential_ref="ENV:REF_01",
        notes="Primary account",
    )
    # Account 2: Priority 1, 85 daily limit
    manager.add_account(
        email="prio1_b@example.com",
        daily_limit=85,
        monthly_limit=2500,
        priority=1,
        credential_ref="ENV:REF_02",
        notes="Primary secondary account",
    )
    # Account 3: Priority 2, 85 daily limit
    manager.add_account(
        email="prio2@example.com",
        daily_limit=85,
        monthly_limit=2500,
        priority=2,
        credential_ref="ENV:REF_03",
        notes="Overflow account",
    )
    # Account 4: Priority 3, 100 daily limit
    manager.add_account(
        email="prio3@example.com",
        daily_limit=100,
        monthly_limit=3000,
        priority=3,
        credential_ref="ENV:REF_04",
        notes="Backup account",
    )
    return manager
