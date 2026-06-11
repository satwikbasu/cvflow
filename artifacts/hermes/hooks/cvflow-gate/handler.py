"""Hermes command hook: routes /apply and /skip to cvflow's deterministic gate.

Runs in the gateway process, OUTSIDE the agent loop. Returns a Hermes
{"decision": "handled", "message": ...} dict so the reply is sent and the brain
never processes the command. Unauthorized users -> {} (fall through). This is
the human-approval path; the LLM agent has no approve tool and cannot reach it.

The command verb is ``/apply`` (not ``/approve``, which is a Hermes built-in) so
there is no collision and no fragile bare-command special-casing.
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

    root = _repo_root()
    cfg = load_config(os.path.join(root, "config.yaml"))
    # config db_path is relative ("data/cvflow.db"); resolve it against the repo root, NOT the
    # gateway's cwd (~/.hermes). Otherwise the gate opens a phantom ~/.hermes/data/cvflow.db with
    # no digest_slots and every /apply <n> returns "unknown" (it never sees the discovery DB).
    db_path = cfg.storage.db_path
    if not os.path.isabs(db_path):
        db_path = os.path.join(root, db_path)
    return ApplicationStore(db_path)


def _authorized_user_id():
    _ensure_import()
    from cvflow.config import load_config

    cfg = load_config(os.path.join(_repo_root(), "config.yaml"))
    return cfg.telegram.authorized_user_id


async def handle(event_type: str, context: dict[str, Any]) -> dict[str, Any]:
    _ensure_import()
    from cvflow.gate import handle_gate_command

    res = handle_gate_command(
        command=str(context.get("command", "")),
        args=str(context.get("args", "")),
        user_id=context.get("user_id"),
        authorized_user_id=_authorized_user_id(),
        store=_build_store(),
    )
    if not res.handled:
        return {}
    return {"decision": "handled", "message": res.message}
