#!/usr/bin/env bash
# Deploy all services to the public ANS mesh via the main daemon
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PY="$ROOT/.venv/bin/python"
cd "$ROOT"

export ANET_BASE_URL=http://127.0.0.1:14101

green() { printf "\033[32m✓\033[0m %s\n" "$*"; }
yellow() { printf "\033[33m·\033[0m %s\n" "$*"; }

# Start NLP services
start_svc() {
  local module="${1:-}" name="${2:-}" logfile="/tmp/deploy-${2:-unknown}.log"
  "$PY" -m "$module" > "$logfile" 2>&1 &
  echo $! >> /tmp/deploy-pids
  yellow "$name → started (log=$logfile)"
}

rm -f /tmp/deploy-pids

start_svc agents.translate       translate-svc
start_svc agents.extract         extract-svc
start_svc agents.sentiment       sentiment-svc
start_svc agents.summarise       summarise-svc
start_svc agents.classify        classify-svc
start_svc agents.orchestrator    orchestrator-svc
start_svc agents.factcheck       factcheck-svc
start_svc agents.translate_en_zh translate-en-zh-svc
start_svc agents.keywords        keywords-svc

# Protocol services
start_svc agents.reputation       reputation-svc
start_svc agents.auction          auction-svc
start_svc agents.market_dashboard market-dashboard-svc

# Wait for all to come up
yellow "waiting for services..."
for port in 7401 7402 7403 7404 7405 7406 7407 7408 7409 7420 7421 7422; do
  for i in $(seq 1 30); do
    curl -sf -m 1 "http://127.0.0.1:$port/health" >/dev/null 2>&1 && break
    sleep 0.5
  done
  curl -sf -m 1 "http://127.0.0.1:$port/health" >/dev/null 2>&1 \
    && green "service on :$port healthy" \
    || { echo "FAIL: service on :$port did not come up"; exit 1; }
done

# Register NLP services with the main daemon
yellow "registering NLP services on public ANS..."

anet svc register --name translate-svc --endpoint http://127.0.0.1:7401 \
  --paths /v1/translate,/health,/meta --modes rr --per-call 5 \
  --tags translate,zh-en,content-intel \
  --description "Chinese to English translation service" \
  --health-check /health --meta-path /meta 2>&1 | grep -E "✓|error"

anet svc register --name extract-svc --endpoint http://127.0.0.1:7402 \
  --paths /v1/extract,/health,/meta --modes rr --per-call 8 \
  --tags extract,ner,content-intel \
  --description "Named entity extraction service" \
  --health-check /health --meta-path /meta 2>&1 | grep -E "✓|error"

anet svc register --name sentiment-svc --endpoint http://127.0.0.1:7403 \
  --paths /v1/sentiment,/health,/meta --modes rr --per-call 5 \
  --tags sentiment,content-intel \
  --description "Sentiment analysis service" \
  --health-check /health --meta-path /meta 2>&1 | grep -E "✓|error"

anet svc register --name summarise-svc --endpoint http://127.0.0.1:7404 \
  --paths /v1/summarise,/health,/meta --modes rr --per-call 10 \
  --tags summarise,content-intel \
  --description "Text summarization service" \
  --health-check /health --meta-path /meta 2>&1 | grep -E "✓|error"

anet svc register --name classify-svc --endpoint http://127.0.0.1:7405 \
  --paths /v1/classify,/health,/meta --modes rr --per-call 5 \
  --tags classify,topic,content-intel \
  --description "Topic classification service" \
  --health-check /health --meta-path /meta 2>&1 | grep -E "✓|error"

anet svc register --name orchestrator-svc --endpoint http://127.0.0.1:7406 \
  --paths /v1/analyze,/v1/discover,/v1/marketplace,/health,/meta --modes rr --free \
  --tags orchestrator,content-intel,multi-agent,shell-market \
  --description "Shell Market Protocol orchestrator — auctioneer + pipeline composer" \
  --health-check /health --meta-path /meta 2>&1 | grep -E "✓|error"

anet svc register --name factcheck-svc --endpoint http://127.0.0.1:7407 \
  --paths /v1/factcheck,/health,/meta --modes rr --per-call 8 \
  --tags factcheck,content-intel \
  --description "Fact-checking service" \
  --health-check /health --meta-path /meta 2>&1 | grep -E "✓|error"

anet svc register --name translate-en-zh-svc --endpoint http://127.0.0.1:7408 \
  --paths /v1/translate-en-zh,/health,/meta --modes rr --per-call 5 \
  --tags translate-en-zh,content-intel \
  --description "English to Chinese translation" \
  --health-check /health --meta-path /meta 2>&1 | grep -E "✓|error"

anet svc register --name keywords-svc --endpoint http://127.0.0.1:7409 \
  --paths /v1/keywords,/health,/meta --modes rr --per-call 3 \
  --tags keywords,content-intel \
  --description "Keyword extraction service" \
  --health-check /health --meta-path /meta 2>&1 | grep -E "✓|error"

green "all services deployed to public ANS!"
echo ""
echo "Protocol services:"
echo "  reputation-svc      :7420  (free)"
echo "  auction-svc         :7421  (free)"
echo "  market-dashboard-svc:7422  (free)"
echo ""
echo "Dashboard: http://127.0.0.1:7422"
echo "PIDs in /tmp/deploy-pids"
echo "To stop: while read p; do kill \$p 2>/dev/null; done < /tmp/deploy-pids"
