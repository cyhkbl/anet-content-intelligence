"""market-feed-svc — Shell Market Protocol live event firehose (SSE).

Any market participant can POST events here, and any subscriber can read
them as a Server-Sent-Events stream. Powers the live tape on the market
dashboard and any external bot listening to the marketplace.

Endpoints
---------
  POST /v1/publish    {kind, data}                     → broadcast
  GET  /v1/stream                                      → SSE feed
  GET  /v1/recent     ?limit=50                        → buffered events
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import threading
import time
from collections import deque

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from register import register_until_ready  # noqa: E402

NAME = os.environ.get("MARKET_FEED_NAME", "market-feed-svc")
PORT = int(os.environ.get("MARKET_FEED_PORT", "7426"))
ANET_BASE_URL = os.environ.get("ANET_BASE_URL", "http://127.0.0.1:14113")
BUF_MAX = 500

app = FastAPI(title=NAME)
_lock = threading.Lock()
_events: deque[dict] = deque(maxlen=BUF_MAX)
_seq = 0
_subscribers: list[asyncio.Queue] = []


def _publish(ev: dict) -> None:
    global _seq
    with _lock:
        _seq += 1
        ev["seq"] = _seq
        ev["ts"] = ev.get("ts") or time.time()
        _events.appendleft(ev)
        subs = list(_subscribers)
    for q in subs:
        try:
            q.put_nowait(ev)
        except asyncio.QueueFull:
            pass


@app.get("/health")
def health():
    return {"ok": True, "agent": NAME}


@app.get("/meta")
def meta():
    return {"name": NAME, "version": "1.0.0", "skill": "market-feed",
            "protocol": "shell-market/feed", "buffer": BUF_MAX, "free": True}


@app.post("/v1/publish")
async def do_publish(req: Request):
    body = await req.json() or {}
    kind = (body.get("kind") or "").strip() or "event"
    data = body.get("data") or {}
    ev = {"kind": kind, "data": data}
    _publish(ev)
    return JSONResponse({"ok": True, "seq": ev["seq"], "kind": kind})


@app.get("/v1/recent")
def do_recent(limit: int = 50, kind: str = ""):
    with _lock:
        rows = list(_events)
    if kind:
        rows = [r for r in rows if r.get("kind") == kind]
    return JSONResponse({"events": rows[:limit], "count": len(rows[:limit])})


async def _sse_gen(request: Request):
    q: asyncio.Queue = asyncio.Queue(maxsize=200)
    with _lock:
        backlog = list(_events)[:20][::-1]
        _subscribers.append(q)
    try:
        for ev in backlog:
            yield f"data: {json.dumps(ev)}\n\n"
        while True:
            if await request.is_disconnected():
                break
            try:
                ev = await asyncio.wait_for(q.get(), timeout=15.0)
                yield f"data: {json.dumps(ev)}\n\n"
            except asyncio.TimeoutError:
                yield ": ping\n\n"
    finally:
        with _lock:
            try:
                _subscribers.remove(q)
            except ValueError:
                pass


@app.get("/v1/stream")
async def do_stream(request: Request):
    return StreamingResponse(_sse_gen(request), media_type="text/event-stream")


def main() -> None:
    threading.Thread(
        target=lambda: register_until_ready(
            NAME, PORT,
            paths=["/v1/publish", "/v1/stream", "/v1/recent",
                   "/health", "/meta"],
            tags=["market-feed", "shell-market", "protocol"],
            description="Shell Market Protocol — live event feed (SSE)",
            per_call=0, base_url=ANET_BASE_URL,
        ),
        daemon=True,
    ).start()
    uvicorn.run(app, host=os.environ.get("LISTEN_HOST", "0.0.0.0"),
                port=PORT, log_level="warning")


if __name__ == "__main__":
    main()
