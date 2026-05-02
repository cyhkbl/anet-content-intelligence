"""keywords-svc — TF-style keyword extractor.

Exposes POST /v1/keywords {text, top_k?} → {keywords: [{word, score}], agent}.

Distinct from extract-svc (which does NER): this returns the most salient
content words regardless of entity type. Uses a tiny English stop-word list
and a term-frequency + length-boost scoring — cheap, deterministic, and
runs offline.
"""

from __future__ import annotations

import math
import os
import re
import sys
import threading
from collections import Counter
from typing import Optional

import uvicorn
from fastapi import FastAPI, Header, Request
from fastapi.responses import JSONResponse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from register import register_until_ready  # noqa: E402

NAME = "keywords-svc"
PORT = int(os.environ.get("KEYWORDS_PORT", "7409"))
PER_CALL = int(os.environ.get("KEYWORDS_PER_CALL", "3"))

STOPWORDS = {
    "the", "a", "an", "is", "are", "was", "were", "be", "been", "being",
    "in", "on", "at", "of", "for", "to", "from", "by", "with", "as",
    "and", "or", "but", "not", "no", "so", "if", "then", "than", "that",
    "this", "these", "those", "it", "its", "he", "she", "they", "we",
    "i", "me", "you", "your", "their", "our", "his", "her", "them",
    "will", "would", "could", "should", "can", "may", "might", "has", "have",
    "had", "do", "does", "did", "done", "just", "only", "also", "too",
    "very", "much", "more", "most", "some", "any", "all", "each", "every",
    "about", "into", "onto", "over", "under", "between", "among",
    "there", "here", "when", "where", "why", "how", "what", "who",
    "said", "says", "say", "one", "two", "three",
}

TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z\-']+")

app = FastAPI(title=NAME)


def score_keywords(text: str, top_k: int = 8) -> list[dict]:
    tokens = [t.lower() for t in TOKEN_RE.findall(text)]
    if not tokens:
        return []
    freq: Counter[str] = Counter()
    for t in tokens:
        if t in STOPWORDS or len(t) < 3:
            continue
        freq[t] += 1
    if not freq:
        return []
    max_f = max(freq.values())
    scored: list[tuple[str, float]] = []
    for w, f in freq.items():
        tf = 0.5 + 0.5 * f / max_f
        length_boost = min(1.5, 1.0 + math.log1p(len(w)) / 6.0)
        scored.append((w, round(tf * length_boost, 4)))
    scored.sort(key=lambda kv: (-kv[1], kv[0]))
    return [{"word": w, "score": s} for w, s in scored[: max(1, top_k)]]


@app.get("/health")
def health():
    return {"ok": True, "agent": NAME}


@app.get("/meta")
def meta():
    return {
        "name": NAME, "version": "0.1.0", "skill": "keywords",
        "method": "tf + length-boost, stopword filtered",
    }


@app.post("/v1/keywords")
async def do_keywords(
    req: Request,
    x_agent_did: Optional[str] = Header(default=None, convert_underscores=True),
):
    body = await req.json() or {}
    text = body.get("text") or ""
    top_k = int(body.get("top_k") or 8)
    kws = score_keywords(text, top_k=top_k)
    print(
        f"[keywords] caller={x_agent_did} top_k={top_k} hits={len(kws)}",
        flush=True,
    )
    return JSONResponse({"keywords": kws, "count": len(kws), "agent": NAME})


def main() -> None:
    base_url = os.environ.get("ANET_BASE_URL", "http://127.0.0.1:14109")
    threading.Thread(
        target=lambda: register_until_ready(
            NAME, PORT,
            paths=["/v1/keywords", "/health", "/meta"],
            tags=["keywords", "tf", "content-intel"],
            description="TF-based keyword extractor with length boost",
            per_call=PER_CALL, base_url=base_url,
        ),
        daemon=True,
    ).start()
    uvicorn.run(app, host="127.0.0.1", port=PORT, log_level="warning")


if __name__ == "__main__":
    main()
