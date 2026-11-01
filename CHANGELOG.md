# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.2.1] - 2026-10-31

### Changed
- **Consolidated Official Template**:
  - Maintained `templates/flow_accounts_template.xlsx` as the sole official public XLSX model.
  - Removed duplicate/redundant `templates/accounts_template.xlsx` to avoid confusion.
  - Retained `templates/accounts_template.csv` as a simple alternative for CSV-only environments while designating XLSX as the official recommended format.
  - Updated `README.md` to point strictly to `templates/flow_accounts_template.xlsx` as the source to copy to `config/flow_accounts.xlsx`.
  - Documented that the `notes` column in the `ACCOUNTS` sheet is strictly optional and does not affect account rotation logic.

## [1.2.0] - 2026-10-31

### Added
- **Public Model Template**:
  - `templates/flow_accounts_template.xlsx`: clean public workbook with exactly two sheets (`ACCOUNTS` and `SETTINGS`).
  - Standardized mock accounts (`flow_test01@example.com`, `flow_test02@example.com`, `flow_test03@example.com`) with `KEYRING:<email>` references.
  - Detailed descriptions for all settings (`rotation_strategy`, `default_daily_limit=85`, `default_monthly_limit=1500`, `timezone=America/Bahia`, `auto_rotate=YES`).
- **Complete Onboarding & Public Documentation**:
  - Fully rewritten, beginner-friendly `README.md` covering architecture, installation, spreadsheet creation, secure password storage, synchronization, didactic reservation walkthrough, and autonomous agent protocols.
  - Clear explanation of all 4 rotation strategies (`sequential`, `random`, `round-robin`, `lowest-usage`).
  - Account-level limits override global default limits.
- **Privacy & Security Hardening**:
  - Excluded `config/` directory from Git tracking in `.gitignore` so real user credentials and accounts are never committed.
  - Comprehensive verification of zero real personal emails, credentials, or paths in the public repository.
- **Test Suite**:
  - Added `tests/test_v120_public_experience.py` expanding coverage to 127 automated tests passing with 100% success.

## [1.1.0] - 2026-10-31

### Added
- **Credit Pre-Check & Reservation System**:
  - Implemented `used_today + active_reservations + cost <= daily_limit` capacity checking.
  - `flow-account can-consume <amount>`: dry-run pre-check determining if current or standby accounts can execute.
  - `flow-account reserve <amount>`: creates an atomic PENDING reservation locking capacity to prevent double-spending in parallel workers.
  - `flow-account reserve-confirm <id>` (alias: `commit`): debits real consumption upon successful job completion.
  - `flow-account reserve-cancel <id>` (alias: `cancel`): releases reserved capacity upon failure without debiting credits.
  - `flow-account reservations`: inspects active and historical reservations.
  - Automatic expiration (TTL) for abandoned reservations.
- **Central XLSX Configuration (`config/flow_accounts.xlsx`)**:
  - Multi-tab configuration workbook with `ACCOUNTS` and `SETTINGS` sheets.
  - Dynamic user-driven account pool editing (orders, limits, enabled status, references, notes).
  - Configurable rotation strategy (`sequential` or `random`), default daily limit, timezone, and auto-rotate mode.
  - `flow-account sync [config/flow_accounts.xlsx]`: synchronizes spreadsheet with the SQLite database.
  - Disappearing accounts are safely marked as `DISABLED`/`ARCHIVED` without destroying historical consumption logs.
- **Secure Keyring Credential Storage**:
  - Integrated `keyring` support for Linux SecretService / macOS Keychain / Windows Credential Manager.
  - `flow-account credential set <email_or_ref>`: interactive hidden prompt storing passwords securely.
  - `flow-account credential test <email_or_ref>`: verifies credential presence without exposing secret values.
  - Secured restricted fallback vault (`~/.flow-accounts/.vault_store`, mode 0600) for headless servers.
- **Agent Operational Governance**:
  - Strict 7-step mandatory agent workflow documented in `SKILL.md` enforcing pre-checks before launching any generation in Google Flow.
- **Test Coverage**:
  - Added `tests/test_v110_features.py` covering all 20 required criteria. Test suite expanded to 121 comprehensive tests passing with 100% success.

## [1.0.0] - 2026-10-31

### Added
- Public release under the name **Flow Credit Rotator** (`flow-credit-rotator`).
- Dynamic account pool supporting arbitrary quantities of accounts (1, 2, 5, 20, 50, 100+) without any hardcoded limit or ceiling.
- Sequential rotation strategy as default (`1 -> 2 -> 3`), holding eligible accounts, auto-advancing on exhaustion, and looping back.
- Random rotation strategy with 100% eligibility guarantee (skipping disabled, exhausted, and human-intervention accounts).
- Full support for `round-robin`, `lowest-usage`, and `priority` strategies.
- Configurable default daily limit (`DEFAULT_DAILY_LIMIT=85` by default) and monthly quota tracking.
- Operational modes: `AUTO` (unattended rotation) and `MANUAL` (pinned account control).
- Human verification workflow: `human-required` and `human-cleared` (`NEEDS_HUMAN` lifecycle).
- Timezone-aware automatic and manual daily/monthly resets (default timezone: `America/Bahia`).
- Concurrent SQLite persistence in WAL mode with busy timeout handling.
- Secure CSV and XLSX batch account importing with strict column validation and forbidden secret rejection.
- Credential isolation model enforcing indirect references (`ENV:`, `OP:`, `BWS:`, `KEYRING:`, `FILE:`).
- Cross-platform installers: `install.sh` for Linux/macOS and `install.ps1` for Windows.
- Hermes and Claude Code skill registration under `flow-credit-rotator`.
- Comprehensive test suite covering concurrency, limits, persistence, reset, security, and dynamic rotation.
