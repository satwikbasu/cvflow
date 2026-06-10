"""Hermes command hook: routes /discover to cvflow's deterministic discovery trigger.

Runs in the gateway process, OUTSIDE the agent loop. A run takes ~15-20 min, so this
spawns a detached ``python -m cvflow.cron discover --progress`` (start_new_session=True)
and acks instantly; the detached run streams progress/digest/drop-report via HermesNotifier
(no gateway, no LLM). Unauthorized users -> {} (fall through). This is a *trigger*, never an
approval — the approval-gate invariant is untouched.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from typing import Any


def _repo_root() -> str:
    return os.environ.get("CVFLOW_ROOT", os.path.expanduser("~/cvflow"))


def _ensure_import() -> None:
    root_src = os.path.join(_repo_root(), "src")
    if root_src not in sys.path:
        sys.path.insert(0, root_src)


def _authorized_user_id():
    _ensure_import()
    from cvflow.config import load_config

    cfg = load_config(os.path.join(_repo_root(), "config.yaml"))
    return cfg.telegram.authorized_user_id


def _lock_path() -> str:
    return os.path.join(_repo_root(), "data", "discover.lock")


def _is_locked() -> bool:
    _ensure_import()
    from cvflow.runlock import is_locked

    return is_locked(_lock_path())


def _spawn() -> None:
    root = _repo_root()
    python = os.path.join(root, ".venv", "bin", "python")
    os.makedirs(os.path.join(root, "data"), exist_ok=True)
    log_path = os.path.join(root, "data", f"discover-manual-{int(time.time())}.log")
    # Open the log, hand it to the child, then close our copy: Popen dups the fd into the
    # child, so the detached run keeps writing while the long-lived gateway leaks nothing.
    with open(log_path, "w") as log:
        subprocess.Popen(  # noqa: S603 — fixed argv, no shell
            [python, "-m", "cvflow.cron", "discover", "--progress"],
            cwd=root,
            start_new_session=True,
            stdout=log,
            stderr=subprocess.STDOUT,
        )


async def handle(event_type: str, context: dict[str, Any]) -> dict[str, Any]:
    try:
        _ensure_import()
        from cvflow.discover_command import handle_discover_command

        res = handle_discover_command(
            user_id=context.get("user_id"),
            authorized_user_id=_authorized_user_id(),
            spawn=_spawn,
            is_locked=_is_locked,
        )
        if not res.handled:
            return {}
        return {"decision": "handled", "message": res.message}
    except Exception:  # noqa: BLE001 — never leak a stack trace to the user; fall through
        return {}
