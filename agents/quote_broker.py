"""quote-broker-svc — Shell Market Protocol quote aggregator.

Discovers every provider for a skill and pulls a fresh /v1/quote in one
shot. Useful for clients who want a market snapshot without running an
auction (e.g. dashboards, bots, analysts). Also caches the last quote
per (peer, service) so dashboards can render even when providers are
between bids.

Endpoints
---------
  POST /v1/aggregate  {skill, text}                    → all current quotes
  GET  /v1/snapshot   ?skill=                          → last cached quotes
  GET  /v1/spread     ?skill=                          → min/max/median price
"""

from __future__ import annotations

import os
import statistics
import sys
import threading
import time
from typing import Any

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from register import register_until_ready  # noqa: E402

from anet_sdk import SvcClient  # noqa: E402

NAME = os.environ.get("QUOTE_BROKER_NAME", "quote-broker-svc")
PORT = int(os.environ.get("QUOTE_BROKER_PORT", "7425"))
ANET_BASE_URL = os.environ.get("ANET_BASE_URL", "http://127.0.0.1:14113")

app = FastAPI(title=NAME)
_lock = threading.Lock()
_cache: dict[str, list[dict]] = {}


def _peer_has_path(peer: dict, path: str) -> bool:
    for s in peer.get("services") or []:
        for p in s.get("paths") or []:
            prefix = p.get("prefix") if isinstance(p, dict) else str(p)
            if prefix and path.startswith(prefix):
                return True
    return False


def _fetch_quotes(skill: str, text: str) -> list[dict]:
    out: list[dict] = []
    with SvcClient(base_url=ANET_BASE_URL) as svc:
        try:
            peers = svc.discover(skill=skill, limit=20)
        except Exception:  # noqa: BLE001
            peers = []
        for p in peers:
            if not _peer_has_path(p, "/v1/quote"):
                continue
            sb = (p.get("services") or [{}])[0]
            t0 = time.time()
            try:
                resp = svc.call(p["peer_id"], sb.get("name"), "/v1/quote",
                                method="POST", body={"text": text})
                body = resp.get("body") or {}
                if not isinstance(body, dict) or "bid" not in body:
                    continue
                rtt = int((time.time() - t0) * 1000)
                out.append({
                    "peer": p["peer_id"][:18],
                    "service": sb.get("name"),
                    "skill": skill,
                    "bid": body.get("bid"),
                    "eta_ms": body.get("eta_ms"),
                    "style": body.get("style"),
                    "load": body.get("load"),
                    "quote_rtt_ms": rtt,
                    "ts": time.time(),
                })
            except Exception:  # noqa: BLE001
                continue
    return out


@app.get("/health")
def health():
    return {"ok": True, "agent": NAME}


@app.get("/meta")
def meta():
    return {"name": NAME, "version": "1.0.0", "skill": "quote-broker",
            "protocol": "shell-market/quote-broker", "free": True}


@app.post("/v1/aggregate")
async def do_aggregate(req: Request):
    body = await req.json() or {}
    skill = (body.get("skill") or "").strip()
    text = body.get("text") or ""
    if not skill:
        return JSONResponse({"error": "skill required"}, status_code=400)
    quotes = _fetch_quotes(skill, text)
    with _lock:
        _cache[skill] = quotes
    print(f"[quote-broker] aggregate skill={skill} bidders={len(quotes)}",
          flush=True)
    return JSONResponse({"skill": skill, "quotes": quotes,
                         "count": len(quotes), "ts": time.time()})


@app.get("/v1/snapshot")
def do_snapshot(skill: str = ""):
    with _lock:
        if skill:
            return JSONResponse({"skill": skill,
                                 "quotes": _cache.get(skill, [])})
        return JSONResponse({"by_skill": dict(_cache),
                             "skills": list(_cache.keys())})


@app.get("/v1/spread")
def do_spread(skill: str = ""):
    with _lock:
        quotes = _cache.get(skill, [])
    bids = [q["bid"] for q in quotes if q.get("bid") is not None]
    if not bids:
        return JSONResponse({"skill": skill, "count": 0})
    return JSONResponse({
        "skill": skill, "count": len(bids),
        "min": min(bids), "max": max(bids),
        "median": statistics.median(bids),
        "mean": round(statistics.mean(bids), 2),
        "stdev": round(statistics.pstdev(bids), 2) if len(bids) > 1 else 0,
    })


def main() -> None:
    threading.Thread(
        target=lambda: register_until_ready(
            NAME, PORT,
            paths=["/v1/aggregate", "/v1/snapshot", "/v1/spread",
                   "/health", "/meta"],
            tags=["quote-broker", "shell-market", "protocol"],
            description="Shell Market Protocol — quote aggregator",
            per_call=0, base_url=ANET_BASE_URL,
        ),
        daemon=True,
    ).start()
    uvicorn.run(app, host=os.environ.get("LISTEN_HOST", "0.0.0.0"),
                port=PORT, log_level="warning")


if __name__ == "__main__":
    main()
