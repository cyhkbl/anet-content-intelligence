# AgentNetwork Hackathon — Content Intelligence Pipeline

## Project Overview
A multi-agent P2P service that processes text through a pipeline of specialized AI agents.
Each agent runs as an independent P2P node on Agent Network, discovers others via the gateway,
and chains calls to produce a comprehensive content analysis report.

## Architecture
- **translate-svc** (port 7401, daemon :14101): Chinese→English translation (rule-based)
- **extract-svc** (port 7402, daemon :14102): Named entity extraction (regex + rules)
- **sentiment-svc** (port 7403, daemon :14103): Sentiment analysis (lexicon-based)
- **summarise-svc** (port 7404, daemon :14104): Text summarization (heuristic)
- **classify-svc** (port 7405, daemon :14105): Topic classification (keyword matching)
- **orchestrator-svc** (port 7406, daemon :14106): Service-of-services, discovers all agents and chains them
- **client.py**: CLI client that calls orchestrator and displays the full report

## Key Technical Decisions
- All agents use FastAPI + uvicorn for HTTP backends
- All agents register with anet gateway via SvcClient
- Each agent runs on its own anet daemon (simulating real P2P)
- Cost model: per_call pricing (0 for free services, 5-15 for paid ones)
- Orchestrator discovers agents by skill tags, not hardcoded addresses
- The run script sets up 6 daemons, seeds cross-node credits, starts all agents

## File Structure
```
~/anet-hackathon/
├── CLAUDE.md          # This file
├── SPEC.md            # Detailed spec
├── README.md          # User-facing docs
├── pyproject.toml     # Python deps
├── agents/
│   ├── __init__.py
│   ├── register.py    # Shared registration helper
│   ├── translate.py   # Agent 1: zh→en
│   ├── extract.py     # Agent 2: entity extraction
│   ├── sentiment.py   # Agent 3: sentiment analysis
│   ├── summarise.py   # Agent 4: summarization
│   ├── classify.py    # Agent 5: topic classification
│   └── orchestrator.py # Agent 6: pipeline orchestrator
├── client.py          # CLI entry point
├── scripts/
│   ├── setup-nodes.sh # Start 6 daemons + seed credits
│   ├── run-all.sh     # Start all agents in background
│   ├── run.sh         # Main entry: setup + run + test
│   └── stop.sh        # Kill everything
└── tests/
    └── test_pipeline.py # Integration test
```

## anet SDK Usage Pattern
```python
from anet.svc import SvcClient

# Register
with SvcClient(base_url=base_url) as svc:
    svc.register(
        name="my-svc",
        endpoint="http://127.0.0.1:PORT",
        paths=["/v1/action", "/health", "/meta"],
        modes=["rr"],
        per_call=5,
        tags=["tag1", "tag2"],
        description="description",
        health_check="/health",
        meta_path="/meta",
    )

# Discover
peers = svc.discover(skill="translate")
target = peers[0]

# Call
resp = svc.call(target["peer_id"], target["services"][0]["name"],
                "/v1/translate", method="POST", body={"text": "hello"})
body = resp.get("body") or {}
```

## Multi-Daemon Setup Pattern
See ~/anet-p2p-starter-kit/examples/03-multi-agent-pipeline/scripts/four-node.sh for reference.
Each daemon needs:
- Unique HOME dir (e.g., /tmp/anet-ci-u1)
- Unique API port (e.g., 14101)
- Unique P2P port (e.g., 14201)
- Bootstrap peers pointing to daemon-1
- Cross-node credit seeding for priced calls

## Important Notes
- The register.py helper from the example is at ~/anet-p2p-starter-kit/examples/03-multi-agent-pipeline/register.py
- anet is installed at /usr/local/bin/anet
- Python SDK: `pip install anet` (already in the venv or install it)
- Keep all agent logic rule-based (no external model deps) so judges can run without GPU
- The orchestrator is the star: it should discover ALL agents dynamically and chain them
- README must be crystal clear with "5-minute quickstart"
- Tag #AgentNetwork in the GitHub repo
