# Flow Credit Rotator

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python 3.9+](https://img.shields.io/badge/python-3.9+-blue.svg)](https://www.python.org/downloads/)
[![Status](https://img.shields.io/badge/status-production--ready-green.svg)](#)

A robust, production-grade management and rotation system for Google Flow accounts used by automation agents and high-throughput video generation pipelines.

The rotator automatically tracks daily and monthly credit limits, rotates accounts intelligently using sequential or random strategies, isolates credentials safely, and handles CAPTCHA/human verification challenges without interrupting pipeline throughput.

---

## Key Features

- **Dynamic Account Pool**: Scales to any number of accounts (1, 2, 5, 20, 50, 100+) with zero hardcoded limits and no fixed ceiling.
- **Credit Quota Enforcement**: Tracks `used_today` and `used_month` against per-account quotas and configurable system defaults (`DEFAULT_DAILY_LIMIT=85` by default).
- **Intelligent Rotation Strategies**:
  - `sequential` **(Default)**: Deterministic ordering (`1 -> 2 -> 3`), holding the active account while eligible, auto-advancing on exhaustion, skipping unavailable accounts, and looping back to the first eligible account.
  - `random`: Uniform selection among eligible accounts with a 100% eligibility guarantee (never selects exhausted, disabled, or human-intervention accounts).
  - `round-robin`: Cycles deterministically across eligible accounts on each rotation.
  - `lowest-usage`: Selects the account with the lowest `used_today`, tie-broken by priority and monthly usage.
  - `priority`: Priority-based selection order (alias to sequential rotation).
- **Safe Credential Isolation**: Rejects plaintext passwords or secrets in import files and database storage. Uses `credential_ref` referencing external vaults or environment variables (`ENV:`, `OP:`, `BWS:`, `KEYRING:`, etc.).
- **Human Verification Handling (`NEEDS_HUMAN`)**: When an account triggers CAPTCHA or Google verification challenges, flag it with `human-required`. Automation immediately rotates to another healthy account. Once solved, `human-cleared` restores it to rotation.
- **Timezone-Aware Resets**: Daily and monthly rollovers occur automatically based on the configured timezone (default: `America/Bahia` using standard Python `zoneinfo`).
- **Operational Modes**:
  - `AUTO`: Unattended automation mode. Automatically selects eligible accounts and rotates when limits are reached or challenges appear.
  - `MANUAL`: Explicit operator control. The selected account remains pinned until changed.
- **Durable & Concurrent SQLite**: Built with WAL mode (`PRAGMA journal_mode=WAL;`), busy timeout (`5000ms`), foreign keys, and atomic transactions.
- **Zero Third-Party Runtime Dependencies**: Core features run on Python 3.9+ standard library; `openpyxl` is required only for `.xlsx` spreadsheet imports.
- **CLI Compatibility**: The CLI executable command is `flow-account` across all platforms.

---

## Architecture

```
flow-credit-rotator/
├── flow_account/             # Core Python package
│   ├── __init__.py           # Package exports and version (1.0.0)
│   ├── config.py             # Configuration defaults and env resolution
│   ├── models.py             # Data models (Account, UsageRecord, SystemState)
│   ├── db.py                 # SQLite engine, WAL pragmas, schema migrations
│   ├── reset.py              # Timezone-aware daily/monthly rollover logic
│   ├── strategies.py         # Sequential, Random, Round-Robin, Lowest-Usage
│   ├── importer.py           # CSV and XLSX importer with security validations
│   ├── manager.py            # FlowAccountManager orchestration engine
│   └── cli.py                # Command-line interface with formatted & JSON output
├── bin/
│   └── flow-account          # Portable Unix executable launcher
├── templates/
│   ├── accounts_template.csv  # Safe CSV import template (fictional accounts)
│   └── accounts_template.xlsx # Safe XLSX import template (no passwords)
├── tests/                    # Pytest test suite covering full functional surface
├── install.sh                # Linux/macOS installer and Hermes skill setup
├── install.ps1               # Portable Windows installer and launcher setup
├── pyproject.toml            # Public build metadata and entrypoints
├── SKILL.md                  # Hermes / Claude Code skill documentation
├── .env.example              # Environment variables template
├── LICENSE                   # MIT License
├── CHANGELOG.md              # Release history
├── SECURITY.md               # Security policy and credential isolation model
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

The script:
1. Verifies Python 3 availability.
2. Creates `${HOME}/.local/bin` and `${HOME}/.flow-accounts`.
3. Symlinks `${HOME}/.local/bin/flow-account` to `bin/flow-account`.
4. Installs the package in editable mode (`pip install -e . --no-deps`).
5. Registers the skill at `${HOME}/.hermes/skills/flow-credit-rotator` (migrating old references if present).

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

The installer:
1. Locates Python (`python`, `py`, or `python3`).
2. Creates `$env:USERPROFILE\.local\bin` and `$env:USERPROFILE\.flow-accounts`.
3. Generates portable `flow-account.cmd` and `flow-account.ps1` launchers.
4. Installs the package in editable mode (`pip install -e . --no-deps`).
5. Registers the skill in `$env:USERPROFILE\.hermes\skills\flow-credit-rotator` if Hermes is present.
6. Adds `$env:USERPROFILE\.local\bin` to the user `PATH`.

---

## Configuration

Settings can be defined in environment variables or loaded from a `.env` file:

| Variable | Default | Description |
|---|---|---|
| `FLOW_ACCOUNT_DB_PATH` | `~/.flow-accounts/flow_accounts.db` | Path to SQLite database file |
| `FLOW_ACCOUNT_DEFAULT_DAILY_LIMIT` | `85` | Default daily credit limit applied to new accounts |
| `FLOW_ACCOUNT_TIMEZONE` | `America/Bahia` | Timezone for daily and monthly credit rollovers |
| `FLOW_ACCOUNT_STRATEGY` | `sequential` | Default strategy (`sequential`, `random`, `round-robin`, `lowest-usage`, `priority`) |

---

## Credential Safety Model

To protect production environments and prevent credential leaks:
1. **No Plaintext Passwords**: Raw passwords, API secrets, and tokens are never stored in the database or import spreadsheets.
2. **Import File Rejection**: The spreadsheet importer scans for forbidden column names (`password`, `pass`, `senha`, `secret`, `token`, `api_key`, etc.) and immediately rejects the file if any are present.
3. **Indirect References**: Accounts specify a `credential_ref` referencing an external secrets manager or environment variable:
   - Environment variable: `ENV:FLOW_ACCOUNT_01_PASS`
   - Password Manager (1Password): `OP:vault/item/pass`
   - Bitwarden Secrets Manager: `BWS:secret-id`
   - OS Keyring: `KEYRING:account-email`
   - File path: `FILE:secrets/token.json`

---

## Account Import: CSV and XLSX

You can import accounts using CSV or Excel files. Both formats require an `email` column and support optional configuration columns.

### Supported Columns
- `email` *(required)*: Account email address.
- `daily_limit` *(optional)*: Daily credit quota (defaults to system setting, e.g. 85).
- `monthly_limit` *(optional)*: Monthly credit quota.
- `priority` *(optional)*: Priority rank (1 = highest priority).
- `status` *(optional)*: Initial status (`ACTIVE`, `DISABLED`, `EXHAUSTED`, `NEEDS_HUMAN`).
- `credential_ref` *(optional)*: Safe indirect credential reference.
- `notes` *(optional)*: Contextual notes for operators.

### Importing Templates
```bash
# Import from CSV
flow-account import templates/accounts_template.csv

# Import from XLSX
flow-account import templates/accounts_template.xlsx
```

---

## CLI Command Reference

### Status and Inspection
```bash
# View general status, pool metrics, and active account
flow-account status
flow-account status --json

# List all accounts with quotas, usage, and eligibility
flow-account list
flow-account list --json

# Display currently active account
flow-account current
flow-account current --json

# View credit consumption audit log
flow-account history --limit 50
```

### Account Management
```bash
# Add a new account manually
flow-account add "flow.user@example.com" --daily 85 --monthly 2500 --priority 1 --credential-ref "ENV:FLOW_01"

# Update limits for a specific account
flow-account set-limit "flow.user@example.com" 100 --monthly 3000

# Set default daily limit for future accounts
flow-account set-default-limit 85

# Enable or disable an account
flow-account disable "flow.user@example.com"
flow-account enable "flow.user@example.com"
```

### Rotation and Modes
```bash
# Set mode to AUTO (automatic rotation) or MANUAL (pinned account)
flow-account mode auto
flow-account mode manual

# View or set rotation strategy
flow-account strategy
flow-account strategy sequential
flow-account strategy random
flow-account strategy round-robin
flow-account strategy lowest-usage
flow-account strategy priority

# Advance to next eligible account manually
flow-account next

# Select a specific account manually
flow-account select "flow.user@example.com"
```

### Credit Consumption & Human Verification
```bash
# Deduct credits from active account with audit note
flow-account consume 5 --note "Render video scene 01"

# Mark account as needing human intervention (CAPTCHA, verification prompt)
flow-account human-required "flow.user@example.com" "Google CAPTCHA prompt"

# Clear verification flag after solving challenge
flow-account human-cleared "flow.user@example.com"

# Trigger manual rollover
flow-account reset-daily
flow-account reset-monthly
```

---

## Python API Usage

```python
from flow_account import (
    FlowAccountManager,
    NoAccountsAvailableError,
    AccountStatus,
    Mode,
    Strategy,
)

# Initialize manager (optionally provide custom db_path)
manager = FlowAccountManager()

# Inspect active account
account = manager.current_account()
if account:
    print(f"Active account: {account.email} (Available credits: {account.available_credits})")

# Consume credits
try:
    acc, record = manager.consume(amount=5, note="Batch render scene #104")
    print(f"Consumed 5 credits. Remaining today: {acc.available_credits}")
except NoAccountsAvailableError:
    print("All accounts exhausted. Pausing until daily rollover.")

# Handling CAPTCHA / Human Verification
try:
    # Run automation job...
    pass
except Exception as captcha_exception:
    if account:
        manager.human_required(account.id, reason="CAPTCHA encountered during generation")
        # In AUTO mode, next call to current_account() selects the next eligible account
        next_acc = manager.current_account()
        print(f"Rotated to account: {next_acc.email if next_acc else 'None'}")
```

---

## Troubleshooting

- **`NoAccountsAvailableError` during consume**:
  Check `flow-account list`. All accounts may have exhausted their daily/monthly limit or be marked `NEEDS_HUMAN` or `DISABLED`. You can run `flow-account reset-daily` to test manual rollover.
- **Account stuck in `NEEDS_HUMAN`**:
  Run `flow-account human-cleared <email>` after resolving the CAPTCHA or verification prompt in a browser.
- **`flow-account: command not found`**:
  Ensure `${HOME}/.local/bin` (Linux/macOS) or `$env:USERPROFILE\.local\bin` (Windows) is in your system `PATH`.
- **Import rejected due to forbidden columns**:
  Ensure your CSV/XLSX does not contain raw password columns like `password`, `senha`, or `secret`. Use `credential_ref` instead.

---

## Uninstallation

### Linux / macOS
```bash
rm -f "${HOME}/.local/bin/flow-account"
rm -rf "${HOME}/.hermes/skills/flow-credit-rotator"
pip uninstall -y flow-credit-rotator 2>/dev/null || true
# To delete data as well:
# rm -rf "${HOME}/.flow-accounts"
```

### Windows (PowerShell)
```powershell
Remove-Item -Path "$env:USERPROFILE\.local\bin\flow-account.cmd" -Force -ErrorAction SilentlyContinue
Remove-Item -Path "$env:USERPROFILE\.local\bin\flow-account.ps1" -Force -ErrorAction SilentlyContinue
Remove-Item -Path "$env:USERPROFILE\.hermes\skills\flow-credit-rotator" -Recurse -Force -ErrorAction SilentlyContinue
python -m pip uninstall -y flow-credit-rotator
# To delete data as well:
# Remove-Item -Path "$env:USERPROFILE\.flow-accounts" -Recurse -Force -ErrorAction SilentlyContinue
```

---

## Testing

Run the full pytest suite:

```bash
pytest -v
```

---

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for code style, development workflow, and testing practices.

---

## Security

See [SECURITY.md](SECURITY.md) for security policy, reporting vulnerabilities, and credential isolation practices.

---

## License

This project is licensed under the [MIT License](LICENSE).
