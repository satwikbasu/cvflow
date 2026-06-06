#!/usr/bin/env bash
# cvflow weekly preference-learning suggestions — invoked by `hermes cron --no-agent`.
set -euo pipefail
cd /home/ubuntu/cvflow
exec /home/ubuntu/cvflow/.venv/bin/python -m cvflow.cron learn
