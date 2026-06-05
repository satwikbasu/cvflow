"""Outbound user notices via `hermes send` (Phase 11).

`hermes send` reuses the gateway's Telegram credentials, needs no running gateway
and no LLM. This is the single outbound-notice path for cvflow (mid-flow crash/OTP
notices + scheduled digest/heartbeat/sweep). It NEVER raises: a notification
failure must not crash the automation that emitted it (invariant 3 stays a log +
best-effort send, never a silent crash).
"""

from __future__ import annotations

import logging
import subprocess
from collections.abc import Callable
from typing import Any

logger = logging.getLogger("cvflow.notify")


class HermesNotifier:
    """Callable that pushes a message to Telegram via `hermes send`."""

    def __init__(
        self, target: str = "telegram", runner: Callable[..., Any] = subprocess.run
    ) -> None:
        self._target = target
        self._runner = runner

    def __call__(self, message: str) -> None:
        try:
            self._runner(
                ["hermes", "send", "--to", self._target, "--quiet", message],
                check=True,
            )
        except Exception as exc:  # noqa: BLE001 — notice must not crash the caller
            logger.warning("hermes send failed (%s); message was: %s", exc, message)
