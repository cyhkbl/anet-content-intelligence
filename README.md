# Content Intelligence Pipeline · #AgentNetwork

A multi-agent P2P service built on **AgentNetwork** that turns any blob of text
into a complete intelligence report — translation, entity extraction, sentiment,
summarisation, and topic classification — by chaining six independent agents
across six daemons over the anet mesh.

The orchestrator never knows where any agent lives. It discovers them through
the mesh by **skill tag**, calls them via P2P, and pays for each call out of its
own shell budget. Five specialists run on five separate daemons; the
orchestrator runs on a sixth and is itself just another P2P-callable service.

```
                   ┌──── client.py ────┐
                   │   (any daemon)     │
                   └────────┬──────────┘
                            ▼  P2P call  /v1/analyze
                  ┌─────────────────────┐
                  │  orchestrator-svc   │  (u6 :14106)
                  │   skill=orchestrator│
                  └──┬───┬───┬───┬──────┘
        discover by  │   │   │   │
        skill-tag    ▼   ▼   ▼   ▼
                  extract sentiment summarise classify
                   (u2)    (u3)      (u4)      (u5)
                                      │
                                      ▼ (cross-call when input is zh)
                                    translate  (u1)
```

## 5-minute quickstart

```bash
# 0. Prerequisites:  uv  +  the anet daemon CLI on $PATH
which anet                  # → /usr/local/bin/anet (or similar)

# 1. Install python deps into a fresh venv
rm -rf .venv && uv venv .venv --python 3.11
uv pip install --python .venv/bin/python httpx fastapi uvicorn python-dotenv

# 2. One-shot end-to-end: 6 daemons → seed credits → 6 agents → demo client
bash scripts/run.sh

# 3. (Optional) Try a different prompt
.venv/bin/python client.py "Tesla stock fell 5% after the CEO announced layoffs."
.venv/bin/python client.py "上海明天天气怎么样？"   # Chinese — triggers translate hop

# 4. Tear it all down
bash scripts/stop.sh
```

You should see a pretty-printed report with translated/source language, summary,
sentiment label + score, top entities, topic + keywords, plus a per-daemon
audit trail showing exactly which P2P calls landed on which node. Total
wall-clock from `run.sh` start to client output: well under 60 s on a laptop.

## What each agent does

| Agent              | Daemon   | Port | Skill tag      | Cost  | Trick                                   |
|--------------------|----------|------|----------------|-------|-----------------------------------------|
| `translate-svc`    | u1 14101 | 7401 | `translate`    | 5 ¢  | longest-match zh→en dictionary           |
| `extract-svc`      | u2 14102 | 7402 | `extract`      | 8 ¢  | regex + lexicon NER (PERSON/ORG/…)       |
| `sentiment-svc`    | u3 14103 | 7403 | `sentiment`    | 5 ¢  | lexicon w/ intensifier-aware scoring     |
| `summarise-svc`    | u4 14104 | 7404 | `summarise`    | 10 ¢ | extractive; **calls translate** for zh   |
| `classify-svc`     | u5 14105 | 7405 | `classify`     | 5 ¢  | keyword voting across 7 topics           |
| `orchestrator-svc` | u6 14106 | 7406 | `orchestrator` | free | discovers + chains the 4 specialists     |

The shell economy means the orchestrator runs free for end users, but each leg
of the pipeline is a paid P2P call audited on both ends.

## How it works (the interesting bits)

1. **Six independent daemons.** `scripts/setup-nodes.sh` boots one libp2p daemon
   per agent — each with its own `HOME`, P2P port, REST API port, identity, and
   credit ledger. Daemons 2-6 bootstrap off daemon-1 to form a single mesh.
2. **Cross-node credit seeding.** Provider ledgers don't know about caller DIDs
   until they receive a credit event. The setup script seeds 30 mutual
   transfers (500 shells each) so any of the six daemons can charge any other
   without 402s.
3. **Service registration.** Each agent's FastAPI backend exposes `/v1/...`,
   `/health`, `/meta`. On startup, `agents/register.py` waits for `/health` to
   respond, then POSTs `/api/svc/register` with skill tags + cost model.
4. **Skill-based discovery.** The orchestrator does **not** know any peer URL.
   For each leg, it calls `svc.discover(skill="<tag>")`, picks the first
   responder, and then `svc.call(peer_id, svc_name, "/v1/<endpoint>", ...)`.
5. **Cross-agent hop.** `summarise-svc` discovers `translate` itself when input
   is Chinese — so an English-output pipeline is built mid-flight.
6. **Audit trail.** Every P2P call is logged in `svc_call_log` on both
   the caller's and provider's daemons. `client.py` queries each daemon's
   `/api/svc/audit` after the run to surface a reconciliation-friendly summary.

## Project layout

```
anet-hackathon/
├── README.md          ← this file
├── CLAUDE.md / SPEC.md  ← design notes
├── pyproject.toml
├── client.py          ← CLI: discover orchestrator → /v1/analyze → pretty-print
├── agents/
│   ├── anet_sdk.py    ← minimal SvcClient shim around /api/svc/*
│   ├── register.py    ← shared "wait healthy → register" helper
│   ├── translate.py        (port 7401, skill=translate)
│   ├── extract.py          (port 7402, skill=extract)
│   ├── sentiment.py        (port 7403, skill=sentiment)
│   ├── summarise.py        (port 7404, skill=summarise — calls translate)
│   ├── classify.py         (port 7405, skill=classify)
│   └── orchestrator.py     (port 7406, skill=orchestrator — chains the four)
├── scripts/
│   ├── setup-nodes.sh ← 6 daemons + cross-node credit seeding
│   ├── run-all.sh     ← 6 agents as background processes
│   ├── run.sh         ← end-to-end (setup + run + client)
│   └── stop.sh        ← kill everything
└── tests/
    └── test_pipeline.py  ← smoke test the agents' pure functions
```

## Why this is interesting for AgentNetwork

* **Service-of-services.** The orchestrator is itself a P2P service callable by
  anyone with the `orchestrator` skill tag — it's a meta-agent built out of
  other agents in the mesh.
* **No hard-coded peer URLs.** Every cross-agent call resolves through ANS skill
  tags, so any of the five specialists can be replaced or moved between nodes
  and the pipeline keeps working.
* **A real cost model.** Free vs priced services live side-by-side; cross-node
  credit seeding is part of the demo so the audit trail shows non-zero costs.
* **Judge-friendly.** `bash scripts/run.sh` is the only thing a reviewer needs
  to run. No GPU, no API keys, finishes in seconds.

## Tests

```bash
.venv/bin/python -m pytest tests/    # pure-function smoke tests
```

The integration test exercises the agent logic in isolation (no daemon
needed). The `scripts/run.sh` entry point is the de-facto end-to-end test.

#AgentNetwork
