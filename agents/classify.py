"""classify-svc — keyword-based topic classifier.

Exposes POST /v1/classify {text: "…"} → {topic, confidence, keywords, agent}.
Topics: technology, business, science, politics, sports, entertainment, health, other.
"""

from __future__ import annotations

import os
import sys
import threading
from typing import Optional

import uvicorn
from fastapi import FastAPI, Header, Request
from fastapi.responses import JSONResponse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from register import register_until_ready  # noqa: E402
from quote_helpers import make_quote  # noqa: E402

NAME = os.environ.get("CLASSIFY_NAME", "classify-svc")
PORT = int(os.environ.get("CLASSIFY_PORT", "7405"))
PER_CALL = int(os.environ.get("CLASSIFY_PER_CALL", "5"))
BASE_ETA_MS = int(os.environ.get("CLASSIFY_BASE_ETA_MS", "60"))
QUOTE_STYLE = os.environ.get("CLASSIFY_QUOTE_STYLE", "balanced")

TOPIC_KEYWORDS: dict[str, set[str]] = {
    "technology": {
        "ai", "artificial", "intelligence", "model", "software", "data",
        "computer", "algorithm", "tech", "technology", "api", "cloud",
        "agent", "network", "p2p", "blockchain", "robot",
    },
    "business": {
        "market", "stock", "investment", "profit", "revenue", "company",
        "ceo", "business", "customer", "product", "launch", "growth",
        "shares", "ipo", "funding", "acquisition",
    },
    "science": {
        "research", "study", "experiment", "discovery", "scientist",
        "physics", "biology", "chemistry", "space", "climate",
        "gene", "quantum", "particle",
    },
    "politics": {
        "government", "president", "election", "vote", "policy",
        "congress", "senate", "party", "minister", "law", "treaty",
    },
    "sports": {
        "game", "match", "player", "team", "score", "win", "lose",
        "championship", "league", "coach", "tournament", "goal",
    },
    "entertainment": {
        "movie", "film", "actor", "music", "song", "album", "concert",
        "album", "tv", "show", "celebrity", "series",
    },
    "health": {
        "health", "doctor", "patient", "hospital", "disease", "virus",
        "vaccine", "medicine", "treatment", "cancer", "mental",
    },
}

app = FastAPI(title=NAME)


def tokenize(text: str) -> list[str]:
    return [t.strip(".,!?;:\"'()[]{}").lower() for t in text.split() if t]


def classify(text: str) -> tuple[str, float, list[str]]:
    toks = tokenize(text)
    if not toks:
        return "other", 0.0, []
    tokset = set(toks)
    scores: dict[str, int] = {}
    hits: dict[str, list[str]] = {}
    for topic, kws in TOPIC_KEYWORDS.items():
        matched = sorted(tokset & kws)
        if matched:
            scores[topic] = len(matched)
            hits[topic] = matched
    if not scores:
        return "other", 0.0, []
    top = max(scores, key=lambda k: scores[k])
    total = sum(scores.values())
    confidence = round(scores[top] / total, 3)
    return top, confidence, hits[top]


@app.get("/health")
def health():
    return {"ok": True, "agent": NAME}


@app.get("/meta")
def meta():
    return {
        "name": NAME, "version": "0.1.0", "skill": "classify",
        "topics": list(TOPIC_KEYWORDS.keys()) + ["other"],
    }


@app.post("/v1/quote")
async def do_quote(req: Request):
    body = await req.json() or {}
    return JSONResponse(make_quote(
        text=body.get("text") or "", skill="classify", agent=NAME,
        base_cost=PER_CALL, base_eta_ms=BASE_ETA_MS, style=QUOTE_STYLE,
    ))


@app.post("/v1/classify")
async def do_classify(
    req: Request,
    x_agent_did: Optional[str] = Header(default=None, convert_underscores=True),
):
    body = await req.json()
    text = (body or {}).get("text") or ""
    topic, confidence, keywords = classify(text)
    print(f"[classify] caller={x_agent_did} topic={topic} conf={confidence}", flush=True)
    return JSONResponse({
        "topic": topic,
        "confidence": confidence,
        "keywords": keywords,
        "agent": NAME,
    })


def main() -> None:
    base_url = os.environ.get("ANET_BASE_URL", "http://127.0.0.1:14105")
    threading.Thread(
        target=lambda: register_until_ready(
            NAME, PORT,
            paths=["/v1/classify", "/v1/quote", "/health", "/meta"],
            tags=["classify", "topic", "content-intel"],
            description="Keyword-based topic classifier",
            per_call=PER_CALL, base_url=base_url,
        ),
        daemon=True,
    ).start()
    uvicorn.run(app, host="127.0.0.1", port=PORT, log_level="warning")


if __name__ == "__main__":
    main()
