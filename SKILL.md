---
name: flow-credit-rotator
description: Multi-account credit rotation, pre-check reservation, and quota manager for Google Flow automation agents.
---

# Flow Credit Rotator

Orchestrates multiple Google Flow accounts with a dynamic pool (supporting any number of accounts without a fixed ceiling), enforcing daily and monthly quotas, rotating accounts sequentially by default (or randomly on demand), performing pre-checks and credit reservations before operations, isolating credentials safely via keyring, and handling CAPTCHA/human verification challenges without blocking automation.

## Mandatory Agent Workflow

Before performing ANY action in Google Flow that consumes credits, autonomous agents MUST follow this strict 7-step protocol:

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
   (e.g. account: account02@example.com, reservation: res_ab12cd34ef56)
      │
      ▼
4. Load Credentials for this Account (if needed)
   $ flow-account credential test account02@example.com
      │
      ▼
5. Execute Video Generation in Google Flow
      │
      ├───────────────────────────────┐
   [SUCCESS]                       [FAILURE / ABORT]
      │                               │
      ▼                               ▼
6. Confirm Consumption:         7. Cancel Reservation:
   $ flow-account reserve-confirm  $ flow-account reserve-cancel
     res_ab12cd34ef56                res_ab12cd34ef56 --reason "Browser crashed"
   (Deducts real credits)          (Zero credits charged, capacity released)
```

**CRITICAL RULE**: The agent must NEVER spend credits first and register afterwards without verifying capacity. Always call `flow-account reserve` before launching generation.

---

## When to Use

Use this skill whenever:
- Executing unattended video rendering pipelines that consume Google Flow credits across multiple accounts.
- Pre-checking if the active or standby accounts have sufficient credit capacity (`used_today + active_reservations + cost <= daily_limit`).
- Reserving credits to protect against concurrency race conditions and double-spending across parallel workers.
- Confirming credit debits upon task completion or releasing reservations upon job failure.
- Synchronizing user configuration from the central `config/flow_accounts.xlsx` workbook.
- Securely storing or checking account credentials via system keyring without exposing plaintext passwords.
- Handling Google verification or CAPTCHA flags (`NEEDS_HUMAN`) without interrupting pipeline execution.
- Switching between unattended automatic rotation (`AUTO`) and pinned operator control (`MANUAL`).

---

## Prerequisites

- **Python**: 3.9 or higher (with standard library `zoneinfo`, `sqlite3`, and `getpass`).
- **Dependencies**: `openpyxl>=3.0.0` (for Excel imports and central XLSX sync), `keyring` (for secure credential storage).
- **Environment Variables**:
  - `FLOW_ACCOUNT_DB_PATH`: SQLite database path (default: `~/.flow-accounts/flow_accounts.db`).
  - `FLOW_ACCOUNT_DEFAULT_DAILY_LIMIT`: Default daily limit for newly added accounts (default: `85`).
  - `FLOW_ACCOUNT_TIMEZONE`: Rollover timezone (default: `America/Bahia`).
  - `FLOW_ACCOUNT_STRATEGY`: Default rotation strategy (default: `sequential`; also `random`, `round-robin`, `lowest-usage`, `priority`).

---

## Quick Reference: CLI Commands

| Command | Description |
|---|---|
| `flow-account status [--json]` | Show overall status, pool metrics, and current active account. |
| `flow-account list [--json]` | List all accounts, limits, usage, priority, eligibility, and credential references. |
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

## Python API Usage for Orchestrators

```python
from flow_account import FlowAccountManager, NoAccountsAvailableError, InvalidOperationError

manager = FlowAccountManager()

# 1. Pre-check and reserve credits before starting generation
try:
    account, reservation = manager.reserve(cost=10, note="Video scene batch render #104")
    print(f"Authorized account: {account.email} (Reservation: {reservation.id})")
except NoAccountsAvailableError:
    print("All accounts exhausted or lack sufficient capacity. Pausing until daily rollover.")
    raise

# 2. Execute video generation using account credentials
success = False
try:
    # ... call Google Flow automation with account.email ...
    success = True
finally:
    if success:
        # 3. Confirm and commit credit deduction
        updated_acc, usage_record = manager.confirm_reservation(reservation.id, actual_amount=10)
        print(f"Charged 10 credits. Available remaining: {updated_acc.available_credits}")
    else:
        # 4. Release reservation on failure (zero credits debited)
        manager.cancel_reservation(reservation.id, reason="Generation job aborted")
        print("Reservation cancelled. Zero credits consumed.")
```

---

## Central Configuration via XLSX

Users administer accounts and settings by copying `templates/flow_accounts_template.xlsx` to `config/flow_accounts.xlsx` (which is gitignored), editing it, and running:
```bash
flow-account sync config/flow_accounts.xlsx
```

The workbook contains two sheets:
1. **`ACCOUNTS`**:
   - `order`: Numeric priority sequence (1, 2, 3...)
   - `email`: Account email address
   - `daily_limit`: Daily credit limit (e.g. 85)
   - `monthly_limit`: Optional monthly limit (e.g. 1500)
   - `enabled`: `YES` or `NO`
   - `credential_ref`: Safe indirect reference (`KEYRING:user@example.com`, `ENV:FLOW01`)
   - `notes`: Operational description
2. **`SETTINGS`**:
   - `rotation_strategy`: `sequential` (default) or `random`
   - `default_daily_limit`: 85
   - `default_monthly_limit`: 1500
   - `timezone`: `America/Bahia`
   - `auto_rotate`: `YES` (AUTO mode) or `NO` (MANUAL mode)

*Notice: Accounts removed from the spreadsheet are archived (`DISABLED`) in the database; their historical consumption log is NEVER destroyed.*

---

## Credential Safety Model

1. **Strictly No Plaintext Passwords**: Neither CSV/XLSX spreadsheets nor the SQLite database store plaintext passwords.
2. **Keyring Integration**:
   - Use `flow-account credential set <ref>` to store passwords in OS Keyring / Windows Credential Manager.
   - Accounts reference stored keys via `KEYRING:<id>` or `ENV:<VAR_NAME>`.
