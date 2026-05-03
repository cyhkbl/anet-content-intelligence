"""settlement-svc — Shell Market Protocol settlement ledger.

Every auction close that pays a winner is recorded here as an immutable
ledger row: who paid whom, how many shells, for which auction, at what
time. This is the *economic* layer of Shell Market — the public proof
that the protocol is moving real value, not just routing requests.

Endpoints
---------
  POST /v1/record   {auction_id, payer_peer, payee_peer, payee_service,
                     skill, shells, eta_ms?}     → recorded row
  GET  /v1/ledger   ?limit=50                    → recent settlements
  GET  /v1/totals                                → aggregate volume + by-skill
  GET  /v1/provider/{service}                    → revenue + call count

Free service (per_call=0). All state is in-memory.
"""

from __future__ import annotations

import os
import sys
import threading
import time
from collections import defaultdict, deque

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from register import register_until_ready  # noqa: E402

NAME = os.environ.get("SETTLEMENT_NAME", "settlement-svc")
PORT = int(os.environ.get("SETTLEMENT_PORT", "7423"))
ANET_BASE_URL = os.environ.get("ANET_BASE_URL", "http://127.0.0.1:14111")
LEDGER_MAX = 500

app = FastAPI(title=NAME)
_lock = threading.Lock()
_started = time.time()
_ledger: deque[dict] = deque(maxlen=LEDGER_MAX)
_seq = 0


@app.get("/health")
def health():
    return {"ok": True, "agent": NAME}


@app.get("/meta")
def meta():
    return {"name": NAME, "version": "1.0.0", "skill": "settlement",
            "protocol": "shell-market/settlement",
            "ledger_max": LEDGER_MAX, "free": True}


@app.post("/v1/record")
async def do_record(req: Request):
    global _seq
    body = await req.json() or {}
    if not body.get("auction_id") or not body.get("payee_service"):
        return JSONResponse({"error": "auction_id and payee_service required"},
                            status_code=400)
    with _lock:
        _seq += 1
        row = {
            "seq": _seq,
            "auction_id": body.get("auction_id"),
            "skill": body.get("skill") or "?",
            "payer_peer": (body.get("payer_peer") or "")[:18],
            "payee_peer": (body.get("payee_peer") or "")[:18],
            "payee_service": body.get("payee_service"),
            "shells": int(body.get("shells", 0) or 0),
            "eta_ms": int(body.get("eta_ms", 0) or 0),
            "ts": time.time(),
        }
        _ledger.appendleft(row)
    print(f"[settlement] #{_seq} {row['skill']} → {row['payee_service']} "
          f"{row['shells']}sh", flush=True)
    return JSONResponse({"ok": True, "row": row})


@app.get("/v1/ledger")
def do_ledger(limit: int = 50):
    with _lock:
        rows = list(_ledger)[:limit]
    return JSONResponse({"ledger": rows, "count": len(rows)})


@app.get("/v1/totals")
def do_totals():
    with _lock:
        total_shells = sum(r["shells"] for r in _ledger)
        total_calls = len(_ledger)
        by_skill = defaultdict(lambda: {"shells": 0, "calls": 0})
        by_provider = defaultdict(lambda: {"shells": 0, "calls": 0})
        for r in _ledger:
            by_skill[r["skill"]]["shells"] += r["shells"]
            by_skill[r["skill"]]["calls"] += 1
            by_provider[r["payee_service"]]["shells"] += r["shells"]
            by_provider[r["payee_service"]]["calls"] += 1
    return JSONResponse({
        "total_shells": total_shells,
        "total_calls": total_calls,
        "uptime_s": int(time.time() - _started),
        "by_skill": dict(by_skill),
        "by_provider": dict(by_provider),
    })


@app.get("/v1/provider/{service}")
def do_provider(service: str):
    with _lock:
        rows = [r for r in _ledger if r["payee_service"] == service]
    return JSONResponse({
        "service": service,
        "shells": sum(r["shells"] for r in rows),
        "calls": len(rows),
        "recent": rows[:20],
    })


def main() -> None:
    threading.Thread(
        target=lambda: register_until_ready(
            NAME, PORT,
            paths=["/v1/record", "/v1/ledger", "/v1/totals", "/v1/provider",
                   "/health", "/meta"],
            tags=["settlement", "shell-market", "protocol"],
            description="Shell Market Protocol — settlement ledger",
            per_call=0, base_url=ANET_BASE_URL,
        ),
        daemon=True,
    ).start()
    uvicorn.run(app, host=os.environ.get("LISTEN_HOST", "0.0.0.0"),
                port=PORT, log_level="warning")


if __name__ == "__main__":
    main()
