"""Outbound user notices via `hermes send` (Phase 11).

`hermes send` reuses the gateway's Telegram credentials, needs no running gateway
and no LLM. This is the single outbound-notice path for cvflow (mid-flow crash/OTP
notices + scheduled digest/heartbeat/sweep). It NEVER raises: a notification
failure must not crash the automation that emitted it (invariant 3 stays a log +
best-effort send, never a silent crash).
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
from collections.abc import Callable
from typing import Any

logger = logging.getLogger("cvflow.notify")


def _resolve_hermes() -> str:
    """Absolute path to the `hermes` binary.

    Under systemd/cron the subprocess PATH often omits `~/.local/bin` (where Hermes's
    single-curl installer puts the CLI), so a bare ``"hermes"`` raises FileNotFoundError
    and the notice is lost (the digest then only reaches the terminal). Resolve it to an
    absolute path, falling back to the known install locations.
    """
    found = shutil.which("hermes")
    if found:
        return found
    for candidate in (
        os.path.expanduser("~/.local/bin/hermes"),
        "/usr/local/bin/hermes",
        "/usr/bin/hermes",
    ):
        if os.path.exists(candidate):
            return candidate
    return "hermes"  # last resort — let it fail loudly-but-logged, not silently


class HermesNotifier:
    """Callable that pushes a message to Telegram via `hermes send`."""

    def __init__(
        self,
        target: str = "telegram",
        runner: Callable[..., Any] = subprocess.run,
        binary: str | None = None,
    ) -> None:
        self._target = target
        self._runner = runner
        self._binary = binary or _resolve_hermes()

    def __call__(self, message: str) -> None:
        try:
            self._runner(
                [self._binary, "send", "--to", self._target, "--quiet", message],
                check=True,
            )
        except Exception as exc:  # noqa: BLE001 — notice must not crash the caller
            logger.warning("hermes send failed (%s); message was: %s", exc, message)


class TelegramDocumentSender:
    """Uploads a file to the Telegram chat via the Bot API ``sendDocument``.

    ``hermes send`` is text-only, so résumé PDFs go straight through the Bot API using
    cvflow's own bot token + chat id (no extra cost, no running gateway needed). Best-effort:
    a delivery failure logs and returns — it must never crash the tailoring run (invariant 3).
    """

    def __init__(
        self,
        bot_token: str,
        chat_id: int,
        *,
        poster: Callable[..., Any] | None = None,
        timeout: int = 60,
    ) -> None:
        self._token = bot_token
        self._chat_id = chat_id
        self._poster = poster
        self._timeout = timeout

    def __call__(self, path: str, caption: str = "") -> None:
        try:
            poster = self._poster
            if poster is None:
                import requests

                poster = requests.post
            url = f"https://api.telegram.org/bot{self._token}/sendDocument"
            with open(path, "rb") as fh:
                resp = poster(
                    url,
                    data={"chat_id": self._chat_id, "caption": caption},
                    files={"document": fh},
                    timeout=self._timeout,
                )
            raise_for_status = getattr(resp, "raise_for_status", None)
            if callable(raise_for_status):
                raise_for_status()
        except Exception as exc:  # noqa: BLE001 — best-effort; never crash the tailoring run
            logger.warning("telegram sendDocument failed for %s: %s", path, exc)
