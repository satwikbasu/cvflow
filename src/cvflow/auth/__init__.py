"""Auth (Phase 10): Fernet token vault + non-blocking email-OTP coordinator.

Encrypted vault + browser-SSO model (locked decision): sites are logged into via
the Phase-9 persistent browser context; this module secures secrets at rest and
runs the email-OTP fallback that messages the user, waits up to a timeout, and
marks ``otp_timeout`` + notifies on expiry -- never silently (invariant 3).
"""

from __future__ import annotations

import os
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from cryptography.fernet import Fernet, InvalidToken

from cvflow.statemachine import Status
from cvflow.storage import UnknownJob

__all__ = ["TokenVault", "VaultError", "OtpCoordinator"]


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


def _now_utc() -> datetime:
    return datetime.now(UTC)


class OtpCoordinator:
    """Non-blocking email-OTP wait: deadline + lazy/proactive expiry. Never silent."""

    def __init__(
        self, *, store: Any, notify: Callable[[str], None], timeout_minutes: int,
        now: Callable[[], datetime] = _now_utc,
    ) -> None:
        self._store = store
        self._notify = notify
        self._timeout = timeout_minutes
        self._now = now

    def request(
        self, job_id: str, destination: str, *, now: datetime | None = None
    ) -> dict[str, Any]:
        """Start an OTP wait: record the deadline, notify the user, pause."""
        if self._store.get(job_id) is None:
            raise UnknownJob(job_id)
        moment = now or self._now()
        deadline = moment + timedelta(minutes=self._timeout)
        self._store.set_otp_deadline(job_id, deadline.isoformat())
        self._notify(
            f"🔐 An OTP was sent to {destination} for job {job_id}. "
            f"Reply within {self._timeout} min."
        )
        return {
            "needs_otp": True, "job_id": job_id,
            "destination": destination, "deadline": deadline.isoformat(),
        }

    def provide(self, job_id: str, otp: str, *, now: datetime | None = None) -> str | None:
        """Resolve a user-supplied OTP. On time -> return it; late/none -> otp_timeout."""
        app = self._store.get(job_id)
        if app is None:
            raise UnknownJob(job_id)
        moment = now or self._now()
        deadline = app.otp_deadline
        if deadline is not None and moment <= datetime.fromisoformat(deadline):
            self._store.set_otp_deadline(job_id, None)
            return otp
        self._expire(job_id)
        return None

    def expire_overdue(self, *, now: datetime | None = None) -> list[str]:
        """Sweep: mark every approved app whose OTP deadline has passed as otp_timeout."""
        moment = now or self._now()
        expired: list[str] = []
        for app in self._store.list_awaiting_otp():
            if (
                app.status is Status.APPROVED
                and app.otp_deadline is not None
                and moment > datetime.fromisoformat(app.otp_deadline)
            ):
                self._expire(app.job_id)
                expired.append(app.job_id)
        return expired

    def _expire(self, job_id: str) -> None:
        app = self._store.get(job_id)
        if app is not None and app.status is Status.APPROVED:
            self._store.set_status(job_id, Status.OTP_TIMEOUT)
        self._store.set_otp_deadline(job_id, None)
        self._notify(f"⏰ OTP for job {job_id} expired; marked otp_timeout.")
