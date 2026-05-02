# SPEC.md — Content Intelligence Pipeline for AgentNetwork Hackathon

## Overview
Build a multi-agent P2P service on Agent Network that demonstrates the power of
agent-to-agent collaboration. When a user submits text, an orchestrator agent
discovers and calls 5 specialized agents through the P2P gateway to produce a
comprehensive content analysis report.

## Why This Wins
1. **Service-of-services**: The orchestrator is itself a service that discovers and calls other services
2. **Dynamic discovery**: Agents find each other by skill tag, not hardcoded URLs
3. **Full pipeline**: 5 distinct capabilities chained through P2P
4. **Judge-friendly**: One command to run, clear output, works without GPU
5. **Cost model**: Each agent has per_call pricing, demonstrating the shell economy

## Agent Specifications

### 1. translate-svc (zh→en translation)
- Port: 7401, Skill tag: "translate"
- POST /v1/translate {text: string} → {translated: string, lang: string}
- Rule-based: dictionary lookup + character pass-through
- per_call: 5 shells

### 2. extract-svc (named entity extraction)
- Port: 7402, Skill tag: "extract"
- POST /v1/extract {text: string} → {entities: [{text, type, start, end}]}
- Rule-based: regex patterns for common entity types (Chinese names, dates, numbers, places)
- Types: PERSON, PLACE, ORG, DATE, NUMBER
- per_call: 8 shells

### 3. sentiment-svc (sentiment analysis)
- Port: 7403, Skill tag: "sentiment"
- POST /v1/sentiment {text: string} → {label: string, score: float}
- Lexicon-based: count positive/negative words, return label + confidence
- Labels: positive, negative, neutral
- per_call: 5 shells

### 4. summarise-svc (text summarization)
- Port: 7404, Skill tag: "summarise"
- POST /v1/summarise {text: string, max_sentences: int} → {summary: string, sentences: int}
- Heuristic: extract first N sentences, cap at max length
- If input is Chinese, call translate-svc first (cross-agent call!)
- per_call: 10 shells

### 5. classify-svc (topic classification)
- Port: 7405, Skill tag: "classify"
- POST /v1/classify {text: string} → {topic: string, confidence: float, keywords: [string]}
- Keyword-based: match against topic keyword lists
- Topics: technology, business, science, politics, sports, entertainment, health, other
- per_call: 5 shells

### 6. orchestrator-svc (pipeline orchestrator)
- Port: 7406, Skill tag: "orchestrator"
- POST /v1/analyze {text: string} → Full analysis report
- This is the STAR agent — it discovers all others and chains them
- Pipeline: extract → sentiment → summarise → classify → compile
- Calls each agent via svc.discover(skill=...) + svc.call(...)
- Returns combined JSON with all results
- per_call: 0 (free entry point for clients)

### 7. client.py
- CLI tool that calls orchestrator-svc via P2P
- Pretty-prints the analysis report
- Shows audit trail from all daemons

## Multi-Daemon Setup
6 daemons, each on unique ports:
- Daemon 1: API 14101, P2P 14201, HOME /tmp/anet-ci-u1
- Daemon 2: API 14102, P2P 14202, HOME /tmp/anet-ci-u2
- Daemon 3: API 14103, P2P 14203, HOME /tmp/anet-ci-u3
- Daemon 4: API 14104, P2P 14204, HOME /tmp/anet-ci-u4
- Daemon 5: API 14105, P2P 14205, HOME /tmp/anet-ci-u5
- Daemon 6: API 14106, P2P 14206, HOME /tmp/anet-ci-u6

Daemon 2-6 bootstrap off daemon 1. Cross-node credits seeded between all pairs.

## Run Script
scripts/run.sh should:
1. Install deps (pip install anet httpx fastapi uvicorn)
2. Start 6 daemons via setup-nodes.sh
3. Wait for all daemons to be alive
4. Seed cross-node credits
5. Start all 6 agents as background processes
6. Wait for all agents' /health to respond
7. Run client.py with a demo text
8. Print success/failure summary

## Success Criteria
- `bash scripts/run.sh` completes without errors
- Client prints a full analysis report with results from all 5 agents
- Audit trail shows cross-node calls in svc_call_log
- Total run time < 60 seconds
