"""orchestrator-svc — the star of the show.

Exposes POST /v1/analyze {text: "…"} → full content-intelligence report.

Discovers all peer agents dynamically via the anet gateway (no hard-coded
URLs) and chains them into a single report:

  extract → sentiment → summarise → classify → compile

Each sub-call is logged so the client can present an audit trail.
"""

from __future__ import annotations

import os
import sys
import threading
import time
from typing import Any, Optional

import uvicorn
from fastapi import FastAPI, Header, Request
from fastapi.responses import JSONResponse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from register import register_until_ready  # noqa: E402

from anet_sdk import SvcClient  # noqa: E402

NAME = "orchestrator-svc"
PORT = int(os.environ.get("ORCHESTRATOR_PORT", "7406"))
PER_CALL = int(os.environ.get("ORCHESTRATOR_PER_CALL", "0"))
ANET_BASE_URL = os.environ.get("ANET_BASE_URL", "http://127.0.0.1:14106")

# Pipeline stages: (skill, path, input-builder, result-key).
# input-builder gets the original text + intermediate results dict.
PIPELINE_SKILLS = ["extract", "sentiment", "summarise", "classify"]

app = FastAPI(title=NAME)


def _discover(svc: SvcClient, skill: str, retries: int = 10) -> Optional[dict]:
    for _ in range(retries):
        peers = svc.discover(skill=skill)
        if peers:
            return peers[0]
        time.sleep(1)
    return None


def _call(svc: SvcClient, target: dict, path: str, body: dict) -> dict:
    resp = svc.call(
        target["peer_id"], target["services"][0]["name"],
        path, method="POST", body=body,
    )
    env = resp.get("body") or {}
    return env if isinstance(env, dict) else {}


def run_pipeline(text: str) -> dict[str, Any]:
    started = time.time()
    steps: list[dict[str, Any]] = []
    results: dict[str, Any] = {}
    missing: list[str] = []

    with SvcClient(base_url=ANET_BASE_URL) as svc:
        # Resolve all peers up front so we can report what was available.
        targets: dict[str, Optional[dict]] = {}
        for skill in PIPELINE_SKILLS:
            targets[skill] = _discover(svc, skill)
            if targets[skill] is None:
                missing.append(skill)

        # ── 1. extract ───────────────────────────────────────────────────
        if targets["extract"]:
            t0 = time.time()
            extract = _call(svc, targets["extract"], "/v1/extract", {"text": text})
            steps.append({
                "skill": "extract", "peer": targets["extract"]["peer_id"][:18],
                "ms": int((time.time() - t0) * 1000),
                "svc": targets["extract"]["services"][0]["name"],
            })
            results["entities"] = extract.get("entities", [])
            results["entity_count"] = extract.get("count", 0)

        # ── 2. sentiment (always run on original text) ───────────────────
        if targets["sentiment"]:
            t0 = time.time()
            sent = _call(svc, targets["sentiment"], "/v1/sentiment", {"text": text})
            steps.append({
                "skill": "sentiment", "peer": targets["sentiment"]["peer_id"][:18],
                "ms": int((time.time() - t0) * 1000),
                "svc": targets["sentiment"]["services"][0]["name"],
            })
            results["sentiment"] = {
                "label": sent.get("label"),
                "score": sent.get("score"),
            }

        # ── 3. summarise (auto-translates zh internally) ─────────────────
        summary_text = text
        if targets["summarise"]:
            t0 = time.time()
            summ = _call(
                svc, targets["summarise"], "/v1/summarise",
                {"text": text, "max_sentences": 2},
            )
            steps.append({
                "skill": "summarise", "peer": targets["summarise"]["peer_id"][:18],
                "ms": int((time.time() - t0) * 1000),
                "svc": targets["summarise"]["services"][0]["name"],
            })
            summary_text = summ.get("summary") or text
            results["summary"] = summary_text
            results["source_lang"] = summ.get("source_lang")

        # ── 4. classify (run on summary if available, else original) ─────
        if targets["classify"]:
            t0 = time.time()
            clf = _call(svc, targets["classify"], "/v1/classify", {"text": summary_text})
            steps.append({
                "skill": "classify", "peer": targets["classify"]["peer_id"][:18],
                "ms": int((time.time() - t0) * 1000),
                "svc": targets["classify"]["services"][0]["name"],
            })
            results["topic"] = clf.get("topic")
            results["topic_confidence"] = clf.get("confidence")
            results["topic_keywords"] = clf.get("keywords", [])

    results["pipeline"] = steps
    results["missing_skills"] = missing
    results["total_ms"] = int((time.time() - started) * 1000)
    results["input"] = text
    results["agent"] = NAME
    return results


@app.get("/health")
def health():
    return {"ok": True, "agent": NAME}


@app.get("/meta")
def meta():
    return {
        "name": NAME, "version": "0.1.0", "skill": "orchestrator",
        "calls_into": PIPELINE_SKILLS,
    }


@app.post("/v1/analyze")
async def do_analyze(
    req: Request,
    x_agent_did: Optional[str] = Header(default=None, convert_underscores=True),
):
    body = await req.json()
    text = (body or {}).get("text") or ""
    print(f"[orchestrator] caller={x_agent_did} text={text[:60]!r}", flush=True)
    report = run_pipeline(text)
    return JSONResponse(report)


def main() -> None:
    threading.Thread(
        target=lambda: register_until_ready(
            NAME, PORT,
            paths=["/v1/analyze", "/health", "/meta"],
            tags=["orchestrator", "pipeline", "content-intel"],
            description="Chains translate/extract/sentiment/summarise/classify",
            per_call=PER_CALL, base_url=ANET_BASE_URL,
        ),
        daemon=True,
    ).start()
    uvicorn.run(app, host="127.0.0.1", port=PORT, log_level="warning")


if __name__ == "__main__":
    main()
