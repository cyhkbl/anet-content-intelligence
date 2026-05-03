"""market-dashboard-svc — Shell Market Protocol live monitor.

This is the *market* dashboard, distinct from the per-pipeline observer in
agents/dashboard.py. It speaks only to the protocol services (auction-svc
and reputation-svc) over P2P, so it works with any mesh that runs the
Shell Market Protocol — it doesn't have to know about NLP at all.

Pages
-----
  GET  /                     → HTML dashboard (live auction tape +
                                reputation leaderboard)
  GET  /api/leaderboard      → reputation leaderboard via P2P
  GET  /api/auctions         → recent auction history via P2P
  GET  /api/active           → currently open auctions via P2P
  GET  /api/stats            → reputation totals + auction counts
  GET  /health               → liveness
"""

from __future__ import annotations

import asyncio
import os
import sys
import threading
import time
from typing import Any

import uvicorn
from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from register import register_until_ready  # noqa: E402

from anet_sdk import SvcClient  # noqa: E402

NAME = os.environ.get("MARKET_DASHBOARD_NAME", "market-dashboard-svc")
PORT = int(os.environ.get("MARKET_DASHBOARD_PORT", "7422"))
ANET_BASE_URL = os.environ.get("ANET_BASE_URL", "http://127.0.0.1:14113")

app = FastAPI(title=NAME)


def _call_first(skill: str, path: str, method: str = "GET",
                body: Any = None) -> dict:
    """Discover one peer that offers `skill` and call `path`."""
    try:
        with SvcClient(base_url=ANET_BASE_URL) as svc:
            peers = svc.discover(skill=skill, limit=5)
            for p in peers:
                s = (p.get("services") or [{}])[0]
                resp = svc.call(p["peer_id"], s["name"], path,
                                method=method, body=body)
                out = resp.get("body") or {}
                if isinstance(out, dict):
                    return out
    except Exception as e:  # noqa: BLE001
        return {"error": str(e), "skill": skill, "path": path}
    return {"error": "no provider", "skill": skill, "path": path}


async def _async_call(skill: str, path: str, method: str = "GET",
                      body: Any = None) -> dict:
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _call_first, skill, path,
                                      method, body)


@app.get("/health")
def health():
    return {"ok": True, "agent": NAME}


@app.get("/meta")
def meta():
    return {"name": NAME, "version": "1.0.0",
            "skill": "market-dashboard",
            "protocol": "shell-market/dashboard"}


@app.get("/api/leaderboard")
async def api_leaderboard(limit: int = 20):
    return JSONResponse(
        await _async_call("reputation", f"/v1/leaderboard?limit={limit}")
    )


@app.get("/api/auctions")
async def api_auctions(limit: int = 30):
    return JSONResponse(
        await _async_call("auction", f"/v1/history?limit={limit}")
    )


@app.get("/api/active")
async def api_active():
    return JSONResponse(await _async_call("auction", "/v1/active"))


@app.get("/api/stats")
async def api_stats():
    rep = await _async_call("reputation", "/v1/stats")
    hist = await _async_call("auction", "/v1/history?limit=100")
    auctions = (hist.get("history") or [])
    by_skill: dict[str, int] = {}
    for a in auctions:
        by_skill[a.get("skill", "?")] = by_skill.get(a.get("skill", "?"), 0) + 1
    return JSONResponse({
        "reputation": rep,
        "auctions": {
            "total": len(auctions),
            "by_skill": by_skill,
        },
        "now": time.time(),
    })


@app.get("/", response_class=HTMLResponse)
def index():
    return HTMLResponse(_HTML)


_HTML = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Shell Market Protocol — Live</title>
<style>
  :root {
    --bg:#0a0e14; --panel:#0f1620; --border:#1f2933;
    --fg:#d6deeb; --dim:#7a8794; --accent:#7fdbca;
    --good:#a3be8c; --bad:#bf616a; --warn:#ebcb8b;
  }
  * { box-sizing:border-box; }
  body { margin:0; background:var(--bg); color:var(--fg);
         font-family:"JetBrains Mono","SF Mono",Menlo,monospace;
         font-size:13px; }
  header { padding:16px 24px; border-bottom:1px solid var(--border);
           display:flex; align-items:baseline; gap:18px; }
  header h1 { margin:0; font-size:18px; color:var(--accent); }
  header .tag { color:var(--dim); font-size:11px; }
  header .live { margin-left:auto; color:var(--good); font-size:11px; }
  main { display:grid; grid-template-columns:1fr 1fr;
         gap:16px; padding:16px 24px; }
  section { background:var(--panel); border:1px solid var(--border);
            border-radius:6px; overflow:hidden; }
  section h2 { margin:0; padding:10px 14px;
               border-bottom:1px solid var(--border);
               font-size:12px; color:var(--accent);
               text-transform:uppercase; letter-spacing:1px; }
  table { width:100%; border-collapse:collapse; font-size:12px; }
  th,td { padding:6px 12px; text-align:left;
          border-bottom:1px solid #14202a; }
  th { color:var(--dim); font-weight:normal; font-size:11px;
       text-transform:uppercase; letter-spacing:1px; }
  tr:hover td { background:#121a24; }
  .num { text-align:right; font-variant-numeric:tabular-nums; }
  .winner { color:var(--good); }
  .loser { color:var(--dim); }
  .pos { color:var(--good); }
  .neg { color:var(--bad); }
  .pill { display:inline-block; padding:1px 6px; border-radius:3px;
          background:#1d2a36; color:var(--accent); font-size:10px; }
  footer { padding:12px 24px; color:var(--dim); font-size:11px;
           border-top:1px solid var(--border); }
  .empty { padding:18px; color:var(--dim); font-style:italic; }
  #stats { display:flex; gap:24px; padding:12px 14px;
           font-size:12px; color:var(--dim); }
  #stats b { color:var(--fg); font-weight:normal; }
  details { padding:6px 14px; }
  summary { cursor:pointer; color:var(--dim); }
</style>
</head>
<body>
<header>
  <h1>Shell Market Protocol</h1>
  <span class="tag">live mesh • auction + reputation</span>
  <span class="live">● refresh 3s</span>
</header>

<section>
  <h2>Mesh Stats</h2>
  <div id="stats">loading…</div>
</section>

<main>
  <section>
    <h2>Reputation Leaderboard</h2>
    <div id="leaderboard"><div class="empty">loading…</div></div>
  </section>

  <section>
    <h2>Recent Auctions</h2>
    <div id="auctions"><div class="empty">loading…</div></div>
  </section>
</main>

<footer>
  Powered by reputation-svc + auction-svc + market-dashboard-svc — three
  protocol services on the AgentNetwork mesh. Any provider speaking /v1/quote
  joins automatically.
</footer>

<script>
async function jget(url) {
  try {
    const r = await fetch(url);
    if (!r.ok) return {error: r.statusText};
    return await r.json();
  } catch (e) { return {error: e.message}; }
}

function fmtTs(t) {
  if (!t) return '';
  const d = new Date(t * 1000);
  return d.toTimeString().slice(0, 8);
}

async function refreshStats() {
  const s = await jget('/api/stats');
  const rep = s.reputation || {};
  const auc = s.auctions || {};
  const skills = Object.entries(auc.by_skill || {})
    .map(([k,v]) => `${k}:${v}`).join(' ');
  document.getElementById('stats').innerHTML = `
    <span>reputation records <b>${rep.records || 0}</b></span>
    <span>calls <b>${rep.total_calls || 0}</b></span>
    <span>wins <b class="pos">${rep.total_wins || 0}</b></span>
    <span>losses <b class="neg">${rep.total_losses || 0}</b></span>
    <span>auctions <b>${auc.total || 0}</b></span>
    <span class="tag">by skill: ${skills || '—'}</span>
  `;
}

async function refreshLeaderboard() {
  const j = await jget('/api/leaderboard?limit=15');
  const rows = (j.leaderboard || []);
  const el = document.getElementById('leaderboard');
  if (!rows.length) {
    el.innerHTML = '<div class="empty">no calls yet — run the demo client</div>';
    return;
  }
  el.innerHTML = `<table>
    <tr><th>#</th><th>service</th><th>peer</th>
        <th class="num">score</th><th class="num">wins</th>
        <th class="num">losses</th><th class="num">bonus</th></tr>
    ${rows.map((r,i) => `
      <tr>
        <td class="num">${i+1}</td>
        <td>${r.service}</td>
        <td class="loser">${r.short_peer || ''}</td>
        <td class="num ${r.score > 0 ? 'pos' : r.score < 0 ? 'neg' : ''}">${r.score}</td>
        <td class="num pos">${r.wins}</td>
        <td class="num neg">${r.losses}</td>
        <td class="num">${(r.bonus||0).toFixed(2)}</td>
      </tr>
    `).join('')}
  </table>`;
}

async function refreshAuctions() {
  const j = await jget('/api/auctions?limit=12');
  const rows = (j.history || []);
  const el = document.getElementById('auctions');
  if (!rows.length) {
    el.innerHTML = '<div class="empty">no auctions yet</div>';
    return;
  }
  el.innerHTML = rows.map(a => {
    const w = (a.winners || [])[0];
    return `
      <details>
        <summary>
          <span class="pill">${a.skill}</span>
          <span style="margin:0 8px">${fmtTs(a.closed_at || a.opened_at)}</span>
          <span class="loser">${a.bid_count} bidders</span>
          → <span class="winner">${w ? w.service : 'NONE'}</span>
          ${w ? `<span class="loser"> @ ${w.bid}sh / ${w.eta_ms}ms (score ${w.score})</span>` : ''}
        </summary>
        <div style="padding:6px 0 12px">
          <div class="loser" style="margin-bottom:4px">
            "${(a.input_preview || '').replace(/"/g,'')}"
          </div>
          <span class="tag">auction ${a.auction_id}</span>
        </div>
      </details>
    `;
  }).join('');
}

async function tick() {
  await Promise.all([refreshStats(), refreshLeaderboard(), refreshAuctions()]);
}

tick();
setInterval(tick, 3000);
</script>
</body>
</html>
"""


def main() -> None:
    threading.Thread(
        target=lambda: register_until_ready(
            NAME, PORT,
            paths=["/api/leaderboard", "/api/auctions", "/api/active",
                   "/api/stats", "/health", "/meta"],
            tags=["market-dashboard", "shell-market", "protocol"],
            description="Shell Market Protocol — live market dashboard",
            per_call=0, base_url=ANET_BASE_URL,
        ),
        daemon=True,
    ).start()
    print(f"[{NAME}] serving on http://127.0.0.1:{PORT}", flush=True)
    uvicorn.run(app, host="127.0.0.1", port=PORT, log_level="warning")


if __name__ == "__main__":
    main()
