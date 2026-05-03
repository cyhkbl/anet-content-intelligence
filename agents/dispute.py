"""dispute-svc — Shell Market Protocol dispute / arbitration registry.

When a caller is dissatisfied with a winner's output, they file a dispute
here. Disputes carry a stake; if upheld, reputation-svc penalises the
provider; if dismissed, the staker pays. Demo arbitration is automatic
(simple heuristic) so the loop closes without a human juror.

Endpoints
---------
  POST /v1/file       {auction_id, accuser, accused_service, reason, stake?}
                                                       → dispute_id
  POST /v1/resolve/{dispute_id}                        → arbitration verdict
  GET  /v1/active                                      → unresolved disputes
  GET  /v1/history    ?limit=50                        → recent verdicts
"""

from __future__ import annotations

import os
import secrets
import sys
import threading
import time
from collections import deque

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from register import register_until_ready  # noqa: E402

NAME = os.environ.get("DISPUTE_NAME", "dispute-svc")
PORT = int(os.environ.get("DISPUTE_PORT", "7424"))
ANET_BASE_URL = os.environ.get("ANET_BASE_URL", "http://127.0.0.1:14112")

app = FastAPI(title=NAME)
_lock = threading.Lock()
_active: dict[str, dict] = {}
_history: deque[dict] = deque(maxlen=200)


def _new_id() -> str:
    return "dis_" + secrets.token_hex(4)


def _arbitrate(reason: str) -> tuple[str, str]:
    """Cheap deterministic arbiter — keeps the demo honest enough."""
    r = (reason or "").lower()
    if any(k in r for k in ("empty", "blank", "missing", "no result", "timeout")):
        return ("upheld", "missing/incomplete output")
    if any(k in r for k in ("wrong", "incorrect", "garbage")):
        return ("upheld", "output flagged incorrect")
    if any(k in r for k in ("slow", "latency", "delay")):
        return ("partial", "SLA breach but output usable")
    return ("dismissed", "no protocol violation found")


@app.get("/health")
def health():
    return {"ok": True, "agent": NAME}


@app.get("/meta")
def meta():
    return {"name": NAME, "version": "1.0.0", "skill": "dispute",
            "protocol": "shell-market/dispute",
            "verdicts": ["upheld", "partial", "dismissed"], "free": True}


@app.post("/v1/file")
async def do_file(req: Request):
    body = await req.json() or {}
    if not body.get("accused_service"):
        return JSONResponse({"error": "accused_service required"}, status_code=400)
    did = _new_id()
    rec = {
        "dispute_id": did,
        "auction_id": body.get("auction_id", ""),
        "accuser": (body.get("accuser") or "")[:18],
        "accused_service": body.get("accused_service"),
        "reason": (body.get("reason") or "").strip()[:200],
        "stake": int(body.get("stake", 5) or 5),
        "filed_at": time.time(),
        "status": "open",
    }
    with _lock:
        _active[did] = rec
    print(f"[dispute] file {did} vs {rec['accused_service']} "
          f"reason={rec['reason'][:40]!r}", flush=True)
    return JSONResponse({"dispute_id": did, **rec})


@app.post("/v1/resolve/{dispute_id}")
def do_resolve(dispute_id: str):
    with _lock:
        rec = _active.pop(dispute_id, None)
    if not rec:
        return JSONResponse({"error": "not found"}, status_code=404)
    verdict, rationale = _arbitrate(rec["reason"])
    rec.update({
        "status": "closed", "verdict": verdict, "rationale": rationale,
        "resolved_at": time.time(),
    })
    with _lock:
        _history.appendleft(rec)
    print(f"[dispute] resolve {dispute_id} → {verdict} ({rationale})", flush=True)
    return JSONResponse(rec)


@app.get("/v1/active")
def do_active():
    with _lock:
        rows = list(_active.values())
    return JSONResponse({"active": rows, "count": len(rows)})


@app.get("/v1/history")
def do_history(limit: int = 50):
    with _lock:
        rows = list(_history)[:limit]
    return JSONResponse({"history": rows, "count": len(rows)})


def main() -> None:
    threading.Thread(
        target=lambda: register_until_ready(
            NAME, PORT,
            paths=["/v1/file", "/v1/resolve", "/v1/active", "/v1/history",
                   "/health", "/meta"],
            tags=["dispute", "shell-market", "protocol"],
            description="Shell Market Protocol — dispute resolution registry",
            per_call=0, base_url=ANET_BASE_URL,
        ),
        daemon=True,
    ).start()
    uvicorn.run(app, host=os.environ.get("LISTEN_HOST", "0.0.0.0"),
                port=PORT, log_level="warning")


if __name__ == "__main__":
    main()
