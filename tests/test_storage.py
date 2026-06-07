"""Tests for the SQLite application store and the form-fields loader (Phase 1)."""

from pathlib import Path

import pytest

from cvflow.statemachine import IllegalTransition, Status
from cvflow.storage import (
    Application,
    ApplicationStore,
    DuplicateJob,
    FormFields,
    MissingField,
    UnknownJob,
)


def _store() -> ApplicationStore:
    return ApplicationStore(":memory:")


def test_add_and_get_round_trip() -> None:
    store = _store()
    app = store.add("job-1", "Acme", "SRE", "https://acme.example/jobs/1")
    assert isinstance(app, Application)
    assert app.status is Status.DISCOVERED
    fetched = store.get("job-1")
    assert fetched is not None
    assert fetched.company == "Acme"
    assert fetched.role == "SRE"
    assert fetched.discovered_at  # populated


def test_duplicate_job_id_raises() -> None:
    store = _store()
    store.add("job-1", "Acme", "SRE", "u")
    with pytest.raises(DuplicateJob):
        store.add("job-1", "Other", "Dev", "u2")


def test_get_missing_returns_none_and_exists() -> None:
    store = _store()
    assert store.get("nope") is None
    assert store.exists("nope") is False
    store.add("job-1", "Acme", "SRE", "u")
    assert store.exists("job-1") is True


def test_set_status_legal_transition_persists() -> None:
    store = _store()
    store.add("job-1", "Acme", "SRE", "u")
    store.set_status("job-1", Status.PENDING_REVIEW)
    assert store.get("job-1").status is Status.PENDING_REVIEW


def test_set_status_illegal_transition_raises() -> None:
    store = _store()
    store.add("job-1", "Acme", "SRE", "u")
    with pytest.raises(IllegalTransition):
        store.set_status("job-1", Status.APPLIED)  # discovered -> applied is illegal


def test_set_status_cannot_reach_approved() -> None:
    store = _store()
    store.add("job-1", "Acme", "SRE", "u")
    store.set_status("job-1", Status.PENDING_REVIEW)
    with pytest.raises(IllegalTransition):
        store.set_status("job-1", Status.APPROVED)


def test_approve_is_the_only_path_to_approved() -> None:
    store = _store()
    store.add("job-1", "Acme", "SRE", "u")
    store.set_status("job-1", Status.PENDING_REVIEW)
    store.approve("job-1")
    assert store.get("job-1").status is Status.APPROVED


def test_approve_from_wrong_state_raises() -> None:
    store = _store()
    store.add("job-1", "Acme", "SRE", "u")
    with pytest.raises(IllegalTransition):
        store.approve("job-1")  # still discovered


def test_applied_at_set_on_apply() -> None:
    store = _store()
    store.add("job-1", "Acme", "SRE", "u")
    store.set_status("job-1", Status.PENDING_REVIEW)
    store.approve("job-1")
    store.set_status("job-1", Status.APPLIED)
    app = store.get("job-1")
    assert app.status is Status.APPLIED
    assert app.applied_at is not None


def test_list_by_status_filters() -> None:
    store = _store()
    store.add("a", "A", "r", "u")
    store.add("b", "B", "r", "u")
    store.set_status("b", Status.SKIPPED)
    discovered = store.list_by_status(Status.DISCOVERED)
    assert [a.job_id for a in discovered] == ["a"]


def test_unknown_job_raises() -> None:
    store = _store()
    with pytest.raises(UnknownJob):
        store.set_status("ghost", Status.PENDING_REVIEW)


def test_set_tailored_pdf_and_confirmation() -> None:
    store = _store()
    store.add("job-1", "Acme", "SRE", "u")
    store.set_tailored_pdf("job-1", "data/resumes/job-1.pdf")
    store.set_confirmation("job-1", "CONF-123")
    app = store.get("job-1")
    assert app.tailored_pdf_path == "data/resumes/job-1.pdf"
    assert app.confirmation_ref == "CONF-123"


# --- form fields loader ---

FORM_FIELDS = Path("profile/form_fields.json")


def test_form_fields_loads_and_splits_populated_vs_missing(tmp_path: Path) -> None:
    p = tmp_path / "ff.json"
    p.write_text(
        '{"_comment": "x", "full_name": "Satwik", "salary_expectation": "", "phone": ""}'
    )
    ff = FormFields.load(p)
    assert ff.values == {"full_name": "Satwik", "salary_expectation": "", "phone": ""}
    assert ff.populated == {"full_name": "Satwik"}
    assert sorted(ff.missing()) == ["phone", "salary_expectation"]


def test_form_fields_require_raises_on_empty_and_unknown(tmp_path: Path) -> None:
    p = tmp_path / "ff.json"
    p.write_text('{"full_name": "Satwik", "salary_expectation": ""}')
    ff = FormFields.load(p)
    assert ff.require("full_name") == "Satwik"
    assert ff.is_filled("full_name") is True
    assert ff.is_filled("salary_expectation") is False
    with pytest.raises(MissingField):
        ff.require("salary_expectation")
    with pytest.raises(MissingField):
        ff.require("unknown_key")


def test_form_fields_loads_committed_form_fields() -> None:
    if not FORM_FIELDS.exists():
        pytest.skip("committed form_fields.json not present")
    ff = FormFields.load(FORM_FIELDS)
    assert "_comment" not in ff.values


def test_set_proof_persists_url_screenshot_title():
    from cvflow.storage import ApplicationStore

    store = ApplicationStore(":memory:")
    store.add("indeed:9", "Acme", "Backend", "https://jobs/9")
    store.set_proof(
        "indeed:9", url="https://acme/confirm",
        screenshot_path="/data/proof/9.png", page_title="Application received",
    )
    app = store.get("indeed:9")
    assert app.proof_url == "https://acme/confirm"
    assert app.proof_screenshot_path == "/data/proof/9.png"
    assert app.proof_page_title == "Application received"


def test_set_otp_deadline_and_list_awaiting_otp():
    from cvflow.storage import ApplicationStore

    store = ApplicationStore(":memory:")
    store.add("indeed:1", "Acme", "Backend", "https://jobs/1")
    store.add("indeed:2", "Globex", "Platform", "https://jobs/2")
    assert store.list_awaiting_otp() == []

    store.set_otp_deadline("indeed:1", "2026-06-05T10:00:00+00:00")
    assert store.get("indeed:1").otp_deadline == "2026-06-05T10:00:00+00:00"
    awaiting = store.list_awaiting_otp()
    assert [a.job_id for a in awaiting] == ["indeed:1"]

    store.set_otp_deadline("indeed:1", None)
    assert store.get("indeed:1").otp_deadline is None
    assert store.list_awaiting_otp() == []


def test_migration_adds_missing_columns_to_old_db(tmp_path):
    import sqlite3

    from cvflow.storage import ApplicationStore

    db = tmp_path / "old.db"
    # Simulate a pre-Phase-9 DB: applications table without the additive columns.
    conn = sqlite3.connect(str(db))
    conn.executescript(
        "CREATE TABLE applications ("
        " job_id TEXT PRIMARY KEY, company TEXT NOT NULL, role TEXT NOT NULL,"
        " jd_url TEXT NOT NULL, status TEXT NOT NULL, discovered_at TEXT NOT NULL,"
        " applied_at TEXT);"
    )
    conn.execute(
        "INSERT INTO applications (job_id, company, role, jd_url, status, discovered_at) "
        "VALUES ('indeed:1','Acme','Backend','https://jobs/1','discovered','2026-06-05T00:00:00')"
    )
    conn.commit()
    conn.close()

    store = ApplicationStore(db)  # must migrate without error
    app = store.get("indeed:1")
    assert app.proof_url is None
    assert app.otp_deadline is None
    # and the new setters work on the migrated DB
    store.set_otp_deadline("indeed:1", "2026-06-05T10:00:00+00:00")
    assert store.get("indeed:1").otp_deadline == "2026-06-05T10:00:00+00:00"


def test_digest_slots_roundtrip_and_replace():
    from cvflow.storage import ApplicationStore
    s = ApplicationStore(":memory:")
    s.set_digest_slots(["indeed:a", "indeed:b", "indeed:c"])
    assert s.get_digest_slot(1) == "indeed:a"
    assert s.get_digest_slot(3) == "indeed:c"
    assert s.get_digest_slot(9) is None
    assert s.digest_slots() == ["indeed:a", "indeed:b", "indeed:c"]
    s.set_digest_slots(["indeed:x"])
    assert s.get_digest_slot(1) == "indeed:x"
    assert s.get_digest_slot(2) is None


def test_crux_cache_roundtrip():
    from cvflow.storage import ApplicationStore
    s = ApplicationStore(":memory:")
    assert s.get_crux("indeed:1") is None
    s.save_crux("indeed:1", '{"job_id":"indeed:1"}')
    assert s.get_crux("indeed:1") == '{"job_id":"indeed:1"}'
    s.save_crux("indeed:1", '{"job_id":"indeed:1","v":2}')  # upsert
    assert s.get_crux("indeed:1") == '{"job_id":"indeed:1","v":2}'


def test_decisions_log_roundtrip():
    from cvflow.storage import ApplicationStore
    s = ApplicationStore(":memory:")
    s.add_decision(job_id="indeed:1", decision="apply", cohort="M", benchmark=80,
                   fit_score=85, role_family="devops", company="Acme",
                   company_type="product", ctc_lpa=18.0, concerns=["FX"])
    s.add_decision(job_id="indeed:2", decision="skip", cohort="N", benchmark=40,
                   fit_score=40, role_family="frontend", company="Svc",
                   company_type="service", ctc_lpa=None, concerns=[])
    rows = s.recent_decisions(10)
    assert len(rows) == 2
    assert rows[0]["decision"] in ("apply", "skip")
    apply_rows = [r for r in rows if r["decision"] == "apply"]
    assert apply_rows[0]["role_family"] == "devops"
    assert apply_rows[0]["ctc_lpa"] == 18.0
