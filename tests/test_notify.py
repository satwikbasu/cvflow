"""HermesNotifier — builds the hermes send argv; never raises. No real subprocess."""

import subprocess

from cvflow.notify import HermesNotifier


def test_calls_hermes_send_with_expected_argv():
    calls = []

    def fake_runner(argv, **kwargs):
        calls.append((argv, kwargs))
        return None

    HermesNotifier(runner=fake_runner, binary="hermes")("hello there")
    argv, kwargs = calls[0]
    assert argv == ["hermes", "send", "--to", "telegram", "--quiet", "hello there"]
    assert kwargs.get("check") is True


def test_resolves_hermes_to_absolute_path_when_on_path(monkeypatch):
    # bare "hermes" fails under systemd/cron when ~/.local/bin isn't on PATH; resolve it.
    import cvflow.notify as notify
    monkeypatch.setattr(notify.shutil, "which", lambda _: "/home/ubuntu/.local/bin/hermes")
    calls = []
    notify.HermesNotifier(runner=lambda argv, **k: calls.append(argv))("hi")
    assert calls[0][0] == "/home/ubuntu/.local/bin/hermes"


def test_custom_target():
    calls = []
    HermesNotifier(target="telegram:123", runner=lambda argv, **k: calls.append(argv))("hi")
    assert calls[0][3] == "telegram:123"


def test_never_raises_on_runner_failure():
    attempted = []

    def boom(argv, **kwargs):
        attempted.append(argv)
        raise subprocess.CalledProcessError(1, argv)

    # must NOT raise — a notification failure can't be allowed to crash the caller
    HermesNotifier(runner=boom)("important notice")
    assert attempted  # the send was attempted; the failure was swallowed (logged)


def test_telegram_document_sender_posts_multipart(tmp_path):
    from cvflow.notify import TelegramDocumentSender

    pdf = tmp_path / "r.pdf"
    pdf.write_bytes(b"%PDF-1.5")
    calls = []

    def fake_post(url, **kwargs):
        calls.append((url, kwargs))
        class _Resp:
            def raise_for_status(self):
                return None
        return _Resp()

    TelegramDocumentSender("TOK", 4242, poster=fake_post)(str(pdf), "caption here")
    url, kwargs = calls[0]
    assert url == "https://api.telegram.org/botTOK/sendDocument"
    assert kwargs["data"] == {"chat_id": 4242, "caption": "caption here"}
    assert "document" in kwargs["files"]


def test_telegram_document_sender_never_raises_on_failure(tmp_path):
    from cvflow.notify import TelegramDocumentSender

    pdf = tmp_path / "r.pdf"
    pdf.write_bytes(b"%PDF-1.5")

    def boom(url, **kwargs):
        raise RuntimeError("network down")

    # best-effort: a delivery failure must not crash the tailoring run
    TelegramDocumentSender("TOK", 1, poster=boom)(str(pdf), "cap")


def test_telegram_document_sender_missing_file_does_not_raise():
    from cvflow.notify import TelegramDocumentSender

    sent = []
    TelegramDocumentSender("TOK", 1, poster=lambda *a, **k: sent.append(1))("/no/such.pdf")
    assert not sent  # open() failed before posting; swallowed, not raised
