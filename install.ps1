<#
.SYNOPSIS
    Installs Flow Credit Rotator on Windows.

.DESCRIPTION
    Configures portable user directories ($env:USERPROFILE\.flow-accounts,
    $env:USERPROFILE\.local\bin), installs the package in editable mode (--no-deps),
    creates flow-account.cmd and flow-account.ps1 launchers, and registers the skill in
    $env:USERPROFILE\.hermes\skills if present.
    Preserves all existing user state, SQLite databases, credentials, and configurations.
#>

[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'

$scriptDir = $PSScriptRoot
if (-not $scriptDir) {
    $scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
}

$userHome = $env:USERPROFILE
if (-not $userHome) {
    $userHome = $HOME
}
if (-not $userHome) {
    Write-Error "[ERROR] Neither USERPROFILE nor HOME environment variable is set."
    exit 1
}

$binTarget = Join-Path $userHome ".local\bin"
$stateDir = Join-Path $userHome ".flow-accounts"
$dbFile = Join-Path $stateDir "flow_accounts.db"
$vaultFile = Join-Path $stateDir ".vault_store"
$configDir = Join-Path $scriptDir "config"
$launcherCmd = Join-Path $binTarget "flow-account.cmd"
$launcherPs1 = Join-Path $binTarget "flow-account.ps1"

Write-Host "=========================================================="
Write-Host "Installing Flow Credit Rotator"
Write-Host "=========================================================="

# Check for existing installation
$oldCommand = Get-Command "flow-account" -ErrorAction SilentlyContinue
if ($oldCommand) {
    $oldPath = $oldCommand.Source
    Write-Host "[INFO] OLD_FLOW_ACCOUNT_PATH=$oldPath"
} else {
    Write-Host "[INFO] OLD_FLOW_ACCOUNT_PATH=NONE"
}

# Locate python
$pythonExe = $null
if (Get-Command "python" -ErrorAction SilentlyContinue) {
    $pythonExe = "python"
} elseif (Get-Command "py" -ErrorAction SilentlyContinue) {
    $pythonExe = "py"
} elseif (Get-Command "python3" -ErrorAction SilentlyContinue) {
    $pythonExe = "python3"
} else {
    Write-Error "[ERROR] Python 3 is required but was not found in PATH."
    exit 1
}

Write-Host "[INFO] Using Python interpreter: $pythonExe"

# Directory isolation and state preservation
Write-Host "[INFO] PROGRAM FILES: $scriptDir"
Write-Host "[INFO] STATE/DATABASE: $stateDir"
Write-Host "[INFO] CREDENTIAL STORAGE: OS Keyring / $vaultFile"
Write-Host "[INFO] USER CONFIG: $configDir"

if (Test-Path $dbFile) {
    Write-Host "[OK] Existing SQLite database preserved: $dbFile"
}
if (Test-Path $vaultFile) {
    Write-Host "[OK] Existing credential vault preserved: $vaultFile"
}

# Ensure directory structures exist without modifying existing contents
if (-not (Test-Path $binTarget)) {
    New-Item -ItemType Directory -Path $binTarget -Force | Out-Null
}
if (-not (Test-Path $stateDir)) {
    New-Item -ItemType Directory -Path $stateDir -Force | Out-Null
}

# Create portable Windows CMD launcher
$launcherCmdContent = @'
@echo off
setlocal
where python >nul 2>nul
if %ERRORLEVEL% EQU 0 (
    python -m flow_account.cli %*
    exit /b %ERRORLEVEL%
)
where py >nul 2>nul
if %ERRORLEVEL% EQU 0 (
    py -m flow_account.cli %*
    exit /b %ERRORLEVEL%
)
python3 -m flow_account.cli %*
exit /b %ERRORLEVEL%
'@

Set-Content -Path $launcherCmd -Value $launcherCmdContent -Encoding ASCII
Write-Host "[OK] Created CMD launcher at $launcherCmd"

# Create PowerShell launcher using raw single-quote string (no interpolation, zero backticks)
$launcherPs1Content = @'
[CmdletBinding()]
param(
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$Arguments
)

$pythonCmd = if (Get-Command python -ErrorAction SilentlyContinue) {
    'python'
} elseif (Get-Command py -ErrorAction SilentlyContinue) {
    'py'
} else {
    'python3'
}

& $pythonCmd -m flow_account.cli @Arguments
exit $LASTEXITCODE
'@

Set-Content -Path $launcherPs1 -Value $launcherPs1Content -Encoding ASCII
Write-Host "[OK] Created PowerShell launcher at $launcherPs1"

# Install package in editable mode without pulling online dependencies
try {
    Write-Host "[INFO] Installing flow-credit-rotator package (pip install -e . --no-deps)..."
    & $pythonExe -m pip install -e "$scriptDir" --no-deps | Out-Null
    Write-Host "[OK] Package installed in editable mode."
} catch {
    Write-Warning "[WARN] Could not run pip automatically. Ensure pip is installed and run: pip install -e . --no-deps"
}

# Register skill with Hermes if directory exists
$hermesSkillsDir = Join-Path $userHome ".hermes\skills"
if (Test-Path $hermesSkillsDir) {
    $oldSkillLink = Join-Path $hermesSkillsDir "google-flow-account-manager"
    if (Test-Path $oldSkillLink) {
        Remove-Item -Path $oldSkillLink -Recurse -Force -ErrorAction SilentlyContinue
        Write-Host "[OK] Removed legacy skill link: $oldSkillLink"
    }

    $skillLink = Join-Path $hermesSkillsDir "flow-credit-rotator"
    if (Test-Path $skillLink) {
        Remove-Item -Path $skillLink -Recurse -Force -ErrorAction SilentlyContinue
    }

    try {
        New-Item -ItemType SymbolicLink -Path $skillLink -Target $scriptDir -Force | Out-Null
        Write-Host "[OK] Registered skill symlink with Hermes at $skillLink"
    } catch {
        try {
            New-Item -ItemType Junction -Path $skillLink -Target $scriptDir -Force | Out-Null
            Write-Host "[OK] Registered skill junction with Hermes at $skillLink"
        } catch {
            try {
                Copy-Item -Path $scriptDir -Destination $skillLink -Recurse -Force | Out-Null
                Write-Host "[OK] Registered skill directory with Hermes at $skillLink"
            } catch {
                Write-Warning "[WARN] Could not create skill link at $skillLink. Manual copy may be required."
            }
        }
    }
}

# Add ~/.local/bin to User PATH if not already present
$userPath = [Environment]::GetEnvironmentVariable("Path", "User")
if ($null -ne $userPath -and $userPath -notlike "*$binTarget*") {
    try {
        $updatedPath = "$userPath;$binTarget".Trim(';')
        [Environment]::SetEnvironmentVariable("Path", $updatedPath, "User")
        Write-Host "[OK] Added $binTarget to user PATH."
    } catch {
        Write-Warning "[WARN] Could not update user PATH automatically. Add $binTarget to your PATH."
    }
}

# Update PATH in current process session so flow-account resolves immediately
if ($env:Path -notlike "*$binTarget*") {
    $env:Path = "$binTarget;$env:Path"
}

# Validate new installation resolution
$newCommand = Get-Command "flow-account" -ErrorAction SilentlyContinue
if ($newCommand) {
    $newPath = $newCommand.Source
    Write-Host "[INFO] NEW_FLOW_ACCOUNT_PATH=$newPath"
} else {
    Write-Host "[INFO] NEW_FLOW_ACCOUNT_PATH=$launcherCmd"
}

Write-Host ""
Write-Host "=========================================================="
Write-Host "[OK] Installation successful!"
Write-Host "=========================================================="
Write-Host "Verify installation with:"
Write-Host "  flow-account status"
Write-Host "Ensure $binTarget is in your system PATH."
