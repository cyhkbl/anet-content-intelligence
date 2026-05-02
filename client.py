"""Content Intelligence Pipeline — CLI client (self-composing edition).

Talks to its local anet daemon, discovers the orchestrator agent, calls
/v1/analyze with user-supplied text, and pretty-prints the report plus:

  • a catalogue of every content-intel service the orchestrator found
  • an ASCII pipeline call-graph
  • per-call cost + cumulative shell spend
  • per-daemon credit balance (how much each node can afford)
  • per-daemon audit trail (recent P2P calls)
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent / "agents"))
from anet_sdk import SvcAPIError, SvcClient  # noqa: E402

DEFAULT_BASE_URL = os.environ.get("ANET_BASE_URL", "http://127.0.0.1:14101")

DAEMONS = [
    ("u1 translate      ", "http://127.0.0.1:14101", "/tmp/anet-ci-u1"),
    ("u2 extract        ", "http://127.0.0.1:14102", "/tmp/anet-ci-u2"),
    ("u3 sentiment      ", "http://127.0.0.1:14103", "/tmp/anet-ci-u3"),
    ("u4 summarise      ", "http://127.0.0.1:14104", "/tmp/anet-ci-u4"),
    ("u5 classify       ", "http://127.0.0.1:14105", "/tmp/anet-ci-u5"),
    ("u6 orchestrator   ", "http://127.0.0.1:14106", "/tmp/anet-ci-u6"),
    ("u7 factcheck      ", "http://127.0.0.1:14107", "/tmp/anet-ci-u7"),
    ("u8 translate-en-zh", "http://127.0.0.1:14108", "/tmp/anet-ci-u8"),
    ("u9 keywords       ", "http://127.0.0.1:14109", "/tmp/anet-ci-u9"),
]

DEMO_TEXT = (
    "OpenAI announced its newest AI model today, sending shares of Nvidia "
    "rising 12% in the stock market. CEO Sam Altman said the launch is a "
    "major breakthrough for artificial intelligence and product growth."
)


def _has_path(peer: dict, path: str) -> bool:
    for s in peer.get("services") or []:
        for p in s.get("paths") or []:
            prefix = p.get("prefix") if isinstance(p, dict) else str(p)
            if prefix and path.startswith(prefix):
                return True
    return False


def find(svc: SvcClient, skill: str, *, require_path: str | None = None,
         retries: int = 30) -> dict | None:
    """Discover peers by skill, optionally filtering to those that expose a
    given path. Lets us ignore unrelated orchestrators on the public mesh."""
    for _ in range(retries):
        peers = svc.discover(skill=skill)
        if peers:
            if require_path:
                matches = [p for p in peers if _has_path(p, require_path)]
                if matches:
                    return matches[0]
            else:
                return peers[0]
        time.sleep(1)
    return None


def _balance(base_url: str, token: str) -> int | None:
    """Best-effort query of a daemon's credit balance. Tolerates 404s."""
    for path in ("/api/credits/balance", "/api/credits", "/api/status"):
        try:
            r = httpx.get(
                f"{base_url.rstrip('/')}{path}",
                headers={"Authorization": f"Bearer {token}"},
                timeout=2.0,
            )
            if r.status_code != 200:
                continue
            j = r.json()
            for key in ("balance", "credits", "shell", "shells"):
                if isinstance(j, dict) and key in j and isinstance(j[key], (int, float)):
                    return int(j[key])
            # Some daemons expose a map {did: balance}
            if path.endswith("/credits") and isinstance(j, dict):
                total = sum(v for v in j.values() if isinstance(v, (int, float)))
                if total:
                    return int(total)
            if path.endswith("/status") and isinstance(j, dict):
                b = j.get("credits") or j.get("balance")
                if isinstance(b, (int, float)):
                    return int(b)
                if isinstance(b, dict):
                    total = sum(v for v in b.values() if isinstance(v, (int, float)))
                    return int(total)
        except httpx.HTTPError:
            pass
    return None


def print_discovery(catalogue: list[dict]) -> None:
    print("\n🔎  Services discovered on the content-intel mesh:")
    if not catalogue:
        print("   (none)")
        return
    by_skill: dict[str, list[dict]] = {}
    for s in catalogue:
        by_skill.setdefault(s.get("skill") or "?", []).append(s)
    for skill in sorted(by_skill):
        for s in by_skill[skill]:
            cm = s.get("cost") or {}
            cost = "free" if cm.get("free") else f"{cm.get('per_call', '?')}¢/call"
            pid = (s.get("peer_id") or "")[:18]
            print(f"   • {skill:<18} svc={s.get('service','?'):<22} "
                  f"peer={pid}…  {cost}")


def print_pipeline_graph(pipeline: list[dict]) -> None:
    if not pipeline:
        return
    print("\n🔗  Pipeline call chain:")
    width = max(len(s.get("skill", "")) for s in pipeline)
    for i, step in enumerate(pipeline):
        connector = "    │" if i < len(pipeline) - 1 else "    ▼"
        arrow = "└──▶" if i == 0 else "├──▶"
        cost = step.get("cost", 0)
        cost_txt = f"  {cost}¢" if cost else "  free"
        print(f"   {arrow} {step['skill']:<{width}}  "
              f"svc={step['svc']:<22} peer={step['peer']}…  {step['ms']}ms{cost_txt}")
        if i < len(pipeline) - 1:
            print(f"   {connector}")


def pretty(report: dict) -> None:
    print("\n" + "═" * 76)
    print("📊  CONTENT INTELLIGENCE REPORT  ·  self-composing orchestrator")
    print("═" * 76)
    print(f"\nInput:\n  {report.get('input', '')!r}")
    print(f"Intent : {report.get('intent')}")

    print_discovery(report.get("discovered_services") or [])

    plan = report.get("pipeline_plan") or []
    if plan:
        print(f"\n🧠  Orchestrator plan: {' → '.join(plan)}")

    print_pipeline_graph(report.get("pipeline") or [])

    # ── output fields ───────────────────────────────────────────────────
    print("\n📝  Results:")
    if report.get("source_lang"):
        print(f"  source_lang   : {report['source_lang']}")
    if report.get("translated"):
        print(f"  translated    : {report['translated']}")
    if report.get("summary"):
        print(f"  summary       : {report['summary']}")
    sent = report.get("sentiment") or {}
    if sent:
        print(f"  sentiment     : {sent.get('label')} (score={sent.get('score')})")
    if "topic" in report:
        kws = ", ".join(report.get("topic_keywords") or [])
        print(f"  topic         : {report.get('topic')} "
              f"(confidence={report.get('topic_confidence')}, kws=[{kws}])")
    kw = report.get("keywords") or []
    if kw:
        kw_txt = ", ".join(f"{k['word']}({k['score']})" for k in kw[:6])
        print(f"  keywords      : {kw_txt}")
    ents = report.get("entities") or []
    if ents:
        print(f"  entities ({report.get('entity_count', len(ents))}):")
        for e in ents[:8]:
            print(f"    • {e['type']:<7} {e['text']!r}  @[{e['start']}:{e['end']}]")
    fc = report.get("factcheck") or {}
    if fc:
        print(f"  factcheck     : verdict={fc.get('verdict')} "
              f"counts={fc.get('counts')}")
        for c in (fc.get("claims") or [])[:5]:
            print(f"    • {c.get('claim')!r:<20} → {c.get('status')} "
                  f"({c.get('reason','')[:60]})")
    if report.get("translated_zh"):
        print(f"  translated_zh : {report['translated_zh']}")

    # ── cost / latency ──────────────────────────────────────────────────
    pipe = report.get("pipeline") or []
    print("\n💰  Shell economy:")
    print(f"  total shells spent : {report.get('total_cost', 0)}  "
          f"(across {len(pipe)} P2P hops)")
    if pipe:
        by_skill = {}
        for step in pipe:
            by_skill[step["skill"]] = by_skill.get(step["skill"], 0) + int(step.get("cost") or 0)
        for skill, cost in sorted(by_skill.items(), key=lambda kv: -kv[1]):
            print(f"     · {skill:<18}  {cost}¢")
    print(f"  total wall-clock   : {report.get('total_ms')} ms")

    if report.get("missing_skills"):
        print(f"\n⚠  Orchestrator couldn't find handlers for: "
              f"{', '.join(report['missing_skills'])}")
    print("═" * 76)


def audit_trail() -> None:
    print("\n📜  Per-daemon svc_call_log (last 3 each) + shell balance:")
    for label, base, home in DAEMONS:
        token_path = Path(home) / ".anet" / "api_token"
        if not token_path.exists():
            print(f"  {label}: no token (daemon down?)")
            continue
        token = token_path.read_text().strip()
        balance = _balance(base, token)
        bal_txt = f"balance={balance}" if balance is not None else "balance=?"
        try:
            with SvcClient(base_url=base, token=token) as s:
                rows = s.audit(limit=3)
        except SvcAPIError as e:
            print(f"  {label}: {bal_txt}  audit error {e.status} {e.message[:60]}")
            continue
        except Exception as e:  # noqa: BLE001
            print(f"  {label}: {bal_txt}  audit failed: {e}")
            continue
        if not rows:
            print(f"  {label}: {bal_txt}  (no calls)")
            continue
        print(f"  {label}: {bal_txt}")
        for r in rows:
            svc = r.get("service") or "?"
            method = r.get("method") or "?"
            path = r.get("path") or "?"
            status = r.get("status") or 0
            cost = r.get("cost", 0)
            print(f"     · {svc:<22} {method:<5} {path:<22} "
                  f"status={status} cost={cost}")


def main() -> int:
    ap = argparse.ArgumentParser(description="Content Intelligence Pipeline client")
    ap.add_argument("text", nargs="?", default=DEMO_TEXT,
                    help="Text to analyze (default: bundled English demo)")
    ap.add_argument("--intent", default="analyze",
                    choices=["analyze", "translate-to-zh"],
                    help="User intent hint passed to orchestrator")
    ap.add_argument("--base-url", default=DEFAULT_BASE_URL,
                    help="Local anet daemon REST URL (default: u1=14101)")
    ap.add_argument("--no-audit", action="store_true",
                    help="Skip per-daemon audit + balance summary")
    ap.add_argument("--json", action="store_true",
                    help="Print raw JSON report instead of pretty layout")
    args = ap.parse_args()

    print(f"[client] connecting via {args.base_url}", file=sys.stderr)
    with SvcClient(base_url=args.base_url) as svc:
        target = find(svc, "orchestrator", require_path="/v1/analyze")
        if not target:
            print("[client] no orchestrator-svc peers (mesh not converged?)",
                  file=sys.stderr)
            return 1
        peer_id = target["peer_id"]
        name = target["services"][0]["name"]
        print(f"[client] orchestrator={name} peer={peer_id[:18]}…",
              file=sys.stderr)

        resp = svc.call(peer_id, name, "/v1/analyze", method="POST",
                        body={"text": args.text, "intent": args.intent})
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
