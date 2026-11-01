# Flow Credit Rotator

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python 3.9+](https://img.shields.io/badge/python-3.9+-blue.svg)](https://www.python.org/downloads/)
[![Version](https://img.shields.io/badge/version-v1.2.2-blue.svg)](https://github.com/igorjribeiro/flow-credit-rotator/releases/tag/v1.2.2)
[![Status](https://img.shields.io/badge/status-production--ready-green.svg)](#)

A robust, production-grade credit rotation, pre-check reservation, and quota management system for multiple Google Flow accounts. Designed specifically for autonomous AI agents and automated video rendering pipelines.

Flow Credit Rotator dynamically tracks daily and monthly quotas, provides atomic pre-checks and reservations to eliminate concurrency races and over-limit spending, rotates accounts sequentially or randomly, synchronizes configuration from a central Excel workbook, and isolates credentials safely using the OS Keyring.

---

## Table of Contents

1. [Key Features](#key-features)
2. [Architecture](#architecture)
3. [Linux / macOS Installation](#linux--macos-installation)
4. [Windows Installation (PowerShell)](#windows-installation-powershell)
5. [How to Create the Real Configuration Spreadsheet](#how-to-create-the-real-configuration-spreadsheet)
6. [Storing Credentials Securely](#storing-credentials-securely)
7. [Synchronizing Configuration](#synchronizing-configuration)
8. [Rotation Strategies Explained](#rotation-strategies-explained)
9. [Pre-Check & Reservation Lifecycle](#pre-check--reservation-lifecycle)
10. [Autonomous AI Agent Protocol](#autonomous-ai-agent-protocol)
11. [CLI Command Reference](#cli-command-reference)
12. [Python API Usage](#python-api-usage)
13. [Troubleshooting](#troubleshooting)
14. [Uninstallation](#uninstallation)
15. [Contributing & Security](#contributing--security)
16. [License](#license)

---

## Key Features

- **Credit Pre-Check & Reservation Lock**:
  - Atomically verifies `used_today + active_reservations + estimated_cost <= daily_limit` (and monthly limits) prior to launching jobs.
  - Locks reserved capacity to prevent double-spending across parallel workers.
  - Explicit confirmation (`flow-account reserve-confirm`) debits credits only upon success; cancellation (`flow-account reserve-cancel`) releases capacity with zero credit charges.
- **Central XLSX Configuration (`config/flow_accounts.xlsx`)**:
  - Administer your entire account pool and system settings in a familiar, user-friendly Excel file.
  - `ACCOUNTS` sheet: set order, limits, enabled status, credential references, and operational notes.
  - `SETTINGS` sheet: set global defaults, rotation strategy, and timezone.
  - Sync with a single command: `flow-account sync config/flow_accounts.xlsx`.
  - Disappearing accounts are safely disabled/archived, never destroying historical consumption records.
- **Dynamic Account Pool**:
  - Scales to any number of accounts (1, 2, 5, 20, 50, 100+) without arbitrary ceilings or fixed limits.
- **Safe Credential Isolation**:
  - Strictly zero plaintext passwords in spreadsheets, SQLite database files, logs, or Git.
  - Passwords are stored in the OS Keyring (Windows Credential Manager, Linux SecretService, macOS Keychain).
- **Human Verification Handling (`NEEDS_HUMAN`)**:
  - When an account triggers a Google verification prompt or CAPTCHA, it is automatically rotated out of service.
  - Once solved by an operator, `flow-account human-cleared` restores it to rotation.
- **Timezone-Aware Resets**:
  - Automated daily and monthly quota rollovers (default: `America/Bahia` timezone).
- **Concurrent SQLite Engine**:
  - Thread-safe and process-safe with SQLite WAL mode (`PRAGMA journal_mode=WAL;`), 30-second busy timeout, and foreign key integrity.

---

## Architecture

```
flow-credit-rotator/
├── flow_account/             # Core Python package (v1.2.2)
│   ├── __init__.py           # Package exports and versioning
│   ├── config.py             # Defaults and path resolution
│   ├── models.py             # Account, Reservation, UsageRecord, SystemState
│   ├── db.py                 # SQLite WAL engine and schema migrations
│   ├── reset.py              # Timezone-aware rollover logic
│   ├── strategies.py         # Capacity-aware selection algorithms
│   ├── credentials.py        # OS Keyring integration with 0600 fallback vault
│   ├── importer.py           # Multi-tab XLSX parser and sync engine
│   ├── manager.py            # Orchestration engine with reservation lifecycle
│   └── cli.py                # Command-line interface
├── bin/
│   └── flow-account          # Portable Unix executable launcher
├── config/
│   └── README.md             # Local configuration documentation
├── templates/
│   └── flow_accounts_template.xlsx # Sole official XLSX configuration template (mock data)
├── tests/                    # Comprehensive pytest test suite (130+ tests)
├── install.sh                # Linux/macOS installer and Hermes skill setup
├── install.ps1               # Portable Windows PowerShell installer
├── pyproject.toml            # Build metadata (v1.2.2)
├── SKILL.md                  # Autonomous agent skill specification
├── .env.example              # Environment variables template
├── LICENSE                   # MIT License
├── CHANGELOG.md              # Semantic release notes
├── SECURITY.md               # Credential isolation and vulnerability policy
├── CONTRIBUTING.md           # Contribution guidelines
└── README.md                 # Public documentation
```

---

## Linux / macOS Installation

Execute the installation script in a bash terminal:

```bash
git clone https://github.com/igorjribeiro/flow-credit-rotator.git
cd flow-credit-rotator
chmod +x install.sh
./install.sh
```

The script:
1. Verifies that Python 3.9+ is available.
2. Creates `${HOME}/.local/bin` and `${HOME}/.flow-accounts`.
3. Symlinks `${HOME}/.local/bin/flow-account` to `bin/flow-account`.
4. Installs the package in editable mode (`pip install -e . --no-deps`).
5. Registers the skill for Hermes and Claude Code at `${HOME}/.hermes/skills/flow-credit-rotator`.

Ensure `${HOME}/.local/bin` is in your environment `PATH`:
```bash
export PATH="${HOME}/.local/bin:${PATH}"
```

Validate installation:
```bash
flow-account --help
flow-account status
```

---

## Windows Installation (PowerShell)

Open PowerShell and run:

```powershell
git clone https://github.com/igorjribeiro/flow-credit-rotator.git
cd flow-credit-rotator

# Allow execution of local scripts if required by your policy:
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass

.\install.ps1
```

The installer:
1. Locates Python (`python`, `py`, or `python3`).
2. Creates `$env:USERPROFILE\.local\bin` and `$env:USERPROFILE\.flow-accounts`.
3. Generates portable `flow-account.cmd` and `flow-account.ps1` launchers.
4. Installs the package in editable mode (`pip install -e . --no-deps`).
5. Registers the skill in `$env:USERPROFILE\.hermes\skills\flow-credit-rotator` if Hermes is present.

Validate installation:
```powershell
flow-account --help
flow-account status
```

---

## How to Create the Real Configuration Spreadsheet

Follow this 10-step onboarding flow to set up your accounts:

1. **Install the CLI**:
   Run `./install.sh` (Linux/macOS) or `.\install.ps1` (Windows).

2. **Copy the Official Template**:
   Copy the official template `templates/flow_accounts_template.xlsx` to your local configuration path:
   ```bash
   cp templates/flow_accounts_template.xlsx config/flow_accounts.xlsx
   ```
   > 📌 **Best Practice**: **Never edit the original template for daily use.** Always edit your local copy in `config/flow_accounts.xlsx`. The `config/flow_accounts.xlsx` file and any `config/*.xlsx` are strictly ignored by Git via `.gitignore`, ensuring your real accounts and personal data are never committed.

3. **Open `config/flow_accounts.xlsx`**:
   Open the file in Excel, LibreOffice, or Google Sheets.

4. **Edit the `ACCOUNTS` Sheet (Emails & Order)**:
   Replace the fictional emails with your real Google Flow account emails:

   | order | email | daily_limit | monthly_limit | enabled | credential_ref | notes |
   |---|---|---|---|---|---|---|
   | 1 | flow_user01@example.com | 85 | 1500 | YES | KEYRING:flow_user01@example.com | Primary production account |
   | 2 | flow_user02@example.com | 85 | 1500 | YES | KEYRING:flow_user02@example.com | Secondary rotation account |
   | 3 | flow_user03@example.com | 70 | 1200 | YES | KEYRING:flow_user03@example.com | Reserve account |

   - **`order`**: Priority sequence (1 = highest priority, used in sequential rotation).
   - **`email`**: The Google account email address.
   - **`daily_limit`**: Daily credit quota for this specific account.
   - **`monthly_limit`**: Monthly credit quota for this specific account (optional).
   - **`enabled`**: `YES` to include in active rotation, `NO` to disable.
   - **`credential_ref`**: Safe reference key (recommended: `KEYRING:<email>`).
   - **`notes`**: **(Optional)** Field for human observations only. Does NOT influence selection, rotation, limits, priority, or credentials.

   > ⚠️ **CRITICAL SECURITY RULE**: **NEVER put passwords or raw API keys in the Excel spreadsheet.** Store only the `KEYRING:<email>` reference.

5. **Edit the `SETTINGS` Sheet (Global Defaults)**:
   Configure system-wide defaults in the `SETTINGS` sheet:

   | Setting | Value | Description |
   |---|---|---|
   | `rotation_strategy` | `sequential` | Selection strategy: sequential \| random \| round-robin \| lowest-usage |
   | `default_daily_limit` | `85` | Default daily credit limit applied when an account-specific value is not provided |
   | `default_monthly_limit` | `1500` | Default monthly credit limit applied when an account-specific value is not provided |
   | `timezone` | `America/Bahia` | Timezone for daily and monthly credit rollovers |
   | `auto_rotate` | `YES` | YES for automatic account rotation, NO for manual/pinned mode |

6. **Save Passwords Securely**:
   For each account, register its password securely into the OS Keyring:
   ```bash
   flow-account credential set user@example.com
   ```
   The password input is completely hidden in the terminal and never printed to screen, database, or spreadsheet.

7. **Test Credentials**:
   Verify that your registered credential is functional:
   ```bash
   flow-account credential test user@example.com
   ```

8. **Synchronize Configuration**:
   Import your spreadsheet into the database engine:
   ```bash
   flow-account sync config/flow_accounts.xlsx
   ```

9. **Verify Accounts & Active Selection**:
   Check the status of your pool:
   ```bash
   flow-account list
   flow-account status
   flow-account current
   ```

10. **Ready for Production**:
    Your automated pipelines and AI agents can now reserve and consume credits safely.

---

## Storing Credentials Securely

Passwords and tokens are stored in the operating system's native secure credential manager (Windows Credential Manager, macOS Keychain, Linux SecretService) or a restricted local vault (`mode 0600`).

To store a password for an account:
```bash
flow-account credential set flow_user01@example.com
```

The command prompts for the password with input hidden (no echo):
```text
Enter secret/credential for 'flow_user01@example.com' (input hidden):
✓ Credential securely stored under reference 'KEYRING:flow_user01@example.com'
Plaintext password will never appear in SQLite, XLSX spreadsheets, or logs.
```

To test if a credential exists and is accessible:
```bash
flow-account credential test flow_user01@example.com
```

---

## Synchronizing Configuration

After creating or updating `config/flow_accounts.xlsx`, synchronize your changes into the database:

```bash
flow-account sync config/flow_accounts.xlsx
```

Output:
```text
=== FLOW ACCOUNTS SYNCHRONIZATION ===
Configuration file: config/flow_accounts.xlsx
Accounts added:     3
Accounts updated:   0
Accounts disabled:  0
Total in sheet:     3
Updated settings:
  - strategy: sequential
  - default_daily_limit: 85
  - default_monthly_limit: 1500
  - timezone: America/Bahia
  - mode: AUTO
```

Verify your active pool:
```bash
flow-account list
flow-account status
flow-account current
```

*Safety guarantee: If you remove an account from the spreadsheet, it is safely marked as `DISABLED` in the database. Its historical credit usage log is NEVER deleted.*

---

## Rotation Strategies Explained

Flow Credit Rotator supports 4 capacity-aware rotation algorithms:

1. **`sequential` (Default)**:
   - Uses strict numerical ordering: `1 → 2 → 3 → 4...`
   - Keeps using Account 1 as long as it has sufficient capacity for the requested operation.
   - When Account 1 reaches daily/monthly limit, is disabled, or lacks capacity for the job, auto-advances to Account 2.
   - On reaching the end of the pool, wraps back to the first eligible account.
2. **`random`**:
   - Uniformly selects at random strictly among accounts that have sufficient capacity for the requested operation.
3. **`round-robin`**:
   - Deterministically cycles across eligible accounts with sufficient capacity.
4. **`lowest-usage`**:
   - Selects the eligible account with the lowest `used_today` (including pending reservations).

> 💡 **Priority of Limits**: Account-specific limits configured in the `ACCOUNTS` sheet always override global defaults from the `SETTINGS` sheet. For example, if `default_daily_limit` is 85, but an account specifies `daily_limit = 70`, that account will strictly enforce the 70 credit limit.

---

## Pre-Check & Reservation Lifecycle

Autonomous agents must verify that an account has sufficient capacity before launching video generation.

### Example Scenario:
- **Active Account**: Has consumed 80 of its 85 daily credits (5 remaining).
- **Incoming Generation Job**: Costs 10 credits.
- **Pre-check Calculation**: `80 + 10 = 90 > 85`. The active account cannot run this job.

### 1. Pre-Check & Reserve
```bash
flow-account reserve 10 --note "Render video scene #402"
```
Output:
```text
✓ Credit Reservation Created
Reservation ID:   res_9b2d881ef032
Authorized Email: flow_user02@example.com (#2)
Reserved Credits: 10
Expires At:       2026-10-31T21:30:00-03:00
Note:             Render video scene #402
```
The rotator automatically selects `flow_user02@example.com` because it has available capacity. The 10 credits are now reserved, preventing other workers from double-spending this capacity.

### 2A. Job Succeeded: Confirm and Commit
```bash
flow-account reserve-confirm res_9b2d881ef032 --amount 10
```
Output:
```text
✓ Reservation 'res_9b2d881ef032' Confirmed
Account:         flow_user02@example.com (#2)
Charged Credits: 10
Remaining Today: 75
Account Status:  ACTIVE
```

### 2B. Job Failed or Aborted: Cancel
```bash
flow-account reserve-cancel res_9b2d881ef032 --reason "Browser navigation timeout"
```
Output:
```text
✓ Reservation 'res_9b2d881ef032' Cancelled
Zero credits were charged. Reserved capacity has been released.
```

---

## Autonomous AI Agent Protocol

When an autonomous agent interacts with Google Flow, it MUST follow this strict 6-step lifecycle:

```
[Agent Task]
      │
      ▼
1. Determine Expected Cost (e.g. 5 or 10 credits)
      │
      ▼
2. Call Pre-Check / Reserve:
   $ flow-account reserve 10 --note "Job #1042 batch render"
      │
      ▼
3. Obtain Authorized Account & Reservation ID
      │
      ▼
4. Execute Video Generation in Google Flow using Authorized Account
      │
      ├───────────────────────────────┐
   [SUCCESS]                       [FAILURE / ABORT]
      │                               │
      ▼                               ▼
5. Confirm Consumption:         6. Cancel Reservation:
   $ flow-account reserve-confirm  $ flow-account reserve-cancel
     <reservation_id>                <reservation_id> --reason "Error"
   (Deducts real credits)          (Zero credits charged, capacity freed)
```

> 🛑 **MANDATORY POLICY**: Agents must NEVER execute a generation first and register consumption afterwards without a pre-check reservation.

---

## CLI Command Reference

| Command | Description |
|---|---|
| `flow-account status [--json]` | Show overall status, pool metrics, and current active account. |
| `flow-account list [--json]` | List all accounts, limits, usage, priority, and eligibility. |
| `flow-account current [--json]` | Show currently active account details and remaining credits. |
| `flow-account next [--json]` | Manually rotate to the next eligible account in sequence. |
| `flow-account can-consume <amount> [--json]` | Pre-check capacity for an operation without creating a reservation. |
| `flow-account reserve <amount> [--note <text>] [--json]` | Pre-check and reserve credits before generation; returns account and reservation ID. |
| `flow-account reserve-confirm <res_id> [--amount N] [--json]` | Confirm reservation (alias: `commit`); debits credits to usage history. |
| `flow-account reserve-cancel <res_id> [--reason <text>] [--json]` | Cancel reservation (alias: `cancel`); releases capacity without charging credits. |
| `flow-account reservations [--status STATUS] [--json]` | List recent reservations (`PENDING`, `COMMITTED`, `CANCELLED`, `EXPIRED`). |
| `flow-account sync [config/flow_accounts.xlsx] [--json]` | Synchronize accounts, order, and global settings from central XLSX spreadsheet. |
| `flow-account credential set <email_or_ref>` | Securely store account secret in system keyring via hidden terminal prompt. |
| `flow-account credential test <email_or_ref>` | Verify if a credential exists and is accessible. |
| `flow-account mode [auto\|manual] [--json]` | View or set rotation mode (`AUTO` or `MANUAL`). |
| `flow-account select <id\|email> [--json]` | Select a specific account to use. |
| `flow-account consume <amount> [--note <text>] [--json]` | Direct debit of credits from active account. |
| `flow-account add <email> [--daily N] [--monthly M] [--priority N] [--credential-ref REF]` | Add a new account manually. |
| `flow-account set-limit <account> <daily> [--monthly M] [--json]` | Adjust daily/monthly limits for an account. |
| `flow-account human-required <account> [reason] [--json]` | Mark account as requiring CAPTCHA/human intervention; triggers immediate auto-rotation. |
| `flow-account human-cleared <account> [--json]` | Restore account to active rotation after human solves verification. |
| `flow-account disable <account>` / `enable <account>` | Disable or re-enable an account. |
| `flow-account reset-daily [--json]` | Manually trigger daily quota reset and restore eligible accounts. |
| `flow-account history [--limit N] [--account ID\|EMAIL] [--json]` | View credit consumption audit log. |

---

## Python API Usage

```python
from flow_account import FlowAccountManager, NoAccountsAvailableError

manager = FlowAccountManager()

# 1. Pre-check and reserve credits
try:
    acc, res = manager.reserve(cost=10, note="Video generation batch #501")
    print(f"Using {acc.email} (Reservation: {res.id})")
except NoAccountsAvailableError:
    print("All accounts exhausted or lack sufficient capacity. Pausing until daily rollover.")
    raise

# 2. Execute task
success = False
try:
    # ... call Google Flow automation using acc.email ...
    success = True
finally:
    if success:
        # 3. Confirm and commit credit deduction
        updated_acc, usage_record = manager.confirm_reservation(res.id, actual_amount=10)
        print(f"Charged 10 credits. Available remaining: {updated_acc.available_credits}")
    else:
        # 4. Release reservation on failure (zero credits debited)
        manager.cancel_reservation(res.id, reason="Job aborted")
        print("Reservation cancelled. Zero credits consumed.")
```

---

## Troubleshooting

- **`NoAccountsAvailableError` during reserve / consume**:
  Check `flow-account list`. All accounts may have exhausted their daily limit, reached their monthly quota, or be flagged as `DISABLED` or `NEEDS_HUMAN`. You can test manual reset via `flow-account reset-daily`.
- **Account stuck in `NEEDS_HUMAN`**:
  Run `flow-account human-cleared <email>` after resolving the Google CAPTCHA or verification challenge in a browser.
- **`flow-account: command not found`**:
  Ensure `${HOME}/.local/bin` (Linux/macOS) or `$env:USERPROFILE\.local\bin` (Windows) is in your system `PATH`.
- **Import / Sync rejected with `SecurityImportError`**:
  Your spreadsheet contains forbidden password headers (e.g. `password`, `senha`, `secret`). Remove those columns and use `credential_ref` referencing `KEYRING:<email>` instead.

---

## Uninstallation

### Linux / macOS
```bash
rm -f "${HOME}/.local/bin/flow-account"
rm -rf "${HOME}/.hermes/skills/flow-credit-rotator"
pip uninstall -y flow-credit-rotator 2>/dev/null || true
```

### Windows (PowerShell)
```powershell
Remove-Item -Path "$env:USERPROFILE\.local\bin\flow-account.cmd" -Force -ErrorAction SilentlyContinue
Remove-Item -Path "$env:USERPROFILE\.local\bin\flow-account.ps1" -Force -ErrorAction SilentlyContinue
Remove-Item -Path "$env:USERPROFILE\.hermes\skills\flow-credit-rotator" -Recurse -Force -ErrorAction SilentlyContinue
python -m pip uninstall -y flow-credit-rotator
```

---

## Contributing & Security

- See [CONTRIBUTING.md](CONTRIBUTING.md) for code style, development workflow, and testing practices.
- See [SECURITY.md](SECURITY.md) for security policies, vulnerability reporting, and credential isolation.

---

## License

This project is licensed under the [MIT License](LICENSE).
