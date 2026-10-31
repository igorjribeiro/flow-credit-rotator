# Google Flow Account Manager

A robust, production-grade management system for orchestrating multiple Google Flow accounts with a dynamic account pool supporting any number of accounts with no fixed ceiling in high-throughput video generation pipelines.

The manager automatically tracks daily and monthly credit limits, rotates accounts intelligently based on configurable strategies, isolates credentials safely, and seamlessly pauses accounts flagged for CAPTCHA or human intervention without stalling the overall pipeline.

---

## Key Features

- **Scalable Multi-Account Pool**: Dynamic account pool supporting any number of accounts with no fixed ceiling and zero hardcoded limits.
- **Credit Quota Enforcement**: Tracks both `used_today` and `used_month` against per-account quotas and system defaults.
- **Intelligent Rotation Strategies**:
  - `sequential`: Cycles accounts in strict deterministic sequence (Account 1 -> Account 2 -> Account 3 based on priority and registration), holding the current account while eligible, auto-advancing when limits are reached or status changes, and wrapping back to the first eligible account.
  - `random`: Uniformly selects among eligible accounts with 100% eligibility guarantee (never selects exhausted, disabled, or human-intervention accounts).
  - `priority`: Priority-based selection order (alias to sequential rotation).
  - `round-robin`: Cycles deterministically across eligible accounts on each rotation.
  - `lowest-usage`: Selects the account with the lowest `used_today`, tie-broken by priority and monthly usage.
- **Safe Credential Isolation**: Strictly rejects plaintext passwords or secrets in import files and database storage. Uses `credential_ref` referencing external vaults or environment variables.
- **Human Verification Handling**: When an account triggers a CAPTCHA or Google verification challenge, mark it with `human-required`. Automation immediately rotates to another account. Once resolved, `human-cleared` restores it to rotation.
- **Timezone-Aware Resets**: Daily and monthly rollovers occur automatically according to the configured timezone (default: `America/Bahia` using standard Python `zoneinfo`).
- **Modes of Operation**:
  - `AUTO`: Unattended automation mode. Automatically selects the best account and seamlessly rotates when daily limits are reached or accounts need verification.
  - `MANUAL`: Explicit operator control. The selected account is pinned until explicitly changed.
- **Thread-Safe & Durable SQLite**: Built with WAL mode (`PRAGMA journal_mode=WAL;`), busy timeout (`5000ms`), foreign keys, and atomic transactions.
- **Zero Third-Party Runtime Dependencies**: Core features run on Python 3.9+ standard library; `openpyxl` is required only for `.xlsx` spreadsheet imports.

---

## Architecture

```
google-flow-account-manager/
├── flow_account/
│   ├── __init__.py           # Package exports and versioning
│   ├── config.py             # Environment configurations and defaults
│   ├── models.py             # Data models (Account, UsageRecord, SystemState)
│   ├── db.py                 # SQLite database engine, WAL pragmas, transactions
│   ├── reset.py              # Timezone-aware daily/monthly rollover logic
│   ├── strategies.py         # Priority, Round-Robin, and Lowest-Usage algorithms
│   ├── importer.py           # CSV and XLSX importer with security validations
│   ├── manager.py            # Main FlowAccountManager orchestration engine
│   └── cli.py                # Command-line interface with formatted & JSON output
├── bin/
│   └── flow-account          # Direct executable script
├── templates/
│   └── accounts_template.csv # Sample CSV import template
├── tests/                    # Pytest test suite covering all 16 required categories
├── install.sh                # Installation and symlinking script
├── pyproject.toml            # Package configuration
├── SKILL.md                  # Hermes skill specification
├── .env.example              # Configuration variables example
└── README.md                 # Project documentation
```

---

## Installation

### Quick Install

Run the provided installation script:

```bash
chmod +x install.sh
./install.sh
```

This symlinks `bin/flow-account` into `~/.local/bin/flow-account` and ensures `~/.flow-accounts/` exists.

### Development / Editable Install

```bash
pip install -e .
```

Verify the installation:

```bash
flow-account status
```

---

## Configuration

Settings can be specified via environment variables or loaded from a `.env` file:

| Variable | Default | Description |
|---|---|---|
| `FLOW_ACCOUNT_DB_PATH` | `~/.flow-accounts/flow_accounts.db` | Path to SQLite database |
| `FLOW_ACCOUNT_DEFAULT_DAILY_LIMIT` | `85` | Default daily limit for newly added accounts |
| `FLOW_ACCOUNT_TIMEZONE` | `America/Bahia` | Timezone for daily/monthly rollovers |
| `FLOW_ACCOUNT_STRATEGY` | `sequential` | Selection strategy (`sequential`, `random`, `round-robin`, `lowest-usage`, `priority`) |

---

## Security Model: Credential Isolation

To protect production environments and prevent credential leaks:
1. **No Plaintext Passwords**: Neither this application nor its database stores plaintext passwords.
2. **Import File Rejection**: The spreadsheet importer scans for forbidden column names (`password`, `pass`, `senha`, `secret`, `token`, etc.) and immediately rejects the file if any are found.
3. **Indirect References**: Accounts specify `credential_ref`, which points to an external credential store:
   - Environment variable: `ENV:FLOW_ACCOUNT_01_PASS`
   - Password Manager: `OP:vault/item/pass` or `BWS:secret-id`
   - Secret Keyring: `KEYRING:account-email`

---

## CLI Command Reference

### System & Account Monitoring

```bash
# View general status, pool metrics, and active account
flow-account status
flow-account status --json

# List all accounts with usage, limits, and eligibility
flow-account list
flow-account list --json

# Display currently active account
flow-account current

# View credit consumption audit log
flow-account history --limit 50
```

### Account Management

```bash
# Add a new account manually
flow-account add "flow.user@example.com" --daily 85 --monthly 2500 --priority 1 --credential-ref "ENV:FLOW_01"

# Import batch of accounts from CSV or XLSX
flow-account import templates/accounts_template.csv

# Update limits for a specific account
flow-account set-limit "flow.user@example.com" 100 --monthly 3000

# Set default daily limit for future accounts
flow-account set-default-limit 85

# Enable or disable an account
flow-account disable "flow.user@example.com"
flow-account enable "flow.user@example.com"
```

### Operation Modes & Strategies

```bash
# Set mode to AUTO (automatic rotation) or MANUAL
flow-account mode auto
flow-account mode manual

# View or set selection strategy
flow-account strategy
flow-account strategy sequential
flow-account strategy random
flow-account strategy round-robin
flow-account strategy lowest-usage
flow-account strategy priority

# Legacy command to set strategy
flow-account set-strategy sequential

# Rotate to next eligible account manually
flow-account next

# Select a specific account manually
flow-account select "flow.user@example.com"
```

### Credit Consumption & CAPTCHA Workflows

```bash
# Consume credits from currently active account
flow-account consume 5 --note "Render scene 01"

# Mark account as needing human intervention (e.g. CAPTCHA, SMS verification)
# In AUTO mode, automation rotates away immediately
flow-account human-required "flow.user@example.com" "Google CAPTCHA prompt"

# Clear verification flag after solving challenge
flow-account human-cleared "flow.user@example.com"

# Manually trigger daily or monthly resets
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
)

# Initialize manager
manager = FlowAccountManager()

# Check current active account
account = manager.current_account()
if account:
    print(f"Using {account.email} with {account.available_credits} credits available")

# Consume credits
try:
    acc, record = manager.consume(amount=5, note="Video batch render #402")
    print(f"Consumed 5 credits. Remaining today: {acc.available_credits}")
except NoAccountsAvailableError:
    print("All accounts exhausted for today. Waiting for midnight rollover.")

# Handling CAPTCHA or verification
try:
    # ... some Google Flow browser automation step ...
    pass
except Exception as captcha_detected:
    if account:
        manager.human_required(account.id, reason="CAPTCHA encountered during login")
        # In AUTO mode, next call to current_account() selects a healthy account
        new_account = manager.current_account()
```

---

## Testing

Run the comprehensive pytest suite:

```bash
pytest -v
```
