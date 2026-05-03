"""factcheck-svc — rule-based plausibility checker.

Exposes POST /v1/factcheck {text: "…"} → {claims: [{claim, status, confidence, reason}]}.

Checks the numbers, percentages, dates and well-known organisations in a
piece of text for obvious implausibilities (impossible percentages, future
dates masquerading as events, misspelled company names, …). No external
APIs — pure heuristics so the demo runs offline.
"""

from __future__ import annotations

import os
import re
import sys
import threading
from datetime import datetime
from typing import Optional

import uvicorn
from fastapi import FastAPI, Header, Request
from fastapi.responses import JSONResponse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from register import register_until_ready  # noqa: E402
from quote_helpers import make_quote  # noqa: E402

NAME = os.environ.get("FACTCHECK_NAME", "factcheck-svc")
PORT = int(os.environ.get("FACTCHECK_PORT", "7407"))
PER_CALL = int(os.environ.get("FACTCHECK_PER_CALL", "8"))
BASE_ETA_MS = int(os.environ.get("FACTCHECK_BASE_ETA_MS", "90"))
QUOTE_STYLE = os.environ.get("FACTCHECK_QUOTE_STYLE", "thorough")

# Canonical organisation names — misspellings are flagged as suspect.
KNOWN_ORGS = {
    "openai", "anthropic", "google", "microsoft", "apple", "meta",
    "amazon", "nvidia", "tesla", "alibaba", "tencent", "baidu",
    "bytedance", "huawei", "agentnetwork",
}

PERCENT_RE = re.compile(r"(-?\d+(?:\.\d+)?)\s*%")
YEAR_RE = re.compile(r"\b(19|20)\d{2}\b")
BIG_NUMBER_RE = re.compile(r"\b(\d+(?:\.\d+)?)\s*(trillion|billion|million|bn|mn)\b", re.IGNORECASE)

app = FastAPI(title=NAME)


def check_percentages(text: str) -> list[dict]:
    out: list[dict] = []
    for m in PERCENT_RE.finditer(text):
        try:
            v = float(m.group(1))
        except ValueError:
            continue
        span = m.group(0)
        if v > 1000:
            out.append({
                "claim": span, "status": "suspect", "confidence": 0.9,
                "reason": "percentage exceeds 1000%, likely an error or sensationalism",
            })
        elif v < -100:
            out.append({
                "claim": span, "status": "suspect", "confidence": 0.9,
                "reason": "negative percentage below -100% (nothing can fall more than 100%)",
            })
        elif abs(v) > 100:
            out.append({
                "claim": span, "status": "plausible-unusual", "confidence": 0.6,
                "reason": "percentage above 100% — plausible for growth/returns, flag for review",
            })
        else:
            out.append({
                "claim": span, "status": "plausible", "confidence": 0.8,
                "reason": "percentage within normal 0–100% range",
            })
    return out


def check_years(text: str) -> list[dict]:
    out: list[dict] = []
    now = datetime.utcnow().year
    for m in YEAR_RE.finditer(text):
        year = int(m.group(0))
        if year > now + 5:
            out.append({
                "claim": m.group(0), "status": "suspect", "confidence": 0.7,
                "reason": f"year is >5 years in the future (now={now})",
            })
        elif year < 1900:
            out.append({
                "claim": m.group(0), "status": "plausible-historic", "confidence": 0.6,
                "reason": "year is pre-1900 — plausible historic reference",
            })
        else:
            out.append({
                "claim": m.group(0), "status": "plausible", "confidence": 0.85,
                "reason": f"year within plausible range (1900..{now + 5})",
            })
    return out


def check_big_numbers(text: str) -> list[dict]:
    out: list[dict] = []
    for m in BIG_NUMBER_RE.finditer(text):
        val_str, scale = m.group(1), m.group(2).lower()
        try:
            v = float(val_str)
        except ValueError:
            continue
        scale_k = {"trillion": 1e12, "billion": 1e9, "bn": 1e9,
                   "million": 1e6, "mn": 1e6}[scale]
        abs_val = v * scale_k
        if abs_val > 1e14:
            out.append({
                "claim": m.group(0), "status": "suspect", "confidence": 0.8,
                "reason": "magnitude exceeds $100T (larger than any real economy)",
            })
        else:
            out.append({
                "claim": m.group(0), "status": "plausible", "confidence": 0.75,
                "reason": "magnitude within plausible economic range",
            })
    return out


def check_org_spellings(text: str) -> list[dict]:
    out: list[dict] = []
    lower = text.lower()
    suspects = {
        "openai": ["openia", "opemai", "openei"],
        "nvidia": ["nvdia", "invidia"],
        "google": ["googel", "gogle"],
        "microsoft": ["microsft", "mircosoft"],
        "anthropic": ["antropic", "anthropik"],
    }
    for correct, bad_list in suspects.items():
        for bad in bad_list:
            if bad in lower:
                out.append({
                    "claim": bad, "status": "suspect", "confidence": 0.95,
                    "reason": f"likely misspelling of '{correct}'",
                })
    return out


def factcheck(text: str) -> dict:
    claims: list[dict] = []
    claims += check_percentages(text)
    claims += check_years(text)
    claims += check_big_numbers(text)
    claims += check_org_spellings(text)

    buckets = {"plausible": 0, "suspect": 0, "other": 0}
    for c in claims:
        s = c["status"]
        if s.startswith("plausible"):
            buckets["plausible"] += 1
        elif s == "suspect":
            buckets["suspect"] += 1
        else:
            buckets["other"] += 1

    total = max(1, len(claims))
    verdict = "clean"
    if buckets["suspect"] >= 2 or buckets["suspect"] / total >= 0.5:
        verdict = "flagged"
    elif buckets["suspect"] >= 1:
        verdict = "review"
    return {"claims": claims, "verdict": verdict, "counts": buckets, "total": len(claims)}


@app.get("/health")
def health():
    return {"ok": True, "agent": NAME}


@app.get("/meta")
def meta():
    return {
        "name": NAME, "version": "0.1.0", "skill": "factcheck",
        "checks": ["percentages", "years", "big-numbers", "org-spellings"],
    }


@app.post("/v1/quote")
async def do_quote(req: Request):
    body = await req.json() or {}
    return JSONResponse(make_quote(
        text=body.get("text") or "", skill="factcheck", agent=NAME,
        base_cost=PER_CALL, base_eta_ms=BASE_ETA_MS, style=QUOTE_STYLE,
    ))


@app.post("/v1/factcheck")
async def do_factcheck(
    req: Request,
    x_agent_did: Optional[str] = Header(default=None, convert_underscores=True),
):
    body = await req.json()
    text = (body or {}).get("text") or ""
    result = factcheck(text)
    print(
        f"[factcheck] caller={x_agent_did} claims={result['total']} "
        f"verdict={result['verdict']}",
        flush=True,
    )
    return JSONResponse({**result, "agent": NAME})


def main() -> None:
    base_url = os.environ.get("ANET_BASE_URL", "http://127.0.0.1:14107")
    threading.Thread(
        target=lambda: register_until_ready(
            NAME, PORT,
            paths=["/v1/factcheck", "/v1/quote", "/health", "/meta"],
            tags=["factcheck", "verification", "content-intel"],
            description="Rule-based plausibility checker for numbers, dates, orgs",
            per_call=PER_CALL, base_url=base_url,
        ),
        daemon=True,
    ).start()
    uvicorn.run(app, host=os.environ.get("LISTEN_HOST","0.0.0.0"), port=PORT, log_level="warning")


if __name__ == "__main__":
    main()
