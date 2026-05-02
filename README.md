# Content Intelligence Mesh · #AgentNetwork

> 🟢 **LIVE on AgentNetwork public mesh** — 9 P2P services + self-composing
> orchestrator + web dashboard. Every service is discoverable by the skill
> tag `content-intel`.

A **self-composing** multi-agent P2P service built on **AgentNetwork** that
turns any blob of text into a complete intelligence report. Instead of a
hard-coded pipeline, the orchestrator discovers every `content-intel`
skill on the mesh, picks the ones relevant to the input, chains them
dynamically, and pays each specialist in shells. Boot a brand-new
service tomorrow tagged `content-intel` — the orchestrator finds and
uses it with **zero config change**.

```
                        ┌──── client.py OR dashboard (7400) ─────┐
                        │                 (any daemon)            │
                        └────────────────────┬───────────────────┘
                                             ▼  P2P /v1/analyze
                                 ┌───────────────────────┐
                                 │   orchestrator-svc    │  (u6 :14106)
                                 │  self-composes plan   │
                                 │  from ANS discovery   │
                                 └──┬──┬──┬──┬──┬──┬──┬──┘
          discover by skill tag     │  │  │  │  │  │  │
                                    ▼  ▼  ▼  ▼  ▼  ▼  ▼
      translate    extract    keywords   sentiment   summarise   classify   factcheck
        (u1)         (u2)       (u9)        (u3)        (u4)       (u5)       (u7)
          │                                                 │
          ▲───────────── cross-call when input is zh ───────┘
                           (summarise → translate)
                                                                translate-en-zh (u8)
```

## ⚡ Why AgentNetwork — why this couldn't be MCP or A2A

| Feature                              | MCP | A2A | Static pipeline |  **AgentNetwork**  |
|--------------------------------------|:--:|:---:|:---------------:|:------------------:|
| Tools served over **P2P**            | ❌ | ❌  | ❌               | ✅                 |
| **Skill-tag discovery** (no URLs)    | ❌ | ⚠  | ❌               | ✅ (ANS)           |
| **Pay-per-call** shell economy       | ❌ | ❌  | ❌               | ✅                 |
| **Self-composing** at run-time       | ❌ | ⚠  | ❌               | ✅                 |
| Cross-trust audit log                | ❌ | ❌  | ❌               | ✅ (`svc_call_log`) |
| Join a strangers' service seamlessly | ❌ | ❌  | ❌               | ✅                 |

In short: **MCP gives a single agent its tools. AgentNetwork lets agents
become each other's tools.** The orchestrator here isn't a client of fixed
dependencies — it's a market-maker for any skill the mesh offers.

## 🚀 5-minute quickstart (for the judges)

```bash
# 0. Prerequisites:  uv  +  the anet daemon CLI on $PATH
which anet                  # → /usr/local/bin/anet (or similar)

# 1. Install python deps into a fresh venv (≈ 15 s)
rm -rf .venv && uv venv .venv --python 3.11
uv pip install --python .venv/bin/python httpx fastapi uvicorn python-dotenv

# 2. One-shot end-to-end: 9 daemons → seed credits → 9 agents + dashboard → demo
bash scripts/run.sh

# 3. Open the live dashboard in your browser
#    → http://127.0.0.1:7400
#    (watch the mesh animate as the orchestrator discovers peers + fires calls)

# 4. (Optional) Try a different prompt from the CLI
.venv/bin/python client.py "Tesla stock fell 5% after the CEO announced layoffs."
.venv/bin/python client.py "上海明天天气怎么样？"            # Chinese — triggers translate hop
.venv/bin/python client.py "Revenue grew 9999% overnight." # triggers factcheck flag

# 5. Tear it all down
bash scripts/stop.sh
```

The terminal client prints:
```
📊  CONTENT INTELLIGENCE REPORT  ·  self-composing orchestrator
🔎  Services discovered on the content-intel mesh:  (8 services)
🧠  Orchestrator plan: translate → extract → keywords → sentiment → summarise → classify → factcheck
🔗  Pipeline call chain: ASCII graph with latency + shell cost per hop
📝  Results: summary / sentiment / topic / entities / keywords / factcheck verdict
💰  Shell economy: total spend + per-skill breakdown
📜  Per-daemon svc_call_log + balance
```
Total wall-clock from `run.sh` start to client output: **< 60 s on a laptop.**

The dashboard shows:
* every discovered P2P service as a live node
* animated edges as the orchestrator calls each hop
* real-time stream of mesh changes via SSE
* preset prompts, custom text, bilingual intent toggle

## 🧠 What makes this different

1. **Self-composing orchestrator.** Every `/v1/analyze` call re-discovers
   the mesh. The execution plan is a pure function of
   `(input features, available skills)`. Add a service → it joins the
   pipeline automatically. Kill a service → the plan gracefully skips it.
2. **Zero hard-coded peers.** No URL, no port, no DID is baked in.
   Every cross-agent hop resolves through `svc.discover(skill=...)`.
3. **Nine independent daemons.** Each agent has its own libp2p identity,
   P2P port, REST port, and shell ledger — simulating nine organisations
   collaborating over an open mesh.
4. **A real cost model.** Free vs priced services live side-by-side;
   cross-node credit seeding is part of the demo so the audit trail shows
   non-zero shell spend per run.
5. **Live observability.** The dashboard streams SSE events the moment
   the orchestrator starts a run, renders every P2P hop on an animated
   graph, and surfaces the full ANS catalogue.

## 📦 What each agent does

| Agent                  | Daemon   | HTTP | Skill tag           | Cost  | Trick                                     |
|------------------------|----------|------|---------------------|-------|-------------------------------------------|
| `translate-svc`        | u1 14101 | 7401 | `translate`         | 5 ¢   | longest-match zh→en dictionary             |
| `extract-svc`          | u2 14102 | 7402 | `extract`           | 8 ¢   | regex + lexicon NER (PERSON/ORG/…)         |
| `sentiment-svc`        | u3 14103 | 7403 | `sentiment`         | 5 ¢   | lexicon w/ intensifier-aware scoring       |
| `summarise-svc`        | u4 14104 | 7404 | `summarise`         | 10 ¢  | extractive; **calls translate** for zh     |
| `classify-svc`         | u5 14105 | 7405 | `classify`          | 5 ¢   | keyword voting across 7 topics             |
| `orchestrator-svc`     | u6 14106 | 7406 | `orchestrator`      | free  | self-composes pipeline from ANS discovery  |
| `factcheck-svc`        | u7 14107 | 7407 | `factcheck`         | 8 ¢   | plausibility rules for %, years, orgs      |
| `translate-en-zh-svc`  | u8 14108 | 7408 | `translate-en-zh`   | 5 ¢   | longest-match en→zh dictionary             |
| `keywords-svc`         | u9 14109 | 7409 | `keywords`          | 3 ¢   | TF + length-boost + stopword filter        |
| `dashboard-svc`        | reuses u1 | 7400 | (web UI, not ANS)  | —     | FastAPI + SSE · animated topology + logs   |

## 🏗 How it works (the interesting bits)

1. **Nine independent daemons.** `scripts/setup-nodes.sh` boots nine libp2p
   daemons — each with its own `HOME`, P2P port, REST port, identity, and
   credit ledger. Daemons 2-9 bootstrap off daemon-1 to form a single mesh.
2. **Cross-node credit seeding.** Provider ledgers don't know about caller
   DIDs until they receive a credit event. The setup script seeds 72 mutual
   transfers (500 shells each) so any of the nine daemons can charge any
   other without 402s.
3. **Service registration.** Each agent's FastAPI backend exposes `/v1/…`,
   `/health`, `/meta`. On startup, `agents/register.py` waits for `/health`
   to respond, then POSTs `/api/svc/register` with skill tags + cost model.
4. **Skill-based discovery.** The orchestrator **never** hardcodes a peer
   URL. For each leg, it calls `svc.discover(skill="<tag>")`, picks the
   first responder, then `svc.call(peer_id, svc_name, "/v1/<endpoint>", …)`.
5. **Dynamic plan.** Input features → plan:
   * Chinese text? → prepend `translate`
   * Numbers/dates/percentages? → append `factcheck`
   * `intent=translate-to-zh`? → append `translate-en-zh`
   * Always include whichever signal-gathering skills are live
6. **Cross-agent hop.** `summarise-svc` discovers `translate` itself when
   input is Chinese — a sub-pipeline built mid-flight.
7. **Audit trail.** Every P2P call is logged in `svc_call_log` on both the
   caller's and provider's daemons. `client.py` queries each daemon's
   `/api/svc/audit` after the run and surfaces a reconciliation-friendly
   summary alongside shell balances.

## 🎛 Project layout

```
anet-hackathon/
├── README.md                ← this file
├── CLAUDE.md / SPEC.md      ← design notes
├── IMPROVE.md               ← upgrade plan (this iteration)
├── pyproject.toml
├── client.py                ← CLI: discover orchestrator → /v1/analyze → pretty report
├── static/
│   └── index.html           ← terminal-style web dashboard (no build step)
├── agents/
│   ├── anet_sdk.py          ← minimal SvcClient shim around /api/svc/*
│   ├── register.py          ← shared "wait healthy → register" helper
│   ├── translate.py         (port 7401, skill=translate)
│   ├── extract.py           (port 7402, skill=extract)
│   ├── sentiment.py         (port 7403, skill=sentiment)
│   ├── summarise.py         (port 7404, skill=summarise — calls translate)
│   ├── classify.py          (port 7405, skill=classify)
│   ├── orchestrator.py      (port 7406, skill=orchestrator — self-composes)
│   ├── factcheck.py         (port 7407, skill=factcheck)       🆕
│   ├── translate_en_zh.py   (port 7408, skill=translate-en-zh) 🆕
│   ├── keywords.py          (port 7409, skill=keywords)        🆕
│   └── dashboard.py         (port 7400, SSE + static HTML)     🆕
├── scripts/
│   ├── setup-nodes.sh       ← 9 daemons + cross-node credit seeding
│   ├── run-all.sh           ← 9 agents + dashboard as background procs
│   ├── run.sh               ← one-shot end-to-end (setup + run + client)
│   └── stop.sh              ← kill everything
└── tests/
    └── test_pipeline.py     ← smoke test the agents' pure functions
```

## 🌐 Public Network Registration

Services are registered on the public AgentNetwork mesh. Anyone can
discover and call them from any anet daemon:

```bash
# Discover everything tagged content-intel
anet svc discover --skill content-intel

# Ask the orchestrator to analyze text — it will auto-compose the pipeline
anet svc call <peer_id> orchestrator-svc /v1/analyze --method POST \
  --body '{"text":"Your text here"}'
```

## 🧪 Tests

```bash
.venv/bin/python -m pytest tests/    # pure-function smoke tests
```

The integration test exercises the agent logic in isolation (no daemon
needed). The `scripts/run.sh` entry point is the de-facto end-to-end test.

#AgentNetwork
