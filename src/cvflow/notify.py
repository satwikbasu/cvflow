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
