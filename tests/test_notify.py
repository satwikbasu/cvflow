"""HermesNotifier — builds the hermes send argv; never raises. No real subprocess."""

import subprocess

from cvflow.notify import HermesNotifier


def test_calls_hermes_send_with_expected_argv():
    calls = []

    def fake_runner(argv, **kwargs):
        calls.append((argv, kwargs))
        return None

    HermesNotifier(runner=fake_runner)("hello there")
    argv, kwargs = calls[0]
    assert argv == ["hermes", "send", "--to", "telegram", "--quiet", "hello there"]
    assert kwargs.get("check") is True


def test_custom_target():
    calls = []
    HermesNotifier(target="telegram:123", runner=lambda argv, **k: calls.append(argv))("hi")
    assert calls[0][3] == "telegram:123"


def test_never_raises_on_runner_failure(caplog):
    def boom(argv, **kwargs):
        raise subprocess.CalledProcessError(1, argv)

    # must NOT raise — a notification failure can't be allowed to crash the caller
    HermesNotifier(runner=boom)("important notice")
    assert "important notice" in caplog.text
