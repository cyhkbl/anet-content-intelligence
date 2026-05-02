#!/usr/bin/env bash
# One-shot end-to-end: stop anything stale → bring up daemons → start agents
# + dashboard → run the demo client. Exits non-zero if any step fails.

set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

PY="${PY:-$ROOT/.venv/bin/python}"

bold()  { printf "\n\033[1;36m═══ %s ═══\033[0m\n" "$*"; }

bold "1/4  Stopping any prior daemons / agents"
bash "$ROOT/scripts/stop.sh" || true

bold "2/4  Starting 9 anet daemons + seeding credits"
bash "$ROOT/scripts/setup-nodes.sh" start

bold "3/4  Starting 9 FastAPI agents + dashboard"
bash "$ROOT/scripts/run-all.sh"

bold "4/4  Running demo client (orchestrator auto-composes the pipeline)"
# Pin client to u1's daemon so the orchestrator (on u6) is reached cross-node.
TOKEN="$(tr -d '[:space:]' < /tmp/anet-ci-u1/.anet/api_token)"
if [ "$#" -ge 1 ] && [ -n "$1" ]; then
  HOME=/tmp/anet-ci-u1 ANET_BASE_URL="http://127.0.0.1:14101" ANET_TOKEN="$TOKEN" \
    "$PY" "$ROOT/client.py" "$1"
else
  HOME=/tmp/anet-ci-u1 ANET_BASE_URL="http://127.0.0.1:14101" ANET_TOKEN="$TOKEN" \
    "$PY" "$ROOT/client.py"
fi

echo
echo "✓ pipeline succeeded — agents + dashboard still running."
echo "▶ Dashboard: http://127.0.0.1:7400"
echo "▶ Try another prompt: .venv/bin/python client.py 'Tesla stock fell 5%.'"
echo "▶ Clean up: bash scripts/stop.sh"
