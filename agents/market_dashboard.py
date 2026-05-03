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
import httpx

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from register import register_until_ready  # noqa: E402

from anet_sdk import SvcClient  # noqa: E402

NAME = os.environ.get("MARKET_DASHBOARD_NAME", "market-dashboard-svc")
PORT = int(os.environ.get("MARKET_DASHBOARD_PORT", "7422"))
ANET_BASE_URL = os.environ.get("ANET_BASE_URL", "http://127.0.0.1:14113")

# When P2P discovery would dial-to-self (sibling on same daemon), use direct
# local HTTP. Maps skill → (host, port).
_LOCAL_FALLBACK = {
    "auction":           ("127.0.0.1", 7421),
    "reputation":        ("127.0.0.1", 7420),
    "settlement":        ("127.0.0.1", 7423),
    "dispute":           ("127.0.0.1", 7424),
    "quote-broker":      ("127.0.0.1", 7425),
    "market-feed":       ("127.0.0.1", 7426),
    "provider-registry": ("127.0.0.1", 7427),
}

app = FastAPI(title=NAME)


def _call_first(skill: str, path: str, method: str = "GET",
                body: Any = None) -> dict:
    """Discover one peer that offers `skill` and call `path`.

    On dial-to-self failures (siblings on same daemon), retry over local HTTP.
    """
    last_err: dict | None = None
    try:
        with SvcClient(base_url=ANET_BASE_URL) as svc:
            peers = svc.discover(skill=skill, limit=5)
            for p in peers:
                s = (p.get("services") or [{}])[0]
                try:
                    resp = svc.call(p["peer_id"], s["name"], path,
                                    method=method, body=body)
                    out = resp.get("body") or {}
                    if isinstance(out, dict):
                        return out
                except Exception as e:  # noqa: BLE001
                    msg = str(e)
                    last_err = {"error": msg, "skill": skill, "path": path}
                    if "dial to self" not in msg:
                        continue
                    # fall through to local fallback below
                    break
    except Exception as e:  # noqa: BLE001
        last_err = {"error": str(e), "skill": skill, "path": path}

    fb = _LOCAL_FALLBACK.get(skill)
    if fb:
        host, port = fb
        try:
            with httpx.Client(timeout=4.0) as c:
                r = c.request(method, f"http://{host}:{port}{path}",
                              json=body if method != "GET" else None)
                if r.status_code < 400:
                    try:
                        return r.json()
                    except Exception:  # noqa: BLE001
                        return {"raw": r.text}
        except Exception as e:  # noqa: BLE001
            last_err = {"error": f"local fallback: {e}",
                        "skill": skill, "path": path}

    return last_err or {"error": "no provider", "skill": skill, "path": path}


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


@app.get("/api/settlement")
async def api_settlement(limit: int = 50):
    totals = await _async_call("settlement", "/v1/totals")
    ledger = await _async_call("settlement", f"/v1/ledger?limit={limit}")
    return JSONResponse({"totals": totals, "ledger": ledger})


@app.get("/api/disputes")
async def api_disputes(limit: int = 30):
    return JSONResponse(
        await _async_call("dispute", f"/v1/history?limit={limit}")
    )


@app.get("/api/providers")
async def api_providers():
    return JSONResponse(await _async_call("provider-registry", "/v1/list"))


@app.get("/api/feed")
async def api_feed(limit: int = 30):
    return JSONResponse(
        await _async_call("market-feed", f"/v1/recent?limit={limit}")
    )


@app.get("/api/spread")
async def api_spread(skill: str = ""):
    return JSONResponse(
        await _async_call("quote-broker", f"/v1/spread?skill={skill}")
    )


@app.get("/api/protocol_health")
async def api_protocol_health():
    skills = ["auction", "reputation", "settlement", "dispute",
              "quote-broker", "market-feed", "provider-registry",
              "market-dashboard"]
    out = []
    for sk in skills:
        try:
            with SvcClient(base_url=ANET_BASE_URL) as svc:
                peers = svc.discover(skill=sk, limit=3)
            out.append({"skill": sk, "providers": len(peers),
                        "ok": len(peers) > 0})
        except Exception:  # noqa: BLE001
            out.append({"skill": sk, "providers": 0, "ok": False})
    return JSONResponse({"protocol_services": out})


@app.get("/api/stats")
async def api_stats():
    rep = await _async_call("reputation", "/v1/stats")
    hist = await _async_call("auction", "/v1/history?limit=100")
    settle = await _async_call("settlement", "/v1/totals")
    auctions = (hist.get("history") or [])
    by_skill: dict[str, int] = {}
    for a in auctions:
        by_skill[a.get("skill", "?")] = by_skill.get(a.get("skill", "?"), 0) + 1
    return JSONResponse({
        "reputation": rep,
        "settlement": settle,
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
           display:flex; align-items:baseline; gap:18px; flex-wrap:wrap; }
  header h1 { margin:0; font-size:18px; color:var(--accent); }
  header .tag { color:var(--dim); font-size:11px; }
  header .live { margin-left:auto; color:var(--good); font-size:11px; }
  main { display:grid; grid-template-columns:1fr 1fr;
         gap:16px; padding:16px 24px; }
  section { background:var(--panel); border:1px solid var(--border);
            border-radius:6px; overflow:hidden; }
  section.full { grid-column: span 2; }
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
  .pill.bad { color:var(--bad); background:#2a1718; }
  .pill.warn { color:var(--warn); background:#2a2417; }
  footer { padding:12px 24px; color:var(--dim); font-size:11px;
           border-top:1px solid var(--border); }
  .empty { padding:18px; color:var(--dim); font-style:italic; }
  #stats { display:flex; gap:18px; padding:12px 14px;
           font-size:12px; color:var(--dim); flex-wrap:wrap; }
  #stats b { color:var(--fg); font-weight:normal; }
  #protostatus { padding:8px 14px; display:flex; gap:10px; flex-wrap:wrap; }
  #protostatus .pill { font-size:11px; padding:3px 8px; }
  details { padding:6px 14px; }
  summary { cursor:pointer; color:var(--dim); }
  #feed { max-height: 360px; overflow:auto; padding:6px 14px;
          font-size:11px; line-height:1.7; }
  #feed .ev { color:var(--dim); }
  #feed .ev b { color:var(--accent); }
  #feed .ev .skill { color:var(--warn); }
  #feed .ev .ok { color:var(--good); }
  #feed .ev .bad { color:var(--bad); }
  #feed .ts { color:#3e4a55; margin-right:6px; }
  .bar { display:inline-block; height:8px; background:var(--accent);
         border-radius:2px; vertical-align:middle; margin-right:6px; }
</style>
</head>
<body>
<header>
  <h1>Shell Market Protocol</h1>
  <span class="tag">8 protocol services • live mesh • externally reachable</span>
  <span class="live">● live · auto-refresh 3s · SSE feed</span>
</header>

<section>
  <h2>Mesh Stats</h2>
  <div id="stats">loading…</div>
  <div id="protostatus"></div>
</section>

<main>
  <section>
    <h2>Reputation Leaderboard</h2>
    <div id="leaderboard"><div class="empty">loading…</div></div>
  </section>

  <section>
    <h2>Settlement Ledger — Revenue by Provider</h2>
    <div id="settlement"><div class="empty">loading…</div></div>
  </section>

  <section>
    <h2>Recent Auctions</h2>
    <div id="auctions"><div class="empty">loading…</div></div>
  </section>

  <section>
    <h2>Live Market Feed</h2>
    <div id="feed"><div class="empty">connecting…</div></div>
  </section>

  <section>
    <h2>Provider Registry</h2>
    <div id="providers"><div class="empty">loading…</div></div>
  </section>

  <section>
    <h2>Disputes</h2>
    <div id="disputes"><div class="empty">loading…</div></div>
  </section>
</main>

<footer>
  Shell Market Protocol — 8 protocol services on AgentNetwork:
  <span class="pill">auction</span>
  <span class="pill">reputation</span>
  <span class="pill">settlement</span>
  <span class="pill">dispute</span>
  <span class="pill">quote-broker</span>
  <span class="pill">market-feed</span>
  <span class="pill">provider-registry</span>
  <span class="pill">market-dashboard</span>
  &nbsp; · any provider exposing /v1/quote joins automatically.
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
  const set = s.settlement || {};
  const skills = Object.entries(auc.by_skill || {})
    .map(([k,v]) => `${k}:${v}`).join(' ');
  document.getElementById('stats').innerHTML = `
    <span>auctions <b>${auc.total || 0}</b></span>
    <span>shells settled <b class="pos">${set.total_shells || 0}</b></span>
    <span>paid calls <b>${set.total_calls || 0}</b></span>
    <span>reputation records <b>${rep.records || 0}</b></span>
    <span>wins <b class="pos">${rep.total_wins || 0}</b></span>
    <span>losses <b class="neg">${rep.total_losses || 0}</b></span>
    <span class="tag">by skill: ${skills || '—'}</span>
  `;

  const ph = await jget('/api/protocol_health');
  const items = (ph.protocol_services || []).map(p => {
    const cls = p.ok ? '' : 'bad';
    const tag = p.ok ? `${p.providers}p` : 'down';
    return `<span class="pill ${cls}">${p.skill} · ${tag}</span>`;
  }).join('');
  document.getElementById('protostatus').innerHTML = items;
}

async function refreshLeaderboard() {
  const j = await jget('/api/leaderboard?limit=15');
  const rows = (j.leaderboard || []);
  const el = document.getElementById('leaderboard');
  if (!rows.length) {
    el.innerHTML = '<div class="empty">no calls yet — run scripts/seed_market.py</div>';
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

async function refreshSettlement() {
  const j = await jget('/api/settlement?limit=30');
  const t = (j.totals || {});
  const byp = t.by_provider || {};
  const el = document.getElementById('settlement');
  const rows = Object.entries(byp).sort((a,b) => b[1].shells - a[1].shells);
  if (!rows.length) {
    el.innerHTML = '<div class="empty">no settled shells yet</div>';
    return;
  }
  const max = Math.max(1, ...rows.map(r => r[1].shells));
  el.innerHTML = `<table>
    <tr><th>service</th><th class="num">shells</th>
        <th class="num">calls</th><th>volume</th></tr>
    ${rows.map(([svc, v]) => `
      <tr>
        <td>${svc}</td>
        <td class="num pos">${v.shells}</td>
        <td class="num">${v.calls}</td>
        <td><span class="bar" style="width:${Math.round(120*v.shells/max)}px"></span></td>
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

async function refreshProviders() {
  const j = await jget('/api/providers');
  const rows = (j.providers || []);
  const el = document.getElementById('providers');
  if (!rows.length) {
    el.innerHTML = '<div class="empty">no provider metadata yet</div>';
    return;
  }
  el.innerHTML = `<table>
    <tr><th>service</th><th>region</th><th>version</th>
        <th>capabilities</th><th class="num">SLA p99</th></tr>
    ${rows.map(p => `
      <tr>
        <td>${p.service}</td>
        <td class="loser">${p.region}</td>
        <td class="loser">${p.version}</td>
        <td><span class="loser">${(p.capabilities||[]).join(', ')}</span></td>
        <td class="num">${(p.sla||{}).p99_ms || '—'}ms</td>
      </tr>
    `).join('')}
  </table>`;
}

async function refreshDisputes() {
  const j = await jget('/api/disputes?limit=15');
  const rows = (j.history || []);
  const el = document.getElementById('disputes');
  if (!rows.length) {
    el.innerHTML = '<div class="empty">no disputes filed</div>';
    return;
  }
  el.innerHTML = `<table>
    <tr><th>id</th><th>accused</th><th>verdict</th>
        <th>reason</th></tr>
    ${rows.map(d => {
      const cls = d.verdict === 'upheld' ? 'pill bad'
                : d.verdict === 'partial' ? 'pill warn' : 'pill';
      return `<tr>
        <td class="loser">${d.dispute_id}</td>
        <td>${d.accused_service}</td>
        <td><span class="${cls}">${d.verdict}</span></td>
        <td class="loser">${(d.reason||'').slice(0,50)}</td>
      </tr>`;
    }).join('')}
  </table>`;
}

function feedAdd(ev) {
  const el = document.getElementById('feed');
  if (el.querySelector('.empty')) el.innerHTML = '';
  const ts = fmtTs(ev.ts);
  const d = ev.data || {};
  let line = '';
  if (ev.kind === 'auction.closed') {
    line = `<b>auction</b> <span class="skill">${d.skill}</span>
            ${d.bidders} bidders → <span class="ok">${d.winner_service||'NONE'}</span>`;
  } else if (ev.kind === 'step.completed') {
    const cls = d.ok ? 'ok' : 'bad';
    line = `<b>step</b> <span class="skill">${d.skill}</span>
            ${d.service} <span class="${cls}">${d.ms}ms ${d.cost}sh</span>`;
  } else {
    line = `<b>${ev.kind}</b> ${JSON.stringify(d).slice(0,100)}`;
  }
  const div = document.createElement('div');
  div.className = 'ev';
  div.innerHTML = `<span class="ts">${ts}</span> ${line}`;
  el.insertBefore(div, el.firstChild);
  while (el.children.length > 60) el.removeChild(el.lastChild);
}

function startSSE() {
  try {
    const es = new EventSource('/api/feed');
  } catch(e) {}
  // dashboard polls /api/feed since the SSE on market-feed-svc is on a
  // different port; we tail it by polling.
  let lastSeq = 0;
  async function pollFeed() {
    const j = await jget('/api/feed?limit=30');
    const evs = (j.events || []).filter(e => e.seq > lastSeq);
    evs.reverse().forEach(e => { lastSeq = Math.max(lastSeq, e.seq); feedAdd(e); });
  }
  pollFeed();
  setInterval(pollFeed, 1500);
}

async function tick() {
  await Promise.all([
    refreshStats(),
    refreshLeaderboard(),
    refreshSettlement(),
    refreshAuctions(),
    refreshProviders(),
    refreshDisputes(),
  ]);
}

tick();
setInterval(tick, 3000);
startSSE();
</script>
</body>
</html>
"""


def main() -> None:
    threading.Thread(
        target=lambda: register_until_ready(
            NAME, PORT,
            paths=["/api/leaderboard", "/api/auctions", "/api/active",
                   "/api/stats", "/api/settlement", "/api/disputes",
                   "/api/providers", "/api/feed", "/api/spread",
                   "/api/protocol_health",
                   "/health", "/meta"],
            tags=["market-dashboard", "shell-market", "protocol"],
            description="Shell Market Protocol — live market dashboard",
            per_call=0, base_url=ANET_BASE_URL,
        ),
        daemon=True,
    ).start()
    print(f"[{NAME}] serving on http://127.0.0.1:{PORT}", flush=True)
    uvicorn.run(app, host=os.environ.get("LISTEN_HOST","0.0.0.0"), port=PORT, log_level="warning")


if __name__ == "__main__":
    main()
