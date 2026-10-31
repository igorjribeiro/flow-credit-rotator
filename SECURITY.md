# Security Policy

## Supported Versions

| Version | Supported          |
| ------- | ------------------ |
| 1.0.x   | :white_check_mark: |

## Security Model: Credential Isolation

Flow Credit Rotator is designed specifically for automated production environments where secret leakage cannot be tolerated.

### 1. Zero Plaintext Credentials
- Neither the SQLite database nor any export/import mechanism stores plaintext passwords, API keys, or raw authentication tokens.
- All accounts use an indirect `credential_ref` referencing an external secret provider (e.g. `ENV:VAR_NAME`, `OP:vault/item`, `BWS:uuid`, `KEYRING:name`).

### 2. Proactive Import Shield
The spreadsheet importer (`flow-account import`) rejects files containing forbidden secret column names (`password`, `pass`, `senha`, `secret`, `token`, `pwd`, `key`, `api_key`). Any attempt to import plaintext credentials raises `SecurityImportError` before writing to the database.

### 3. Log and Output Sanitization
- CLI outputs (`flow-account list`, `flow-account current`, `flow-account history`) sanitize credential references and never expose sensitive data.
- Exceptions and audit notes do not echo sensitive values.

## Reporting a Vulnerability

If you discover a security vulnerability in Flow Credit Rotator, please report it privately:

- **Email**: security@hermesflow.example.com
- Do **not** open a public issue before the vulnerability has been evaluated and resolved.
- Include step-by-step reproduction instructions and affected environment details.
- You will receive an acknowledgment within 48 hours and regular updates until resolution.
