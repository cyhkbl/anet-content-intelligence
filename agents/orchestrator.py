"""orchestrator-svc — pipeline composer over the Shell Market Protocol.

This orchestrator does not run its own auction or reputation ledger. It is a
*client* of the Shell Market Protocol services on the mesh:

  - auction-svc      runs sealed reverse auctions and picks winners
  - reputation-svc   tracks per-(peer, service) trust over time

For every skill in a content-intel plan, the orchestrator:

  1. opens an auction with auction-svc      (POST /v1/open)
  2. asks every candidate provider for a /v1/quote
  3. forwards each quote into the auction   (POST /v1/bid)
  4. closes the auction                     (POST /v1/close)
  5. calls the winner's actual skill endpoint
  6. reports the outcome to reputation-svc  (POST /v1/report)

If the protocol services are unreachable the orchestrator degrades to a
local "best-bid" pick — auctions and reputation are *protocol* services, not
hard dependencies. New providers join the marketplace just by tagging
`content-intel` and exposing `/v1/quote`.

Endpoints
---------
  POST /v1/analyze     {text, intent?, consensus?}    → full report
  GET  /v1/discover                                    → mesh catalogue
  GET  /v1/marketplace                                 → live reputation+auction
"""

from __future__ import annotations

import os
import re
import sys
import threading
import time
from collections import Counter
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

CONSENSUS_SKILLS = {"sentiment"}
CONSENSUS_K = 2

app = FastAPI(title=NAME)


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


def _discover_protocol(svc: SvcClient, skill: str) -> Optional[dict]:
    """Find the first peer offering a Shell Market protocol service."""
    try:
        peers = svc.discover(skill=skill, limit=5)
    except Exception:  # noqa: BLE001
        return None
    for p in peers:
        s_list = p.get("services") or []
        if s_list:
            return {"peer_id": p["peer_id"], "service": s_list[0]["name"]}
    return None


def _proto_call(svc: SvcClient, target: dict, path: str,
                method: str = "POST", body: Any = None) -> dict:
    try:
        resp = svc.call(target["peer_id"], target["service"],
                        path, method=method, body=body)
        out = resp.get("body") or {}
        return out if isinstance(out, dict) else {}
    except Exception as e:  # noqa: BLE001
        return {"error": str(e)}


def _call(svc: SvcClient, target: dict, path: str, body: dict) -> tuple[dict, int, int]:
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
    if not _peer_has_path(peer, "/v1/quote"):
        return None
    body, ms, status = _call(svc, peer, "/v1/quote", {"text": text})
    if status >= 400 or not isinstance(body, dict) or "bid" not in body:
        return None
    body["_quote_rtt_ms"] = ms
    body["_peer_id"] = peer["peer_id"]
    body["_svc_name"] = peer["services"][0]["name"]
    return body


def _local_score(q: dict) -> float:
    """Fallback scoring when auction-svc is unreachable."""
    return float(q.get("bid", 0)) + float(q.get("eta_ms", 0)) / 20.0


def _run_auction_via_protocol(
    svc: SvcClient, auction_target: dict,
    skill: str, peers: list[dict], text: str,
) -> dict[str, Any]:
    """Open → bid → close, all through auction-svc on the mesh."""
    open_resp = _proto_call(svc, auction_target, "/v1/open",
                            body={"skill": skill, "text": text,
                                  "k": CONSENSUS_K if skill in CONSENSUS_SKILLS else 1})
    aid = open_resp.get("auction_id")
    if not aid:
        return {"_protocol_failed": True, "_reason": open_resp.get("error", "no auction id")}

    quotes: list[dict] = []
    for p in peers:
        q = _fetch_quote(svc, p, text)
        if q is None:
            q = {
                "agent": (p.get("services") or [{}])[0].get("name", "?"),
                "skill": skill, "bid": 999, "eta_ms": 999, "load": None,
                "style": "no-quote",
                "_peer_id": p["peer_id"],
                "_svc_name": p["services"][0]["name"],
                "_no_quote": True,
            }
        quotes.append(q)
        _proto_call(svc, auction_target, "/v1/bid", body={
            "auction_id": aid,
            "peer_id": q["_peer_id"],
            "service": q["_svc_name"],
            "bid": int(q.get("bid", 999)),
            "eta_ms": int(q.get("eta_ms", 999)),
            "style": q.get("style", "balanced"),
            "load": q.get("load"),
        })

    close_resp = _proto_call(svc, auction_target,
                             f"/v1/close/{aid}", body={})
    if "error" in close_resp:
        return {"_protocol_failed": True, "_reason": close_resp["error"],
                "auction_id": aid}

    return {
        "auction_id": aid,
        "skill": skill,
        "via_protocol": True,
        "quotes": [
            {
                "peer": (b.get("short_peer") or b.get("peer_id", ""))[:18],
                "service": b.get("service"),
                "bid": b.get("bid"),
                "eta_ms": b.get("eta_ms"),
                "load": b.get("load"),
                "style": b.get("style"),
                "score": b.get("score"),
                "rep_bonus": b.get("rep_bonus", 0.0),
                "winner": b.get("winner", False),
                "no_quote": False,
            }
            for b in close_resp.get("all_bids") or []
        ],
        "winners": close_resp.get("winners") or [],
        "winner_peer": (close_resp.get("winners") or [{}])[0].get("peer_id"),
        "winner_service": (close_resp.get("winners") or [{}])[0].get("service"),
        "duration_ms": close_resp.get("duration_ms"),
    }


def _run_auction_local(svc: SvcClient, skill: str, peers: list[dict],
                       text: str) -> dict[str, Any]:
    """Fallback when auction-svc is unreachable — score locally."""
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
    quotes.sort(key=_local_score)
    winner = quotes[0] if quotes else None
    return {
        "skill": skill,
        "via_protocol": False,
        "_local_fallback": True,
        "quotes": [
            {
                "peer": q["_peer_id"][:18],
                "service": q["_svc_name"],
                "bid": q.get("bid"), "eta_ms": q.get("eta_ms"),
                "load": q.get("load"), "style": q.get("style"),
                "score": round(_local_score(q), 2),
                "rep_bonus": 0.0,
                "winner": q is winner,
                "no_quote": q.get("_no_quote", False),
            }
            for q in quotes
        ],
        "winners": ([{"peer_id": winner["_peer_id"],
                      "service": winner["_svc_name"]}]
                    if winner else []),
        "winner_peer": winner["_peer_id"] if winner else None,
        "winner_service": winner["_svc_name"] if winner else None,
    }


def _report_reputation(svc: SvcClient, rep_target: Optional[dict],
                       peer_id: str, service: str, success: bool) -> None:
    if not rep_target:
        return
    try:
        _proto_call(svc, rep_target, "/v1/report", body={
            "peer_id": peer_id, "service": service, "success": success,
        })
    except Exception:  # noqa: BLE001
        pass


def _find_target(peers: list[dict], short_peer: str,
                 service: str) -> Optional[dict]:
    for p in peers:
        if p["services"][0]["name"] != service:
            continue
        if p["peer_id"].startswith(short_peer) or short_peer.startswith(p["peer_id"][:18]):
            return p
    for p in peers:
        if p["services"][0]["name"] == service:
            return p
    return None


def run_pipeline(text: str, intent: str, consensus: bool) -> dict[str, Any]:
    started = time.time()
    steps: list[dict[str, Any]] = []
    auctions: list[dict[str, Any]] = []
    consensus_reports: list[dict[str, Any]] = []
    results: dict[str, Any] = {}
    protocol_status: dict[str, Any] = {}

    with SvcClient(base_url=ANET_BASE_URL) as svc:
        per_skill, catalogue = _discover_all(svc)
        plan = decide_plan(text, per_skill, intent)

        auction_target = _discover_protocol(svc, "auction")
        rep_target = _discover_protocol(svc, "reputation")
        settle_target = _discover_protocol(svc, "settlement")
        feed_target = _discover_protocol(svc, "market-feed")
        protocol_status = {
            "auction_svc": auction_target["service"] if auction_target else None,
            "reputation_svc": rep_target["service"] if rep_target else None,
            "settlement_svc": settle_target["service"] if settle_target else None,
            "market_feed_svc": feed_target["service"] if feed_target else None,
        }

        def _publish(kind: str, data: dict) -> None:
            if feed_target:
                _proto_call(svc, feed_target, "/v1/publish",
                            body={"kind": kind, "data": data})

        for skill in plan:
            peers = per_skill[skill]

            if auction_target:
                auction = _run_auction_via_protocol(
                    svc, auction_target, skill, peers, text)
                if auction.get("_protocol_failed"):
                    auction = _run_auction_local(svc, skill, peers, text)
            else:
                auction = _run_auction_local(svc, skill, peers, text)
            auctions.append(auction)
            _publish("auction.closed", {
                "auction_id": auction.get("auction_id"),
                "skill": skill,
                "bidders": len(auction.get("quotes") or []),
                "winner_service": auction.get("winner_service"),
                "winner_peer": (auction.get("winner_peer") or "")[:18],
                "winners": auction.get("winners") or [],
            })

            do_consensus = (
                consensus and skill in CONSENSUS_SKILLS and len(peers) >= 2
            )
            chosen_quotes = (
                [q for q in auction["quotes"] if q.get("winner")]
                or auction["quotes"][:1]
            )
            if do_consensus and len(chosen_quotes) < CONSENSUS_K:
                # ensure we hit K providers when consensus is requested
                rest = [q for q in auction["quotes"] if q not in chosen_quotes]
                chosen_quotes = (chosen_quotes + rest)[:CONSENSUS_K]

            path, build = SKILL_PATHS[skill]
            votes: list[tuple[dict, dict, int, int]] = []
            for q in chosen_quotes:
                target = _find_target(peers, q["peer"], q["service"])
                if target is None:
                    continue
                body = build(text, results)
                out, ms, status = _call(svc, target, path, body)
                ok = status < 400 and not out.get("error")
                _report_reputation(svc, rep_target,
                                   target["peer_id"],
                                   target["services"][0]["name"], ok)
                votes.append((q, out, ms, status))
                cost_model = target["services"][0].get("cost_model") or {}
                cost = cost_model.get("per_call", 0) or 0
                if ok and settle_target and cost > 0:
                    _proto_call(svc, settle_target, "/v1/record", body={
                        "auction_id": auction.get("auction_id") or "",
                        "skill": skill,
                        "payer_peer": "orchestrator",
                        "payee_peer": target["peer_id"],
                        "payee_service": target["services"][0]["name"],
                        "shells": cost,
                        "eta_ms": ms,
                    })
                _publish("step.completed", {
                    "skill": skill, "service": target["services"][0]["name"],
                    "peer": target["peer_id"][:18], "ms": ms,
                    "cost": cost, "ok": ok,
                    "auction_id": auction.get("auction_id"),
                })
                steps.append({
                    "skill": skill,
                    "peer": target["peer_id"][:18],
                    "peer_full": target["peer_id"],
                    "svc": target["services"][0]["name"],
                    "ms": ms,
                    "cost": cost,
                    "status": status,
                    "auctioned": True,
                    "winner": q is chosen_quotes[0],
                    "consensus_member": do_consensus,
                })

            primary_out = votes[0][1] if votes else {}
            if do_consensus and len(votes) > 1:
                labels = [
                    (v[1].get("label") or "neutral")
                    for v in votes if isinstance(v[1], dict)
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

        # Pull a fresh leaderboard from reputation-svc for the report.
        leaderboard = []
        if rep_target:
            lb = _proto_call(svc, rep_target, "/v1/leaderboard?limit=8",
                             method="GET", body=None)
            leaderboard = (lb.get("leaderboard") or [])[:8]

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
        "reputation_top": leaderboard,
        "consensus_enabled": consensus,
        "protocol_services": protocol_status,
        "agent": NAME,
    }


@app.get("/health")
def health():
    return {"ok": True, "agent": NAME}


@app.get("/meta")
def meta():
    return {
        "name": NAME, "version": "1.0.0", "skill": "orchestrator",
        "known_skills": KNOWN_SKILLS,
        "consensus_skills": list(CONSENSUS_SKILLS),
        "mode": "shell-market-protocol client",
        "protocol_dependencies": ["auction", "reputation"],
    }


@app.get("/v1/discover")
def do_discover():
    with SvcClient(base_url=ANET_BASE_URL) as svc:
        per_skill, catalogue = _discover_all(svc)
        auction_target = _discover_protocol(svc, "auction")
        rep_target = _discover_protocol(svc, "reputation")
    return {
        "catalogue": catalogue,
        "by_skill": {
            k: [{"peer": p["peer_id"][:18], "service": p["services"][0]["name"]}
                for p in v]
            for k, v in per_skill.items()
        },
        "known_skills": KNOWN_SKILLS,
        "count": len(catalogue),
        "protocol_services": {
            "auction": auction_target,
            "reputation": rep_target,
        },
    }


@app.get("/v1/marketplace")
def do_marketplace():
    """Live snapshot from the protocol services."""
    with SvcClient(base_url=ANET_BASE_URL) as svc:
        rep_target = _discover_protocol(svc, "reputation")
        auction_target = _discover_protocol(svc, "auction")
        leaderboard = []
        history = []
        if rep_target:
            lb = _proto_call(svc, rep_target, "/v1/leaderboard?limit=20",
                             method="GET", body=None)
            leaderboard = lb.get("leaderboard") or []
        if auction_target:
            h = _proto_call(svc, auction_target, "/v1/history?limit=20",
                            method="GET", body=None)
            history = h.get("history") or []
    return {
        "reputation": leaderboard,
        "recent_auctions": history,
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
        f"[orchestrator] caller={x_agent_did} intent={intent} "
        f"consensus={consensus} text={text[:60]!r}",
        flush=True,
    )
    report = run_pipeline(text, intent, consensus)
    print(
        f"[orchestrator]   ↳ plan={report['pipeline_plan']} "
        f"ms={report['total_ms']} cost={report['total_cost']} "
        f"auctions={len(report['auctions'])} "
        f"protocol={report['protocol_services']}",
        flush=True,
    )
    return JSONResponse(report)


def main() -> None:
    threading.Thread(
        target=lambda: register_until_ready(
            NAME, PORT,
            paths=["/v1/analyze", "/v1/discover", "/v1/marketplace",
                   "/health", "/meta"],
            tags=["orchestrator", "pipeline", "shell-market", "content-intel"],
            description="Pipeline composer over the Shell Market Protocol",
            per_call=PER_CALL, base_url=ANET_BASE_URL,
        ),
        daemon=True,
    ).start()
    uvicorn.run(app, host=os.environ.get("LISTEN_HOST","0.0.0.0"), port=PORT, log_level="warning")


if __name__ == "__main__":
    main()
