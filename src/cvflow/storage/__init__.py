"""Durable tracking store (SQLite) + the form-fields loader.

The store routes every status change through :mod:`cvflow.statemachine`, so the
gate's invariants hold at the persistence layer too: ``set_status`` can never
write ``approved`` (the state machine refuses it), and :meth:`ApplicationStore.approve`
is the only path that does — via :func:`cvflow.statemachine.approve`.

The form-fields loader keeps the never-fabricate rule (CLAUDE.md invariant 2):
an empty value is a *known-but-unfilled* field that must be asked, never guessed.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from cvflow.statemachine import Status, approve, transition

__all__ = [
    "Application",
    "ApplicationStore",
    "DuplicateJob",
    "UnknownJob",
    "FormFields",
    "MissingField",
]


class DuplicateJob(Exception):
    """Raised when adding a job_id that already exists (dedup enforcement)."""


class UnknownJob(Exception):
    """Raised when operating on a job_id that is not in the store."""


class MissingField(Exception):
    """Raised when a form field is unknown or known-but-empty (never guess)."""


def _now() -> str:
    return datetime.now(UTC).isoformat()


@dataclass(frozen=True)
class Application:
    job_id: str
    company: str
    role: str
    jd_url: str
    status: Status
    discovered_at: str
    applied_at: str | None = None
    tailored_pdf_path: str | None = None
    confirmation_ref: str | None = None


_SCHEMA = """
CREATE TABLE IF NOT EXISTS applications (
    job_id            TEXT PRIMARY KEY,
    company           TEXT NOT NULL,
    role              TEXT NOT NULL,
    jd_url            TEXT NOT NULL,
    status            TEXT NOT NULL,
    discovered_at     TEXT NOT NULL,
    applied_at        TEXT,
    tailored_pdf_path TEXT,
    confirmation_ref  TEXT
);
"""


class ApplicationStore:
    """SQLite-backed store for application records, keyed by stable ``job_id``."""

    def __init__(self, db_path: str | Path) -> None:
        if str(db_path) != ":memory:":
            Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(db_path))
        self._conn.row_factory = sqlite3.Row
        self._conn.execute(_SCHEMA)
        self._conn.commit()

    def _row_to_app(self, row: sqlite3.Row) -> Application:
        return Application(
            job_id=row["job_id"],
            company=row["company"],
            role=row["role"],
            jd_url=row["jd_url"],
            status=Status(row["status"]),
            discovered_at=row["discovered_at"],
            applied_at=row["applied_at"],
            tailored_pdf_path=row["tailored_pdf_path"],
            confirmation_ref=row["confirmation_ref"],
        )

    def add(self, job_id: str, company: str, role: str, jd_url: str) -> Application:
        if self.exists(job_id):
            raise DuplicateJob(f"job_id already tracked: {job_id}")
        self._conn.execute(
            "INSERT INTO applications (job_id, company, role, jd_url, status, discovered_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (job_id, company, role, jd_url, Status.DISCOVERED.value, _now()),
        )
        self._conn.commit()
        app = self.get(job_id)
        assert app is not None  # just inserted
        return app

    def get(self, job_id: str) -> Application | None:
        row = self._conn.execute(
            "SELECT * FROM applications WHERE job_id = ?", (job_id,)
        ).fetchone()
        return self._row_to_app(row) if row is not None else None

    def exists(self, job_id: str) -> bool:
        return (
            self._conn.execute(
                "SELECT 1 FROM applications WHERE job_id = ?", (job_id,)
            ).fetchone()
            is not None
        )

    def list_by_status(self, status: Status) -> list[Application]:
        rows = self._conn.execute(
            "SELECT * FROM applications WHERE status = ? ORDER BY discovered_at", (status.value,)
        ).fetchall()
        return [self._row_to_app(r) for r in rows]

    def _require(self, job_id: str) -> Application:
        app = self.get(job_id)
        if app is None:
            raise UnknownJob(f"no such job_id: {job_id}")
        return app

    def set_status(self, job_id: str, target: Status) -> None:
        """Apply a legal non-approval transition. Cannot write ``approved``."""
        app = self._require(job_id)
        new_status = transition(app.status, target)  # refuses approved; validates table
        applied_at = _now() if new_status is Status.APPLIED else app.applied_at
        self._conn.execute(
            "UPDATE applications SET status = ?, applied_at = ? WHERE job_id = ?",
            (new_status.value, applied_at, job_id),
        )
        self._conn.commit()

    def approve(self, job_id: str) -> None:
        """The only store path to ``approved`` (via statemachine.approve)."""
        app = self._require(job_id)
        new_status = approve(app.status)
        self._conn.execute(
            "UPDATE applications SET status = ? WHERE job_id = ?", (new_status.value, job_id)
        )
        self._conn.commit()

    def set_tailored_pdf(self, job_id: str, path: str) -> None:
        self._require(job_id)
        self._conn.execute(
            "UPDATE applications SET tailored_pdf_path = ? WHERE job_id = ?", (path, job_id)
        )
        self._conn.commit()

    def set_confirmation(self, job_id: str, ref: str) -> None:
        self._require(job_id)
        self._conn.execute(
            "UPDATE applications SET confirmation_ref = ? WHERE job_id = ?", (ref, job_id)
        )
        self._conn.commit()


@dataclass(frozen=True)
class FormFields:
    """Recurring form values. Empty string = known field, not yet filled → must ask."""

    values: dict[str, str]

    @classmethod
    def load(cls, path: str | Path) -> FormFields:
        raw = json.loads(Path(path).read_text())
        values = {k: v for k, v in raw.items() if k != "_comment"}
        return cls(values=values)

    @property
    def populated(self) -> dict[str, str]:
        return {k: v for k, v in self.values.items() if v != ""}

    def missing(self) -> list[str]:
        return [k for k, v in self.values.items() if v == ""]

    def is_filled(self, key: str) -> bool:
        return self.values.get(key, "") != ""

    def require(self, key: str) -> str:
        if not self.is_filled(key):
            raise MissingField(
                f"form field {key!r} is unknown or empty; must be asked, never guessed"
            )
        return self.values[key]
