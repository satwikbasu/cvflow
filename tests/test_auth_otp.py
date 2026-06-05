"""OtpCoordinator — request/provide/expire_overdue with an injected clock. No network."""

from datetime import UTC, datetime, timedelta

from cvflow.auth import OtpCoordinator
from cvflow.statemachine import Status
from cvflow.storage import ApplicationStore


def _store():
    s = ApplicationStore(":memory:")
    s.add("indeed:1", "Acme", "Backend", "https://jobs/1")
    s.set_status("indeed:1", Status.PENDING_REVIEW)
    s.approve("indeed:1")  # status APPROVED (an OTP only happens mid-application)
    return s


def _coord(store, notes, *, clock):
    return OtpCoordinator(
        store=store, notify=lambda m: notes.append(m),
        timeout_minutes=15, now=lambda: clock["t"],
    )


def test_request_sets_deadline_and_notifies():
    store = _store()
    notes = []
    t0 = datetime(2026, 6, 5, 10, 0, tzinfo=UTC)
    coord = _coord(store, notes, clock={"t": t0})
    out = coord.request("indeed:1", "burner@example.com")
    assert out["needs_otp"] is True
    assert out["destination"] == "burner@example.com"
    assert store.get("indeed:1").otp_deadline == (t0 + timedelta(minutes=15)).isoformat()
    assert any("burner@example.com" in n for n in notes)


def test_provide_on_time_returns_otp_and_clears_deadline():
    store = _store()
    notes = []
    clock = {"t": datetime(2026, 6, 5, 10, 0, tzinfo=UTC)}
    coord = _coord(store, notes, clock=clock)
    coord.request("indeed:1", "burner@example.com")
    clock["t"] += timedelta(minutes=5)  # within 15
    code = coord.provide("indeed:1", "123456")
    assert code == "123456"
    assert store.get("indeed:1").otp_deadline is None
    assert store.get("indeed:1").status == Status.APPROVED  # still mid-application


def test_provide_late_marks_otp_timeout_and_returns_none():
    store = _store()
    notes = []
    clock = {"t": datetime(2026, 6, 5, 10, 0, tzinfo=UTC)}
    coord = _coord(store, notes, clock=clock)
    coord.request("indeed:1", "burner@example.com")
    clock["t"] += timedelta(minutes=20)  # past 15
    code = coord.provide("indeed:1", "123456")
    assert code is None
    assert store.get("indeed:1").status == Status.OTP_TIMEOUT
    assert store.get("indeed:1").otp_deadline is None
    assert any("timed out" in n.lower() or "expired" in n.lower() for n in notes)


def test_expire_overdue_sweeps_only_overdue_approved_apps():
    store = _store()
    store.add("indeed:2", "Globex", "Platform", "https://jobs/2")
    store.set_status("indeed:2", Status.PENDING_REVIEW)
    store.approve("indeed:2")
    notes = []
    clock = {"t": datetime(2026, 6, 5, 10, 0, tzinfo=UTC)}
    coord = _coord(store, notes, clock=clock)
    coord.request("indeed:1", "a@x.com")          # deadline 10:15
    clock["t"] = datetime(2026, 6, 5, 10, 10, tzinfo=UTC)
    coord.request("indeed:2", "b@x.com")          # deadline 10:25
    clock["t"] = datetime(2026, 6, 5, 10, 20, tzinfo=UTC)  # job1 overdue, job2 not
    expired = coord.expire_overdue()
    assert expired == ["indeed:1"]
    assert store.get("indeed:1").status == Status.OTP_TIMEOUT
    assert store.get("indeed:2").status == Status.APPROVED
    assert store.get("indeed:2").otp_deadline is not None
