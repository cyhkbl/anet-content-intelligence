"""Minimal Python SDK shim for the anet daemon's /api/svc/* endpoints.

The official AgentNetwork Python SDK isn't on PyPI under the name `anet`
(that name is squatted by an unrelated Datalayer package), so this module
implements the small `SvcClient` surface that the hackathon agents need by
calling the local daemon's REST API directly with httpx.

Endpoints used:
  POST /api/svc/register     register a local service
  POST /api/svc/unregister   tear down a registration
  GET  /api/svc/discover     find peers offering a skill tag
  POST /api/svc/call         call a remote peer's service via P2P
  GET  /api/svc/audit        recent call-log rows
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Iterable, Optional

import httpx


class SvcAPIError(RuntimeError):
    def __init__(self, status: int, message: str, payload: Any = None):
        super().__init__(f"svc api error {status}: {message}")
        self.status = status
        self.message = message
        self.payload = payload


class AuthMissingError(RuntimeError):
    pass


def _resolve_token(base_url: str) -> Optional[str]:
    tok = os.environ.get("ANET_TOKEN")
    if tok:
        return tok.strip()
    # Try $HOME/.anet/api_token (set per-daemon via the HOME env var).
    home = os.environ.get("HOME") or str(Path.home())
    candidate = Path(home) / ".anet" / "api_token"
    if candidate.exists():
        try:
            return candidate.read_text().strip()
        except OSError:
            pass
    return None


def _normalize_paths(paths: Iterable[str]) -> list[dict]:
    out: list[dict] = []
    for p in paths:
        if isinstance(p, dict):
            out.append(p)
        else:
            out.append({"prefix": str(p)})
    return out


class SvcClient:
    def __init__(self, base_url: str, token: Optional[str] = None, timeout: float = 30.0):
        self.base_url = base_url.rstrip("/")
        self.token = (token or _resolve_token(base_url) or "").strip()
        self._client = httpx.Client(timeout=timeout)

    def __enter__(self) -> "SvcClient":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def close(self) -> None:
        try:
            self._client.close()
        except Exception:  # noqa: BLE001
            pass

    # ── HTTP plumbing ────────────────────────────────────────────────────
    def _headers(self) -> dict:
        if not self.token:
            raise AuthMissingError(
                "no API token; set ANET_TOKEN or run with HOME=<daemon home>"
            )
        return {"Authorization": f"Bearer {self.token}",
                "Content-Type": "application/json"}

    def _request(self, method: str, path: str, **kwargs) -> dict:
        url = f"{self.base_url}{path}"
        resp = self._client.request(method, url, headers=self._headers(), **kwargs)
        try:
            payload = resp.json()
        except Exception:  # noqa: BLE001
            payload = {"raw": resp.text}
        if resp.status_code >= 400:
            msg = payload.get("message") or payload.get("error") or resp.text
            raise SvcAPIError(resp.status_code, msg, payload)
        return payload

    # ── service lifecycle ────────────────────────────────────────────────
    def register(
        self,
        *,
        name: str,
        endpoint: str,
        paths: Iterable[str],
        modes: Iterable[str] = ("rr",),
        per_call: Optional[int] = None,
        free: bool = False,
        tags: Iterable[str] = (),
        description: str = "",
        health_check: str = "/health",
        meta_path: str = "/meta",
    ) -> dict:
        body: dict[str, Any] = {
            "name": name,
            "endpoint": endpoint,
            "paths": _normalize_paths(paths),
            "modes": list(modes),
            "tags": list(tags),
            "description": description,
            "health_check": health_check,
            "meta_path": meta_path,
        }
        if free or not per_call or per_call <= 0:
            body["cost_model"] = {"free": True}
        else:
            body["cost_model"] = {"per_call": int(per_call)}
        return self._request("POST", "/api/svc/register", json=body)

    def unregister(self, name: str) -> dict:
        return self._request("POST", "/api/svc/unregister", json={"name": name})

    # ── discovery & calling ──────────────────────────────────────────────
    def discover(self, *, skill: str, limit: int = 10) -> list[dict]:
        resp = self._request(
            "GET", "/api/svc/discover", params={"skill": skill, "limit": limit}
        )
        return resp.get("results") or []

    def call(
        self,
        peer_id: str,
        service: str,
        path: str,
        *,
        method: str = "POST",
        body: Any = None,
        passthrough_status: bool = False,
    ) -> dict:
        payload = {
            "peer_id": peer_id,
            "service": service,
            "path": path,
            "method": method,
        }
        if body is not None:
            payload["body"] = body
        if passthrough_status:
            payload["passthrough_status"] = True
        return self._request("POST", "/api/svc/call", json=payload)

    def audit(self, *, limit: int = 50, name: Optional[str] = None) -> list[dict]:
        params: dict[str, Any] = {"limit": limit}
        if name:
            params["name"] = name
        resp = self._request("GET", "/api/svc/audit", params=params)
        return resp.get("calls") or []
