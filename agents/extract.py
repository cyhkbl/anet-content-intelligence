"""extract-svc — named entity extraction (regex + rules).

Exposes POST /v1/extract {text: "…"} → {entities: [{text, type, start, end}]}.

Recognises PERSON, PLACE, ORG, DATE, NUMBER. No external deps — pure regex.
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
from quote_helpers import make_quote  # noqa: E402

NAME = os.environ.get("EXTRACT_NAME", "extract-svc")
PORT = int(os.environ.get("EXTRACT_PORT", "7402"))
PER_CALL = int(os.environ.get("EXTRACT_PER_CALL", "8"))
BASE_ETA_MS = int(os.environ.get("EXTRACT_BASE_ETA_MS", "70"))
QUOTE_STYLE = os.environ.get("EXTRACT_QUOTE_STYLE", "thorough")

PLACES = {
    "shanghai", "beijing", "shenzhen", "guangzhou", "hangzhou",
    "china", "usa", "america", "london", "tokyo", "new york",
    "san francisco", "berlin", "paris",
}
ORGS = {
    "openai", "anthropic", "google", "microsoft", "apple", "meta",
    "amazon", "nvidia", "tesla", "alibaba", "tencent", "baidu",
    "bytedance", "huawei", "agentnetwork", "claude",
}
PERSONS = {
    # token-level hints; matched only when capitalized in english output
    "elon", "musk", "sam", "altman", "dario", "amodei",
    "jensen", "huang", "mark", "zuckerberg",
}

DATE_RE = re.compile(
    r"\b(?:today|tomorrow|yesterday|\d{4}-\d{2}-\d{2}|"
    r"\d{1,2}/\d{1,2}(?:/\d{2,4})?|"
    r"(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)\w*\s+\d{1,2}(?:,\s*\d{4})?)\b",
    re.IGNORECASE,
)
NUMBER_RE = re.compile(
    r"(?<![\w.])(?:\$|¥|€)?\d+(?:[\.,]\d+)*(?:%|[kmb]n?)?(?![\w])",
    re.IGNORECASE,
)

app = FastAPI(title=NAME)


def _find_all(pattern: re.Pattern, text: str, etype: str) -> list[dict]:
    out = []
    for m in pattern.finditer(text):
        out.append({"text": m.group(), "type": etype, "start": m.start(), "end": m.end()})
    return out


def _find_lexicon(text: str, lexicon: set[str], etype: str) -> list[dict]:
    out = []
    lower = text.lower()
    for term in sorted(lexicon, key=len, reverse=True):
        start = 0
        while True:
            idx = lower.find(term, start)
            if idx == -1:
                break
            # ensure word boundary (regex too slow per-term; manual check)
            before = idx == 0 or not lower[idx - 1].isalnum()
            end = idx + len(term)
            after = end == len(lower) or not lower[end].isalnum()
            if before and after:
                out.append(
                    {"text": text[idx:end], "type": etype, "start": idx, "end": end}
                )
            start = end
    return out


def extract(text: str) -> list[dict]:
    spans: list[dict] = []
    spans += _find_lexicon(text, PLACES, "PLACE")
    spans += _find_lexicon(text, ORGS, "ORG")
    spans += _find_lexicon(text, PERSONS, "PERSON")
    spans += _find_all(DATE_RE, text, "DATE")
    spans += _find_all(NUMBER_RE, text, "NUMBER")
    # dedupe by (start, end, type)
    seen = set()
    unique = []
    for s in spans:
        key = (s["start"], s["end"], s["type"])
        if key in seen:
            continue
        seen.add(key)
        unique.append(s)
    unique.sort(key=lambda s: s["start"])
    return unique


@app.get("/health")
def health():
    return {"ok": True, "agent": NAME}


@app.get("/meta")
def meta():
    return {
        "name": NAME, "version": "0.1.0", "skill": "extract",
        "types": ["PERSON", "PLACE", "ORG", "DATE", "NUMBER"],
    }


@app.post("/v1/quote")
async def do_quote(req: Request):
    body = await req.json() or {}
    return JSONResponse(make_quote(
        text=body.get("text") or "", skill="extract", agent=NAME,
        base_cost=PER_CALL, base_eta_ms=BASE_ETA_MS, style=QUOTE_STYLE,
    ))


@app.post("/v1/extract")
async def do_extract(
    req: Request,
    x_agent_did: Optional[str] = Header(default=None, convert_underscores=True),
):
    body = await req.json()
    text = (body or {}).get("text") or ""
    ents = extract(text)
    print(f"[extract] caller={x_agent_did} found={len(ents)} text={text[:60]!r}", flush=True)
    return JSONResponse({"entities": ents, "count": len(ents), "agent": NAME})


def main() -> None:
    base_url = os.environ.get("ANET_BASE_URL", "http://127.0.0.1:14102")
    threading.Thread(
        target=lambda: register_until_ready(
            NAME, PORT,
            paths=["/v1/extract", "/v1/quote", "/health", "/meta"],
            tags=["extract", "ner", "content-intel"],
            description="Regex-based named entity extractor",
            per_call=PER_CALL, base_url=base_url,
        ),
        daemon=True,
    ).start()
    uvicorn.run(app, host=os.environ.get("LISTEN_HOST","0.0.0.0"), port=PORT, log_level="warning")


if __name__ == "__main__":
    main()
