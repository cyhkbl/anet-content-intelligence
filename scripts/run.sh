#!/usr/bin/env bash
# One-shot end-to-end: stop anything stale → bring up daemons → start agents
# + protocol services → run the demo client. Exits non-zero on any failure.

set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

PY="${PY:-$ROOT/.venv/bin/python}"

bold()  { printf "\n\033[1;36m═══ %s ═══\033[0m\n" "$*"; }

bold "1/5  Stopping any prior daemons / agents"
bash "$ROOT/scripts/stop.sh" || true

bold "2/5  Starting 13 anet daemons + seeding credits"
bash "$ROOT/scripts/setup-nodes.sh" start

bold "3/5  Starting agents + Shell Market Protocol services"
bash "$ROOT/scripts/run-all.sh"

bold "4/5  Seeding live market activity (auctions, settlements, reputation)"
TOKEN="$(tr -d '[:space:]' < /tmp/anet-ci-u1/.anet/api_token)"
HOME=/tmp/anet-ci-u1 ANET_BASE_URL="http://127.0.0.1:14101" ANET_TOKEN="$TOKEN" \
  "$PY" "$ROOT/scripts/seed_market.py" --rounds "${SEED_ROUNDS:-12}" || \
  echo "(seed-market reported issues — protocol still demoable)"

bold "5/5  Running demo client (orchestrator drives the protocol)"
if [ "$#" -ge 1 ] && [ -n "$1" ]; then
  HOME=/tmp/anet-ci-u1 ANET_BASE_URL="http://127.0.0.1:14101" ANET_TOKEN="$TOKEN" \
    "$PY" "$ROOT/client.py" "$1"
else
  HOME=/tmp/anet-ci-u1 ANET_BASE_URL="http://127.0.0.1:14101" ANET_TOKEN="$TOKEN" \
    "$PY" "$ROOT/client.py"
fi

PUB="$(tailscale ip 2>/dev/null | head -1 | tr -d '[:space:]' || echo 127.0.0.1)"
echo
echo "✓ pipeline succeeded — agents + protocol services still running."
echo "▶ Shell Market Protocol Dashboard:  http://${PUB}:7422"
echo "▶ Pipeline Observer Dashboard:      http://${PUB}:7400"
echo "▶ Live event feed (SSE):            http://${PUB}:7426/v1/stream"
echo "▶ Try another prompt:  .venv/bin/python client.py 'Tesla stock fell 5%.'"
echo "▶ Re-seed market:      .venv/bin/python scripts/seed_market.py --rounds 20"
echo "▶ Clean up:            bash scripts/stop.sh"
