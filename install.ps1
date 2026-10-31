<#
.SYNOPSIS
    Installs Flow Credit Rotator on Windows.

.DESCRIPTION
    Configures portable user directories ($env:USERPROFILE\.flow-accounts,
    $env:USERPROFILE\.local\bin), installs the package in editable mode (--no-deps),
    creates a flow-account.cmd launcher, and registers the skill in
    $env:USERPROFILE\.hermes\skills if present.
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
    Write-Error "USERPROFILE environment variable is not set."
    exit 1
}

$binTarget = Join-Path $userHome ".local\bin"
$dbDir = Join-Path $userHome ".flow-accounts"
$launcherCmd = Join-Path $binTarget "flow-account.cmd"
$launcherPs1 = Join-Path $binTarget "flow-account.ps1"

Write-Host "=========================================================="
Write-Host "Installing Flow Credit Rotator"
Write-Host "=========================================================="

# Locate python
$pythonExe = $null
if (Get-Command "python" -ErrorAction SilentlyContinue) {
    $pythonExe = "python"
} elseif (Get-Command "py" -ErrorAction SilentlyContinue) {
    $pythonExe = "py"
} elseif (Get-Command "python3" -ErrorAction SilentlyContinue) {
    $pythonExe = "python3"
} else {
    Write-Error "Python 3 is required but was not found in PATH."
    exit 1
}

# Ensure directories exist
if (-not (Test-Path $binTarget)) {
    New-Item -ItemType Directory -Path $binTarget -Force | Out-Null
}
if (-not (Test-Path $dbDir)) {
    New-Item -ItemType Directory -Path $dbDir -Force | Out-Null
}

# Create portable Windows CMD launcher
$launcherCmdContent = @"
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
"@
Set-Content -Path $launcherCmd -Value $launcherCmdContent -Encoding ASCII
Write-Host "✓ Created launcher at $launcherCmd"

# Create PowerShell launcher
$launcherPs1Content = @"
param(
    [Parameter(ValueFromRemainingArguments = `$true)]
    [string[]]`$Arguments
)

`$pythonCmd = if (Get-Command python -ErrorAction SilentlyContinue) {
    'python'
} elseif (Get-Command py -ErrorAction SilentlyContinue) {
    'py'
} else {
    'python3'
}

& `$pythonCmd -m flow_account.cli @Arguments
exit `$LASTEXITCODE
"@
Set-Content -Path $launcherPs1 -Value $launcherPs1Content -Encoding UTF8
Write-Host "✓ Created PowerShell launcher at $launcherPs1"

# Install in editable mode without pulling online dependencies
try {
    Write-Host "Installing flow-credit-rotator package (pip install -e . --no-deps)..."
    & $pythonExe -m pip install -e "$scriptDir" --no-deps | Out-Null
    Write-Host "✓ Package installed in editable mode."
} catch {
    Write-Warning "Could not run pip automatically. Ensure pip is installed and run: pip install -e . --no-deps"
}

# Register skill with Hermes if directory exists
$hermesSkillsDir = Join-Path $userHome ".hermes\skills"
if (Test-Path $hermesSkillsDir) {
    $oldSkillLink = Join-Path $hermesSkillsDir "google-flow-account-manager"
    if (Test-Path $oldSkillLink) {
        Remove-Item -Path $oldSkillLink -Recurse -Force -ErrorAction SilentlyContinue
        Write-Host "✓ Removed legacy skill link: $oldSkillLink"
    }

    $skillLink = Join-Path $hermesSkillsDir "flow-credit-rotator"
    if (Test-Path $skillLink) {
        Remove-Item -Path $skillLink -Recurse -Force -ErrorAction SilentlyContinue
    }

    try {
        New-Item -ItemType SymbolicLink -Path $skillLink -Target $scriptDir -Force | Out-Null
        Write-Host "✓ Registered skill with Hermes at $skillLink"
    } catch {
        try {
            New-Item -ItemType Junction -Path $skillLink -Target $scriptDir -Force | Out-Null
            Write-Host "✓ Registered skill junction with Hermes at $skillLink"
        } catch {
            Write-Warning "Could not create skill symlink/junction at $skillLink. Manual copy or elevated permissions may be required."
        }
    }
}

# Add ~/.local/bin to User PATH if not already present
$userPath = [Environment]::GetEnvironmentVariable("Path", "User")
if ($userPath -notlike "*$binTarget*") {
    try {
        $updatedPath = "$userPath;$binTarget".Trim(';')
        [Environment]::SetEnvironmentVariable("Path", $updatedPath, "User")
        Write-Host "✓ Added $binTarget to user PATH."
    } catch {
        Write-Warning "Could not update user PATH automatically. Add $binTarget to your PATH."
    }
}

Write-Host ""
Write-Host "Installation successful!"
Write-Host "Check installation with:"
Write-Host "  flow-account status"
Write-Host "Make sure $binTarget is in your PATH."
