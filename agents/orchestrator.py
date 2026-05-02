"""orchestrator-svc — self-composing market-maker over the content-intel mesh.

Exposes:
  POST /v1/analyze     {text, intent?, consensus?}  → full report
  GET  /v1/discover                                  → live mesh catalogue
  GET  /v1/marketplace                               → reputation + win counts

The orchestrator does not know which specialists exist. On every request it:

  1. Discovers every peer offering each known content-intel skill.
  2. For each step in the plan, asks **every** competing peer for a /v1/quote
     bid (price + ETA + self-reported load).
  3. Scores bids by   `bid + eta_ms/20  -  reputation_bonus`   and picks the
     cheapest qualified provider — a reverse auction.
  4. For high-stakes skills (sentiment by default) it can run **consensus**:
     query the top-K cheapest providers in parallel and take the majority
     label, paying every consulted bidder.
  5. Tracks per-(peer_id, service) reputation across requests: every
     successful call earns +1, every failure -2. Reputation gives a small
     scoring bonus on the next auction so reliable providers slowly win
     more of the mesh — emergent market dynamics, no scheduling.

This is the primitive that turns AgentNetwork from "P2P RPC" into a
runtime-composed marketplace. New providers join just by tagging
`content-intel`; the orchestrator finds them next request and they
compete on price/ETA/reputation immediately.
"""

from __future__ import annotations

import os
import re
import sys
import threading
import time
from collections import Counter, defaultdict
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

# Skills the orchestrator has a handler for. Discovered skills not in this
# list still appear in the mesh catalogue.
KNOWN_SKILLS = [
    "translate", "translate-en-zh", "extract", "keywords",
    "sentiment", "summarise", "classify", "factcheck",
]

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

# Skills that benefit from cross-provider consensus voting.
CONSENSUS_SKILLS = {"sentiment"}
CONSENSUS_K = 2  # how many cheapest providers to poll for a vote

app = FastAPI(title=NAME)


# ── reputation ledger (in-memory, persists across requests) ─────────────
_rep_lock = threading.Lock()
_reputation: dict[tuple[str, str], dict[str, int]] = defaultdict(
    lambda: {"wins": 0, "losses": 0, "score": 0, "calls": 0}
)


def _rep_key(peer_id: str, svc_name: str) -> tuple[str, str]:
    return (peer_id or "", svc_name or "")


def _rep_bonus(peer_id: str, svc_name: str) -> float:
    """Reputation discount applied when scoring an auction bid (lower=better)."""
    with _rep_lock:
        rec = _reputation.get(_rep_key(peer_id, svc_name))
    if not rec:
        return 0.0
    return min(4.0, max(-3.0, rec["score"] * 0.25))


def _rep_record(peer_id: str, svc_name: str, *, success: bool) -> None:
    with _rep_lock:
        rec = _reputation[_rep_key(peer_id, svc_name)]
        rec["calls"] += 1
        if success:
            rec["wins"] += 1
            rec["score"] += 1
        else:
            rec["losses"] += 1
            rec["score"] -= 2


def _rep_snapshot() -> list[dict]:
    with _rep_lock:
        out = []
        for (peer, svc_name), rec in _reputation.items():
            out.append({
                "peer": peer[:20], "service": svc_name,
                "wins": rec["wins"], "losses": rec["losses"],
                "calls": rec["calls"], "score": rec["score"],
            })
        out.sort(key=lambda r: -r["score"])
        return out


# ── helpers ─────────────────────────────────────────────────────────────

def looks_chinese(text: str) -> bool:
    return any("\u4e00" <= ch <= "\u9fff" for ch in text)


def has_numbers_or_dates(text: str) -> bool:
    return bool(re.search(r"\d{2,}|\d+%|\b(19|20)\d{2}\b", text))


def decide_plan(text: str, available: dict[str, Any], intent: str) -> list[str]:
    is_zh = looks_chinese(text)
    has_num = has_numbers_or_dates(text)
    wants_zh_out = intent == "translate-to-zh"

    plan: list[str] = []
    if is_zh and "translate" in available:
        plan.append("translate")
    for skill in ("extract", "keywords", "sentiment", "summarise"):
        if skill in available:
            plan.append(skill)
    if "classify" in available:
        plan.append("classify")
    if has_num and "factcheck" in available:
        plan.append("factcheck")
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
    """{skill: [compatible peers...]} plus the raw catalogue of every
    content-intel service seen on the mesh. Each per-skill list contains
    *all* providers, not just one — the auction picks among them."""
    per_skill: dict[str, list[dict]] = {}
    seen_by_key: dict[tuple[str, str], dict] = {}
    for skill in KNOWN_SKILLS:
        path = SKILL_PATHS.get(skill, (None, None))[0]
        try:
            peers = svc.discover(skill=skill, limit=20)
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
            svc_block = (p.get("services") or [{}])[0]
            key = (p.get("peer_id") or "", svc_block.get("name") or "")
            if key not in seen_by_key:
                seen_by_key[key] = {
                    "peer_id": p.get("peer_id"),
                    "service": svc_block.get("name"),
                    "skill": skill,
                    "cost": svc_block.get("cost_model") or {},
                    "description": svc_block.get("description", ""),
                }
    try:
        for p in svc.discover(skill="content-intel", limit=20):
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


def _call(svc: SvcClient, target: dict, path: str, body: dict) -> tuple[dict, int, int]:
    """Returns (json_body, latency_ms, status). status==0 means transport fail."""
    t0 = time.time()
    try:
        resp = svc.call(
            target["peer_id"], target["services"][0]["name"],
            path, method="POST", body=body,
        )
        ms = int((time.time() - t0) * 1000)
        env = resp.get("body") or {}
        status = int(resp.get("status") or 200)
        return (env if isinstance(env, dict) else {}, ms, status)
    except Exception as e:  # noqa: BLE001
        ms = int((time.time() - t0) * 1000)
        return ({"error": str(e)}, ms, 0)


def _fetch_quote(svc: SvcClient, peer: dict, text: str) -> dict | None:
    """Ask one peer for a /v1/quote. Returns None if the peer doesn't speak
    the quote protocol or fails."""
    if not _peer_has_path(peer, "/v1/quote"):
        return None
    body, ms, status = _call(svc, peer, "/v1/quote", {"text": text})
    if status >= 400 or not isinstance(body, dict) or "bid" not in body:
        return None
    body["_quote_rtt_ms"] = ms
    body["_peer_id"] = peer["peer_id"]
    body["_svc_name"] = peer["services"][0]["name"]
    return body


def _score_quote(q: dict) -> float:
    """Lower is better.

    Composite: bid (shells) + eta_ms/20 (eta has ~5% the weight of cost)
    minus reputation bonus (reliable peers get a small discount).
    """
    bid = float(q.get("bid", 0))
    eta = float(q.get("eta_ms", 0))
    bonus = _rep_bonus(q["_peer_id"], q["_svc_name"])
    return bid + eta / 20.0 - bonus


def _auction_skill(
    svc: SvcClient, skill: str, peers: list[dict], text: str,
) -> dict[str, Any]:
    """Run a reverse auction for one skill. Returns the auction record."""
    quotes: list[dict] = []
    for p in peers:
        q = _fetch_quote(svc, p, text) or {
            "agent": (p.get("services") or [{}])[0].get("name", "?"),
            "skill": skill, "bid": 999, "eta_ms": 999, "load": None,
            "style": "no-quote",
            "_peer_id": p["peer_id"],
            "_svc_name": p["services"][0]["name"],
            "_no_quote": True,
        }
        quotes.append(q)
    quotes.sort(key=_score_quote)
    winner = quotes[0]
    return {
        "skill": skill,
        "quotes": [
            {
                "peer": q["_peer_id"][:18],
                "service": q["_svc_name"],
                "bid": q.get("bid"),
                "eta_ms": q.get("eta_ms"),
                "load": q.get("load"),
                "style": q.get("style"),
                "score": round(_score_quote(q), 2),
                "rep_bonus": round(_rep_bonus(q["_peer_id"], q["_svc_name"]), 2),
                "winner": q is winner,
                "no_quote": q.get("_no_quote", False),
            }
            for q in quotes
        ],
        "winner_peer": winner["_peer_id"],
        "winner_service": winner["_svc_name"],
    }


def run_pipeline(text: str, intent: str, consensus: bool) -> dict[str, Any]:
    started = time.time()
    steps: list[dict[str, Any]] = []
    auctions: list[dict[str, Any]] = []
    consensus_reports: list[dict[str, Any]] = []
    results: dict[str, Any] = {}

    with SvcClient(base_url=ANET_BASE_URL) as svc:
        per_skill, catalogue = _discover_all(svc)
        plan = decide_plan(text, per_skill, intent)

        for skill in plan:
            peers = per_skill[skill]
            path, build = SKILL_PATHS[skill]

            # ── auction ──────────────────────────────────────────────
            auction = _auction_skill(svc, skill, peers, text)
            auctions.append(auction)

            # ── consensus (sentiment): poll cheapest CONSENSUS_K  ────
            do_consensus = consensus and skill in CONSENSUS_SKILLS and len(peers) >= 2
            chosen_quotes = (
                auction["quotes"][:CONSENSUS_K] if do_consensus
                else [auction["quotes"][0]]
            )

            votes: list[tuple[dict, dict, int, int]] = []  # (quote, body, ms, status)
            for q in chosen_quotes:
                target = next(
                    (p for p in peers
                     if p["peer_id"].startswith(q["peer"])
                     and p["services"][0]["name"] == q["service"]),
                    None,
                )
                if target is None:
                    continue
                body = build(text, results)
                out, ms, status = _call(svc, target, path, body)
                ok = status < 400 and not out.get("error")
                _rep_record(target["peer_id"], target["services"][0]["name"], success=ok)
                votes.append((q, out, ms, status))
                cost_model = target["services"][0].get("cost_model") or {}
                cost = cost_model.get("per_call", 0) or 0
                steps.append({
                    "skill": skill,
                    "peer": target["peer_id"][:18],
                    "peer_full": target["peer_id"],
                    "svc": target["services"][0]["name"],
                    "ms": ms,
                    "cost": cost,
                    "status": status,
                    "auctioned": True,
                    "winner": q is auction["quotes"][0],
                    "consensus_member": do_consensus,
                })

            # ── pick the canonical answer ────────────────────────────
            primary_out = votes[0][1] if votes else {}
            if do_consensus and len(votes) > 1:
                labels = [
                    (v[1].get("label") or "neutral") for v in votes if isinstance(v[1], dict)
                ]
                tally = Counter(labels)
                majority_label, _ = tally.most_common(1)[0]
                avg_score = round(
                    sum((v[1].get("score") or 0.5) for v in votes) / len(votes), 3
                )
                consensus_reports.append({
                    "skill": skill,
                    "votes": [
                        {"peer": v[0]["peer"], "service": v[0]["service"],
                         "label": (v[1] or {}).get("label"),
                         "score": (v[1] or {}).get("score")}
                        for v in votes
                    ],
                    "tally": dict(tally),
                    "majority": majority_label,
                    "avg_score": avg_score,
                    "agreement": round(tally[majority_label] / len(votes), 2),
                })
                primary_out = {
                    **primary_out,
                    "label": majority_label,
                    "score": avg_score,
                    "agent": "consensus(" + ",".join(v[0]["service"] for v in votes) + ")",
                    "consensus": True,
                }

            # ── merge typed outputs ──────────────────────────────────
            out = primary_out
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
                    "consensus": out.get("consensus", False),
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
    missing = [k for k in KNOWN_SKILLS if k not in per_skill]
    return {
        **results,
        "input": text,
        "intent": intent,
        "pipeline": steps,
        "pipeline_plan": plan,
        "auctions": auctions,
        "consensus_reports": consensus_reports,
        "discovered_services": catalogue,
        "missing_skills": missing,
        "total_cost": total_cost,
        "total_ms": int((time.time() - started) * 1000),
        "reputation_top": _rep_snapshot()[:8],
        "consensus_enabled": consensus,
        "agent": NAME,
    }


@app.get("/health")
def health():
    return {"ok": True, "agent": NAME}


@app.get("/meta")
def meta():
    return {
        "name": NAME, "version": "0.3.0", "skill": "orchestrator",
        "known_skills": KNOWN_SKILLS,
        "consensus_skills": list(CONSENSUS_SKILLS),
        "mode": "self-composing reverse-auction marketplace",
    }


@app.get("/v1/discover")
def do_discover():
    with SvcClient(base_url=ANET_BASE_URL) as svc:
        per_skill, catalogue = _discover_all(svc)
    return {
        "catalogue": catalogue,
        "by_skill": {
            k: [{"peer": p["peer_id"][:18], "service": p["services"][0]["name"]}
                for p in v]
            for k, v in per_skill.items()
        },
        "known_skills": KNOWN_SKILLS,
        "count": len(catalogue),
    }


@app.get("/v1/marketplace")
def do_marketplace():
    """Reputation snapshot — who's earned the most trust over time."""
    return {
        "reputation": _rep_snapshot(),
        "consensus_skills": list(CONSENSUS_SKILLS),
        "consensus_k": CONSENSUS_K,
    }


@app.post("/v1/analyze")
async def do_analyze(
    req: Request,
    x_agent_did: Optional[str] = Header(default=None, convert_underscores=True),
):
    body = await req.json() or {}
    text = body.get("text") or ""
    intent = (body.get("intent") or "analyze").strip()
    consensus = bool(body.get("consensus", True))
    print(
        f"[orchestrator] caller={x_agent_did} intent={intent} consensus={consensus} "
        f"text={text[:60]!r}",
        flush=True,
    )
    report = run_pipeline(text, intent, consensus)
    print(
        f"[orchestrator]   ↳ plan={report['pipeline_plan']} "
        f"ms={report['total_ms']} cost={report['total_cost']} "
        f"auctions={len(report['auctions'])}",
        flush=True,
    )
    return JSONResponse(report)


def main() -> None:
    threading.Thread(
        target=lambda: register_until_ready(
            NAME, PORT,
            paths=["/v1/analyze", "/v1/discover", "/v1/marketplace",
                   "/health", "/meta"],
            tags=["orchestrator", "pipeline", "marketplace", "content-intel"],
            description="Self-composing reverse-auction marketplace orchestrator",
            per_call=PER_CALL, base_url=ANET_BASE_URL,
        ),
        daemon=True,
    ).start()
    uvicorn.run(app, host="127.0.0.1", port=PORT, log_level="warning")


if __name__ == "__main__":
    main()
