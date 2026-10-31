# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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
