---
name: flow-credit-rotator
description: Multi-account credit rotation and quota manager for Google Flow automation agents.
---

# Flow Credit Rotator

Orchestrates multiple Google Flow accounts with a dynamic account pool supporting any number of accounts without a fixed ceiling, enforcing daily and monthly quotas, rotating accounts sequentially by default (or randomly on demand), isolating credentials safely, and handling CAPTCHA/human verification challenges without blocking automation.

## When to Use

Use this skill whenever:
- Executing unattended video rendering pipelines that consume Google Flow credits across multiple accounts.
- Inspecting available daily or monthly quotas before scheduling high-volume generation jobs.
- Selecting the current active account or advancing to the next eligible account.
- Recording credit consumption with audit notes and automatic quota tracking.
- Handling CAPTCHA or Google verification flags (`NEEDS_HUMAN`) without interrupting overall pipeline throughput.
- Switching between unattended automatic rotation (`AUTO`) and pinned operator control (`MANUAL`).
- Importing new account batches from CSV or XLSX spreadsheets.
- Resetting daily or monthly credit counters manually or verifying timezone-based rollover.

## Prerequisites

- **Python**: 3.9 or higher (with standard library `zoneinfo` and `sqlite3`).
- **Dependencies**: `openpyxl>=3.0.0` (required for `.xlsx` spreadsheet imports).
- **Environment Variables**:
  - `FLOW_ACCOUNT_DB_PATH`: SQLite database path (default: `~/.flow-accounts/flow_accounts.db`).
  - `FLOW_ACCOUNT_DEFAULT_DAILY_LIMIT`: Default daily limit for newly added accounts (default: `85`, fully configurable).
  - `FLOW_ACCOUNT_TIMEZONE`: Timezone for rollover calculations (default: `America/Bahia`).
  - `FLOW_ACCOUNT_STRATEGY`: Default rotation strategy (default: `sequential`; also supports `random`, `round-robin`, `lowest-usage`, `priority`).

## How to Run

Execute via the `flow-account` CLI command or directly via Python module:

```bash
# Using CLI command
flow-account status
flow-account current
flow-account consume 5 --note "Render video scene 1042"

# Direct module execution
python3 -m flow_account.cli status
```

In Python automation scripts:

```python
from flow_account import FlowAccountManager, NoAccountsAvailableError

manager = FlowAccountManager()
account = manager.current_account()
if account:
    account, record = manager.consume(amount=5, note="Render job #1042")
```

## Quick Reference: CLI Commands

| Command | Description |
|---|---|
| `flow-account status [--json]` | Show overall status, pool metrics, and current active account. |
| `flow-account list [--json]` | List all accounts, limits, usage, priority, and eligibility. |
| `flow-account current [--json]` | Show currently active account details and remaining credits. |
| `flow-account next [--json]` | Rotate to the next eligible account in sequence. |
| `flow-account mode [auto\|manual] [--json]` | View or set rotation mode (`AUTO` or `MANUAL`). |
| `flow-account select <id\|email> [--json]` | Select a specific account to use. |
| `flow-account consume <amount> [--note <text>] [--json]` | Deduct credits from active account and record audit log. |
| `flow-account add <email> [--daily N] [--monthly M] [--priority N] [--credential-ref REF] [--notes NOTE]` | Add a new account to the pool. |
| `flow-account import <file.csv\|file.xlsx> [--json]` | Batch import accounts from spreadsheet. |
| `flow-account strategy [strategy_name] [--json]` | View current strategy or switch strategy (`sequential`, `random`, `round-robin`, `lowest-usage`, `priority`). |
| `flow-account set-strategy <strategy> [--json]` | Set rotation strategy. |
| `flow-account set-limit <account> <daily> [--monthly M] [--json]` | Adjust daily/monthly limits for an account. |
| `flow-account set-default-limit <limit> [--json]` | Set system-wide default daily limit for future accounts (default: 85). |
| `flow-account human-required <account> [reason] [--json]` | Mark account as requiring CAPTCHA/human intervention. |
| `flow-account human-cleared <account> [--json]` | Restore account to active rotation after solving verification. |
| `flow-account disable <account> [--json]` | Disable an account from rotation. |
| `flow-account enable <account> [--json]` | Re-enable a disabled account. |
| `flow-account reset-daily [--json]` | Manually trigger daily quota reset and restore eligible accounts. |
| `flow-account reset-monthly [--json]` | Manually trigger monthly quota reset. |
| `flow-account history [--limit N] [--account ID\|EMAIL] [--json]` | View credit consumption audit log. |

## Core Operational Concepts for Agents

### 1. Dynamic Account Pool
There is no hardcoded pool size and no maximum limit on the number of accounts. The pool scales dynamically to 1, 2, 5, 20, 50, 100+ accounts. Never assume a fixed pool ceiling.

### 2. Rotation Strategies: Sequential (Default) vs Random
- **`sequential` (Default)**: Strictly deterministic ordering (Account 1 -> Account 2 -> Account 3 based on priority and ID). Holds the current account while eligible, auto-advances to the next account when quota is exhausted or status changes, skips unavailable accounts, and loops back to the first eligible account.
- **`random`**: Uniformly selects among eligible accounts with a 100% eligibility guarantee (never selects exhausted, disabled, or human-intervention accounts).
- Also available: `round-robin`, `lowest-usage`, and `priority` (alias to sequential).

### 3. Operational Modes: AUTO vs MANUAL
- **`AUTO` (Default)**: Automatically selects the best account and seamlessly rotates when daily limits are reached or when an account is marked for human verification.
- **`MANUAL`**: The operator explicitly pins an account. Quota exhaustion raises an error instead of rotating automatically, preserving operator control.

### 4. Human Verification Cycle (NEEDS_HUMAN)
When an automation agent encounters Google CAPTCHA, SMS verification, or consent screens:
1. Mark the account with human intervention:
   ```bash
   flow-account human-required "account@example.com" "Google CAPTCHA prompt"
   ```
2. In `AUTO` mode, the orchestrator immediately rotates to another healthy account.
3. Once an operator resolves the challenge in a browser:
   ```bash
   flow-account human-cleared "account@example.com"
   ```
   The account is restored to `ACTIVE` (or `EXHAUSTED` if daily/monthly quotas were already reached).

### 5. Credit Limits & Timezone-Aware Resets
- Every account tracks `used_today` against `daily_limit` (default configurable to 85) and optionally `used_month` against `monthly_limit`.
- Daily and monthly rollovers occur automatically according to `FLOW_ACCOUNT_TIMEZONE` (default: `America/Bahia`).

### 6. Credential Safety & Isolation
- **Strictly No Plaintext Passwords**: Neither CSV/XLSX imports nor the SQLite database accept raw passwords or secrets.
- **Indirect References Only**: Accounts store `credential_ref`, referencing external vaults or environment variables:
  - `ENV:FLOW_ACCOUNT_01_PASS`
  - `OP:vault/flow-account-01/pass`
  - `BWS:secret-uuid`
  - `KEYRING:account-email`
  - `FILE:relative/path/to/token`

## Error Handling

- **`NoAccountsAvailableError`**: All accounts in the pool are exhausted, disabled, or flagged `NEEDS_HUMAN`. Automation should pause until the next daily rollover or alert the operator.
- **`SecurityImportError`**: An import spreadsheet contained forbidden columns (`password`, `secret`, `senha`, etc.). The file is rejected before any data is written.
- **`AccountNotFoundError`**: The requested account ID or email does not exist in the database.
- **`InvalidOperationError`**: An invalid operation was attempted (e.g., consuming in `MANUAL` mode without selecting an account, or invalid limit values).
