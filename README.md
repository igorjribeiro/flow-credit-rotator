# Flow Credit Rotator

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python 3.9+](https://img.shields.io/badge/python-3.9+-blue.svg)](https://www.python.org/downloads/)
[![Version](https://img.shields.io/badge/version-v1.1.0-blue.svg)](https://github.com/igorjribeiro/flow-credit-rotator/releases/tag/v1.1.0)
[![Status](https://img.shields.io/badge/status-production--ready-green.svg)](#)

A robust, production-grade management, pre-check credit reservation, and rotation system for Google Flow accounts used by autonomous AI agents and high-throughput video generation pipelines.

The rotator tracks daily and monthly quotas, provides pre-check reservations to eliminate concurrency races and over-limit spending, rotates accounts sequentially or randomly, synchronizes configuration from a central XLSX spreadsheet, and isolates credentials safely using the OS Keyring.

---

## Key Features

- **Credit Pre-Check & Reservation System**:
  - `used_today + active_reservations + estimated_cost <= daily_limit` checked before each job.
  - Locks reserved capacity to avoid double-spend across concurrent agents.
  - Explicit confirmation (`commit` / `reserve-confirm`) upon task success or cancellation (`cancel` / `reserve-cancel`) on failure.
- **Central XLSX Configuration (`config/flow_accounts.xlsx`)**:
  - Full account pool and system settings editable in a user-friendly Excel spreadsheet.
  - `ACCOUNTS` sheet: order, email, daily/monthly limits, enabled flag, credential reference, and notes.
  - `SETTINGS` sheet: rotation strategy, default daily limit, timezone, and auto-rotate mode.
  - One-command synchronization: `flow-account sync config/flow_accounts.xlsx`.
  - Disappearing accounts are disabled/archived, never destroying historical consumption logs.
- **Dynamic Account Pool**:
  - Arbitrary number of accounts (1, 2, 5, 20, 50, 100+) without any hardcoded limit or ceiling.
- **Intelligent Rotation Strategies**:
  - `sequential` **(Default)**: Deterministic ordering (`1 -> 2 -> 3`), holding active accounts while capacity suffices, auto-advancing on exhaustion, and looping back.
  - `random`: Uniform random selection strictly among eligible accounts with sufficient capacity.
  - `round-robin` and `lowest-usage`: Also capacity-aware.
- **Safe Credential Isolation & Keyring Support**:
  - Zero plaintext passwords in spreadsheets, SQLite, or Git.
  - Secure credential storage: `flow-account credential set <email_or_ref>` stores passwords securely in the OS Keyring / Windows Credential Manager.
- **Human Verification Handling (`NEEDS_HUMAN`)**:
  - Immediate rotation away from accounts flagged with CAPTCHA or Google verification challenges.
  - Seamless recovery via `human-cleared` once solved.
- **Timezone-Aware Rollovers**:
  - Automatic daily and monthly quota resets (default timezone: `America/Bahia`).
- **Durable Concurrent SQLite**:
  - SQLite in WAL mode with 30s busy timeout, foreign keys, and atomic immediate transactions.

---

## Architecture

```
flow-credit-rotator/
├── flow_account/             # Core Python package (v1.1.0)
│   ├── __init__.py           # Package exports and versioning
│   ├── config.py             # Environment configurations and defaults
│   ├── models.py             # Account, Reservation, UsageRecord, SystemState
│   ├── db.py                 # SQLite engine, WAL pragmas, reservations table
│   ├── reset.py              # Timezone-aware daily/monthly rollover logic
│   ├── strategies.py         # Capacity-aware Sequential, Random, Lowest-Usage
│   ├── credentials.py        # Keyring integration with secure fallback vault
│   ├── importer.py           # CSV/XLSX importer and central config sync
│   ├── manager.py            # Orchestration engine with reservation lifecycle
│   └── cli.py                # Comprehensive command-line interface
├── config/
│   └── flow_accounts.xlsx    # Central user-editable configuration workbook
├── bin/
│   └── flow-account          # Portable Unix executable launcher
├── templates/
│   ├── accounts_template.csv  # Safe CSV import template
│   ├── accounts_template.xlsx # Safe XLSX import template
│   └── flow_accounts_template.xlsx # Central configuration template
├── tests/                    # 121 comprehensive pytest tests
├── install.sh                # Linux/macOS installer and Hermes skill setup
├── install.ps1               # Portable Windows installer and launcher setup
├── pyproject.toml            # Build metadata (v1.1.0)
├── SKILL.md                  # Autonomous agent skill specification
├── .env.example              # Environment variables template
├── LICENSE                   # MIT License
├── CHANGELOG.md              # Semantic release notes
├── SECURITY.md               # Credential isolation and vulnerability policy
├── CONTRIBUTING.md           # Contribution guidelines
└── README.md                 # Public documentation
```

---

## Installation

### Linux / macOS

```bash
git clone https://github.com/igorjribeiro/flow-credit-rotator.git
cd flow-credit-rotator
chmod +x install.sh
./install.sh
```

Ensure `${HOME}/.local/bin` is in your `PATH`:
```bash
export PATH="${HOME}/.local/bin:${PATH}"
```

### Windows (PowerShell)

```powershell
git clone https://github.com/igorjribeiro/flow-credit-rotator.git
cd flow-credit-rotator
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\install.ps1
```

The installer configures portable launchers (`flow-account.cmd`, `flow-account.ps1`) under `$env:USERPROFILE\.local\bin` and registers the skill in `$env:USERPROFILE\.hermes\skills\flow-credit-rotator`.

---

## Central Configuration via XLSX

Administrate accounts and global settings by editing `config/flow_accounts.xlsx`:

### `ACCOUNTS` Sheet
| order | email | daily_limit | monthly_limit | enabled | credential_ref | notes |
|---|---|---|---|---|---|---|
| 1 | account01@example.com | 85 | 2500 | YES | KEYRING:flow01 | Primary production account |
| 2 | account02@example.com | 85 | 2500 | YES | KEYRING:flow02 | Secondary rotation account |
| 3 | account03@example.com | 70 | 2000 | YES | KEYRING:flow03 | Overflow reserve |

### `SETTINGS` Sheet
| Setting | Value | Description |
|---|---|---|
| `rotation_strategy` | `sequential` | `sequential` (default) or `random` |
| `default_daily_limit` | `85` | Default daily limit for new accounts |
| `default_monthly_limit` | `2500` | Optional default monthly limit |
| `timezone` | `America/Bahia` | Rollover timezone |
| `auto_rotate` | `YES` | `YES` for AUTO mode, `NO` for MANUAL |

After editing, apply all changes:
```bash
flow-account sync config/flow_accounts.xlsx
```

---

## Mandatory Pre-Check & Reservation Lifecycle

```bash
# 1. Check if capacity exists without reserving
flow-account can-consume 10

# 2. Pre-check and reserve credits (locks capacity)
flow-account reserve 10 --note "Render video job #104"
# Output returns:
# Reservation ID:   res_9b2d881ef032
# Authorized Email: account01@example.com

# 3. If job succeeds in Google Flow: confirm and deduct real credits
flow-account reserve-confirm res_9b2d881ef032 --amount 10

# 4. If job fails: cancel reservation (zero credits deducted, capacity freed)
flow-account reserve-cancel res_9b2d881ef032 --reason "Browser navigation timeout"
```

---

## CLI Command Reference

### Status & Inspection
```bash
flow-account status [--json]
flow-account list [--json]
flow-account current [--json]
flow-account next [--json]
flow-account history [--limit 50]
```

### Pre-Checks & Reservations
```bash
flow-account can-consume <amount> [--json]
flow-account reserve <amount> [--note <text>] [--ttl 1800] [--json]
flow-account reserve-confirm <res_id> [--amount <int>] [--note <text>] [--json]
flow-account reserve-cancel <res_id> [--reason <text>] [--json]
flow-account reservations [--status PENDING|COMMITTED|CANCELLED|EXPIRED] [--json]
```

### Configuration & Synchronization
```bash
# Sync central XLSX spreadsheet
flow-account sync [config/flow_accounts.xlsx] [--json]

# Manage secure credentials in OS Keyring
flow-account credential set <email_or_ref>
flow-account credential test <email_or_ref>

# Mode and Strategy
flow-account mode auto
flow-account mode manual
flow-account strategy sequential
flow-account strategy random

# Quotas and Resets
flow-account set-limit "account01@example.com" 100 --monthly 3000
flow-account reset-daily
flow-account reset-monthly
```

### Human Verification Workflows
```bash
flow-account human-required "account01@example.com" "Google CAPTCHA"
flow-account human-cleared "account01@example.com"
```

---

## Python API Usage

```python
from flow_account import FlowAccountManager, NoAccountsAvailableError

manager = FlowAccountManager()

# Pre-check and reserve credits
try:
    acc, res = manager.reserve(cost=10, note="Video batch generation #102")
    print(f"Using {acc.email} (Reservation: {res.id})")
except NoAccountsAvailableError:
    print("All accounts exhausted. Waiting for rollover.")
    raise

# Execute job
job_success = False
try:
    # ... Google Flow automation execution ...
    job_success = True
finally:
    if job_success:
        updated_acc, usage = manager.confirm_reservation(res.id, actual_amount=10)
        print(f"Credits debited. Remaining today: {updated_acc.available_credits}")
    else:
        manager.cancel_reservation(res.id, reason="Job aborted")
        print("Reservation cancelled without debiting credits.")
```

---

## Contributing & Security

- See [CONTRIBUTING.md](CONTRIBUTING.md) for testing and development workflow.
- See [SECURITY.md](SECURITY.md) for security policies and credential isolation.

---

## License

This project is licensed under the [MIT License](LICENSE).
