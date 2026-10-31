---
name: google-flow-account-manager
description: Manage Google Flow accounts, credit limits, and rotation.
---

# Google Flow Account Manager

Orchestrates multiple Google Flow video generation accounts, managing any number of Google Flow accounts with a dynamic pool and no fixed ceiling, enforcing daily and monthly quotas, rotating accounts automatically or manually, and preventing automation blockage during CAPTCHA or human verification challenges.

## When to Use

Use this skill whenever:
- Running video rendering pipelines that consume Google Flow credits across multiple accounts.
- Inspecting available daily or monthly quotas before scheduling high-volume generation jobs.
- Selecting or advancing to the next eligible Google Flow account.
- Handling CAPTCHA or human verification flags (`NEEDS_HUMAN`) without interrupting overall pipeline throughput.
- Importing new account batches from CSV or XLSX spreadsheets.
- Resetting daily or monthly credit counters manually or verifying timezone-based rollover.

## Prerequisites

- **Python**: 3.9 or higher (with `zoneinfo` and `sqlite3` from standard library).
- **Dependencies**: `openpyxl` (for `.xlsx` spreadsheet imports).
- **Environment**:
  - `FLOW_ACCOUNT_DB_PATH`: Database path (default: `~/.flow-accounts/flow_accounts.db`).
  - `FLOW_ACCOUNT_DEFAULT_DAILY_LIMIT`: Initial daily limit for new accounts (default: `85`).
  - `FLOW_ACCOUNT_TIMEZONE`: Rollover timezone (default: `America/Bahia`).
  - `FLOW_ACCOUNT_STRATEGY`: Default strategy (default: `sequential`, also supports `random`, `round-robin`, `lowest-usage`, `priority`).

## How to Run

Execute via the `flow-account` CLI command or directly invoke the Python module:

```bash
# Using CLI binary
flow-account status
flow-account current
flow-account consume 5 --note "Render video job 1042"

# Direct module execution
python3 -m flow_account.cli status
```

In automated Python workflows:
```python
from flow_account import FlowAccountManager, NoAccountsAvailableError

manager = FlowAccountManager()
account = manager.current_account()
if account:
    account, record = manager.consume(amount=5, note="Render job #1042")
```

## Quick Reference

| Command | Description |
|---|---|
| `flow-account status [--json]` | Show overall status, pool metrics, and current account. |
| `flow-account list [--json]` | List all accounts, limits, usage, and eligibility. |
| `flow-account current` | Show currently selected active account. |
| `flow-account next` | Manually advance to the next eligible account. |
| `flow-account mode [auto\|manual]` | View or set rotation mode (`AUTO` or `MANUAL`). |
| `flow-account select <id\|email>` | Select a specific account to use. |
| `flow-account consume <amount> [--note <text>]` | Deduct credits from current account and record audit log. |
| `flow-account add <email> [--daily N] [--priority N]` | Add an individual account. |
| `flow-account import <file.csv\|file.xlsx>` | Batch import accounts from spreadsheet. |
| `flow-account strategy [strategy_name]` | View current strategy or switch strategy (`sequential`, `random`, `round-robin`, `lowest-usage`, `priority`). |
| `flow-account set-strategy <strategy>` | Legacy alias to change rotation strategy. |
| `flow-account set-limit <account> <daily> [--monthly M]` | Adjust limits for an account. |
| `flow-account human-required <account> [reason]` | Mark account as requiring CAPTCHA/human intervention. |
| `flow-account human-cleared <account>` | Restore account to active rotation after solving verification. |
| `flow-account disable <account>` / `enable <account>` | Disable or re-enable an account. |
| `flow-account reset-daily` / `reset-monthly` | Manually trigger rollover. |
| `flow-account history [--limit N]` | View credit consumption audit log. |

## Procedure

### 1. Initial Setup and Import
1. Run `./install.sh` to link `flow-account` to `~/.local/bin`.
2. Prepare an accounts file using `templates/accounts_template.csv`.
3. Import the accounts:
   ```bash
   flow-account import templates/accounts_template.csv
   ```
4. Verify the pool state:
   ```bash
   flow-account list
   flow-account status
   ```

### 2. Standard Production Workflow
1. Ensure the system is in `AUTO` mode and set the desired rotation strategy:
   ```bash
   flow-account mode auto
   # View current strategy:
   flow-account strategy
   # Set sequential strategy (default):
   flow-account strategy sequential
   # Or switch to random distribution across eligible accounts:
   flow-account strategy random
   ```
2. Before launching a video task, retrieve the active account credentials:
   ```bash
   flow-account current --json
   ```
3. After the job consumes credits, register the consumption:
   ```bash
   flow-account consume 10 --note "Video batch render"
   ```
4. If an account exhausts its daily quota (`used_today >= daily_limit`), `FlowAccountManager` automatically rotates to the next highest-priority eligible account.

### 3. Handling Human Verification (CAPTCHAs)
1. When automation detects a CAPTCHA or Google verification challenge:
   ```bash
   flow-account human-required <account> "CAPTCHA prompt on login"
   ```
2. Automation will immediately rotate away from this account and continue running with the remaining pool.
3. Once an operator resolves the challenge in a browser:
   ```bash
   flow-account human-cleared <account>
   ```

## Pitfalls

- **Plaintext Passwords**: Never store raw passwords or API keys in CSV files, database fields, or notes. Always use safe references (`credential_ref`, e.g. `ENV:FLOW_ACCOUNT_01`).
- **Timezone Skew**: Rollovers depend on `FLOW_ACCOUNT_TIMEZONE` (`America/Bahia`). If your system clock or server runs on UTC, the manager correctly converts time to Bahia timezone.
- **MANUAL Mode Traps**: In `MANUAL` mode, `consume` will NOT auto-rotate when an account is exhausted; it will raise an error. Switch to `AUTO` mode for unattended batch jobs.
- **Quota Expiry**: If all accounts are exhausted, `consume` raises `NoAccountsAvailableError`. Handle this gracefully in orchestrators to pause until daily rollover.

## Verification

To verify that the manager is operating correctly:
1. Run test suite:
   ```bash
   pytest -v
   ```
2. Check CLI output:
   ```bash
   flow-account status
   flow-account list
   ```
