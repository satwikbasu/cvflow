"""Auth (Phase 10): Fernet token vault + non-blocking email-OTP coordinator.

Encrypted vault + browser-SSO model (locked decision): sites are logged into via
the Phase-9 persistent browser context; this module secures secrets at rest and
runs the email-OTP fallback that messages the user, waits up to a timeout, and
marks ``otp_timeout`` + notifies on expiry -- never silently (invariant 3).
"""

from __future__ import annotations

import os
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

__all__ = ["TokenVault", "VaultError"]


class VaultError(Exception):
    """Raised on an invalid Fernet key or an undecryptable token."""


class TokenVault:
    """Fernet-backed encryption for secrets at rest (cookies, storage_state, tokens)."""

    def __init__(self, fernet: Fernet) -> None:
        self._fernet = fernet

    @classmethod
    def create_or_load(cls, key_path: str | Path) -> TokenVault:
        path = Path(key_path)
        if path.exists():
            key = path.read_bytes()
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            key = Fernet.generate_key()
            path.write_bytes(key)
            os.chmod(path, 0o600)
        try:
            return cls(Fernet(key))
        except (ValueError, TypeError) as exc:
            raise VaultError(f"invalid Fernet key at {path}") from exc

    def encrypt(self, plaintext: str) -> bytes:
        return self._fernet.encrypt(plaintext.encode())

    def decrypt(self, token: bytes) -> str:
        try:
            return self._fernet.decrypt(token).decode()
        except InvalidToken as exc:
            raise VaultError("could not decrypt token") from exc

    def save_blob(self, path: str | Path, plaintext: str) -> None:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(self.encrypt(plaintext))
        os.chmod(p, 0o600)

    def load_blob(self, path: str | Path) -> str:
        return self.decrypt(Path(path).read_bytes())
