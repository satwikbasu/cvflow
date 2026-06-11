#!/usr/bin/env bash
# cvflow daily discovery digest — invoked by `hermes cron --no-agent`.
#
# A discovery run takes 10–25 min, but (a) Hermes kills any --no-agent script at ~120 s and
# (b) `systemctl restart hermes-gateway` SIGKILLs the gateway's whole cgroup (KillMode=mixed) —
# which is what killed a 15-min run mid-flight on 2026-06-11. So we launch the run in a transient
# *user* systemd unit: it lives under user@.service's cgroup, NOT hermes-gateway.service's, so it
# survives BOTH the 120 s script kill (this wrapper returns in <1 s) AND a gateway restart.
# Requires user-manager lingering once per box: `loginctl enable-linger ubuntu`.
#
# The runlock (runlock.py) still guarantees this daily spawn and a manual /discover can't
# double-run; the digest reaches Telegram from inside the detached process via the notifier.
set -euo pipefail
ROOT="${CVFLOW_ROOT:-/home/ubuntu/cvflow}"
cd "$ROOT"
mkdir -p logs

# Talk to the user manager even from the gateway's system-service context (no session env).
export XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}"
export DBUS_SESSION_BUS_ADDRESS="${DBUS_SESSION_BUS_ADDRESS:-unix:path=${XDG_RUNTIME_DIR}/bus}"

if ! systemd-run --user --collect --unit="cvflow-discover-$(date +%s)" \
        bash -c "cd '$ROOT' && exec .venv/bin/python -m cvflow.cron discover \
            >> logs/cron-discover.log 2>&1" 2>>logs/cron-discover.log; then
    # Fallback (no user manager / lingering): plain detached run. Escapes the 120 s kill but
    # not a gateway restart — still better than dying inline, and never silent (logged).
    echo "$(date -Is) systemd-run --user failed; falling back to setsid" >> logs/cron-discover.log
    nohup setsid .venv/bin/python -m cvflow.cron discover \
        >> logs/cron-discover.log 2>&1 < /dev/null &
fi
exit 0
