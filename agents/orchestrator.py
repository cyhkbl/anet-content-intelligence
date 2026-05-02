"""orchestrator-svc — the star of the show (self-composing edition).

Exposes:
  POST /v1/analyze  {text, intent?}    → full content-intelligence report.
  GET  /v1/discover                    → live catalogue of content-intel
                                         services found on the mesh.

Unlike a hard-coded pipeline, this orchestrator **does not know** which
specialists exist. On every request it queries the anet gateway for every
peer advertising the `content-intel` skill tag, decides which steps are
applicable to the input, and chains the calls. If someone boots a brand
new service tomorrow and registers it with `content-intel`, the
orchestrator picks it up automatically.
"""

from __future__ import annotations

import os
import re
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

# The ordered list of *known* skills the orchestrator has a handler for.
# A skill in this list is *not* required to be present — we use what the
# network offers. Skills discovered on the mesh that aren't in this list
# still show up in the discovery report so operators see the universe.
KNOWN_SKILLS = [
    "translate",          # zh → en
    "translate-en-zh",    # en → zh
    "extract",            # NER
    "keywords",           # TF-style keyword extraction
    "sentiment",          # polarity
    "summarise",          # extractive summary
    "classify",           # topic classification
    "factcheck",          # plausibility
]

# Path + input-builder per skill so we can call whichever we find.
SKILL_PATHS = {
    "translate":          ("/v1/translate",       lambda text, r: {"text": text}),
    "translate-en-zh":    ("/v1/translate-en-zh", lambda text, r: {"text": text}),
    "extract":            ("/v1/extract",         lambda text, r: {"text": text}),
    "keywords":           ("/v1/keywords",        lambda text, r: {"text": text, "top_k": 6}),
    "sentiment":          ("/v1/sentiment",       lambda text, r: {"text": text}),
    "summarise":          ("/v1/summarise",       lambda text, r: {"text": text, "max_sentences": 2}),
    "classify":           ("/v1/classify",        lambda text, r: {"text": r.get("summary") or text}),
    "factcheck":          ("/v1/factcheck",       lambda text, r: {"text": text}),
}

app = FastAPI(title=NAME)


def looks_chinese(text: str) -> bool:
    return any("\u4e00" <= ch <= "\u9fff" for ch in text)


def has_numbers_or_dates(text: str) -> bool:
    return bool(re.search(r"\d{2,}|\d+%|\b(19|20)\d{2}\b", text))


def decide_plan(text: str, available: dict[str, dict], intent: str) -> list[str]:
    """Turn the discovered skill map + intent into an ordered pipeline plan.

    We keep it deterministic and explainable — the /analyze response returns
    the ruleset alongside the chosen plan so demo-watchers can read it.
    """
    is_zh = looks_chinese(text)
    has_num = has_numbers_or_dates(text)
    wants_zh_out = intent == "translate-to-zh"

    plan: list[str] = []

    # 1. translate first if input is zh so downstream agents see english.
    if is_zh and "translate" in available:
        plan.append("translate")

    # 2. fan out over signal-gathering skills.
    for skill in ("extract", "keywords", "sentiment", "summarise"):
        if skill in available:
            plan.append(skill)

    # 3. classify runs on the summary (or translated text) for best accuracy.
    if "classify" in available:
        plan.append("classify")

    # 4. factcheck — only meaningful if the text has numbers/dates/percentages.
    if has_num and "factcheck" in available:
        plan.append("factcheck")

    # 5. optional: english→chinese at the end if user asked for it.
    if wants_zh_out and "translate-en-zh" in available:
        plan.append("translate-en-zh")

    return plan


def _peer_has_path(peer: dict, path: str) -> bool:
    for s in peer.get("services") or []:
        for p in s.get("paths") or []:
            prefix = p.get("prefix") if isinstance(p, dict) else str(p)
            if prefix and path.startswith(prefix):
                return True
    return False


def _discover_all(svc: SvcClient) -> tuple[dict[str, list[dict]], list[dict]]:
    """Return {skill: [peers...]} for every KNOWN skill plus the raw list
    of every content-intel service seen (for the discovery report).

    We filter each skill's peers to those that actually advertise the path
    we intend to call — the public mesh may contain other agents that
    borrow the same skill tag but expose a different API surface.
    """
    per_skill: dict[str, list[dict]] = {}
    seen_by_key: dict[tuple[str, str], dict] = {}
    for skill in KNOWN_SKILLS:
        path = SKILL_PATHS.get(skill, (None, None))[0]
        try:
            peers = svc.discover(skill=skill)
        except Exception:  # noqa: BLE001
            peers = []
        compatible: list[dict] = []
        for p in peers:
            if path and not _peer_has_path(p, path):
                continue
            compatible.append(p)
        if compatible:
            per_skill[skill] = compatible
        for p in peers:
            svc_name = (p.get("services") or [{}])[0].get("name") or ""
            key = (p.get("peer_id") or "", svc_name)
            if key not in seen_by_key:
                seen_by_key[key] = {
                    "peer_id": p.get("peer_id"),
                    "service": svc_name,
                    "skill": skill,
                    "cost": ((p.get("services") or [{}])[0].get("cost_model") or {}),
                    "description": (p.get("services") or [{}])[0].get("description", ""),
                }
    # Also sweep the meta content-intel tag so we surface new-skill services.
    try:
        for p in svc.discover(skill="content-intel"):
            svc_block = (p.get("services") or [{}])[0]
            key = (p.get("peer_id") or "", svc_block.get("name") or "")
            if key not in seen_by_key:
                seen_by_key[key] = {
                    "peer_id": p.get("peer_id"),
                    "service": svc_block.get("name"),
                    "skill": "(unknown-handler)",
                    "cost": svc_block.get("cost_model") or {},
                    "description": svc_block.get("description", ""),
                    "tags": svc_block.get("tags") or [],
                }
    except Exception:  # noqa: BLE001
        pass
    return per_skill, list(seen_by_key.values())


def _call(svc: SvcClient, target: dict, path: str, body: dict) -> tuple[dict, int]:
    t0 = time.time()
    resp = svc.call(
        target["peer_id"], target["services"][0]["name"],
        path, method="POST", body=body,
    )
    ms = int((time.time() - t0) * 1000)
    env = resp.get("body") or {}
    return (env if isinstance(env, dict) else {}, ms)


def run_pipeline(text: str, intent: str) -> dict[str, Any]:
    started = time.time()
    steps: list[dict[str, Any]] = []
    results: dict[str, Any] = {}

    with SvcClient(base_url=ANET_BASE_URL) as svc:
        per_skill, catalogue = _discover_all(svc)
        # Pick first peer per skill (RR mode handles load-balancing at svc side).
        available = {skill: peers[0] for skill, peers in per_skill.items()}

        plan = decide_plan(text, available, intent)

        for skill in plan:
            target = available[skill]
            path, build = SKILL_PATHS[skill]
            body = build(text, results)
            out, ms = _call(svc, target, path, body)
            svc_name = target["services"][0]["name"]
            cost = (target["services"][0].get("cost_model") or {}).get("per_call", 0)
            steps.append({
                "skill": skill,
                "peer": target["peer_id"][:18],
                "peer_full": target["peer_id"],
                "svc": svc_name,
                "ms": ms,
                "cost": cost if cost else 0,
            })
            # Merge typed outputs into the report.
            if skill == "translate":
                results["translated"] = out.get("translated")
                results["source_lang"] = out.get("lang")
            elif skill == "translate-en-zh":
                results["translated_zh"] = out.get("translated")
            elif skill == "extract":
                results["entities"] = out.get("entities", [])
                results["entity_count"] = out.get("count", 0)
            elif skill == "keywords":
                results["keywords"] = out.get("keywords", [])
            elif skill == "sentiment":
                results["sentiment"] = {
                    "label": out.get("label"), "score": out.get("score"),
                }
            elif skill == "summarise":
                results["summary"] = out.get("summary") or text
                results["source_lang"] = out.get("source_lang") or results.get("source_lang")
            elif skill == "classify":
                results["topic"] = out.get("topic")
                results["topic_confidence"] = out.get("confidence")
                results["topic_keywords"] = out.get("keywords", [])
            elif skill == "factcheck":
                results["factcheck"] = {
                    "verdict": out.get("verdict"),
                    "claims": out.get("claims", []),
                    "counts": out.get("counts", {}),
                }

    total_cost = sum(s["cost"] for s in steps)
    missing = [k for k in KNOWN_SKILLS if k not in available]
    return {
        **results,
        "input": text,
        "intent": intent,
        "pipeline": steps,
        "pipeline_plan": plan,
        "discovered_services": catalogue,
        "missing_skills": missing,
        "total_cost": total_cost,
        "total_ms": int((time.time() - started) * 1000),
        "agent": NAME,
    }


@app.get("/health")
def health():
    return {"ok": True, "agent": NAME}


@app.get("/meta")
def meta():
    return {
        "name": NAME, "version": "0.2.0", "skill": "orchestrator",
        "known_skills": KNOWN_SKILLS,
        "mode": "self-composing — discovers content-intel services dynamically",
    }


@app.get("/v1/discover")
def do_discover():
    """Catalogue of everything the orchestrator can see right now."""
    with SvcClient(base_url=ANET_BASE_URL) as svc:
        per_skill, catalogue = _discover_all(svc)
    return {
        "catalogue": catalogue,
        "by_skill": {k: [p["peer_id"] for p in v] for k, v in per_skill.items()},
        "known_skills": KNOWN_SKILLS,
        "count": len(catalogue),
    }


@app.post("/v1/analyze")
async def do_analyze(
    req: Request,
    x_agent_did: Optional[str] = Header(default=None, convert_underscores=True),
):
    body = await req.json() or {}
    text = body.get("text") or ""
    intent = (body.get("intent") or "analyze").strip()
    print(
        f"[orchestrator] caller={x_agent_did} intent={intent} text={text[:60]!r}",
        flush=True,
    )
    report = run_pipeline(text, intent)
    print(
        f"[orchestrator]   ↳ plan={report['pipeline_plan']} "
        f"ms={report['total_ms']} cost={report['total_cost']}",
        flush=True,
    )
    return JSONResponse(report)


def main() -> None:
    threading.Thread(
        target=lambda: register_until_ready(
            NAME, PORT,
            paths=["/v1/analyze", "/v1/discover", "/health", "/meta"],
            tags=["orchestrator", "pipeline", "content-intel"],
            description="Self-composing orchestrator over the content-intel mesh",
            per_call=PER_CALL, base_url=ANET_BASE_URL,
        ),
        daemon=True,
    ).start()
    uvicorn.run(app, host="127.0.0.1", port=PORT, log_level="warning")


if __name__ == "__main__":
    main()
