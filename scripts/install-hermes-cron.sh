#!/usr/bin/env bash
# Idempotent Hermes cron installer for cvflow. Reproduces the full daily schedule from a clone:
# copies the cron scripts into ~/.hermes/scripts/, removes any existing cvflow-* cron jobs, then
# re-creates them from `python -m cvflow.cron print-hermes-schedule` (config-derived, UTC). Safe
# to run repeatedly — the result is always exactly the four cvflow-* jobs.
set -euo pipefail

ROOT="${CVFLOW_ROOT:-$HOME/cvflow}"
HERMES_SCRIPTS="${HERMES_SCRIPTS_DIR:-$HOME/.hermes/scripts}"
cd "$ROOT"

mkdir -p "$HERMES_SCRIPTS"
cp artifacts/hermes/scripts/cvflow-*.sh "$HERMES_SCRIPTS"/
chmod +x "$HERMES_SCRIPTS"/cvflow-*.sh

# Delete existing cvflow-* jobs so re-running is idempotent (hermes cron list prints names).
for name in $(hermes cron list 2>/dev/null | grep -oE 'cvflow-[a-z-]+' | sort -u); do
    echo "removing existing cron job: $name"
    hermes cron delete "$name" || true
done

# Re-create from the config-derived schedule.
while IFS= read -r line; do
    [ -z "$line" ] && continue
    echo "+ $line"
    eval "$line"
done < <(.venv/bin/python -m cvflow.cron print-hermes-schedule)

echo "done — current cvflow cron jobs:"
hermes cron list | grep cvflow || true
