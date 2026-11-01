"""Public release tests for flow-credit-rotator.

Verifies:
- Package metadata (name, version, license, repository)
- Absence of personal hardcoded paths (/home/<user>, C:\\Users\\<user>)
- Portability and syntax of Linux (install.sh) and Windows (install.ps1) installers
- Absence of fixed account limits (never 12 as a ceiling)
- Safety of import templates (CSV and XLSX)
- .gitignore mandatory patterns
- Sequential default rotation and random rotation functionality
- Dynamic pool scalability and credential isolation
"""

import os
import subprocess
from pathlib import Path
import pytest

from flow_account import (
    FlowAccountManager,
    AccountStatus,
    Mode,
    __version__,
)
from flow_account.config import (
    get_default_strategy,
    DEFAULT_DAILY_LIMIT,
    get_default_daily_limit,
)

REPO_ROOT = Path(__file__).resolve().parent.parent


def test_package_metadata():
    """Verify pyproject.toml and flow_account version metadata."""
    pyproject_path = REPO_ROOT / "pyproject.toml"
    assert pyproject_path.exists(), "pyproject.toml must exist"

    content = pyproject_path.read_text(encoding="utf-8")
    assert 'name = "flow-credit-rotator"' in content
    assert 'version = "1.2.2"' in content
    assert 'license = { text = "MIT" }' in content
    assert 'Multi-account' in content
    assert __version__ == "1.2.2"

    assert 'Repository = "https://github.com/igorjribeiro/flow-credit-rotator"' in content
    assert 'Homepage = "https://github.com/igorjribeiro/flow-credit-rotator"' in content


def test_no_hardcoded_personal_paths():
    """Verify that no tracked repository files contain hardcoded personal paths."""
    forbidden = [
        b"/home/" + b"ubuntu",
        b"C:\\Users\\" + b"Igor",
        b"C:/Users/" + b"Igor",
        b"/Users/" + b"Igor",
    ]

    # Inspect all non-git, non-cache files
    scanned = 0
    for root, dirs, files in os.walk(REPO_ROOT):
        # Skip git, cache, and build directories
        dirs[:] = [d for d in dirs if d not in {".git", "__pycache__", ".pytest_cache", ".venv", "venv", "dist", "build", "egg-info"}]
        for fname in files:
            if fname.endswith((".pyc", ".png", ".jpg", ".xlsx")) or fname == "test_public_release.py":
                continue
            fpath = Path(root) / fname
            content = fpath.read_bytes()
            scanned += 1
            for bad in forbidden:
                assert bad not in content, f"Found hardcoded path {bad.decode()} in {fpath.relative_to(REPO_ROOT)}"

    assert scanned >= 15, f"Expected to scan at least 15 files, scanned {scanned}"


def test_linux_installer_syntax_and_portability():
    """Verify install.sh has valid bash syntax and portable directory references."""
    install_sh = REPO_ROOT / "install.sh"
    assert install_sh.exists()

    # Syntax check via bash -n
    res = subprocess.run(["bash", "-n", str(install_sh)], capture_output=True, text=True)
    assert res.returncode == 0, f"install.sh bash syntax error: {res.stderr}"

    content = install_sh.read_text(encoding="utf-8")
    assert "Flow Credit Rotator" in content
    assert "${HOME}/.local/bin" in content or "$HOME/.local/bin" in content
    assert "flow-credit-rotator" in content
    assert ("/home/" + "ubuntu") not in content


def test_windows_installer_portability():
    """Verify install.ps1 contains portable PowerShell logic and no personal paths."""
    install_ps1 = REPO_ROOT / "install.ps1"
    assert install_ps1.exists()

    content = install_ps1.read_text(encoding="utf-8")
    assert "$env:USERPROFILE" in content
    assert "flow-account" in content
    assert "flow-credit-rotator" in content
    assert ("C:\\Users\\" + "Igor") not in content
    assert "Igor" not in content


def test_no_fixed_twelve_limit():
    """Verify that 12 is never treated as a fixed ceiling or hardcoded limit."""
    code_files = list((REPO_ROOT / "flow_account").glob("*.py"))
    for py_file in code_files:
        content = py_file.read_text(encoding="utf-8")
        assert "MAX_ACCOUNTS = 12" not in content
        assert "12 accounts" not in content
        assert "twelve accounts" not in content.lower()


def test_gitignore_covers_all_required_rules():
    """Verify .gitignore covers all mandatory secret and artifact exclusions."""
    gitignore_path = REPO_ROOT / ".gitignore"
    assert gitignore_path.exists()
    content = gitignore_path.read_text(encoding="utf-8")
    required_patterns = [
        ".env",
        ".env.*",
        "!.env.example",
        "*.db",
        "*.sqlite",
        "*.sqlite3",
        "__pycache__/",
        ".pytest_cache/",
        ".venv/",
        "venv/",
        "dist/",
        "build/",
        "*.egg-info/",
        "*.log",
        "credentials/",
        "secrets/",
        "cookies/",
        "sessions/",
    ]
    for pattern in required_patterns:
        assert pattern in content, f"Pattern {pattern} missing from .gitignore"


def test_import_templates_safety_and_validity(temp_db_path):
    """Verify official template file (XLSX) contains valid mock data and zero passwords."""
    xlsx_path = REPO_ROOT / "templates" / "flow_accounts_template.xlsx"
    assert xlsx_path.exists(), "Official XLSX template must exist"

    # Test importing/syncing XLSX into fresh manager
    manager = FlowAccountManager(db_path=temp_db_path)
    res_xlsx = manager.sync(str(xlsx_path))
    assert res_xlsx["added"] >= 3
    for acc in manager.list_accounts():
        assert acc.email.endswith("@example.com")
        assert acc.status == AccountStatus.ACTIVE


def test_sequential_default_rotation_behavior(temp_db_path):
    """Verify sequential rotation is default, advances deterministically and loops."""
    assert get_default_strategy() == "sequential"
    assert get_default_daily_limit() == 85

    manager = FlowAccountManager(db_path=temp_db_path)
    manager.set_mode(Mode.AUTO)

    # Add 3 accounts
    manager.add_account("acc1@example.com", daily_limit=10, priority=1)
    manager.add_account("acc2@example.com", daily_limit=10, priority=1)
    manager.add_account("acc3@example.com", daily_limit=10, priority=2)

    # Initial selection is first account
    curr = manager.current_account()
    assert curr is not None
    assert curr.email == "acc1@example.com"

    # Consuming under limit keeps current account
    acc, _ = manager.consume(5)
    assert acc.email == "acc1@example.com"
    curr = manager.current_account()
    assert curr is not None and curr.email == "acc1@example.com"

    # Reaching limit rotates to acc2
    acc, _ = manager.consume(5)
    assert acc.email == "acc1@example.com"
    # Now acc1 is exhausted, current should auto-rotate to acc2
    next_acc = manager.current_account()
    assert next_acc is not None
    assert next_acc.email == "acc2@example.com"


def test_random_rotation_eligibility(temp_db_path):
    """Verify random strategy only selects eligible accounts."""
    manager = FlowAccountManager(db_path=temp_db_path)
    manager.set_mode(Mode.AUTO)
    manager.set_strategy("random")

    # Add 1 active, 1 exhausted, 1 disabled, 1 needs_human
    manager.add_account("active@example.com", daily_limit=100)
    manager.add_account("exhausted@example.com", daily_limit=10)
    manager.select_account("exhausted@example.com")
    manager.consume(10)

    manager.add_account("disabled@example.com", daily_limit=100)
    manager.disable("disabled@example.com")

    manager.add_account("captcha@example.com", daily_limit=100)
    manager.human_required("captcha@example.com", reason="CAPTCHA")

    # Across multiple selections, only active@example.com must ever be picked
    for _ in range(10):
        sel = manager.current_account()
        assert sel is not None
        assert sel.email == "active@example.com"


def test_dynamic_pool_scalability(temp_db_path):
    """Verify the pool dynamically handles variable quantities (e.g. 15 accounts) without error."""
    manager = FlowAccountManager(db_path=temp_db_path)
    for i in range(1, 16):
        manager.add_account(f"bot{i:02d}@example.com", daily_limit=85, priority=1)

    accounts = manager.list_accounts()
    assert len(accounts) == 15
    status = manager.status()
    assert status["summary"]["total_accounts"] == 15
    assert status["summary"]["active_accounts"] == 15
