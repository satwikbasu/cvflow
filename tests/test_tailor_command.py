"""handle_tailor_command — deterministic single-job /tailor trigger (off the agent loop)."""

from cvflow.tailor_command import handle_tailor_command


class _Job:
    def __init__(self, role, company):
        self.role = role
        self.company = company


class _Store:
    def __init__(self, slots, jobs=None):
        self._slots = slots
        self._jobs = jobs or {}

    def get_digest_slot(self, n):
        return self._slots.get(n)

    def digest_slots(self):
        return list(self._slots.values())

    def get(self, job_id):
        return self._jobs.get(job_id)


def _store():
    return _Store(
        {1: "a:1", 2: "b:2", 4: "d:4"},
        jobs={"d:4": _Job("DevOps Engineer", "Acme")},
    )


def test_single_ordinal_spawns():
    spawned = []
    res = handle_tailor_command(
        args="4", user_id="u", authorized_user_id="u", store=_store(),
        spawn=spawned.append, is_locked=lambda: False,
    )
    assert res.handled and spawned == ["d:4"]
    # ack shows role @ company, not the raw job_id
    assert "DevOps Engineer @ Acme" in res.message and "d:4" not in res.message


def test_multiple_ordinals_rejected():
    spawned = []
    res = handle_tailor_command(
        args="1 2", user_id="u", authorized_user_id="u", store=_store(),
        spawn=spawned.append, is_locked=lambda: False,
    )
    assert res.handled and not spawned and "one job" in res.message.lower()


def test_unauthorized_not_handled():
    res = handle_tailor_command(
        args="1", user_id="intruder", authorized_user_id="u", store=_store(),
        spawn=lambda j: None, is_locked=lambda: False,
    )
    assert res.handled is False


def test_empty_args_shows_usage():
    spawned = []
    res = handle_tailor_command(
        args="   ", user_id="u", authorized_user_id="u", store=_store(),
        spawn=spawned.append, is_locked=lambda: False,
    )
    assert res.handled and not spawned and "usage" in res.message.lower()


def test_unknown_ordinal_rejected_with_distinct_message():
    spawned = []
    res = handle_tailor_command(
        args="99", user_id="u", authorized_user_id="u", store=_store(),
        spawn=spawned.append, is_locked=lambda: False,
    )
    assert res.handled and not spawned
    # distinct from the multi-ordinal "one job" message
    assert "99" in res.message and "one job" not in res.message.lower()


def test_locked_does_not_spawn():
    spawned = []
    res = handle_tailor_command(
        args="4", user_id="u", authorized_user_id="u", store=_store(),
        spawn=spawned.append, is_locked=lambda: True,
    )
    assert res.handled and not spawned and "in progress" in res.message.lower()
