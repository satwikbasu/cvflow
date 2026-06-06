from cvflow.gate import GateResult, handle_gate_command
from cvflow.statemachine import Status
from cvflow.storage import ApplicationStore

USER = 1291545895


def _store_with(status: Status) -> ApplicationStore:
    s = ApplicationStore(":memory:")
    s.add("j1", "Acme", "Engineer", "http://jd")
    if status is Status.PENDING_REVIEW:
        s.set_status("j1", Status.PENDING_REVIEW)
    return s


def test_approve_from_pending_is_only_route_to_approved():
    store = _store_with(Status.PENDING_REVIEW)
    res = handle_gate_command(command="apply", args="j1", user_id=USER,
                              authorized_user_id=USER, store=store)
    assert isinstance(res, GateResult)
    assert res.handled is True
    assert store.get("j1").status is Status.APPROVED
    assert "Approved" in res.message


def test_approve_wrong_state_is_reported_not_silent_and_no_change():
    store = _store_with(Status.DISCOVERED)
    res = handle_gate_command(command="apply", args="j1", user_id=USER,
                              authorized_user_id=USER, store=store)
    assert res.handled is True
    assert res.message and "can't approve" in res.message.lower()
    assert store.get("j1").status is Status.DISCOVERED


def test_approve_unknown_job_reported():
    store = ApplicationStore(":memory:")
    res = handle_gate_command(command="apply", args="nope", user_id=USER,
                              authorized_user_id=USER, store=store)
    assert res.handled is True
    assert "no application" in res.message.lower()


def test_skip_transitions_and_reports():
    store = _store_with(Status.PENDING_REVIEW)
    res = handle_gate_command(command="skip", args="j1", user_id=USER,
                              authorized_user_id=USER, store=store)
    assert store.get("j1").status is Status.SKIPPED
    assert res.handled is True


def test_unauthorized_user_is_silently_ignored_no_state_change():
    store = _store_with(Status.PENDING_REVIEW)
    res = handle_gate_command(command="apply", args="j1", user_id=999,
                              authorized_user_id=USER, store=store)
    assert res.handled is False
    assert res.message is None
    assert store.get("j1").status is Status.PENDING_REVIEW


def test_user_id_compared_as_string_or_int():
    store = _store_with(Status.PENDING_REVIEW)
    res = handle_gate_command(command="apply", args="j1", user_id="1291545895",
                              authorized_user_id=USER, store=store)
    assert res.handled is True
    assert store.get("j1").status is Status.APPROVED


def test_resolve_targets_ordinals_ranges_all_and_raw():
    from cvflow.gate import resolve_targets
    from cvflow.storage import ApplicationStore
    s = ApplicationStore(":memory:")
    s.set_digest_slots(["indeed:a", "indeed:b", "indeed:c"])

    assert resolve_targets("1 2", s) == (["indeed:a", "indeed:b"], [])
    assert resolve_targets("1,3", s) == (["indeed:a", "indeed:c"], [])
    assert resolve_targets("1-3", s) == (["indeed:a", "indeed:b", "indeed:c"], [])
    assert resolve_targets("all", s) == (["indeed:a", "indeed:b", "indeed:c"], [])
    assert resolve_targets("indeed:z", s) == (["indeed:z"], [])
    assert resolve_targets("2 9", s) == (["indeed:b"], ["9"])
    assert resolve_targets("1 1", s) == (["indeed:a"], [])
