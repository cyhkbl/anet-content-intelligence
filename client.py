"""Content Intelligence Pipeline — CLI client.

Talks to its local anet daemon, discovers the orchestrator agent, calls
/v1/analyze with user-supplied text, and pretty-prints the report along
with a per-node audit trail. Demonstrates the P2P round-trip end-to-end.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

# Allow `python client.py` from the repo root.
sys.path.insert(0, str(Path(__file__).resolve().parent / "agents"))
from anet_sdk import SvcAPIError, SvcClient  # noqa: E402

DEFAULT_BASE_URL = os.environ.get("ANET_BASE_URL", "http://127.0.0.1:14101")

DAEMONS = [
    ("u1 translate", "http://127.0.0.1:14101", "/tmp/anet-ci-u1"),
    ("u2 extract  ", "http://127.0.0.1:14102", "/tmp/anet-ci-u2"),
    ("u3 sentiment", "http://127.0.0.1:14103", "/tmp/anet-ci-u3"),
    ("u4 summarise", "http://127.0.0.1:14104", "/tmp/anet-ci-u4"),
    ("u5 classify ", "http://127.0.0.1:14105", "/tmp/anet-ci-u5"),
    ("u6 orchestr.", "http://127.0.0.1:14106", "/tmp/anet-ci-u6"),
]

DEMO_TEXT = (
    "OpenAI announced its newest AI model today, sending shares of Nvidia "
    "rising in the stock market. CEO Sam Altman said the launch is a major "
    "breakthrough for artificial intelligence and product growth."
)


def find(svc: SvcClient, skill: str, retries: int = 30) -> dict | None:
    for _ in range(retries):
        peers = svc.discover(skill=skill)
        if peers:
            return peers[0]
        time.sleep(1)
    return None


def pretty(report: dict) -> None:
    print("\n" + "=" * 72)
    print("📊  CONTENT INTELLIGENCE REPORT")
    print("=" * 72)
    print(f"\nInput:\n  {report.get('input', '')!r}\n")
    if report.get("source_lang"):
        print(f"Source language : {report['source_lang']}")
    if "summary" in report:
        print(f"Summary         : {report['summary']}")
    sent = report.get("sentiment") or {}
    if sent:
        print(f"Sentiment       : {sent.get('label')} (score={sent.get('score')})")
    if "topic" in report:
        kws = ", ".join(report.get("topic_keywords") or [])
        print(f"Topic           : {report['topic']} "
              f"(confidence={report.get('topic_confidence')}, kws=[{kws}])")
    ents = report.get("entities") or []
    if ents:
        print(f"Entities ({report.get('entity_count', len(ents))}):")
        for e in ents[:10]:
            print(f"  • {e['type']:<7} {e['text']!r}  @[{e['start']}:{e['end']}]")
    print("\nPipeline (orchestrator-side):")
    for step in report.get("pipeline") or []:
        print(f"  {step['skill']:<10} svc={step['svc']:<20} "
              f"peer={step['peer']}…  {step['ms']} ms")
    if report.get("missing_skills"):
        print(f"  ⚠ missing skills: {', '.join(report['missing_skills'])}")
    print(f"Total time      : {report.get('total_ms')} ms")
    print("=" * 72)


def audit_trail() -> None:
    print("\n📜  Per-daemon svc_call_log (last 3 each):")
    for label, base, home in DAEMONS:
        token_path = Path(home) / ".anet" / "api_token"
        if not token_path.exists():
            print(f"  {label}: no token (daemon down?)")
            continue
        token = token_path.read_text().strip()
        try:
            with SvcClient(base_url=base, token=token) as s:
                rows = s.audit(limit=3)
        except SvcAPIError as e:
            print(f"  {label}: audit error {e.status} {e.message[:60]}")
            continue
        except Exception as e:  # noqa: BLE001
            print(f"  {label}: audit failed: {e}")
            continue
        if not rows:
            print(f"  {label}: (no calls)")
            continue
        for r in rows:
            svc = r.get("service") or "?"
            method = r.get("method") or "?"
            path = r.get("path") or "?"
            status = r.get("status") or 0
            cost = r.get("cost", 0)
            print(f"  {label}: {svc:<18} {method:<5} {path:<22} "
                  f"status={status} cost={cost}")


def main() -> int:
    ap = argparse.ArgumentParser(description="Content Intelligence Pipeline client")
    ap.add_argument("text", nargs="?", default=DEMO_TEXT,
                    help="Text to analyze (default: bundled English demo)")
    ap.add_argument("--base-url", default=DEFAULT_BASE_URL,
                    help="Local anet daemon REST URL (default: u1=14101)")
    ap.add_argument("--no-audit", action="store_true",
                    help="Skip per-daemon audit summary")
    ap.add_argument("--json", action="store_true",
                    help="Print raw JSON report instead of pretty layout")
    args = ap.parse_args()

    print(f"[client] connecting via {args.base_url}", file=sys.stderr)
    with SvcClient(base_url=args.base_url) as svc:
        target = find(svc, "orchestrator")
        if not target:
            print("[client] no orchestrator-svc peers (mesh not converged?)",
                  file=sys.stderr)
            return 1
        peer_id = target["peer_id"]
        name = target["services"][0]["name"]
        print(f"[client] orchestrator={name} peer={peer_id[:18]}…",
              file=sys.stderr)

        resp = svc.call(peer_id, name, "/v1/analyze",
                        method="POST", body={"text": args.text})
    body = resp.get("body") or {}
    if not isinstance(body, dict):
        print(f"[client] unexpected body: {body!r}", file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps(body, indent=2, ensure_ascii=False))
    else:
        pretty(body)
    if not args.no_audit:
        audit_trail()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
