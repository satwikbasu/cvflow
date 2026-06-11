#!/usr/bin/env bash
# cvflow daily discovery digest — invoked by `hermes cron --no-agent`.
# Hermes kills any --no-agent script at ~120 s, but a discovery run takes 10–25 min, so we
# spawn it DETACHED and exit in <1 s (mirrors the /discover hook). The runlock (runlock.py)
# guarantees this daily spawn and a manual /discover can't double-run; the digest reaches
# Telegram from inside the detached process via the notifier.
set -euo pipefail
cd "${CVFLOW_ROOT:-/home/ubuntu/cvflow}"
mkdir -p logs
nohup setsid .venv/bin/python -m cvflow.cron discover \
    >> logs/cron-discover.log 2>&1 < /dev/null &
exit 0
