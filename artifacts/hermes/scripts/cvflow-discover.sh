#!/usr/bin/env bash
# cvflow daily discovery digest — invoked by `hermes cron --no-agent`.
set -euo pipefail
cd /home/ubuntu/cvflow
exec /home/ubuntu/cvflow/.venv/bin/python -m cvflow.cron discover
