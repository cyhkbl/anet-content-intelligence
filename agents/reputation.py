"""reputation-svc — Shell Market Protocol's global reputation registry.

A standalone P2P service that any provider, orchestrator, or auctioneer can
query. Reputation is per (peer_id, service) tuple. This service holds no
opinion about *what* a service does — it just records who succeeded and who
failed, and reports a numeric trust score that bidders can be discounted by.

Endpoints
---------
  POST /v1/report   {service, peer_id, success}        → record one outcome
  GET  /v1/lookup   ?service=&peer_id=                  → record for one peer
  GET  /v1/leaderboard ?limit=20                        → ranked list
  GET  /v1/bonus    ?service=&peer_id=                  → scoring discount
  GET  /v1/stats                                        → totals + uptime

Reputation = +1 per success, -2 per failure (failures hurt more, classic
trust dynamics). Bonus = clamp(score * 0.25, -3, 4) — applied as a discount
to bids in the auction (lower score = better).

Free service (per_call=0) — reputation is a public good on the mesh.
"""

from __future__ import annotations

import os
import sys
import threading
import time
from collections import defaultdict
from typing import Optional

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from register import register_until_ready  # noqa: E402

NAME = os.environ.get("REPUTATION_NAME", "reputation-svc")
PORT = int(os.environ.get("REPUTATION_PORT", "7420"))
ANET_BASE_URL = os.environ.get("ANET_BASE_URL", "http://127.0.0.1:14111")

app = FastAPI(title=NAME)

_lock = threading.Lock()
_started = time.time()
_records: dict[tuple[str, str], dict] = defaultdict(
    lambda: {"wins": 0, "losses": 0, "score": 0, "calls": 0,
             "first_seen": time.time(), "last_seen": time.time()}
)


def _key(peer_id: str, service: str) -> tuple[str, str]:
    return (peer_id or "", service or "")


def _bonus_for(score: int) -> float:
    return max(-3.0, min(4.0, score * 0.25))


def _snapshot(limit: Optional[int] = None) -> list[dict]:
    with _lock:
        rows = []
        for (peer, service), rec in _records.items():
            rows.append({
                "peer": peer, "short_peer": peer[:18],
                "service": service,
                "wins": rec["wins"], "losses": rec["losses"],
                "calls": rec["calls"], "score": rec["score"],
                "bonus": round(_bonus_for(rec["score"]), 2),
                "first_seen": rec["first_seen"], "last_seen": rec["last_seen"],
            })
    rows.sort(key=lambda r: (-r["score"], -r["calls"]))
    if limit is not None:
        rows = rows[:limit]
    return rows


@app.get("/health")
def health():
    return {"ok": True, "agent": NAME}


@app.get("/meta")
def meta():
    return {
        "name": NAME, "version": "1.0.0", "skill": "reputation",
        "protocol": "shell-market/reputation",
        "scoring": {"win": +1, "loss": -2,
                    "bonus_formula": "clamp(score*0.25, -3, +4)"},
        "free": True,
    }


@app.post("/v1/report")
async def do_report(req: Request):
    body = await req.json() or {}
    peer_id = (body.get("peer_id") or "").strip()
    service = (body.get("service") or "").strip()
    success = bool(body.get("success", True))
    if not service:
        return JSONResponse({"error": "service required"}, status_code=400)
    with _lock:
        rec = _records[_key(peer_id, service)]
        rec["calls"] += 1
        rec["last_seen"] = time.time()
        if success:
            rec["wins"] += 1
            rec["score"] += 1
        else:
            rec["losses"] += 1
            rec["score"] -= 2
        snap = {"peer": peer_id[:18], "service": service,
                "score": rec["score"], "calls": rec["calls"],
                "wins": rec["wins"], "losses": rec["losses"]}
    print(f"[reputation] report {service} peer={peer_id[:18]} "
          f"success={success} → score={snap['score']}", flush=True)
    return JSONResponse({"ok": True, "record": snap})


@app.get("/v1/lookup")
def do_lookup(service: str = "", peer_id: str = ""):
    with _lock:
        rec = _records.get(_key(peer_id, service))
    if not rec:
        return JSONResponse({"service": service, "peer": peer_id[:18],
                             "score": 0, "calls": 0, "bonus": 0.0,
                             "known": False})
    return JSONResponse({
        "service": service, "peer": peer_id[:18],
        "score": rec["score"], "calls": rec["calls"],
        "wins": rec["wins"], "losses": rec["losses"],
        "bonus": round(_bonus_for(rec["score"]), 2),
        "known": True,
    })


@app.get("/v1/leaderboard")
def do_leaderboard(limit: int = 20):
    rows = _snapshot(limit=limit)
    return JSONResponse({"leaderboard": rows, "count": len(rows)})


@app.get("/v1/bonus")
def do_bonus(service: str = "", peer_id: str = ""):
    with _lock:
        rec = _records.get(_key(peer_id, service))
    score = rec["score"] if rec else 0
    return JSONResponse({"service": service, "peer": peer_id[:18],
                         "score": score, "bonus": round(_bonus_for(score), 2)})


@app.get("/v1/stats")
def do_stats():
    with _lock:
        total_records = len(_records)
        total_calls = sum(r["calls"] for r in _records.values())
        total_wins = sum(r["wins"] for r in _records.values())
        total_losses = sum(r["losses"] for r in _records.values())
    return JSONResponse({
        "records": total_records,
        "total_calls": total_calls,
        "total_wins": total_wins,
        "total_losses": total_losses,
        "uptime_s": int(time.time() - _started),
    })


def main() -> None:
    threading.Thread(
        target=lambda: register_until_ready(
            NAME, PORT,
            paths=["/v1/report", "/v1/lookup", "/v1/leaderboard",
                   "/v1/bonus", "/v1/stats", "/health", "/meta"],
            tags=["reputation", "shell-market", "protocol"],
            description="Shell Market Protocol — global reputation registry",
            per_call=0, base_url=ANET_BASE_URL,
        ),
        daemon=True,
    ).start()
    uvicorn.run(app, host="127.0.0.1", port=PORT, log_level="warning")


if __name__ == "__main__":
    main()
