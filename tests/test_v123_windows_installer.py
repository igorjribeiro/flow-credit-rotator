"""Tests for Windows installer (install.ps1) and cross-platform installation integrity.

Validates v1.2.3 requirements:
- 100% pure ASCII encoding (zero mojibake, zero Unicode characters)
- Zero invalid backtick escapes (`$true, `$Arguments, etc.)
- PowerShell AST parsing with zero syntax errors (PowerShell 5.1 & Core compatible)
- Clean installation simulation in isolated environment
- Upgrade simulation with state and database preservation
- GitHub Actions CI workflow existence and coverage
"""

import os
from pathlib import Path
import re
import shutil
import subprocess

REPO_ROOT = Path(__file__).resolve().parent.parent


def test_install_ps1_pure_ascii():
    """Verify install.ps1 is 100% pure 7-bit ASCII to prevent any Windows PowerShell 5.1 encoding/mojibake issues."""
    ps1_path = REPO_ROOT / "install.ps1"
    assert ps1_path.exists(), "install.ps1 must exist"

    raw_bytes = ps1_path.read_bytes()
    try:
        raw_bytes.decode("ascii")
    except UnicodeDecodeError as exc:
        bad_slice = raw_bytes[max(0, exc.start - 5):exc.start + 10]
        assert False, f"install.ps1 contains non-ASCII character at byte {exc.start}: {bad_slice}"


def test_install_ps1_no_invalid_backtick_escapes():
    """Verify install.ps1 contains zero erroneous backtick escapes on variables."""
    ps1_path = REPO_ROOT / "install.ps1"
    text = ps1_path.read_text(encoding="ascii")

    # Look for backticks preceding variable markers
    invalid_escapes = re.findall(r"`(?:\$|true|false|[a-zA-Z0-9_]+)", text)
    assert len(invalid_escapes) == 0, f"Found invalid backticks in install.ps1: {invalid_escapes}"


def test_install_ps1_powershell_ast_parser():
    """Run real PowerShell parser AST to verify 0 syntax errors."""
    ps1_path = REPO_ROOT / "install.ps1"
    pwsh_bin = shutil.which("pwsh") or shutil.which("powershell")
    if not pwsh_bin:
        # Fallback to local user installation
        local_pwsh = Path.home() / ".local" / "bin" / "pwsh"
        if local_pwsh.exists():
            pwsh_bin = str(local_pwsh)

    if not pwsh_bin:
        import pytest
        pytest.skip("PowerShell CLI (pwsh) not found on host to test AST parsing")

    verify_script = (
        f"$errors = @(); "
        f"$ast = [System.Management.Automation.Language.Parser]::ParseFile('{ps1_path}', [ref]$null, [ref]$errors); "
        f"if ($errors.Count -gt 0) {{ "
        f"    $errors | ForEach-Object {{ Write-Host \"ERROR: $($_.Message) at line $($_.Extent.StartLineNumber)\" }}; "
        f"    exit 1 "
        f"}} else {{ "
        f"    Write-Host 'PARSER_SUCCESS' "
        f"}}"
    )

    proc = subprocess.run([pwsh_bin, "-NoProfile", "-Command", verify_script], capture_output=True, text=True)
    assert proc.returncode == 0, f"PowerShell AST parser returned errors:\n{proc.stdout}\n{proc.stderr}"
    assert "PARSER_SUCCESS" in proc.stdout


def test_clean_installation_and_state_preservation(tmp_path):
    """Simulate clean installation followed by reinstallation with existing state in an isolated directory."""
    pwsh_bin = shutil.which("pwsh") or shutil.which("powershell")
    if not pwsh_bin:
        local_pwsh = Path.home() / ".local" / "bin" / "pwsh"
        if local_pwsh.exists():
            pwsh_bin = str(local_pwsh)

    if not pwsh_bin:
        import pytest
        pytest.skip("pwsh not available on test host")

    fake_userprofile = tmp_path / "fake_user"
    fake_userprofile.mkdir(parents=True, exist_ok=True)

    env = os.environ.copy()
    env["USERPROFILE"] = str(fake_userprofile)
    env["HOME"] = str(fake_userprofile)

    ps1_path = REPO_ROOT / "install.ps1"

    # 1. Clean installation run
    proc1 = subprocess.run([pwsh_bin, "-NoProfile", "-File", str(ps1_path)], capture_output=True, text=True, env=env)
    assert proc1.returncode == 0, f"install.ps1 failed on clean install:\n{proc1.stdout}\n{proc1.stderr}"
    assert "OLD_FLOW_ACCOUNT_PATH=" in proc1.stdout
    assert "PROGRAM FILES:" in proc1.stdout
    assert "STATE/DATABASE:" in proc1.stdout

    # Verify created launchers
    cmd_launcher = fake_userprofile / ".local" / "bin" / "flow-account.cmd"
    ps1_launcher = fake_userprofile / ".local" / "bin" / "flow-account.ps1"
    assert cmd_launcher.exists(), "flow-account.cmd launcher was not created"
    assert ps1_launcher.exists(), "flow-account.ps1 launcher was not created"

    # 2. Simulate existing database and state creation
    db_file = fake_userprofile / ".flow-accounts" / "flow_accounts.db"
    db_file.write_text("DUMMY_DATABASE_PAYLOAD", encoding="utf-8")

    # 3. Upgrade / Reinstallation run
    proc2 = subprocess.run([pwsh_bin, "-NoProfile", "-File", str(ps1_path)], capture_output=True, text=True, env=env)
    assert proc2.returncode == 0, f"install.ps1 failed on reinstallation:\n{proc2.stdout}\n{proc2.stderr}"
    assert "Existing SQLite database preserved" in proc2.stdout
    assert db_file.read_text(encoding="utf-8") == "DUMMY_DATABASE_PAYLOAD", "User database state must NOT be overwritten"


def test_github_actions_windows_workflow():
    """Verify that .github/workflows/windows-test.yml covers Windows PowerShell 5.1 and pwsh on windows-latest."""
    workflow_path = REPO_ROOT / ".github" / "workflows" / "windows-test.yml"
    assert workflow_path.exists(), "GitHub Actions Windows workflow must exist"

    content = workflow_path.read_text(encoding="utf-8")
    assert "windows-latest" in content
    assert "powershell" in content
    assert "pwsh" in content
    assert "install.ps1" in content
    assert "flow-account status" in content
