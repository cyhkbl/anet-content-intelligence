"""sentiment-svc — lexicon-based sentiment classifier.

Exposes POST /v1/sentiment {text: "…"} → {label, score, agent}.

Labels: positive, negative, neutral.
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

NAME = "sentiment-svc"
PORT = int(os.environ.get("SENTIMENT_PORT", "7403"))
PER_CALL = int(os.environ.get("SENTIMENT_PER_CALL", "5"))

POSITIVE = {
    "good", "great", "excellent", "amazing", "love", "happy",
    "wonderful", "fantastic", "nice", "pleasant", "hot", "sunny",
    "success", "successful", "rose", "growth", "profit", "gain",
    "breakthrough", "launch", "best", "strong", "positive", "up",
}
NEGATIVE = {
    "bad", "terrible", "awful", "hate", "sad", "worst",
    "horrible", "poor", "cold", "rain", "snow", "failure",
    "failed", "fell", "loss", "decline", "bug", "crash",
    "weak", "negative", "down", "risk", "concern",
}
INTENSIFIERS = {"very", "extremely", "really", "highly", "super"}

app = FastAPI(title=NAME)


def tokenize(text: str) -> list[str]:
    return [t.strip(".,!?;:\"'()[]{}").lower() for t in text.split() if t]


def classify(text: str) -> tuple[str, float]:
    toks = tokenize(text)
    pos = neg = 0
    boost = 1
    for t in toks:
        if t in INTENSIFIERS:
            boost = 2
            continue
        if t in POSITIVE:
            pos += boost
        elif t in NEGATIVE:
            neg += boost
        boost = 1
    total = max(1, pos + neg)
    if pos > neg:
        return "positive", min(1.0, 0.5 + pos / (2 * total))
    if neg > pos:
        return "negative", min(1.0, 0.5 + neg / (2 * total))
    return "neutral", 0.5


@app.get("/health")
def health():
    return {"ok": True, "agent": NAME}


@app.get("/meta")
def meta():
    return {
        "name": NAME, "version": "0.1.0", "skill": "sentiment",
        "labels": ["positive", "negative", "neutral"],
    }


@app.post("/v1/sentiment")
async def do_sentiment(
    req: Request,
    x_agent_did: Optional[str] = Header(default=None, convert_underscores=True),
):
    body = await req.json()
    text = (body or {}).get("text") or ""
    label, score = classify(text)
    print(f"[sentiment] caller={x_agent_did} label={label} score={score:.2f}", flush=True)
    return JSONResponse({"label": label, "score": round(score, 3), "agent": NAME})


def main() -> None:
    base_url = os.environ.get("ANET_BASE_URL", "http://127.0.0.1:14103")
    threading.Thread(
        target=lambda: register_until_ready(
            NAME, PORT,
            paths=["/v1/sentiment", "/health", "/meta"],
            tags=["sentiment", "content-intel"],
            description="Lexicon-based sentiment classifier",
            per_call=PER_CALL, base_url=base_url,
        ),
        daemon=True,
    ).start()
    uvicorn.run(app, host="127.0.0.1", port=PORT, log_level="warning")


if __name__ == "__main__":
    main()
