#!/usr/bin/env bash
# One-shot diagnostic (P1E-1): is the ATS recon instrument actually classifying
# *newly* discovered jobs, or tagging everything 'other'? Writes a dated report
# to $HOME so the founder notices it on login. Scheduled ~1 week after the
# tagging deploy via the user crontab; DELETE that crontab line after reviewing.
#
# The verdict only means something once the 7-day window contains jobs discovered
# *after* tagging went live (gateway restart on 2026-06-13). A SUSPICIOUS verdict
# on jobs that pre-date tagging is expected noise, not a real signal.
set -euo pipefail
cd "${CVFLOW_ROOT:-$HOME/cvflow}"
out="$HOME/recon-healthcheck-$(date +%F).txt"
{
    .venv/bin/python -m cvflow.recon healthcheck 2>&1 || echo "healthcheck failed (see above)"
    echo
    echo "(generated $(date -Is) by scripts/recon-healthcheck.sh — delete the crontab line once reviewed)"
} >"$out"
