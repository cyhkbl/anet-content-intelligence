"""summarise-svc — extractive text summarization (heuristic).

Exposes POST /v1/summarise {text, max_sentences?} → {summary, sentences, agent}.

If the input is Chinese, it first calls translate-svc through the anet gateway
(cross-agent call) before summarising.
"""

from __future__ import annotations

import os
import re
import sys
import threading
from typing import Optional

import uvicorn
from fastapi import FastAPI, Header, Request
from fastapi.responses import JSONResponse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from register import register_until_ready  # noqa: E402

from anet_sdk import SvcClient  # noqa: E402

NAME = "summarise-svc"
PORT = int(os.environ.get("SUMMARISE_PORT", "7404"))
PER_CALL = int(os.environ.get("SUMMARISE_PER_CALL", "10"))
ANET_BASE_URL = os.environ.get("ANET_BASE_URL", "http://127.0.0.1:14104")

SENTENCE_SPLIT = re.compile(r"(?<=[\.!?。！？])\s+")

app = FastAPI(title=NAME)


def looks_chinese(text: str) -> bool:
    return any("\u4e00" <= ch <= "\u9fff" for ch in text)


def sentences(text: str) -> list[str]:
    parts = [s.strip() for s in SENTENCE_SPLIT.split(text) if s.strip()]
    return parts or ([text.strip()] if text.strip() else [])


def summarise(text: str, max_sentences: int = 2, max_chars: int = 240) -> str:
    sents = sentences(text)
    if not sents:
        return ""
    picked = sents[: max(1, max_sentences)]
    out = " ".join(picked)
    if len(out) > max_chars:
        out = out[: max_chars - 1].rstrip() + "…"
    return out


def call_translate(text: str) -> str:
    """Hop summarise→translate through the gateway."""
    with SvcClient(base_url=ANET_BASE_URL) as svc:
        peers = svc.discover(skill="translate")
        if not peers:
            return text
        target = peers[0]
        resp = svc.call(
            target["peer_id"], target["services"][0]["name"],
            "/v1/translate", method="POST", body={"text": text},
        )
        body = resp.get("body") or {}
        if isinstance(body, dict):
            return body.get("translated", text) or text
        return text


@app.get("/health")
def health():
    return {"ok": True, "agent": NAME}


@app.get("/meta")
def meta():
    return {
        "name": NAME, "version": "0.1.0", "skill": "summarise",
        "calls_into": ["translate"],
    }


@app.post("/v1/summarise")
async def do_summarise(
    req: Request,
    x_agent_did: Optional[str] = Header(default=None, convert_underscores=True),
):
    body = await req.json() or {}
    text = body.get("text") or ""
    max_sents = int(body.get("max_sentences") or 2)
    src = "zh" if looks_chinese(text) else "en"
    print(f"[summarise] caller={x_agent_did} src={src} text={text[:60]!r}", flush=True)
    working = call_translate(text) if src == "zh" else text
    if src == "zh":
        print(f"[summarise]   ↳ translated: {working[:80]!r}", flush=True)
    summary = summarise(working, max_sentences=max_sents)
    return JSONResponse({
        "summary": summary,
        "sentences": len(sentences(summary)),
        "source_lang": src,
        "agent": NAME,
    })


def main() -> None:
    threading.Thread(
        target=lambda: register_until_ready(
            NAME, PORT,
            paths=["/v1/summarise", "/health", "/meta"],
            tags=["summarise", "content-intel"],
            description="Extractive summariser (calls translate for zh input)",
            per_call=PER_CALL, base_url=ANET_BASE_URL,
        ),
        daemon=True,
    ).start()
    uvicorn.run(app, host="127.0.0.1", port=PORT, log_level="warning")


if __name__ == "__main__":
    main()
