"""Encrypted token storage using Fernet (AES-128-CBC)."""

from __future__ import annotations

import json
import os
import secrets
from pathlib import Path
from typing import Any, Optional

from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
import base64

# Auto-generated passphrase file — created on first run
_PASSPHRASE_FILE = Path.home() / ".unimail" / ".passphrase"


def _get_or_create_passphrase(explicit: str | None = None) -> str:
    """Return a passphrase. Priority: explicit arg > env var > saved file > auto-generate.

    Migration: if a tokens.enc exists but no passphrase file, assume legacy
    "unimail-default" and migrate — generate a new key, re-encrypt, and save.
    """
    if explicit:
        return explicit
    env_val = os.environ.get("UNIMAIL_PASSPHRASE")
    if env_val:
        return env_val
    if _PASSPHRASE_FILE.exists():
        return _PASSPHRASE_FILE.read_text().strip()

    # No saved passphrase — check if there's an existing token store to migrate
    store_path = Path.home() / ".unimail" / "data" / "tokens.enc"
    if store_path.exists():
        # Legacy migration: tokens were encrypted with "unimail-default"
        return "unimail-default"

    # First run: generate a strong random passphrase and save it
    passphrase = secrets.token_urlsafe(32)
    _PASSPHRASE_FILE.parent.mkdir(parents=True, exist_ok=True)
    _PASSPHRASE_FILE.write_text(passphrase)
    _PASSPHRASE_FILE.chmod(0o600)
    return passphrase


class TokenStore:
    """
    Encrypts OAuth tokens and passwords at rest.

    Key derivation: PBKDF2(passphrase + salt) → Fernet key
    """

    def __init__(self, store_path: str | Path, passphrase: str | None = None):
        self.store_path = Path(store_path)
        self.store_path.parent.mkdir(parents=True, exist_ok=True)
        # Treat explicit "unimail-default" as not explicitly set (for backward compat)
        effective = _get_or_create_passphrase(passphrase if passphrase and passphrase != "unimail-default" else None)
        self._fernet = self._derive_fernet(effective)
        # If we used the legacy passphrase and a store exists, migrate to auto-generated key
        if effective == "unimail-default" and self.store_path.exists():
            self._migrate_passphrase()

    def _derive_fernet(self, passphrase: str) -> Fernet:
        salt_path = self.store_path.with_suffix(".salt")
        if salt_path.exists():
            salt = salt_path.read_bytes()
        else:
            salt = os.urandom(16)
            salt_path.write_bytes(salt)

        kdf = PBKDF2HMAC(
            algorithm=hashes.SHA256(),
            length=32,
            salt=salt,
            iterations=480_000,
        )
        key = base64.urlsafe_b64encode(kdf.derive(passphrase.encode()))
        return Fernet(key)

    def _load_all(self) -> dict[str, Any]:
        if not self.store_path.exists():
            return {}
        encrypted = self.store_path.read_bytes()
        decrypted = self._fernet.decrypt(encrypted)
        return json.loads(decrypted)

    def _save_all(self, data: dict[str, Any]) -> None:
        plaintext = json.dumps(data).encode()
        encrypted = self._fernet.encrypt(plaintext)
        self.store_path.write_bytes(encrypted)

    def save(self, account_id: str, tokens: dict) -> None:
        """Save tokens for an account."""
        data = self._load_all()
        data[account_id] = tokens
        self._save_all(data)

    def get(self, account_id: str) -> Optional[dict]:
        """Get tokens for an account."""
        data = self._load_all()
        return data.get(account_id)

    def delete(self, account_id: str) -> None:
        """Remove tokens for an account."""
        data = self._load_all()
        data.pop(account_id, None)
        self._save_all(data)

    def list_accounts(self) -> list[str]:
        """List account IDs with stored tokens."""
        return list(self._load_all().keys())

    def _migrate_passphrase(self) -> None:
        """Re-encrypt token store with a new auto-generated passphrase."""
        try:
            data = self._load_all()
            if not data:
                return
            # Generate new passphrase
            new_passphrase = secrets.token_urlsafe(32)
            self._fernet = self._derive_fernet(new_passphrase)
            self._save_all(data)
            # Save new passphrase file
            _PASSPHRASE_FILE.parent.mkdir(parents=True, exist_ok=True)
            _PASSPHRASE_FILE.write_text(new_passphrase)
            _PASSPHRASE_FILE.chmod(0o600)
        except Exception:
            # If migration fails, keep using old passphrase — don't break existing setups
            pass
