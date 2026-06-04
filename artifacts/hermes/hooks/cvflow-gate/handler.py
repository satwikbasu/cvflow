"""Hermes command hook: routes /approve and /skip to cvflow's deterministic gate.

Runs in the gateway process, OUTSIDE the agent loop. Returns a Hermes
{"decision": "handled", "message": ...} dict so the reply is sent and the brain
never processes the command. Unauthorized users -> {} (fall through). This is
the human-approval path; the LLM agent has no approve tool and cannot reach it.
"""

from __future__ import annotations

import os
import sys
from typing import Any


def _repo_root() -> str:
    return os.environ.get("CVFLOW_ROOT", os.path.expanduser("~/cvflow"))


def _ensure_import() -> None:
    root_src = os.path.join(_repo_root(), "src")
    if root_src not in sys.path:
        sys.path.insert(0, root_src)


def _build_store():
    _ensure_import()
    from cvflow.config import load_config
    from cvflow.storage import ApplicationStore

    cfg = load_config(os.path.join(_repo_root(), "config.yaml"))
    return ApplicationStore(cfg.storage.db_path)


def _authorized_user_id():
    _ensure_import()
    from cvflow.config import load_config

    cfg = load_config(os.path.join(_repo_root(), "config.yaml"))
    return cfg.telegram.authorized_user_id


async def handle(event_type: str, context: dict[str, Any]) -> dict[str, Any]:
    _ensure_import()
    from cvflow.gate import handle_gate_command

    command = str(context.get("command", ""))
    args = str(context.get("args", "")).strip()
    # /approve is also a Hermes built-in (tool-confirm). A bare /approve (no
    # job_id) belongs to that built-in flow — fall through so we don't hijack it.
    # Only /approve <job_id> is the cvflow gate.
    if command == "approve" and not args:
        return {}

    res = handle_gate_command(
        command=command,
        args=args,
        user_id=context.get("user_id"),
        authorized_user_id=_authorized_user_id(),
        store=_build_store(),
    )
    if not res.handled:
        return {}
    return {"decision": "handled", "message": res.message}
