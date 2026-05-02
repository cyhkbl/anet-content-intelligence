#!/usr/bin/env bash
# Stop everything started by setup-nodes.sh + run-all.sh.

set -uo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"

green() { printf "\033[32m✓\033[0m %s\n" "$*"; }

# Kill agent processes recorded by run-all.sh.
if [ -f /tmp/anet-ci-pids ]; then
  while read -r pid; do
    [ -n "$pid" ] && kill "$pid" 2>/dev/null || true
  done < /tmp/anet-ci-pids
  rm -f /tmp/anet-ci-pids
fi

# Kill anything bound to our agent, dashboard, or daemon ports.
for p in 7400 7401 7402 7403 7404 7405 7406 7407 7408 7409 7413 7415 7419 \
         14101 14102 14103 14104 14105 14106 14107 14108 14109 14110 \
         14201 14202 14203 14204 14205 14206 14207 14208 14209 14210; do
  lsof -ti tcp:"$p" 2>/dev/null | xargs -r kill -9 2>/dev/null || true
done

# Belt-and-braces: kill any remaining anet daemons.
pkill -f "anet daemon" 2>/dev/null || true

green "stopped agents + daemons"
