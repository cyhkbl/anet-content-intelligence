#!/usr/bin/env bash
# Spin up nine independent anet daemons on this laptop and seed cross-node
# credit ledgers so priced calls don't hit 402.
#
# Layout (matches CLAUDE.md / SPEC.md + IMPROVE.md):
#   /tmp/anet-ci-u1   API=:14101  P2P=:14201   (translate-svc)
#   /tmp/anet-ci-u2   API=:14102  P2P=:14202   (extract-svc)
#   /tmp/anet-ci-u3   API=:14103  P2P=:14203   (sentiment-svc)
#   /tmp/anet-ci-u4   API=:14104  P2P=:14204   (summarise-svc)
#   /tmp/anet-ci-u5   API=:14105  P2P=:14205   (classify-svc)
#   /tmp/anet-ci-u6   API=:14106  P2P=:14206   (orchestrator-svc)
#   /tmp/anet-ci-u7   API=:14107  P2P=:14207   (factcheck-svc)
#   /tmp/anet-ci-u8   API=:14108  P2P=:14208   (translate-en-zh-svc)
#   /tmp/anet-ci-u9   API=:14109  P2P=:14209   (keywords-svc)
#   /tmp/anet-ci-u10  API=:14110  P2P=:14210   (alt providers — auction competitors)
#
# Daemons 2-10 bootstrap off daemon-1 to form one mesh.

set -euo pipefail
ANET="${ANET:-anet}"

API=(14101 14102 14103 14104 14105 14106 14107 14108 14109 14110)
P2P=(14201 14202 14203 14204 14205 14206 14207 14208 14209 14210)
HOMES=(
  /tmp/anet-ci-u1 /tmp/anet-ci-u2 /tmp/anet-ci-u3
  /tmp/anet-ci-u4 /tmp/anet-ci-u5 /tmp/anet-ci-u6
  /tmp/anet-ci-u7 /tmp/anet-ci-u8 /tmp/anet-ci-u9
  /tmp/anet-ci-u10
)
N=${#HOMES[@]}

green() { printf "\033[32m✓\033[0m %s\n" "$*"; }
red()   { printf "\033[31m✗\033[0m %s\n" "$*" >&2; }
yellow(){ printf "\033[33m·\033[0m %s\n" "$*"; }

write_config() {
  local dir="$1" api="$2" p2p="$3" boot_csv="$4"
  mkdir -p "$dir/.anet"
  cat > "$dir/.anet/config.json" <<EOF
{
  "listen_addrs": ["/ip4/127.0.0.1/tcp/$p2p"],
  "bootstrap_peers": [$boot_csv],
  "api_port": $api,
  "relay_enabled": false,
  "topics_auto_join": ["/anet/ans", "/anet/credits"],
  "bt_dht": {"enabled": false},
  "overlay": {"enabled": false}
}
EOF
}

api_alive() { curl -sf --noproxy '*' -m 1 "http://127.0.0.1:$1/api/status" >/dev/null 2>&1; }
wait_alive() {
  for _ in $(seq 1 60); do api_alive "$1" && return 0; sleep 1; done
  return 1
}

cmd_start() {
  command -v "$ANET" >/dev/null || { red "anet not on PATH (set ANET=…)"; exit 1; }

  for d in "${HOMES[@]}"; do rm -rf "$d"; mkdir -p "$d"; done
  for p in "${API[@]}" "${P2P[@]}"; do
    lsof -ti tcp:"$p" 2>/dev/null | xargs -r kill -9 2>/dev/null || true
  done

  # ── daemon 1 (bootstrap) ────────────────────────────────────────────
  write_config "${HOMES[0]}" "${API[0]}" "${P2P[0]}" ""
  HOME="${HOMES[0]}" "$ANET" daemon > "${HOMES[0]}/daemon.log" 2>&1 &
  wait_alive "${API[0]}" || { red "daemon-1 failed"; tail -20 "${HOMES[0]}/daemon.log"; exit 1; }
  PEER1=$(curl -sf --noproxy '*' "http://127.0.0.1:${API[0]}/api/status" \
          | python3 -c "import sys,json;print(json.load(sys.stdin)['peer_id'])")
  green "u1 alive PEER=$PEER1"

  # ── daemons 2-N (bootstrapped) ──────────────────────────────────────
  for i in $(seq 1 $((N-1))); do
    BOOT="\"/ip4/127.0.0.1/tcp/${P2P[0]}/p2p/$PEER1\""
    write_config "${HOMES[$i]}" "${API[$i]}" "${P2P[$i]}" "$BOOT"
    HOME="${HOMES[$i]}" "$ANET" daemon > "${HOMES[$i]}/daemon.log" 2>&1 &
    wait_alive "${API[$i]}" || { red "u$((i+1)) failed"; tail -20 "${HOMES[$i]}/daemon.log"; exit 1; }
    green "u$((i+1)) alive on :${API[$i]}"
  done

  # ── seed cross-node credits ─────────────────────────────────────────
  yellow "waiting for mesh convergence…"
  target_peers=$((N-1))
  for _ in $(seq 1 30); do
    p=$(curl -sf --noproxy '*' "http://127.0.0.1:${API[0]}/api/status" \
        | python3 -c "import sys,json;print(json.load(sys.stdin).get('peers',0))" 2>/dev/null || echo 0)
    [ "${p:-0}" -ge "$target_peers" ] && break
    sleep 1
  done

  DIDS=()
  TOKS=()
  for i in $(seq 0 $((N-1))); do
    DIDS+=("$(curl -sf --noproxy '*' "http://127.0.0.1:${API[$i]}/api/status" \
              | python3 -c "import sys,json;print(json.load(sys.stdin)['did'])")")
    TOKS+=("$(tr -d '[:space:]' < "${HOMES[$i]}/.anet/api_token")")
  done

  fail=0
  total=0
  for i in $(seq 0 $((N-1))); do
    for j in $(seq 0 $((N-1))); do
      [ "$i" = "$j" ] && continue
      total=$((total+1))
      r=$(curl -sf --noproxy '*' \
            -H "Authorization: Bearer ${TOKS[$i]}" \
            -H "Content-Type: application/json" \
            -X POST "http://127.0.0.1:${API[$i]}/api/credits/transfer" \
            -d "{\"from\":\"${DIDS[$i]}\",\"to\":\"${DIDS[$j]}\",\"amount\":500,\"reason\":\"ci-seed\"}" \
            2>/dev/null || echo "")
      echo "$r" | grep -q sender_event || fail=$((fail+1))
    done
  done
  if [ "$fail" = 0 ]; then
    green "seeded $total cross-pair transfers (500 shells each)"
  else
    yellow "seed transfers: $fail / $total failed (priced calls may 402)"
  fi
  cat <<EOF

Daemon URLs:
  u1 translate         http://127.0.0.1:${API[0]}   HOME=${HOMES[0]}
  u2 extract           http://127.0.0.1:${API[1]}   HOME=${HOMES[1]}
  u3 sentiment         http://127.0.0.1:${API[2]}   HOME=${HOMES[2]}
  u4 summarise         http://127.0.0.1:${API[3]}   HOME=${HOMES[3]}
  u5 classify          http://127.0.0.1:${API[4]}   HOME=${HOMES[4]}
  u6 orchestrator      http://127.0.0.1:${API[5]}   HOME=${HOMES[5]}
  u7 factcheck         http://127.0.0.1:${API[6]}   HOME=${HOMES[6]}
  u8 translate-en-zh   http://127.0.0.1:${API[7]}   HOME=${HOMES[7]}
  u9 keywords          http://127.0.0.1:${API[8]}   HOME=${HOMES[8]}
  u10 alt-providers    http://127.0.0.1:${API[9]}   HOME=${HOMES[9]}

Next: bash scripts/run-all.sh    # start the 9 agents + dashboard
EOF
}

cmd_stop() {
  for p in "${API[@]}" "${P2P[@]}" 7400 7401 7402 7403 7404 7405 7406 7407 7408 7409 7413 7415 7419; do
    lsof -ti tcp:"$p" 2>/dev/null | xargs -r kill -9 2>/dev/null || true
  done
  pkill -f "anet daemon" 2>/dev/null || true
  green "killed all daemons + agent backends"
}

cmd_status() {
  for i in $(seq 0 $((N-1))); do
    api_alive "${API[$i]}" \
      && green "u$((i+1)) alive  on :${API[$i]}" \
      || red   "u$((i+1)) DOWN   on :${API[$i]}"
  done
}

case "${1:-start}" in
  start)  cmd_start ;;
  stop)   cmd_stop ;;
  status) cmd_status ;;
  *) echo "usage: $0 {start|stop|status}" >&2; exit 2 ;;
esac
