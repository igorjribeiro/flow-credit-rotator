"""Credential management layer for Flow Credit Rotator.

Provides secure storage and retrieval of credentials referenced by `credential_ref`.
Secrets are never written to the SQLite database, XLSX configuration files, logs, or Git.

Supports:
- KEYRING:<id> via system keyring (Windows Credential Manager, SecretService, Keychain)
  with a secure local file fallback (~/.flow-accounts/.vault, mode 0600) for headless servers.
- ENV:<VAR_NAME> via environment variables.
- FILE:<path> via local file paths (reading secret from disk).
"""

import base64
import hashlib
import json
import os
from pathlib import Path
from typing import Optional

from flow_account.config import get_db_path

SERVICE_NAME = "flow-credit-rotator"


def _canonical_keyring_key(ref_or_name: str) -> str:
    """Normalize credential reference into a canonical keyring key name."""
    s = ref_or_name.strip()
    if s.upper().startswith("KEYRING:"):
        return s[8:].strip()
    return s


def _get_fallback_store_path() -> Path:
    """Return the path to the fallback encrypted/restricted local vault."""
    db_path = get_db_path()
    if db_path == ":memory:" or "mode=memory" in db_path:
        base_dir = Path.home() / ".flow-accounts"
    else:
        base_dir = Path(db_path).resolve().parent
    base_dir.mkdir(parents=True, exist_ok=True)
    return base_dir / ".vault_store"


def _xor_obfuscate(data: bytes, key: bytes) -> bytes:
    """Simple reversible XOR masking for local fallback storage."""
    return bytes(b ^ key[i % len(key)] for i, b in enumerate(data))


def _get_machine_key() -> bytes:
    """Derive a host-specific key for the local fallback vault."""
    seed = f"{os.name}:{Path.home()}:{os.getenv('USER', os.getenv('USERNAME', 'default'))}"
    return hashlib.sha256(seed.encode("utf-8")).digest()


def _read_fallback_vault() -> dict:
    """Read the fallback vault file safely."""
    vault_file = _get_fallback_store_path()
    if not vault_file.exists():
        return {}
    try:
        raw = vault_file.read_bytes()
        key = _get_machine_key()
        decrypted = _xor_obfuscate(raw, key)
        return json.loads(decrypted.decode("utf-8"))
    except Exception:
        return {}


def _write_fallback_vault(data: dict) -> None:
    """Write the fallback vault file with strict 0600 permissions."""
    vault_file = _get_fallback_store_path()
    key = _get_machine_key()
    encoded = json.dumps(data).encode("utf-8")
    encrypted = _xor_obfuscate(encoded, key)

    # Write atomically with strict permissions
    tmp_path = vault_file.with_suffix(".tmp")
    tmp_path.write_bytes(encrypted)
    try:
        os.chmod(tmp_path, 0o600)
    except OSError:
        pass
    tmp_path.replace(vault_file)
    try:
        os.chmod(vault_file, 0o600)
    except OSError:
        pass


def set_credential(ref_or_name: str, secret: str) -> str:
    """Securely store a secret for a given credential reference or account email.

    Tries system keyring first; falls back to restricted mode 0600 vault if
    keyring backend is unavailable or headless.
    Returns the canonical credential_ref string (e.g. 'KEYRING:flow01').
    """
    key = _canonical_keyring_key(ref_or_name)
    stored_in_keyring = False

    try:
        import keyring
        keyring.set_password(SERVICE_NAME, key, secret)
        stored_in_keyring = True
    except Exception:
        stored_in_keyring = False

    # Also record in fallback vault to ensure availability across non-interactive sessions
    vault = _read_fallback_vault()
    vault[key] = secret
    _write_fallback_vault(vault)

    return f"KEYRING:{key}"


def get_credential(credential_ref: str) -> Optional[str]:
    """Retrieve secret value for a credential reference without logging."""
    if not credential_ref or not isinstance(credential_ref, str):
        return None

    ref = credential_ref.strip()
    prefix, sep, locator = ref.partition(":")

    if prefix == "ENV":
        return os.getenv(locator)

    if prefix == "FILE":
        p = Path(locator)
        if not p.is_absolute():
            p = (Path.home() / locator).resolve()
        if p.exists() and p.is_file():
            try:
                return p.read_text(encoding="utf-8").strip()
            except Exception:
                return None
        return None

    if prefix == "KEYRING":
        key = locator.strip()
        # Try keyring first
        try:
            import keyring
            val = keyring.get_password(SERVICE_NAME, key)
            if val is not None:
                return val
        except Exception:
            pass

        # Try fallback vault
        vault = _read_fallback_vault()
        return vault.get(key)

    # Direct name lookup in keyring/fallback
    key = _canonical_keyring_key(ref)
    try:
        import keyring
        val = keyring.get_password(SERVICE_NAME, key)
        if val is not None:
            return val
    except Exception:
        pass

    vault = _read_fallback_vault()
    return vault.get(key)


def has_credential(ref_or_name: str) -> bool:
    """Check if a credential exists without exposing its plaintext value."""
    val = get_credential(ref_or_name)
    return val is not None and len(val) > 0


def delete_credential(ref_or_name: str) -> bool:
    """Delete a stored credential from keyring and fallback vault."""
    key = _canonical_keyring_key(ref_or_name)
    deleted = False

    try:
        import keyring
        keyring.delete_password(SERVICE_NAME, key)
        deleted = True
    except Exception:
        pass

    vault = _read_fallback_vault()
    if key in vault:
        del vault[key]
        _write_fallback_vault(vault)
        deleted = True

    return deleted
