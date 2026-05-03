#!/usr/bin/env python
"""Seed the Shell Market with realistic activity by driving the orchestrator
through a curated batch of texts. Each call produces multiple auctions,
settlements, reputation deltas, and feed events — so the dashboard is
*populated* before any judge looks at it.

Usage: python scripts/seed_market.py [--rounds N]
"""

from __future__ import annotations

import argparse
import json
import os
import random
import sys
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# Get token via daemon HOME
HOME = os.environ.get("HOME") or "/tmp/anet-ci-u1"
TOKEN_FILE = Path(HOME) / ".anet" / "api_token"
ANET_BASE_URL = os.environ.get("ANET_BASE_URL", "http://127.0.0.1:14101")
ORCH_URL = os.environ.get("ORCHESTRATOR_URL", "http://127.0.0.1:7406")

CORPUS = [
    ("Tesla announced a new factory in Shanghai, stock rose 4% today.", "analyze", True),
    ("北京天气很好，今天科技市场上涨。", "analyze", True),
    ("Apple reported $90B revenue in Q4, AI investments doubled.", "analyze", True),
    ("OpenAI released a new model. Critics call it overhyped.", "analyze", False),
    ("上海公司发布新产品，客户反应积极。", "translate-to-zh", True),
    ("Bitcoin fell 8% after the SEC announcement on 2025-10-12.", "analyze", True),
    ("The team launched a smooth, efficient new feature.", "analyze", True),
    ("Microsoft and Google compete on cloud AI services.", "analyze", False),
    ("深圳的人工智能投资在2024年增长了30%。", "analyze", True),
    ("This product is a complete disaster, totally broken.", "analyze", True),
    ("NVIDIA beat earnings, datacenter revenue at $30B.", "analyze", True),
    ("杭州科技公司发布数据模型，市场表现良好。", "analyze", False),
]


def _token() -> str:
    tok = os.environ.get("ANET_TOKEN")
    if tok:
        return tok.strip()
    if TOKEN_FILE.exists():
        return TOKEN_FILE.read_text().strip()
    raise SystemExit(f"no token (set ANET_TOKEN or HOME=daemon-home; tried {TOKEN_FILE})")


def _publish(client: httpx.Client, kind: str, data: dict) -> None:
    try:
        client.post(f"{ORCH_URL}/__noop__", timeout=0.001)
    except Exception:
        pass


def fire(client: httpx.Client, text: str, intent: str, consensus: bool) -> dict:
    r = client.post(f"{ORCH_URL}/v1/analyze",
                    json={"text": text, "intent": intent,
                          "consensus": consensus},
                    timeout=30.0)
    r.raise_for_status()
    return r.json()


def file_dispute(token: str) -> None:
    """Discover dispute-svc and file/resolve a dispute via the daemon's call API."""
    try:
        with httpx.Client(timeout=8.0) as c:
            hdr = {"Authorization": f"Bearer {token}",
                   "Content-Type": "application/json"}
            r = c.get(f"{ANET_BASE_URL}/api/svc/discover",
                      params={"skill": "dispute", "limit": 3}, headers=hdr)
            peers = (r.json() or {}).get("results") or []
            if not peers:
                return
            p = peers[0]
            sname = (p.get("services") or [{}])[0].get("name")
            # File one
            fr = c.post(f"{ANET_BASE_URL}/api/svc/call", headers=hdr, json={
                "peer_id": p["peer_id"], "service": sname,
                "path": "/v1/file", "method": "POST",
                "body": {"auction_id": "auc_seed", "accuser": "judge-bot",
                         "accused_service": "summarise-svc",
                         "reason": "summary too slow", "stake": 5},
            })
            did = ((fr.json() or {}).get("body") or {}).get("dispute_id")
            if not did:
                return
            c.post(f"{ANET_BASE_URL}/api/svc/call", headers=hdr, json={
                "peer_id": p["peer_id"], "service": sname,
                "path": f"/v1/resolve/{did}", "method": "POST", "body": {},
            })
            print(f"  · filed+resolved dispute {did}")
    except Exception as e:  # noqa: BLE001
        print(f"  · dispute seed skipped: {e}")


def upsert_provider_metadata(token: str) -> None:
    """Discover provider-registry-svc and upsert metadata for known providers."""
    metadata = [
        ("translate-svc", {"region": "cn-east", "version": "1.0.0",
                           "capabilities": ["zh-en", "rule-based"],
                           "sla": {"p99_ms": 80}, "contact": "team@anet"}),
        ("sentiment-svc", {"region": "global", "version": "1.0.0",
                           "capabilities": ["lexicon"],
                           "sla": {"p99_ms": 50}}),
        ("sentiment-alt-svc", {"region": "us-west", "version": "1.0.0",
                               "capabilities": ["lexicon", "fast"],
                               "sla": {"p99_ms": 30}}),
        ("summarise-svc", {"region": "global", "version": "1.0.0",
                           "capabilities": ["heuristic"],
                           "sla": {"p99_ms": 120}}),
        ("classify-svc", {"region": "global", "version": "1.0.0",
                          "capabilities": ["topic"],
                          "sla": {"p99_ms": 70}}),
        ("classify-alt-svc", {"region": "eu", "version": "1.0.1",
                              "capabilities": ["topic", "fast"],
                              "sla": {"p99_ms": 40}}),
        ("keywords-svc", {"region": "global", "version": "1.0.0",
                          "capabilities": ["tf-idf"],
                          "sla": {"p99_ms": 90}}),
        ("keywords-alt-svc", {"region": "ap-south", "version": "1.0.0",
                              "capabilities": ["tf-idf", "thorough"],
                              "sla": {"p99_ms": 130}}),
    ]
    try:
        with httpx.Client(timeout=8.0) as c:
            hdr = {"Authorization": f"Bearer {token}",
                   "Content-Type": "application/json"}
            r = c.get(f"{ANET_BASE_URL}/api/svc/discover",
                      params={"skill": "provider-registry", "limit": 3},
                      headers=hdr)
            peers = (r.json() or {}).get("results") or []
            if not peers:
                return
            p = peers[0]
            sname = (p.get("services") or [{}])[0].get("name")
            for svc, meta in metadata:
                c.post(f"{ANET_BASE_URL}/api/svc/call", headers=hdr, json={
                    "peer_id": p["peer_id"], "service": sname,
                    "path": "/v1/upsert", "method": "POST",
                    "body": {"service": svc,
                             "peer_id": p["peer_id"], **meta},
                })
            print(f"  · upserted metadata for {len(metadata)} providers")
    except Exception as e:  # noqa: BLE001
        print(f"  · provider-registry seed skipped: {e}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rounds", type=int,
                    default=int(os.environ.get("SEED_ROUNDS", "12")))
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args()

    token = _token()
    print(f"▸ seeding Shell Market via {ORCH_URL} ({args.rounds} rounds)")

    upsert_provider_metadata(token)
    file_dispute(token)

    fired = 0
    failed = 0
    started = time.time()
    with httpx.Client(timeout=30.0) as c:
        for i in range(args.rounds):
            text, intent, consensus = CORPUS[i % len(CORPUS)]
            try:
                rep = fire(c, text, intent, consensus)
                fired += 1
                if not args.quiet:
                    plan = rep.get("pipeline_plan") or []
                    cost = rep.get("total_cost", 0)
                    ms = rep.get("total_ms", 0)
                    print(f"  ✓ round {i+1:>2}  plan={','.join(plan)}  "
                          f"ms={ms} cost={cost} text={text[:48]!r}")
            except Exception as e:  # noqa: BLE001
                failed += 1
                print(f"  ✗ round {i+1}: {e}")
            time.sleep(0.4 + random.random() * 0.4)

    print(f"\n▸ done. fired={fired} failed={failed} "
          f"elapsed={time.time()-started:.1f}s")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
