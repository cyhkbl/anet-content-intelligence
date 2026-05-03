"""translate-en-zh-svc — English→Chinese translator (rule-based, no model deps).

Exposes POST /v1/translate-en-zh {text: "…"} → {translated, lang, agent}.

Uses a small longest-match dictionary lookup so the hackathon demo runs
offline. Complements translate-svc (zh→en) to give the pipeline both
directions.
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

NAME = os.environ.get("TRANSLATE_EN_ZH_NAME", "translate-en-zh-svc")
PORT = int(os.environ.get("TRANSLATE_EN_ZH_PORT", "7408"))
PER_CALL = int(os.environ.get("TRANSLATE_EN_ZH_PER_CALL", "5"))
BASE_ETA_MS = int(os.environ.get("TRANSLATE_EN_ZH_BASE_ETA_MS", "45"))
QUOTE_STYLE = os.environ.get("TRANSLATE_EN_ZH_QUOTE_STYLE", "balanced")

# Reverse of translate.py's TABLE, with a few extras tuned for headlines.
TABLE_PHRASES = [
    ("artificial intelligence", "人工智能"),
    ("stock market", "股市"),
    ("new york", "纽约"),
    ("san francisco", "旧金山"),
    ("breakthrough for", "突破"),
    ("in one sentence", "用一句话"),
    ("give me", "给我"),
    ("how is", "怎么样"),
    ("very good", "很好"),
]
TABLE_WORDS = {
    # places
    "shanghai": "上海", "beijing": "北京", "shenzhen": "深圳",
    "guangzhou": "广州", "hangzhou": "杭州", "china": "中国",
    "usa": "美国", "america": "美国", "london": "伦敦", "tokyo": "东京",
    "berlin": "柏林", "paris": "巴黎",
    # time
    "tomorrow": "明天", "today": "今天", "yesterday": "昨天",
    "now": "现在", "year": "年", "month": "月", "day": "日",
    # weather
    "weather": "天气", "good": "好", "bad": "不好",
    "hot": "热", "cold": "冷", "sunny": "晴", "rain": "雨", "snow": "雪",
    # business / tech
    "company": "公司", "market": "市场", "investment": "投资",
    "stock": "股票", "technology": "科技", "data": "数据",
    "model": "模型", "product": "产品", "service": "服务",
    "customer": "客户", "ceo": "首席执行官", "shares": "股价",
    "growth": "增长", "launch": "发布", "profit": "利润",
    # actions / misc
    "please": "请", "summarise": "总结", "summarize": "总结",
    "hello": "你好", "world": "世界", "released": "发布", "announced": "宣布",
    "said": "表示", "rose": "上涨", "fell": "下跌", "announce": "宣布",
    "news": "新闻", "report": "报告", "agent": "智能体", "network": "网络",
}

app = FastAPI(title=NAME)


def looks_english(text: str) -> bool:
    return any("a" <= ch.lower() <= "z" for ch in text)


def translate(text: str) -> str:
    lowered = text.lower()
    # Phase 1: longest phrase substitutions first.
    for phrase, zh in sorted(TABLE_PHRASES, key=lambda p: -len(p[0])):
        lowered = lowered.replace(phrase, f" {zh} ")
    # Phase 2: token-by-token word lookup preserving punctuation.
    out: list[str] = []
    token = ""
    for ch in lowered + " ":
        if ch.isalpha() or ch == "-":
            token += ch
        else:
            if token:
                if token in TABLE_WORDS:
                    out.append(TABLE_WORDS[token])
                elif all(ord(c) > 127 for c in token):
                    out.append(token)
                else:
                    # unknown english word → drop
                    pass
                token = ""
            if ch.strip():
                # map some common punctuation
                punct = {",": "，", ".": "。", "?": "？", "!": "！",
                         ":": "：", ";": "；"}
                out.append(punct.get(ch, ch))
            else:
                out.append(" ")
    joined = "".join(out)
    # collapse spaces
    return " ".join(joined.split()) or "(empty translation)"


@app.get("/health")
def health():
    return {"ok": True, "agent": NAME}


@app.get("/meta")
def meta():
    return {"name": NAME, "version": "0.1.0", "skill": "translate-en-zh", "lang": "en→zh"}


@app.post("/v1/quote")
async def do_quote(req: Request):
    body = await req.json() or {}
    return JSONResponse(make_quote(
        text=body.get("text") or "", skill="translate-en-zh", agent=NAME,
        base_cost=PER_CALL, base_eta_ms=BASE_ETA_MS, style=QUOTE_STYLE,
    ))


@app.post("/v1/translate-en-zh")
async def do_translate(
    req: Request,
    x_agent_did: Optional[str] = Header(default=None, convert_underscores=True),
):
    body = await req.json()
    text = (body or {}).get("text") or ""
    translated = translate(text) if looks_english(text) else text
    print(
        f"[translate-en-zh] caller={x_agent_did} text={text[:60]!r}",
        flush=True,
    )
    return JSONResponse({"translated": translated, "lang": "en", "agent": NAME})


def main() -> None:
    base_url = os.environ.get("ANET_BASE_URL", "http://127.0.0.1:14108")
    threading.Thread(
        target=lambda: register_until_ready(
            NAME, PORT,
            paths=["/v1/translate-en-zh", "/v1/quote", "/health", "/meta"],
            tags=["translate-en-zh", "en-zh", "content-intel"],
            description="English→Chinese rule-based translator",
            per_call=PER_CALL, base_url=base_url,
        ),
        daemon=True,
    ).start()
    uvicorn.run(app, host=os.environ.get("LISTEN_HOST","0.0.0.0"), port=PORT, log_level="warning")


if __name__ == "__main__":
    main()
