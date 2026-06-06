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

from cvflow.analysis import JDAnalysis
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
    proof_url: str | None = None
    proof_screenshot_path: str | None = None
    proof_page_title: str | None = None
    otp_deadline: str | None = None


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
    confirmation_ref  TEXT,
    proof_url             TEXT,
    proof_screenshot_path TEXT,
    proof_page_title      TEXT,
    otp_deadline          TEXT
);
CREATE TABLE IF NOT EXISTS jd_analyses (
    job_id    TEXT PRIMARY KEY REFERENCES applications(job_id),
    analysis  TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS digest_slots (
    slot         INTEGER PRIMARY KEY,
    job_id       TEXT NOT NULL,
    presented_at TEXT NOT NULL
);
"""


class ApplicationStore:
    """SQLite-backed store for application records, keyed by stable ``job_id``."""

    def __init__(self, db_path: str | Path) -> None:
        if str(db_path) != ":memory:":
            Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(db_path))
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(_SCHEMA)
        self._migrate()
        self._conn.commit()

    def _migrate(self) -> None:
        """Idempotently add columns that newer phases appended to ``applications``.

        ``CREATE TABLE IF NOT EXISTS`` never alters an existing table, so a DB
        created by an earlier version (deploy-by-clone) would be missing the
        additive proof / otp_deadline columns. Add any that are absent.
        """
        existing = {
            row["name"] for row in self._conn.execute("PRAGMA table_info(applications)")
        }
        # column name -> SQL type; all nullable, additive only (never the gate columns).
        added_columns = {
            "tailored_pdf_path": "TEXT",
            "confirmation_ref": "TEXT",
            "proof_url": "TEXT",
            "proof_screenshot_path": "TEXT",
            "proof_page_title": "TEXT",
            "otp_deadline": "TEXT",
        }
        for col, col_type in added_columns.items():
            if col not in existing:
                self._conn.execute(
                    f"ALTER TABLE applications ADD COLUMN {col} {col_type}"
                )

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
            proof_url=row["proof_url"],
            proof_screenshot_path=row["proof_screenshot_path"],
            proof_page_title=row["proof_page_title"],
            otp_deadline=row["otp_deadline"],
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

    def set_proof(
        self, job_id: str, *, url: str, screenshot_path: str, page_title: str
    ) -> None:
        """Persist submission proof. Additive — never touches status/approval."""
        self._require(job_id)
        self._conn.execute(
            "UPDATE applications SET proof_url = ?, proof_screenshot_path = ?, "
            "proof_page_title = ? WHERE job_id = ?",
            (url, screenshot_path, page_title, job_id),
        )
        self._conn.commit()

    def set_otp_deadline(self, job_id: str, deadline: str | None) -> None:
        """Set or clear the pending OTP deadline (ISO-8601). Additive — no status change."""
        self._require(job_id)
        self._conn.execute(
            "UPDATE applications SET otp_deadline = ? WHERE job_id = ?", (deadline, job_id)
        )
        self._conn.commit()

    def list_awaiting_otp(self) -> list[Application]:
        """Return apps with a non-NULL otp_deadline (an OTP wait is pending)."""
        rows = self._conn.execute(
            "SELECT * FROM applications WHERE otp_deadline IS NOT NULL ORDER BY discovered_at"
        ).fetchall()
        return [self._row_to_app(r) for r in rows]

    def set_digest_slots(self, job_ids: list[str]) -> None:
        """Replace the presented-digest ordinal→job_id map (slot 1..N)."""
        with self._conn:
            self._conn.execute("DELETE FROM digest_slots")
            self._conn.executemany(
                "INSERT INTO digest_slots (slot, job_id, presented_at) VALUES (?, ?, ?)",
                [(i, jid, _now()) for i, jid in enumerate(job_ids, start=1)],
            )

    def get_digest_slot(self, slot: int) -> str | None:
        row = self._conn.execute(
            "SELECT job_id FROM digest_slots WHERE slot = ?", (slot,)
        ).fetchone()
        return row["job_id"] if row is not None else None

    def digest_slots(self) -> list[str]:
        rows = self._conn.execute(
            "SELECT job_id FROM digest_slots ORDER BY slot"
        ).fetchall()
        return [r["job_id"] for r in rows]

    def save_analysis(self, job_id: str, analysis: JDAnalysis) -> None:
        """Persist the JD analysis linked to an existing application record."""
        self._require(job_id)
        self._conn.execute(
            "INSERT INTO jd_analyses (job_id, analysis) VALUES (?, ?) "
            "ON CONFLICT(job_id) DO UPDATE SET analysis = excluded.analysis",
            (job_id, analysis.to_json()),
        )
        self._conn.commit()

    def get_analysis(self, job_id: str) -> JDAnalysis | None:
        row = self._conn.execute(
            "SELECT analysis FROM jd_analyses WHERE job_id = ?", (job_id,)
        ).fetchone()
        return JDAnalysis.from_json(row["analysis"]) if row is not None else None


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
