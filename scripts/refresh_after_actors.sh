#!/usr/bin/env bash
# Wait until the ACTOR portion of the retag drains, then stop the (long,
# low-value) event tagging and run a clean full frontier refresh. Events stay
# pending for a later batch. Launch detached under caffeinate:
#   caffeinate -is bash scripts/refresh_after_actors.sh
set -u
ROOT=/Users/carltonbags/Palantino
export PYTHONPATH="$ROOT"

pending_actors() {
  python -c "
import asyncio
from db.session import get_conn
async def m():
    async with get_conn() as c:
        n = await c.fetchval('''select count(*) from nodes n
            where source in ('news_extraction','event_venue') and valid_to is null
              and not (properties ? 'resource_tagged')
              and not exists(select 1 from node_resources nr where nr.node_id=n.id)''')
    print(n)
asyncio.run(m())
"
}

echo "waiter: $(date) — waiting for actor retag to drain"
while true; do
  ap=$(pending_actors 2>/dev/null | tail -1)
  echo "waiter: $(date) — actors pending: ${ap:-?}"
  # residual are usually unparseable nodes that never flag; fire near zero
  if [ -n "${ap:-}" ] && [ "$ap" -le 10 ] 2>/dev/null; then break; fi
  sleep 120
done

echo "waiter: $(date) — actors done; stopping event tagging to free the DB"
pkill -f "scripts.retag_resources" 2>/dev/null
sleep 5

echo "waiter: $(date) — starting full frontier refresh"
python -c "
import asyncio, logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s %(message)s')
from reasoning.candidate_engine import refresh_candidates
print('REFRESH RESULT:', asyncio.run(refresh_candidates()))
"
echo "waiter: $(date) — refresh finished"
