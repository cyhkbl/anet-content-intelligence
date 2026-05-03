#!/usr/bin/env bash
# Deploy all services to the public ANS mesh via the main daemon.
# Auto-detects Tailscale IP so registered endpoints are externally reachable.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PY="$ROOT/.venv/bin/python"
cd "$ROOT"

export ANET_BASE_URL=http://127.0.0.1:14101
PUBLIC_HOST="${PUBLIC_HOST:-$(tailscale ip 2>/dev/null | head -1 | tr -d '[:space:]' || echo 127.0.0.1)}"
PUBLIC_HOST="${PUBLIC_HOST:-127.0.0.1}"
export PUBLIC_HOST
export LISTEN_HOST="${LISTEN_HOST:-0.0.0.0}"

green() { printf "\033[32m✓\033[0m %s\n" "$*"; }
yellow() { printf "\033[33m·\033[0m %s\n" "$*"; }

yellow "advertising endpoints as http://${PUBLIC_HOST}:<port>"

# Start NLP services
start_svc() {
  local module="${1:-}" name="${2:-}" logfile="/tmp/deploy-${2:-unknown}.log"
  PUBLIC_HOST="$PUBLIC_HOST" LISTEN_HOST="$LISTEN_HOST" \
    "$PY" -m "$module" > "$logfile" 2>&1 &
  echo $! >> /tmp/deploy-pids
  yellow "$name → started (log=$logfile)"
}

rm -f /tmp/deploy-pids

start_svc agents.translate         translate-svc
start_svc agents.extract           extract-svc
start_svc agents.sentiment         sentiment-svc
start_svc agents.summarise         summarise-svc
start_svc agents.classify          classify-svc
start_svc agents.orchestrator      orchestrator-svc
start_svc agents.factcheck         factcheck-svc
start_svc agents.translate_en_zh   translate-en-zh-svc
start_svc agents.keywords          keywords-svc

# Protocol services (8 total — Shell Market Protocol)
start_svc agents.reputation        reputation-svc
start_svc agents.auction           auction-svc
start_svc agents.market_dashboard  market-dashboard-svc
start_svc agents.settlement        settlement-svc
start_svc agents.dispute           dispute-svc
start_svc agents.quote_broker      quote-broker-svc
start_svc agents.market_feed       market-feed-svc
start_svc agents.provider_registry provider-registry-svc

# Wait for all to come up
yellow "waiting for services..."
for port in 7401 7402 7403 7404 7405 7406 7407 7408 7409 \
            7420 7421 7422 7423 7424 7425 7426 7427; do
  for i in $(seq 1 30); do
    curl -sf -m 1 "http://127.0.0.1:$port/health" >/dev/null 2>&1 && break
    sleep 0.5
  done
  curl -sf -m 1 "http://127.0.0.1:$port/health" >/dev/null 2>&1 \
    && green "service on :$port healthy" \
    || { echo "FAIL: service on :$port did not come up"; exit 1; }
done

# Register every service with the main daemon, advertising the PUBLIC_HOST.
yellow "registering services on public ANS as http://${PUBLIC_HOST}:<port>..."

reg() {
  local name="$1" port="$2" paths="$3" cost_args="$4" tags="$5" desc="$6"
  # shellcheck disable=SC2086
  anet svc register --name "$name" --endpoint "http://${PUBLIC_HOST}:${port}" \
    --paths "$paths" --modes rr $cost_args \
    --tags "$tags" \
    --description "$desc" \
    --health-check /health --meta-path /meta 2>&1 | grep -E "✓|error" || true
}

reg translate-svc          7401 /v1/translate,/v1/quote,/health,/meta       "--per-call 5"  translate,zh-en,content-intel             "Chinese to English translation service"
reg extract-svc            7402 /v1/extract,/v1/quote,/health,/meta         "--per-call 8"  extract,ner,content-intel                 "Named entity extraction service"
reg sentiment-svc          7403 /v1/sentiment,/v1/quote,/health,/meta       "--per-call 5"  sentiment,content-intel                   "Sentiment analysis service"
reg summarise-svc          7404 /v1/summarise,/v1/quote,/health,/meta       "--per-call 10" summarise,content-intel                   "Text summarization service"
reg classify-svc           7405 /v1/classify,/v1/quote,/health,/meta        "--per-call 5"  classify,topic,content-intel              "Topic classification service"
reg orchestrator-svc       7406 /v1/analyze,/v1/discover,/v1/marketplace,/health,/meta "--free" orchestrator,content-intel,multi-agent,shell-market "Shell Market Protocol orchestrator — auctioneer + pipeline composer"
reg factcheck-svc          7407 /v1/factcheck,/v1/quote,/health,/meta       "--per-call 8"  factcheck,content-intel                   "Fact-checking service"
reg translate-en-zh-svc    7408 /v1/translate-en-zh,/v1/quote,/health,/meta "--per-call 5"  translate-en-zh,content-intel             "English to Chinese translation"
reg keywords-svc           7409 /v1/keywords,/v1/quote,/health,/meta        "--per-call 3"  keywords,content-intel                    "Keyword extraction service"

# Protocol services — all free (per_call=0)
reg reputation-svc         7420 /v1/report,/v1/lookup,/v1/leaderboard,/v1/bonus,/v1/stats,/health,/meta "--free" reputation,shell-market,protocol         "Shell Market Protocol — global reputation registry"
reg auction-svc            7421 /v1/open,/v1/bid,/v1/close,/v1/active,/v1/history,/v1/auction,/health,/meta "--free" auction,shell-market,protocol         "Shell Market Protocol — sealed reverse auction coordinator"
reg market-dashboard-svc   7422 /api/leaderboard,/api/auctions,/api/active,/api/stats,/api/settlement,/api/disputes,/api/providers,/api/feed,/api/spread,/api/protocol_health,/health,/meta "--free" market-dashboard,shell-market,protocol "Shell Market Protocol — live market dashboard"
reg settlement-svc         7423 /v1/record,/v1/ledger,/v1/totals,/v1/provider,/health,/meta "--free" settlement,shell-market,protocol      "Shell Market Protocol — settlement ledger"
reg dispute-svc            7424 /v1/file,/v1/resolve,/v1/active,/v1/history,/health,/meta "--free" dispute,shell-market,protocol            "Shell Market Protocol — dispute resolution registry"
reg quote-broker-svc       7425 /v1/aggregate,/v1/snapshot,/v1/spread,/health,/meta "--free" quote-broker,shell-market,protocol           "Shell Market Protocol — quote aggregator"
reg market-feed-svc        7426 /v1/publish,/v1/stream,/v1/recent,/health,/meta "--free" market-feed,shell-market,protocol                "Shell Market Protocol — live event feed (SSE)"
reg provider-registry-svc  7427 /v1/upsert,/v1/list,/v1/get,/health,/meta "--free" provider-registry,shell-market,protocol                "Shell Market Protocol — provider metadata registry"

green "all 17 services deployed to public ANS at http://${PUBLIC_HOST}!"
echo ""
echo "Shell Market Protocol services (8):"
echo "  reputation-svc        :7420  (free)"
echo "  auction-svc           :7421  (free)"
echo "  market-dashboard-svc  :7422  (free)  ← visit me"
echo "  settlement-svc        :7423  (free)"
echo "  dispute-svc           :7424  (free)"
echo "  quote-broker-svc      :7425  (free)"
echo "  market-feed-svc       :7426  (free, SSE)"
echo "  provider-registry-svc :7427  (free)"
echo ""
echo "Dashboard: http://${PUBLIC_HOST}:7422"
echo "PIDs in /tmp/deploy-pids"
echo "To stop: while read p; do kill \$p 2>/dev/null; done < /tmp/deploy-pids"
