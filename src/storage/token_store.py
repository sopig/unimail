"""Encrypted token storage using Fernet (AES-128-CBC)."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Optional

from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
import base64


class TokenStore:
    """
    Encrypts OAuth tokens and passwords at rest.
    
    Key derivation: PBKDF2(passphrase + salt) → Fernet key
    First run: user sets a passphrase to unlock the store.
    """

    def __init__(self, store_path: str | Path, passphrase: str):
        self.store_path = Path(store_path)
        self.store_path.parent.mkdir(parents=True, exist_ok=True)
        self._fernet = self._derive_fernet(passphrase)

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
