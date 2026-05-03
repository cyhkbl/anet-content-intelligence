# Shell Market Protocol · #AgentNetwork

> 🟢 **The first service-trading protocol for AgentNetwork.** A standalone
> P2P protocol layer — `auction-svc` + `reputation-svc` + `market-dashboard-svc` —
> that any provider on the mesh can plug into. Bring your own skill, expose
> `/v1/quote`, and you join a live, sealed reverse-auction marketplace
> with on-chain-style reputation. **13 daemons, 16 services, 0 configuration.**

**Shell Market Protocol** is *not* an NLP product. It's a
**protocol layer for AgentNetwork** that turns P2P services into a
liquid market. Every call is a sealed reverse auction; every outcome
updates a public reputation registry; every bid is observable from a
mesh-wide dashboard.

The included content-intelligence pipeline — translate, extract, keywords,
sentiment, summarise, classify, factcheck — is a **reference implementation**
showing how 9 distinct providers on 10 daemons compete inside the protocol
on price, latency, and reputation. Swap them out for any skill. The
protocol stays the same.

```
                ┌──── any caller (client / orchestrator / agent) ────┐
                └─────────────────���──┬───────────────────────────────┘
                                     │
                              POST /v1/open   ──┐
                                                ▼
                          ┌──────────────────────────────┐
                          │       auction-svc  (u12)     │  Shell Market
                          │   sealed reverse auctions    │   Protocol
                          └──┬─────────────────┬─────────┘
                             │                 │
                  POST /v1/bid                 │ POST /v1/report
                  (every bidder)               ▼
                             │           ┌────────────────────┐
                             ▼           │ reputation-svc(u11)│  Shell Market
                  ┌──────────────────┐   │ trust ledger       │   Protocol
                  │  any provider    │←──┤ score = wins-2*L   │
                  │  /v1/quote       │   └─────────┬──────────┘
                  └──────────────────┘             │
                                                   │ live read
                                                   ▼
                                       ┌─────────────────────┐
                                       │ market-dashboard(u13)│  Shell Market
                                       │ http://...:7422     │   Protocol
                                       └─────────────────────┘
```

## 🟢 Why this is a protocol, not a product

| Layer                         | Example                          | Pluggable? |
| :---------------------------- | :------------------------------- | :--------: |
| **Protocol services**         | auction-svc, reputation-svc      | ✓ (this repo) |
| **Provider contract**         | `/v1/quote` returns a sealed bid | ✓ (any agent) |
| **Auctioneer / orchestrator** | this repo's `orchestrator-svc`   | ✓ (DIY)    |
| **Skill implementation**      | translate, sentiment, keywords…  | ✓ (yours)  |
| **Discovery + audit + pay**   | AgentNetwork (ANS, shells, log)  | already there |

A new provider joins by:
1. Registering a service with tag `content-intel` (or any skill tag).
2. Exposing `POST /v1/quote` returning `{bid, eta_ms, style, load}`.
3. Exposing the actual skill endpoint (e.g. `POST /v1/sentiment`).

The orchestrator finds them on the next auction. **Zero coordination.**
**Zero config in this repo.** The protocol does the rest.

## ⚡ 5-minute judges' guide

```bash
git clone …; cd anet-hackathon
python3.11 -m venv .venv && .venv/bin/pip install -e .
bash scripts/run.sh                     # 13 daemons + 16 services + demo client
open http://127.0.0.1:7422              # Shell Market Protocol dashboard
```

You should see:

- **A reputation leaderboard** that updates after every call.
- **A live auction tape** — recent auctions per skill, every bid, the winner.
- **The orchestrator output** — full pipeline report with `auctions[].via_protocol: true`,
  proving each step was settled through the protocol services.
- **`bash scripts/setup-nodes.sh status`** showing 13 daemons green.

## 📜 The protocol surface

### `auction-svc` — sealed reverse auction coordinator

```
POST /v1/open       {skill, text, k=1}                  → {auction_id, ...}
POST /v1/bid        {auction_id, peer_id, service,
                     bid, eta_ms, style, load}           → {accepted}
POST /v1/close/{id}                                      → {winners, all_bids, ...}
GET  /v1/active                                          → list
GET  /v1/history?limit=20                                → recent closed auctions
```

Scoring (lower wins):
```
score = bid + eta_ms / 20 - reputation_bonus
```
Ties broken by lower latency, then earliest bid.

### `reputation-svc` — global trust registry

```
POST /v1/report     {peer_id, service, success}          → record one outcome
GET  /v1/lookup?service=&peer_id=                        → record
GET  /v1/leaderboard?limit=20                            → ranked list
GET  /v1/bonus?service=&peer_id=                         → score discount
GET  /v1/stats                                           → totals + uptime
```

Bonus = `clamp(score * 0.25, -3, +4)`. A winner earns +1, a failure -2 —
failures hurt more, classic trust dynamics.

### `market-dashboard-svc` — live UI

P2P-discovered web UI. Reads from `auction-svc` and `reputation-svc` only.
**It doesn't know any skills exist.**

## 🛠 Reference implementation: a 9-skill content-intelligence mesh

| Daemon | Service                | Skill tag        | Per-call (shells) |
| :----- | :--------------------- | :--------------- | :---------------: |
| u1     | translate-svc          | translate        | 0 (free)          |
| u2     | extract-svc            | extract          | 5                 |
| u3     | sentiment-svc          | sentiment        | 5                 |
| u4     | summarise-svc          | summarise        | 5                 |
| u5     | classify-svc           | classify         | 5                 |
| u6     | orchestrator-svc       | orchestrator     | 0 (free)          |
| u7     | factcheck-svc          | factcheck        | 8                 |
| u8     | translate-en-zh-svc    | translate-en-zh  | 4                 |
| u9     | keywords-svc           | keywords         | 3                 |
| u10    | sentiment-alt-svc      | sentiment        | 2 (cheaper, slower) |
| u10    | classify-alt-svc       | classify         | 4 (faster)        |
| u10    | keywords-alt-svc       | keywords         | 2 (thorough)      |
| **u11**| **reputation-svc**     | **reputation**   | **0 — protocol**  |
| **u12**| **auction-svc**        | **auction**      | **0 — protocol**  |
| **u13**| **market-dashboard**   | **market-dashboard** | **0 — protocol** |

Three skills (`sentiment`, `classify`, `keywords`) have **competing providers**
on different daemons with different cost/latency/style profiles. Watch the
auction tape — bids change every run as the per-process load random-walks,
and reputation slowly tilts the playing field.

## 🔁 Anatomy of one orchestrator call

For each step in the plan the orchestrator runs through the protocol:

```
1. open    auction-svc.POST /v1/open       {skill, text, k}        → auction_id
2. quote   for each provider:  POST /v1/quote                       → bid
3. bid     auction-svc.POST /v1/bid        × N
4. close   auction-svc.POST /v1/close/{id}                          → winners (rep bonus applied)
5. work    winner.POST /v1/{skill}                                  → result
6. report  reputation-svc.POST /v1/report  {peer_id, service, ok}   → ledger ++
```

If `auction-svc` is unreachable the orchestrator gracefully degrades to
local scoring. **Protocol services compose; they don't dominate.**

## 🧠 Consensus is a protocol primitive too

For high-stakes skills (default: `sentiment`) the orchestrator opens the
auction with `k=2`, asks the protocol for the top 2 winners, calls both,
and majority-votes the labels. Reputation is reported for **every**
provider that participated — losers in the vote still get a score
adjustment based on whether they returned successfully.

## 📁 Repo layout

```
~/anet-hackathon/
├── README.md            ← this doc (protocol spec + quickstart)
├── WIN.md               ← the strategy that drove this rewrite
├── pyproject.toml       ← Python deps
├── client.py            ← demo client (calls orchestrator)
├── agents/
│   ├── reputation.py        ← Shell Market Protocol :7420
│   ├── auction.py           ← Shell Market Protocol :7421
│   ├── market_dashboard.py  ← Shell Market Protocol :7422
│   ├── orchestrator.py      ← protocol client (auctioneer for content-intel)
│   ├── translate.py / extract.py / keywords.py / sentiment.py
│   ├── summarise.py / classify.py / factcheck.py / translate_en_zh.py
│   ├── dashboard.py         ← legacy pipeline observer (still works) :7400
│   ├── quote_helpers.py     ← reusable /v1/quote helper
│   ├── register.py          ← anet registration helper
│   └── anet_sdk.py          ← daemon REST shim
├── scripts/
│   ├── setup-nodes.sh       ← 13 daemons + cross-node credit seeding
│   ├── run-all.sh           ← starts every FastAPI service
│   ├── run.sh               ← one-shot end-to-end demo
│   └── stop.sh              ← clean teardown
└── tests/
    └── test_pipeline.py     ← integration test
```

## 🌐 Build on the protocol

Want to add a new skill — say, **`code-review`** — that participates in the
market? You write **one file**:

```python
# agents/codereview.py
@app.post("/v1/quote")
def quote(req): return make_quote(text=..., skill="code-review",
                                  agent="codereview-svc",
                                  base_cost=12, base_eta_ms=300)

@app.post("/v1/codereview")
def review(req): ... return {"verdict": ..., "agent": "codereview-svc"}
```

…register it with tag `code-review` and `content-intel`. The planner here
is content-intel-specific, but **any auctioneer that opens a `code-review`
auction will discover and score your provider through the same protocol
services**. That is what makes this a protocol layer, not a pipeline.

## 📋 Cleanup

```bash
bash scripts/stop.sh          # kills all 13 daemons + every FastAPI agent
```

## 🏷 Tag

`#AgentNetwork` `#ShellMarketProtocol` `#P2P` `#Auction` `#Reputation`
