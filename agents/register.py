"""Shared registration helper used by every agent in the pipeline.

Waits for the local FastAPI backend to be healthy, then (re-)registers the
service with the local anet daemon gateway. Any previous registration with
the same name is cleared first so re-running agents is idempotent.
"""

from __future__ import annotations

import os
import sys
import time
from typing import Iterable, Optional

import httpx

from anet_sdk import SvcAPIError, SvcClient  # local shim


def register_until_ready(
    name: str,
    port: int,
    *,
    paths: Iterable[str],
    tags: Iterable[str],
    description: str,
    per_call: int = 0,
    base_url: Optional[str] = None,
) -> None:
    base_url = base_url or os.environ.get("ANET_BASE_URL", "http://127.0.0.1:13921")
    # The local daemon's /api/svc/register enforces a localhost endpoint, so
    # ANS routing always uses 127.0.0.1. Services *also* bind on LISTEN_HOST
    # (default 0.0.0.0), so direct HTTP from outside the box still works —
    # PUBLIC_HOST is what we advertise to humans and external callers.
    public_host = os.environ.get("PUBLIC_HOST", "127.0.0.1").strip() or "127.0.0.1"

    for _ in range(40):
        try:
            r = httpx.get(f"http://127.0.0.1:{port}/health", timeout=1.0)
            if r.status_code == 200:
                break
        except httpx.HTTPError:
            pass
        time.sleep(0.5)
    else:
        print(f"[{name}] backend on :{port} never came up", file=sys.stderr)
        raise SystemExit(1)

    with SvcClient(base_url=base_url) as svc:
        try:
            svc.unregister(name)
        except Exception:  # noqa: BLE001
            pass
        try:
            resp = svc.register(
                name=name,
                endpoint=f"http://127.0.0.1:{port}",
                paths=list(paths),
                modes=["rr"],
                per_call=per_call if per_call > 0 else None,
                free=per_call <= 0,
                tags=list(tags),
                description=description,
                health_check="/health",
                meta_path="/meta",
            )
        except SvcAPIError as e:
            print(f"[{name}] register failed: {e}", file=sys.stderr)
            raise

    ans = (resp.get("ans") or {})
    print(
        f"[{name}] ✓ registered :{port} "
        f"(public=http://{public_host}:{port}, per_call={per_call}, "
        f"ans.published={ans.get('published')})",
        file=sys.stderr,
    )
