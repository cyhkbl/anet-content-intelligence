"""provider-registry-svc — Shell Market Protocol provider metadata registry.

Mesh discovery (`anet svc discover`) only knows endpoint, paths, and
cost. Real providers want to publish *more* — capabilities, SLA targets,
contact, version, region. This registry stores that side-channel data and
lets clients decide whether to bid against a given provider.

Endpoints
---------
  POST /v1/upsert     {service, peer_id, sla?, region?, version?,
                       capabilities?, contact?}        → recorded
  GET  /v1/list                                        → all providers
  GET  /v1/get/{service}                               → one provider
"""

from __future__ import annotations

import os
import sys
import threading
import time

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from register import register_until_ready  # noqa: E402

NAME = os.environ.get("PROVIDER_REGISTRY_NAME", "provider-registry-svc")
PORT = int(os.environ.get("PROVIDER_REGISTRY_PORT", "7427"))
ANET_BASE_URL = os.environ.get("ANET_BASE_URL", "http://127.0.0.1:14112")

app = FastAPI(title=NAME)
_lock = threading.Lock()
_providers: dict[str, dict] = {}


@app.get("/health")
def health():
    return {"ok": True, "agent": NAME}


@app.get("/meta")
def meta():
    return {"name": NAME, "version": "1.0.0", "skill": "provider-registry",
            "protocol": "shell-market/provider-registry", "free": True}


@app.post("/v1/upsert")
async def do_upsert(req: Request):
    body = await req.json() or {}
    service = (body.get("service") or "").strip()
    if not service:
        return JSONResponse({"error": "service required"}, status_code=400)
    rec = {
        "service": service,
        "peer_id": (body.get("peer_id") or "")[:64],
        "short_peer": (body.get("peer_id") or "")[:18],
        "sla": body.get("sla") or {},
        "region": body.get("region") or "global",
        "version": body.get("version") or "1.0.0",
        "capabilities": body.get("capabilities") or [],
        "contact": body.get("contact") or "",
        "updated_at": time.time(),
    }
    with _lock:
        existing = _providers.get(service) or {}
        rec["created_at"] = existing.get("created_at") or rec["updated_at"]
        _providers[service] = rec
    print(f"[provider-registry] upsert {service} caps={rec['capabilities']}",
          flush=True)
    return JSONResponse({"ok": True, "record": rec})


@app.get("/v1/list")
def do_list():
    with _lock:
        rows = list(_providers.values())
    return JSONResponse({"providers": rows, "count": len(rows)})


@app.get("/v1/get/{service}")
def do_get(service: str):
    with _lock:
        rec = _providers.get(service)
    if not rec:
        return JSONResponse({"error": "not found"}, status_code=404)
    return JSONResponse(rec)


def main() -> None:
    threading.Thread(
        target=lambda: register_until_ready(
            NAME, PORT,
            paths=["/v1/upsert", "/v1/list", "/v1/get",
                   "/health", "/meta"],
            tags=["provider-registry", "shell-market", "protocol"],
            description="Shell Market Protocol — provider metadata registry",
            per_call=0, base_url=ANET_BASE_URL,
        ),
        daemon=True,
    ).start()
    uvicorn.run(app, host=os.environ.get("LISTEN_HOST", "0.0.0.0"),
                port=PORT, log_level="warning")


if __name__ == "__main__":
    main()
