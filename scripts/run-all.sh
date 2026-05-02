#!/usr/bin/env bash
# Start all nine agent backends + the dashboard as background processes,
# each pinned to its own daemon HOME so register.py reads the correct
# api_token. Logs go to /tmp/anet-ci-u<N>/agent.log; PIDs to /tmp/anet-ci-pids.

set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PY="${PY:-$ROOT/.venv/bin/python}"
PIDFILE=/tmp/anet-ci-pids
: > "$PIDFILE"

green() { printf "\033[32m✓\033[0m %s\n" "$*"; }
yellow(){ printf "\033[33m·\033[0m %s\n" "$*"; }

start_agent() {
  local home="$1" api="$2" port="$3" module="$4" name="$5"
  local logfile="$home/agent.log"
  ANET_BASE_URL="http://127.0.0.1:$api" \
    HOME="$home" \
    ANET_TOKEN="$(tr -d '[:space:]' < "$home/.anet/api_token")" \
    "$PY" -m "$module" > "$logfile" 2>&1 &
  echo $! >> "$PIDFILE"
  yellow "$name → :$port  (HOME=$home, log=$logfile)"
}

cd "$ROOT"
start_agent /tmp/anet-ci-u1 14101 7401 agents.translate        "translate-svc       "
start_agent /tmp/anet-ci-u2 14102 7402 agents.extract          "extract-svc         "
start_agent /tmp/anet-ci-u3 14103 7403 agents.sentiment        "sentiment-svc       "
start_agent /tmp/anet-ci-u4 14104 7404 agents.summarise        "summarise-svc       "
start_agent /tmp/anet-ci-u5 14105 7405 agents.classify         "classify-svc        "
start_agent /tmp/anet-ci-u6 14106 7406 agents.orchestrator     "orchestrator-svc    "
start_agent /tmp/anet-ci-u7 14107 7407 agents.factcheck        "factcheck-svc       "
start_agent /tmp/anet-ci-u8 14108 7408 agents.translate_en_zh  "translate-en-zh-svc "
start_agent /tmp/anet-ci-u9 14109 7409 agents.keywords         "keywords-svc        "

# ── alt providers on u10 (auction competitors) ──────────────────────────
# Same skill tags, different (NAME, port, cost, style). Orchestrator runs a
# reverse-auction across providers offering the same skill.
start_alt() {
  local home="$1" api="$2" port="$3" module="$4" name="$5"
  shift 5
  local logfile="$home/agent-${port}.log"
  HOME="$home" \
    ANET_BASE_URL="http://127.0.0.1:$api" \
    ANET_TOKEN="$(tr -d '[:space:]' < "$home/.anet/api_token")" \
    "$@" \
    "$PY" -m "$module" > "$logfile" 2>&1 &
  echo $! >> "$PIDFILE"
  yellow "$name → :$port  (HOME=$home, log=$logfile)"
}

# `env -S` lets us inject the per-alt overrides as one argv element.
start_alt /tmp/anet-ci-u10 14110 7413 agents.sentiment "sentiment-alt-svc   " \
  env SENTIMENT_NAME=sentiment-alt-svc SENTIMENT_PORT=7413 SENTIMENT_PER_CALL=2 SENTIMENT_QUOTE_STYLE=cheap SENTIMENT_BASE_ETA_MS=120
start_alt /tmp/anet-ci-u10 14110 7415 agents.classify "classify-alt-svc    " \
  env CLASSIFY_NAME=classify-alt-svc CLASSIFY_PORT=7415 CLASSIFY_PER_CALL=4 CLASSIFY_QUOTE_STYLE=fast CLASSIFY_BASE_ETA_MS=35
start_alt /tmp/anet-ci-u10 14110 7419 agents.keywords "keywords-alt-svc    " \
  env KEYWORDS_NAME=keywords-alt-svc KEYWORDS_PORT=7419 KEYWORDS_PER_CALL=2 KEYWORDS_QUOTE_STYLE=thorough KEYWORDS_BASE_ETA_MS=110

# Dashboard — not a P2P service, just observes. Points at u1's daemon to discover.
start_agent /tmp/anet-ci-u1 14101 7400 agents.dashboard        "dashboard-svc       "

# Wait for each FastAPI backend's /health to come up.
for port in 7401 7402 7403 7404 7405 7406 7407 7408 7409 7413 7415 7419 7400; do
  for _ in $(seq 1 40); do
    curl -sf -m 1 "http://127.0.0.1:$port/health" >/dev/null 2>&1 && break
    sleep 0.5
  done
  curl -sf -m 1 "http://127.0.0.1:$port/health" >/dev/null 2>&1 \
    && green "agent on :$port healthy" \
    || { echo "agent on :$port did NOT come up; see /tmp/anet-ci-u*/agent.log" >&2; exit 1; }
done

# Give ANS a moment to gossip skill tags across the mesh.
sleep 3
green "all nine agents + dashboard up; PIDs in $PIDFILE"
echo
echo "▶ Open the dashboard: http://127.0.0.1:7400"
