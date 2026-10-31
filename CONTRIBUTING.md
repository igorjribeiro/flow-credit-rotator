# Contributing to Flow Credit Rotator

Thank you for contributing to Flow Credit Rotator! We welcome improvements, bug reports, and pull requests.

## Development Setup

1. Clone the repository:
   ```bash
   git clone https://github.com/igorjribeiro/flow-credit-rotator.git
   cd flow-credit-rotator
   ```

2. Create a virtual environment and install dependencies:
   ```bash
   python3 -m venv .venv
   source .venv/bin/activate
   pip install -e .
   pip install pytest pytest-asyncio
   ```

3. Verify tests pass:
   ```bash
   pytest -v
   ```

## Development Rules

- **Zero Hardcoded Limits**: Never introduce fixed account ceilings or constants like `MAX_ACCOUNTS`. The pool must remain completely dynamic.
- **Sequential Default**: The default rotation strategy is `sequential`. Do not alter this default without an explicit migration path.
- **Portability**: Never hardcode personal paths (such as `/home/<user>` or `C:\Users\<user>`). Always use `$HOME`, `Path.home()`, `$env:USERPROFILE`, or environment variables.
- **Credential Isolation**: Do not add fields or functions that accept or store plaintext passwords. Always use `credential_ref`.
- **Backward Compatibility**: Keep the CLI program name as `flow-account`.

## Pull Request Checklist

Before submitting a pull request:
1. Ensure all existing tests pass: `pytest -v`.
2. Add new unit tests covering any new functionality or bug fixes.
3. Update `README.md`, `SKILL.md`, and `CHANGELOG.md` if user-facing behavior changes.
4. Follow standard Python code style (PEP 8).
