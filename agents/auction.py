"""auction-svc — Shell Market Protocol's sealed reverse auction coordinator.

Any orchestrator can open an auction for any skill. Bidders submit sealed
bids (price + ETA + style + a self-reported load metric). When the auction
closes, the auctioneer scores every bid against the reputation registry and
returns the winner.

This is the *protocol layer*. It knows nothing about translation, sentiment,
or pipelines — it only knows about bids, scoring, and history. Any provider
that can answer /v1/quote is a valid bidder; any caller that pays per_call
is a valid auctioneer.

Endpoints
---------
  POST /v1/open       {skill, text, k?}                 → {auction_id, ...}
  POST /v1/bid        {auction_id, peer_id, service,
                       bid, eta_ms, style, load?}        → {accepted}
  POST /v1/close/{id}                                    → {winner, all_bids}
  GET  /v1/active                                        → list of open auctions
  GET  /v1/history    ?limit=20                          → recent closed auctions
  GET  /v1/auction/{id}                                  → full record

Scoring
-------
  score = bid + eta_ms / 20  - reputation_bonus

Lower wins. Ties broken by lower latency, then earliest bid.

The auctioneer queries reputation-svc /v1/bonus on close — if reputation-svc
is unreachable, all bidders get bonus=0 and the auction still resolves on
price + ETA. Graceful degradation is intentional: protocol services compose,
they don't depend.
"""

from __future__ import annotations

import os
import secrets
import sys
import threading
import time
from collections import deque
from typing import Any, Optional

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from register import register_until_ready  # noqa: E402

from anet_sdk import SvcClient  # noqa: E402

NAME = os.environ.get("AUCTION_NAME", "auction-svc")
PORT = int(os.environ.get("AUCTION_PORT", "7421"))
ANET_BASE_URL = os.environ.get("ANET_BASE_URL", "http://127.0.0.1:14112")
HISTORY_MAX = 100

app = FastAPI(title=NAME)

_lock = threading.Lock()
_active: dict[str, dict] = {}
_history: deque[dict] = deque(maxlen=HISTORY_MAX)


def _new_id() -> str:
    return "auc_" + secrets.token_hex(4)


def _fetch_bonus(peer_id: str, service: str) -> float:
    """Look up reputation bonus via the protocol — degrades to 0 on failure."""
    try:
        with SvcClient(base_url=ANET_BASE_URL) as svc:
            peers = svc.discover(skill="reputation", limit=5)
            for p in peers:
                s = (p.get("services") or [{}])[0]
                resp = svc.call(
                    p["peer_id"], s["name"],
                    f"/v1/bonus?service={service}&peer_id={peer_id}",
                    method="GET", body=None,
                )
                body = resp.get("body") or {}
                if isinstance(body, dict) and "bonus" in body:
                    return float(body["bonus"])
    except Exception:  # noqa: BLE001
        return 0.0
    return 0.0


def _score_bid(bid: dict, bonus: float) -> float:
    return float(bid.get("bid", 0)) + float(bid.get("eta_ms", 0)) / 20.0 - bonus


@app.get("/health")
def health():
    return {"ok": True, "agent": NAME}


@app.get("/meta")
def meta():
    return {
        "name": NAME, "version": "1.0.0", "skill": "auction",
        "protocol": "shell-market/auction",
        "scoring_formula": "bid + eta_ms/20 - reputation_bonus",
        "history_max": HISTORY_MAX,
        "free": True,
    }


@app.post("/v1/open")
async def do_open(req: Request):
    body = await req.json() or {}
    skill = (body.get("skill") or "").strip()
    text = body.get("text") or ""
    k = int(body.get("k") or 1)
    if not skill:
        return JSONResponse({"error": "skill required"}, status_code=400)
    aid = _new_id()
    rec = {
        "auction_id": aid,
        "skill": skill,
        "input_chars": len(text),
        "input_preview": text[:80],
        "k": k,
        "opened_at": time.time(),
        "bids": [],
        "status": "open",
    }
    with _lock:
        _active[aid] = rec
    print(f"[auction] open {aid} skill={skill} chars={len(text)} k={k}", flush=True)
    return JSONResponse({
        "auction_id": aid, "skill": skill, "k": k,
        "opened_at": rec["opened_at"],
    })


@app.post("/v1/bid")
async def do_bid(req: Request):
    body = await req.json() or {}
    aid = (body.get("auction_id") or "").strip()
    peer_id = (body.get("peer_id") or "").strip()
    service = (body.get("service") or "").strip()
    if not aid or not service:
        return JSONResponse({"error": "auction_id and service required"},
                            status_code=400)
    with _lock:
        auction = _active.get(aid)
        if not auction:
            return JSONResponse({"error": "auction not found or closed"},
                                status_code=404)
        if auction["status"] != "open":
            return JSONResponse({"error": "auction closed"}, status_code=409)
        bid = {
            "peer_id": peer_id,
            "short_peer": peer_id[:18],
            "service": service,
            "bid": int(body.get("bid", 0)),
            "eta_ms": int(body.get("eta_ms", 0)),
            "style": body.get("style") or "balanced",
            "load": body.get("load"),
            "submitted_at": time.time(),
        }
        auction["bids"].append(bid)
    print(f"[auction] bid {aid} {service} bid={bid['bid']} "
          f"eta={bid['eta_ms']}ms style={bid['style']}", flush=True)
    return JSONResponse({"accepted": True, "auction_id": aid,
                         "bid_count": len(auction["bids"])})


@app.post("/v1/close/{auction_id}")
def do_close(auction_id: str):
    with _lock:
        auction = _active.pop(auction_id, None)
    if not auction:
        return JSONResponse({"error": "auction not found"}, status_code=404)

    bids = auction["bids"]
    scored: list[dict] = []
    for b in bids:
        bonus = _fetch_bonus(b["peer_id"], b["service"])
        score = _score_bid(b, bonus)
        scored.append({**b, "rep_bonus": round(bonus, 2),
                       "score": round(score, 2)})
    scored.sort(key=lambda x: (x["score"], x["eta_ms"], x["submitted_at"]))

    k = max(1, int(auction.get("k") or 1))
    winners = scored[:k] if scored else []
    for i, b in enumerate(scored):
        b["winner"] = i < k

    auction["status"] = "closed"
    auction["closed_at"] = time.time()
    auction["bids"] = scored
    auction["winners"] = [
        {"peer_id": w["peer_id"], "short_peer": w["short_peer"],
         "service": w["service"], "bid": w["bid"], "eta_ms": w["eta_ms"],
         "score": w["score"], "rep_bonus": w["rep_bonus"]}
        for w in winners
    ]

    with _lock:
        _history.appendleft(auction)

    print(f"[auction] close {auction_id} bidders={len(scored)} "
          f"winner={winners[0]['service'] if winners else 'NONE'}",
          flush=True)
    return JSONResponse({
        "auction_id": auction_id,
        "skill": auction["skill"],
        "bid_count": len(scored),
        "winners": auction["winners"],
        "all_bids": scored,
        "duration_ms": int((auction["closed_at"] - auction["opened_at"]) * 1000),
    })


@app.get("/v1/active")
def do_active():
    with _lock:
        return JSONResponse({
            "active": [
                {"auction_id": a["auction_id"], "skill": a["skill"],
                 "opened_at": a["opened_at"], "bids": len(a["bids"]),
                 "input_preview": a["input_preview"]}
                for a in _active.values()
            ],
            "count": len(_active),
        })


@app.get("/v1/history")
def do_history(limit: int = 20):
    with _lock:
        rows = list(_history)[:limit]
    return JSONResponse({
        "history": [
            {"auction_id": a["auction_id"], "skill": a["skill"],
             "opened_at": a["opened_at"], "closed_at": a.get("closed_at"),
             "bid_count": len(a["bids"]),
             "winners": a.get("winners") or [],
             "input_preview": a.get("input_preview", "")}
            for a in rows
        ],
        "count": len(rows),
    })


@app.get("/v1/auction/{auction_id}")
def do_get(auction_id: str):
    with _lock:
        auction = _active.get(auction_id)
        if auction is None:
            for a in _history:
                if a["auction_id"] == auction_id:
                    auction = a
                    break
    if auction is None:
        return JSONResponse({"error": "not found"}, status_code=404)
    return JSONResponse(auction)


def main() -> None:
    threading.Thread(
        target=lambda: register_until_ready(
            NAME, PORT,
            paths=["/v1/open", "/v1/bid", "/v1/close", "/v1/active",
                   "/v1/history", "/v1/auction", "/health", "/meta"],
            tags=["auction", "shell-market", "protocol"],
            description="Shell Market Protocol — sealed reverse auction coordinator",
            per_call=0, base_url=ANET_BASE_URL,
        ),
        daemon=True,
    ).start()
    uvicorn.run(app, host=os.environ.get("LISTEN_HOST","0.0.0.0"), port=PORT, log_level="warning")


if __name__ == "__main__":
    main()
