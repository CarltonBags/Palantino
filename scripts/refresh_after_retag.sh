#!/usr/bin/env bash
# Wait for the resource re-tag backfill to finish, then run a full frontier
# refresh so the synergy candidate pairs reflect the new tags. Meant to be
# launched detached under caffeinate:
#   caffeinate -is bash scripts/refresh_after_retag.sh
set -u
ROOT=/Users/carltonbags/Palantino
export PYTHONPATH="$ROOT"

echo "waiter: $(date) — waiting for retag backfill to exit"
# block while the retag process is alive (grep its module path)
while pgrep -f "scripts.retag_resources" >/dev/null 2>&1; do
  sleep 60
done
echo "waiter: $(date) — retag gone, starting full frontier refresh"

python -c "
import asyncio, logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s %(message)s')
from reasoning.candidate_engine import refresh_candidates
print('REFRESH RESULT:', asyncio.run(refresh_candidates()))
"
echo "waiter: $(date) — refresh finished"
