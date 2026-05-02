"""dashboard-svc — terminal-style web UI for the content-intel mesh.

Serves a single HTML page at `/` that visualises the live P2P service
catalogue discovered through the anet gateway and streams pipeline
execution events via Server-Sent Events.

Endpoints:
  GET  /                 - HTML dashboard
  GET  /api/services     - JSON catalogue of content-intel services
  POST /api/analyze      - kicks off an analyze run, returns the full report
  GET  /api/stream       - SSE stream of real-time events (discovery + calls)
  GET  /health           - liveness probe

This is an observability layer — it calls the orchestrator's /v1/analyze
via P2P and also peeks the anet audit log to surface activity from any
agent on the mesh, whether or not it's ours.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Optional

import httpx
import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from anet_sdk import SvcClient  # noqa: E402

NAME = "dashboard-svc"
PORT = int(os.environ.get("DASHBOARD_PORT", "7400"))
ANET_BASE_URL = os.environ.get("ANET_BASE_URL", "http://127.0.0.1:14101")
STATIC_DIR = Path(__file__).resolve().parents[1] / "static"

# Every daemon the dashboard may peek for audit events. Falls back cleanly
# if some daemons are down.
DAEMON_HOMES = [
    ("u1 translate",       "http://127.0.0.1:14101", "/tmp/anet-ci-u1"),
    ("u2 extract",         "http://127.0.0.1:14102", "/tmp/anet-ci-u2"),
    ("u3 sentiment",       "http://127.0.0.1:14103", "/tmp/anet-ci-u3"),
    ("u4 summarise",       "http://127.0.0.1:14104", "/tmp/anet-ci-u4"),
    ("u5 classify",        "http://127.0.0.1:14105", "/tmp/anet-ci-u5"),
    ("u6 orchestrator",    "http://127.0.0.1:14106", "/tmp/anet-ci-u6"),
    ("u7 factcheck",       "http://127.0.0.1:14107", "/tmp/anet-ci-u7"),
    ("u8 translate-en-zh", "http://127.0.0.1:14108", "/tmp/anet-ci-u8"),
    ("u9 keywords",        "http://127.0.0.1:14109", "/tmp/anet-ci-u9"),
]

SKILL_KNOWN = [
    "translate", "translate-en-zh", "extract", "keywords",
    "sentiment", "summarise", "classify", "factcheck", "orchestrator",
]

DAEMON_HOMES.append(("u10 alt-providers", "http://127.0.0.1:14110", "/tmp/anet-ci-u10"))

app = FastAPI(title=NAME)

# In-process broadcast bus for SSE subscribers.
_subscribers: set[asyncio.Queue] = set()


def _broadcast(event: dict[str, Any]) -> None:
    event.setdefault("ts", time.time())
    for q in list(_subscribers):
        try:
            q.put_nowait(event)
        except asyncio.QueueFull:
            pass


def _discover_catalogue() -> list[dict]:
    """Pull a live catalogue of content-intel services from the local daemon."""
    seen: dict[tuple[str, str], dict] = {}
    with SvcClient(base_url=ANET_BASE_URL) as svc:
        for skill in SKILL_KNOWN:
            try:
                peers = svc.discover(skill=skill)
            except Exception:  # noqa: BLE001
                peers = []
            for p in peers:
                svc_block = (p.get("services") or [{}])[0]
                key = (p.get("peer_id") or "", svc_block.get("name") or "")
                if key in seen:
                    continue
                seen[key] = {
                    "peer_id": p.get("peer_id"),
                    "short_peer": (p.get("peer_id") or "")[:18],
                    "service": svc_block.get("name"),
                    "skill": skill,
                    "cost": svc_block.get("cost_model") or {},
                    "tags": svc_block.get("tags") or [],
                    "description": svc_block.get("description", ""),
                }
        try:
            for p in svc.discover(skill="content-intel"):
                svc_block = (p.get("services") or [{}])[0]
                key = (p.get("peer_id") or "", svc_block.get("name") or "")
                if key in seen:
                    continue
                seen[key] = {
                    "peer_id": p.get("peer_id"),
                    "short_peer": (p.get("peer_id") or "")[:18],
                    "service": svc_block.get("name"),
                    "skill": "(unknown-handler)",
                    "cost": svc_block.get("cost_model") or {},
                    "tags": svc_block.get("tags") or [],
                    "description": svc_block.get("description", ""),
                }
        except Exception:  # noqa: BLE001
            pass
    return list(seen.values())


async def _call_orchestrator(text: str, intent: str) -> dict:
    """Find orchestrator via discover, call /v1/analyze through the gateway.

    Filters the discover results to peers that actually expose /v1/analyze
    so we don't accidentally call an unrelated orchestrator that shares the
    same skill tag but a different API surface.
    """
    loop = asyncio.get_event_loop()

    def _sync() -> dict:
        with SvcClient(base_url=ANET_BASE_URL) as svc:
            peers = svc.discover(skill="orchestrator")
            target = None
            for p in peers:
                for s in p.get("services") or []:
                    for path in s.get("paths") or []:
                        prefix = path.get("prefix") if isinstance(path, dict) else str(path)
                        if prefix and "/v1/analyze".startswith(prefix):
                            target = p
                            break
                    if target:
                        break
                if target:
                    break
            if not target:
                return {"error": "no compatible orchestrator on mesh"}
            resp = svc.call(
                target["peer_id"], target["services"][0]["name"],
                "/v1/analyze", method="POST",
                body={"text": text, "intent": intent, "consensus": True},
            )
            body = resp.get("body") or {}
            return body if isinstance(body, dict) else {"body": body}

    return await loop.run_in_executor(None, _sync)


def _audit_snapshot(limit: int = 5) -> list[dict]:
    """Gather the last N audit rows from every reachable daemon."""
    out: list[dict] = []
    for label, base, home in DAEMON_HOMES:
        tok_path = Path(home) / ".anet" / "api_token"
        if not tok_path.exists():
            continue
        try:
            tok = tok_path.read_text().strip()
            with SvcClient(base_url=base, token=tok) as s:
                rows = s.audit(limit=limit)
        except Exception:  # noqa: BLE001
            continue
        for r in rows:
            out.append({
                "daemon": label,
                "service": r.get("service"),
                "path": r.get("path"),
                "method": r.get("method"),
                "status": r.get("status"),
                "cost": r.get("cost", 0),
                "ts": r.get("ts"),
            })
    return out


# ── routes ──────────────────────────────────────────────────────────────

@app.get("/health")
def health():
    return {"ok": True, "agent": NAME}


@app.get("/")
def index():
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/services")
def api_services():
    catalogue = _discover_catalogue()
    return JSONResponse({"services": catalogue, "count": len(catalogue)})


@app.get("/api/audit")
def api_audit():
    return JSONResponse({"rows": _audit_snapshot(limit=10)})


@app.post("/api/analyze")
async def api_analyze(req: Request):
    body = await req.json() or {}
    text = (body.get("text") or "").strip()
    intent = (body.get("intent") or "analyze").strip()
    if not text:
        return JSONResponse({"error": "text is required"}, status_code=400)

    _broadcast({"type": "pipeline.start", "text": text[:120], "intent": intent})
    result = await _call_orchestrator(text, intent)

    # Broadcast each auction so the UI can render the bid table.
    for auction in result.get("auctions") or []:
        _broadcast({
            "type": "pipeline.auction",
            "skill": auction.get("skill"),
            "quotes": auction.get("quotes") or [],
            "winner_peer": (auction.get("winner_peer") or "")[:18],
            "winner_service": auction.get("winner_service"),
        })

    # Re-broadcast each step as its own event so the UI can animate them.
    for step in result.get("pipeline") or []:
        _broadcast({
            "type": "pipeline.step",
            "skill": step.get("skill"),
            "svc": step.get("svc"),
            "peer": step.get("peer"),
            "ms": step.get("ms"),
            "cost": step.get("cost", 0),
            "winner": step.get("winner", True),
            "consensus_member": step.get("consensus_member", False),
        })

    for cr in result.get("consensus_reports") or []:
        _broadcast({
            "type": "pipeline.consensus",
            "skill": cr.get("skill"),
            "tally": cr.get("tally"),
            "majority": cr.get("majority"),
            "agreement": cr.get("agreement"),
            "votes": cr.get("votes"),
        })
    _broadcast({
        "type": "pipeline.end",
        "plan": result.get("pipeline_plan"),
        "total_ms": result.get("total_ms"),
        "total_cost": result.get("total_cost"),
        "topic": result.get("topic"),
        "sentiment": (result.get("sentiment") or {}).get("label"),
        "reputation": result.get("reputation_top") or [],
    })
    return JSONResponse(result)


@app.get("/api/marketplace")
async def api_marketplace():
    """Proxy the orchestrator's /v1/marketplace via P2P."""
    loop = asyncio.get_event_loop()

    def _sync() -> dict:
        with SvcClient(base_url=ANET_BASE_URL) as svc:
            peers = svc.discover(skill="orchestrator")
            for p in peers:
                for s in p.get("services") or []:
                    for path in s.get("paths") or []:
                        prefix = path.get("prefix") if isinstance(path, dict) else str(path)
                        if prefix and "/v1/marketplace".startswith(prefix):
                            resp = svc.call(
                                p["peer_id"], s["name"],
                                "/v1/marketplace", method="GET", body=None,
                            )
                            body = resp.get("body") or {}
                            return body if isinstance(body, dict) else {"body": body}
            return {"reputation": [], "note": "no orchestrator with /v1/marketplace yet"}

    return JSONResponse(await loop.run_in_executor(None, _sync))


async def _watch_mesh(stop: asyncio.Event) -> None:
    """Background task: every few seconds, broadcast the current catalogue."""
    last_snapshot: list[dict] = []
    while not stop.is_set():
        try:
            cat = _discover_catalogue()
        except Exception:  # noqa: BLE001
            cat = []
        key = [(c.get("peer_id"), c.get("service")) for c in cat]
        if key != [(c.get("peer_id"), c.get("service")) for c in last_snapshot]:
            _broadcast({"type": "mesh.update", "catalogue": cat, "count": len(cat)})
            last_snapshot = cat
        try:
            await asyncio.wait_for(stop.wait(), timeout=5.0)
        except asyncio.TimeoutError:
            pass


@app.on_event("startup")
async def _on_start() -> None:
    app.state.stop = asyncio.Event()
    app.state.watcher = asyncio.create_task(_watch_mesh(app.state.stop))


@app.on_event("shutdown")
async def _on_stop() -> None:
    app.state.stop.set()


@app.get("/api/stream")
async def api_stream(request: Request):
    q: asyncio.Queue = asyncio.Queue(maxsize=256)
    _subscribers.add(q)
    try:
        # Greet with a catalogue so the UI can render immediately.
        cat = _discover_catalogue()
        await q.put({"type": "mesh.update", "catalogue": cat, "count": len(cat),
                     "ts": time.time()})
    except Exception:  # noqa: BLE001
        pass

    async def _gen():
        try:
            while True:
                if await request.is_disconnected():
                    break
                try:
                    ev = await asyncio.wait_for(q.get(), timeout=10.0)
                except asyncio.TimeoutError:
                    yield ": keep-alive\n\n"
                    continue
                yield f"data: {json.dumps(ev, ensure_ascii=False)}\n\n"
        finally:
            _subscribers.discard(q)

    return StreamingResponse(_gen(), media_type="text/event-stream")


def main() -> None:
    print(f"[{NAME}] serving on http://127.0.0.1:{PORT}")
    uvicorn.run(app, host="127.0.0.1", port=PORT, log_level="warning")


if __name__ == "__main__":
    main()
