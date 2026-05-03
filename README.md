# Shell Market Protocol · #AgentNetwork

> 🟢 **The service-trading infrastructure for AgentNetwork.** Eight independent
> P2P protocol services — `auction`, `reputation`, `settlement`, `dispute`,
> `quote-broker`, `market-feed`, `provider-registry`, `market-dashboard` —
> that turn any skill on the mesh into a liquid, audited marketplace. Bring
> your own provider, expose `/v1/quote`, and you join a sealed reverse-auction
> with on-chain-style reputation, settled shells, and live dispute resolution.
> **13 daemons · 17 services · externally reachable · 0 configuration.**

**Shell Market Protocol** is *not* an NLP product. It's the **economic and
trust layer for AgentNetwork** — the equivalent of an exchange, a settlement
bank, and an arbitration registry, all running as standalone P2P services
that any skill can plug into. Every call is a sealed reverse auction; every
winning bid is settled in shells; every outcome updates a public reputation
ledger; every dispute is logged and resolved. The market-dashboard renders
all of it in one terminal-style UI that auto-refreshes every 3 seconds.

The included content-intelligence pipeline — translate, extract, keywords,
sentiment, summarise, classify, factcheck — is a **reference implementation**
showing 12 distinct providers across 13 daemons competing inside the protocol
on price, latency, and reputation. Swap the skills out for code-review,
embeddings, image-gen — the protocol stays the same.

```
                ┌──── any caller (client / orchestrator / agent) ────┐
                └─────────────────────┬───────────────────────────────┘
                                      │
                               POST /v1/open
                                      ▼
        ┌──────────────────────────────────────────────────────────┐
        │  auction-svc       reputation-svc      settlement-svc     │
        │  sealed bids   ↔   trust ledger    ↔   shell ledger       │
        └─────┬──────────────────┬───────────────────┬──────────────┘
              │                  │                   │
              │           ┌──────┴──────┐            │
              │           │ dispute-svc │            │
              │           └──────┬──────┘            │
              ▼                  ▼                   ▼
        ┌─────────────────────────────────────────────────────┐
        │  market-feed-svc  ──SSE──>  market-dashboard-svc    │
        │  provider-registry-svc      :7422  http live UI     │
        │  quote-broker-svc                                   │
        └─────────────────────────────────────────────────────┘
                                  ▲
                          any provider exposing /v1/quote
                          (translate, sentiment, factcheck, …)
                                  joins automatically
```

## 🟢 Why this is a protocol, not a product

| Layer                         | Example                          | Pluggable? |
| :---------------------------- | :------------------------------- | :--------: |
| **Protocol services (8)**     | auction, reputation, settlement, dispute, quote-broker, market-feed, provider-registry, market-dashboard | ✓ (this repo) |
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
bash scripts/run.sh                     # 13 daemons + 17 services + auto-seeded market + demo client
open http://<your-public-ip>:7422       # Shell Market Protocol dashboard (externally reachable)
```

The dashboard auto-refreshes every 3 seconds and shows:

- **Mesh stats** — live counts of auctions, settled shells, wins/losses,
  active reputation records, plus per-skill auction breakdown.
- **Protocol health pills** — at-a-glance reachability of all 8 protocol services.
- **Reputation leaderboard** — every provider, every score, every bonus.
- **Settlement ledger** — revenue per provider with a horizontal bar chart;
  proves real shells are flowing through the protocol.
- **Recent auctions** — drill down into bidders, scores, winners, input preview.
- **Live market feed** — SSE-fed event tape (auction.closed, step.completed,
  dispute.resolved) updating every 1.5s.
- **Provider registry** — region, version, capabilities, SLA p99 for every
  provider that opted into the metadata registry.
- **Disputes** — filed disputes with verdicts (upheld / partial / dismissed)
  and rationale.

## 🌍 External reachability

Every service binds `0.0.0.0` (override via `LISTEN_HOST`) and registers
with ANS using the auto-detected **Tailscale IP** (override via `PUBLIC_HOST`).
Judges can hit any endpoint from outside the host:

```bash
curl http://<tailscale-ip>:7422/api/stats         # market dashboard JSON
curl http://<tailscale-ip>:7423/v1/totals         # settlement totals
curl http://<tailscale-ip>:7426/v1/stream         # SSE live feed
curl -X POST http://<tailscale-ip>:7406/v1/analyze \
     -H 'content-type: application/json' \
     -d '{"text":"Tesla announced a new factory in Shanghai."}'
```

`scripts/run-all.sh` and `scripts/deploy-public.sh` print the resolved
`PUBLIC_HOST` so you know exactly what address ANS advertises.

## 📜 The protocol surface (8 services)

### `auction-svc` — sealed reverse auction coordinator

```
POST /v1/open       {skill, text, k=1}                   → {auction_id, ...}
POST /v1/bid        {auction_id, peer_id, service, ...}  → {accepted}
POST /v1/close/{id}                                      → {winners, all_bids, ...}
GET  /v1/active  /v1/history?limit=20  /v1/auction/{id}
```

Scoring (lower wins): `score = bid + eta_ms / 20 - reputation_bonus`.
Ties broken by lower latency, then earliest bid.

### `reputation-svc` — global trust registry

```
POST /v1/report     {peer_id, service, success}          → record one outcome
GET  /v1/lookup  /v1/leaderboard  /v1/bonus  /v1/stats
```

Bonus = `clamp(score * 0.25, -3, +4)`. Win → +1; loss → −2 (failures hurt
more — classic trust dynamics).

### `settlement-svc` — append-only shell ledger ★ NEW

```
POST /v1/record     {auction_id, payee_service, shells, ...}
GET  /v1/ledger?limit=50    /v1/totals    /v1/provider/{service}
```

Every paid winner-call writes a settlement row. `/v1/totals` rolls up
revenue per skill and per provider — that's what the dashboard's revenue
chart reads. Proves the marketplace is **economic**, not just routing.

### `dispute-svc` — arbitration registry ★ NEW

```
POST /v1/file       {auction_id, accuser, accused_service, reason, stake}
POST /v1/resolve/{dispute_id}                            → verdict
GET  /v1/active  /v1/history
```

Verdicts: `upheld` (provider penalised in reputation), `partial` (SLA
breach but output usable), `dismissed` (no protocol violation).

### `quote-broker-svc` — multi-provider quote aggregator ★ NEW

```
POST /v1/aggregate  {skill, text}    → all current /v1/quote responses
GET  /v1/snapshot?skill=             → cached
GET  /v1/spread?skill=               → min/max/median/stdev of bids
```

Lets clients price-shop without running a full auction. Cache feeds
spread/depth analytics on the dashboard.

### `market-feed-svc` — live SSE event firehose ★ NEW

```
POST /v1/publish    {kind, data}                         → broadcast
GET  /v1/stream                                          → text/event-stream
GET  /v1/recent?limit=50&kind=                           → buffered events
```

The orchestrator publishes `auction.closed` and `step.completed` events
on every analyze call. Subscribe from any browser, dashboard, or bot.

### `provider-registry-svc` — provider metadata ★ NEW

```
POST /v1/upsert     {service, peer_id, region, sla, capabilities, ...}
GET  /v1/list  /v1/get/{service}
```

ANS only knows endpoint, paths, cost. This registry stores everything else
providers want to publish — region, SLA targets, version, capabilities.

### `market-dashboard-svc` — live UI (port 7422)

P2P-discovered web UI. Reads from every other protocol service.
**It doesn't know any skills exist.** All sections auto-refresh every 3s;
the live feed updates every 1.5s.

## 🛠 Reference implementation: a 9-skill content-intelligence mesh

| Daemon | Service                | Skill tag        | Per-call (shells) |
| :----- | :--------------------- | :--------------- | :---------------: |
| u1     | translate-svc          | translate        | 5                 |
| u2     | extract-svc            | extract          | 8                 |
| u3     | sentiment-svc          | sentiment        | 5                 |
| u4     | summarise-svc          | summarise        | 10                |
| u5     | classify-svc           | classify         | 5                 |
| u6     | orchestrator-svc       | orchestrator     | 0 (free)          |
| u7     | factcheck-svc          | factcheck        | 8                 |
| u8     | translate-en-zh-svc    | translate-en-zh  | 5                 |
| u9     | keywords-svc           | keywords         | 3                 |
| u10    | sentiment-alt-svc      | sentiment        | 2 (cheaper, slower) |
| u10    | classify-alt-svc       | classify         | 4 (faster)        |
| u10    | keywords-alt-svc       | keywords         | 2 (thorough)      |
| **u11**| **reputation-svc**     | **reputation**   | **0 — protocol**  |
| **u11**| **settlement-svc**     | **settlement**   | **0 — protocol**  |
| **u12**| **auction-svc**        | **auction**      | **0 — protocol**  |
| **u12**| **dispute-svc**        | **dispute**      | **0 — protocol**  |
| **u12**| **provider-registry-svc** | **provider-registry** | **0 — protocol**  |
| **u13**| **market-dashboard-svc**  | **market-dashboard**  | **0 — protocol** |
| **u13**| **quote-broker-svc**      | **quote-broker**      | **0 — protocol** |
| **u13**| **market-feed-svc**       | **market-feed**       | **0 — protocol** |

Three skills (`sentiment`, `classify`, `keywords`) have **competing providers**
on different daemons with different cost/latency/style profiles. Watch the
auction tape — bids change every run as the per-process load random-walks,
and reputation slowly tilts the playing field.

## 🌊 Auto-seeded market activity

`scripts/run.sh` (and a standalone `scripts/seed_market.py`) drives ~12
varied analyze calls through the orchestrator on startup, producing:

- 50+ closed auctions across 7 skills
- ~30 settled shell-transfers in the ledger
- a populated reputation leaderboard with wins and losses
- one filed-and-resolved dispute
- 8 provider-registry entries with SLA / region / capabilities
- a continuously updating live feed

So the dashboard is **never empty** when a judge opens it. Re-seed any time:

```bash
.venv/bin/python scripts/seed_market.py --rounds 30
```

## 🔁 Anatomy of one orchestrator call

For each step in the plan the orchestrator runs through the protocol:

```
1. open    auction-svc.POST /v1/open       {skill, text, k}        → auction_id
2. quote   for each provider:  POST /v1/quote                       → bid
3. bid     auction-svc.POST /v1/bid        × N
4. close   auction-svc.POST /v1/close/{id}                          → winners (rep bonus applied)
5. work    winner.POST /v1/{skill}                                  → result
6. settle  settlement-svc.POST /v1/record  {auction_id, payee, sh}  → ledger ++
7. report  reputation-svc.POST /v1/report  {peer_id, service, ok}   → trust ++
8. publish market-feed-svc.POST /v1/publish {auction.closed, step}  → SSE fanout
```

If any protocol service is unreachable the orchestrator gracefully degrades
to local scoring and the rest of the loop still runs. **Protocol services
compose; they don't dominate.**

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
├── pyproject.toml       ← Python deps
├── client.py            ← demo client (calls orchestrator)
├── agents/                              port    role
│   ├── reputation.py                  ← 7420   protocol — trust ledger
│   ├── auction.py                     ← 7421   protocol — sealed bids
│   ├── market_dashboard.py            ← 7422   protocol — live UI
│   ├── settlement.py                  ← 7423   protocol — shell ledger
│   ├── dispute.py                     ← 7424   protocol — arbitration
│   ├── quote_broker.py                ← 7425   protocol — quote aggregator
│   ├── market_feed.py                 ← 7426   protocol — SSE feed
│   ├── provider_registry.py           ← 7427   protocol — metadata
│   ├── orchestrator.py                ← 7406   reference auctioneer
│   ├── translate.py / extract.py / keywords.py / sentiment.py
│   ├── summarise.py / classify.py / factcheck.py / translate_en_zh.py
│   ├── dashboard.py                   ← 7400   pipeline observer
│   ├── quote_helpers.py               ← reusable /v1/quote helper
│   ├── register.py                    ← anet registration helper (PUBLIC_HOST aware)
│   └── anet_sdk.py                    ← daemon REST shim
├── scripts/
│   ├── setup-nodes.sh                 ← 13 daemons + cross-node credit seeding
│   ├── run-all.sh                     ← starts every FastAPI service (PUBLIC_HOST aware)
│   ├── run.sh                         ← one-shot: setup + agents + seed + demo
│   ├── seed_market.py                 ← drives auctions to populate the dashboard
│   ├── deploy-public.sh               ← register all 17 services on the public ANS mesh
│   └── stop.sh                        ← clean teardown
└── tests/
    └── test_pipeline.py               ← integration test
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

`#AgentNetwork` `#ShellMarketProtocol` `#P2P` `#Auction` `#Reputation` `#Settlement` `#Dispute`
