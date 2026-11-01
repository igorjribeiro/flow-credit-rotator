# Local Configuration Directory (`config/`)

This directory is designed for your local configuration spreadsheets and environment settings.

## Security Notice

- Real user spreadsheets placed in this folder (e.g. `config/flow_accounts.xlsx` or any `config/*.xlsx`) are **strictly ignored by Git** via `.gitignore`.
- Your real account emails, limits, and configurations will **never** be committed or pushed to any public or private repository.

## Setup Instructions

1. **Copy the Official Template**:
   ```bash
   cp templates/flow_accounts_template.xlsx config/flow_accounts.xlsx
   ```

2. **Edit Your Local Copy**:
   Open `config/flow_accounts.xlsx` in your spreadsheet editor (Excel, LibreOffice, Google Sheets).
   - In the `ACCOUNTS` sheet, fill in your Google Flow account emails, quotas, and enabled flags.
   - The `notes` column is optional and provided for human observations only.
   - In the `SETTINGS` sheet, adjust global defaults if desired.

3. **Store Passwords Securely**:
   > ⚠️ **CRITICAL**: Never type raw passwords into the Excel file.
   Store each account's password in the OS Keyring via:
   ```bash
   flow-account credential set user@example.com
   ```

4. **Synchronize with Flow Credit Rotator**:
   ```bash
   flow-account sync config/flow_accounts.xlsx
   ```
