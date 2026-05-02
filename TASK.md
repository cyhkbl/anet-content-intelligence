Build the entire Content Intelligence Pipeline project for the AgentNetwork hackathon.

## Step 1: Install Dependencies
Run: rm -rf .venv && uv venv .venv --python 3.11 && uv pip install --python .venv/bin/python anet httpx fastapi uvicorn python-dotenv

## Step 2: Create All Files
Create the full project structure as defined in CLAUDE.md and SPEC.md:
- agents/register.py (shared registration helper, copy pattern from ~/anet-p2p-starter-kit/examples/03-multi-agent-pipeline/register.py)
- agents/translate.py (zh→en, port 7401, skill "translate", per_call=5)
- agents/extract.py (entity extraction, port 7402, skill "extract", per_call=8)
- agents/sentiment.py (sentiment, port 7403, skill "sentiment", per_call=5)
- agents/summarise.py (summarize, port 7404, skill "summarise", per_call=10)
- agents/classify.py (topic classify, port 7405, skill "classify", per_call=5)
- agents/orchestrator.py (pipeline, port 7406, skill "orchestrator", per_call=0, discovers+chains all others)
- client.py (CLI that calls orchestrator, pretty-prints report)
- scripts/setup-nodes.sh (6 daemons on ports 14101-14106, P2P 14201-14206, homes /tmp/anet-ci-u1 through u6, seed credits)
- scripts/run-all.sh (start all 6 agents as background processes)
- scripts/run.sh (main entry: setup + run agents + test with demo text)
- scripts/stop.sh (kill everything)
- README.md (with quickstart, architecture diagram, how it works)
- pyproject.toml

## Key Technical Details
- Each agent is a FastAPI app with /health, /meta, and /v1/* endpoints
- Each agent registers with its local anet daemon via SvcClient
- The orchestrator discovers agents by svc.discover(skill="tag"), NOT hardcoded URLs
- Reference code: ~/anet-p2p-starter-kit/examples/03-multi-agent-pipeline/
- anet is at /usr/local/bin/anet
- Python venv is at .venv/bin/python

## Step 3: Test
Run scripts/run.sh and verify the full pipeline works end-to-end.
