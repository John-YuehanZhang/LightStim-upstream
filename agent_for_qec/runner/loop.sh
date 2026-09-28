#!/bin/bash
# Run phases back to back, re-selecting the account between phases.
#   loop.sh N_PHASES [MODEL=opus] [MAX_TURNS=250]
# Stops when PROGRESS.md says "STATUS: DONE". If no account has quota, sleeps
# 30 min and retries (up to 12 times).
set -u
REPO=/nvme2n1/yuehan_zhang/LightStim-upstream
N=${1:-1}; MODEL=${2:-opus}; MT=${3:-250}
i=0; waits=0
while [ $i -lt $N ]; do
  grep -q '^STATUS: DONE' "$REPO/agent_for_qec/PROGRESS.md" && { echo "PROGRESS says DONE"; break; }
  "$REPO/agent_for_qec/runner/run_phase.sh" "$MODEL" "$MT"; rc=$?
  if [ $rc -eq 3 ]; then
    waits=$((waits+1)); [ $waits -gt 12 ] && { echo "no quota for 6h, giving up"; exit 3; }
    echo "no quota for $MODEL on any account; sleeping 30 min"; sleep 1800; continue
  fi
  i=$((i+1)); echo "phase $i/$N finished rc=$rc"
done
