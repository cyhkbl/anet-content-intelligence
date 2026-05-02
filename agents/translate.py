"""translate-svc — Chinese→English translator (rule-based, no model deps).

Exposes POST /v1/translate {text: "…"} → {translated, lang, agent}.

Uses a small longest-match dictionary lookup so the hackathon demo runs
offline. Swap in a real model later; the HTTP surface stays the same.
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
from quote_helpers import make_quote  # noqa: E402

NAME = os.environ.get("TRANSLATE_NAME", "translate-svc")
PORT = int(os.environ.get("TRANSLATE_PORT", "7401"))
PER_CALL = int(os.environ.get("TRANSLATE_PER_CALL", "5"))
BASE_ETA_MS = int(os.environ.get("TRANSLATE_BASE_ETA_MS", "40"))
QUOTE_STYLE = os.environ.get("TRANSLATE_QUOTE_STYLE", "balanced")

TABLE = {
    # places
    "上海": "shanghai", "北京": "beijing", "深圳": "shenzhen",
    "广州": "guangzhou", "杭州": "hangzhou", "中国": "china",
    # time
    "明天": "tomorrow", "今天": "today", "昨天": "yesterday",
    "现在": "now", "年": "year", "月": "month", "日": "day",
    # weather / qualities
    "天气": "weather", "怎么样": "how is", "好": "good",
    "不好": "bad", "很好": "very good", "热": "hot", "冷": "cold",
    "晴": "sunny", "雨": "rain", "雪": "snow",
    # business / tech
    "公司": "company", "市场": "market", "投资": "investment",
    "股票": "stock", "人工智能": "artificial intelligence",
    "科技": "technology", "数据": "data", "模型": "model",
    "产品": "product", "服务": "service", "客户": "customer",
    # actions / misc
    "给我": "give me", "请": "please", "用一句话": "in one sentence",
    "总结": "summarise", "你好": "hello", "世界": "world",
    "发布": "released", "宣布": "announced", "表示": "said",
    "上涨": "rose", "下跌": "fell",
    # punctuation
    "？": "?", "。": ".", "，": ",", "！": "!", "：": ":", "；": ";",
    "（": "(", "）": ")", "、": ",",
}

app = FastAPI(title=NAME)


def looks_chinese(text: str) -> bool:
    return any("\u4e00" <= ch <= "\u9fff" for ch in text)


def translate(text: str) -> str:
    out: list[str] = []
    i = 0
    while i < len(text):
        matched = False
        for span in (4, 3, 2, 1):
            chunk = text[i : i + span]
            if chunk in TABLE:
                out.append(TABLE[chunk])
                i += span
                matched = True
                break
        if not matched:
            ch = text[i]
            out.append(ch if ch.isascii() else "")
            i += 1
    cleaned = " ".join(s for s in " ".join(out).split() if s)
    return cleaned or "(empty translation)"


@app.get("/health")
def health():
    return {"ok": True, "agent": NAME}


@app.get("/meta")
def meta():
    return {"name": NAME, "version": "0.1.0", "skill": "translate", "lang": "zh→en"}


@app.post("/v1/quote")
async def do_quote(req: Request):
    body = await req.json() or {}
    return JSONResponse(make_quote(
        text=body.get("text") or "", skill="translate", agent=NAME,
        base_cost=PER_CALL, base_eta_ms=BASE_ETA_MS, style=QUOTE_STYLE,
    ))


@app.post("/v1/translate")
async def do_translate(
    req: Request,
    x_agent_did: Optional[str] = Header(default=None, convert_underscores=True),
):
    body = await req.json()
    text = (body or {}).get("text") or ""
    src = "zh" if looks_chinese(text) else "en"
    translated = translate(text) if src == "zh" else text
    print(f"[translate] caller={x_agent_did} src={src} text={text[:60]!r}", flush=True)
    return JSONResponse({"translated": translated, "lang": src, "agent": NAME})


def main() -> None:
    base_url = os.environ.get("ANET_BASE_URL", "http://127.0.0.1:14101")
    threading.Thread(
        target=lambda: register_until_ready(
            NAME, PORT,
            paths=["/v1/translate", "/v1/quote", "/health", "/meta"],
            tags=["translate", "zh-en", "content-intel"],
            description="Chinese→English rule-based translator",
            per_call=PER_CALL, base_url=base_url,
        ),
        daemon=True,
    ).start()
    uvicorn.run(app, host="127.0.0.1", port=PORT, log_level="warning")


if __name__ == "__main__":
    main()
