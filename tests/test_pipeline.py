"""Smoke tests for the pure-function logic of each agent.

These don't spin up daemons or HTTP servers — they just exercise the
text-processing functions so we catch regressions before the slow
end-to-end run. Run with `pytest tests/`.
"""

from __future__ import annotations

import sys
from pathlib import Path

# Add agents/ to the path so each module's local imports resolve.
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "agents"))

from agents.translate import translate, looks_chinese
from agents.extract import extract
from agents.sentiment import classify as sentiment_classify
from agents.classify import classify as topic_classify
from agents.summarise import summarise, sentences
from agents.factcheck import factcheck
from agents.translate_en_zh import translate as translate_en_zh
from agents.keywords import score_keywords
from agents.orchestrator import decide_plan, has_numbers_or_dates


def test_translate_zh_passthrough_and_table():
    assert looks_chinese("上海") is True
    assert looks_chinese("hello") is False
    out = translate("上海明天天气怎么样？")
    assert "shanghai" in out
    assert "tomorrow" in out
    assert "weather" in out


def test_extract_finds_org_and_number():
    text = "OpenAI announced GPT-5 today; the stock rose 12% to $200."
    spans = extract(text)
    types = {s["type"] for s in spans}
    assert "ORG" in types
    assert "NUMBER" in types


def test_sentiment_distinguishes_polarity():
    pos = sentiment_classify("this is a great and amazing breakthrough")
    neg = sentiment_classify("this is a terrible and awful failure")
    neu = sentiment_classify("the report contains some words")
    assert pos[0] == "positive"
    assert neg[0] == "negative"
    assert neu[0] == "neutral"


def test_topic_classification_picks_dominant_topic():
    topic, conf, kws = topic_classify(
        "AI artificial intelligence model algorithm software"
    )
    assert topic == "technology"
    assert conf > 0
    assert kws


def test_topic_falls_back_to_other():
    topic, conf, _ = topic_classify("xyzzy plover frobnicate quux")
    assert topic == "other"


def test_summarise_takes_first_n_sentences():
    text = "First sentence. Second sentence. Third sentence."
    s2 = summarise(text, max_sentences=2)
    assert s2.startswith("First sentence")
    assert "Second" in s2
    assert "Third" not in s2


def test_summarise_caps_length():
    long_text = "word " * 200 + "."
    out = summarise(long_text, max_sentences=2, max_chars=120)
    assert len(out) <= 120


def test_sentences_splits_english_terminators():
    parts = sentences("First. Second! Third?")
    assert len(parts) == 3


# ── new agents ──────────────────────────────────────────────────────────

def test_factcheck_flags_absurd_percentage():
    out = factcheck("Revenue grew 9999% overnight.")
    assert any(c["status"] == "suspect" for c in out["claims"])
    assert out["verdict"] in ("flagged", "review")


def test_factcheck_clean_normal_text():
    out = factcheck("Revenue grew 12% in 2024.")
    statuses = {c["status"] for c in out["claims"]}
    assert "suspect" not in statuses
    assert out["verdict"] == "clean"


def test_factcheck_flags_misspelled_org():
    out = factcheck("OpenIA announced a new model.")
    assert any("misspelling" in c.get("reason", "") for c in out["claims"])


def test_translate_en_zh_basic_vocab():
    out = translate_en_zh("Shanghai weather is good today.")
    assert "上海" in out
    assert "好" in out or "天气" in out


def test_keywords_ranks_frequent_content_words():
    text = ("AI model AI model AI launch product launch customer "
            "data data data data model")
    kws = score_keywords(text, top_k=5)
    words = {k["word"] for k in kws}
    assert "data" in words
    assert all(k["score"] > 0 for k in kws)


def test_keywords_drops_stopwords():
    kws = score_keywords("the the the the is is is is model model", top_k=3)
    assert all(k["word"] != "the" for k in kws)
    assert any(k["word"] == "model" for k in kws)


# ── self-composing planner ──────────────────────────────────────────────

def test_plan_skips_translate_for_english_input():
    text = "OpenAI announced a new model."
    available = {
        "translate": {}, "extract": {}, "sentiment": {},
        "summarise": {}, "classify": {}, "keywords": {},
    }
    plan = decide_plan(text, available, "analyze")
    assert "translate" not in plan
    assert "extract" in plan and "classify" in plan


def test_plan_includes_translate_for_chinese_input():
    text = "上海明天天气怎么样？"
    available = {"translate": {}, "extract": {}, "summarise": {}, "classify": {}}
    plan = decide_plan(text, available, "analyze")
    assert plan[0] == "translate"


def test_plan_adds_factcheck_when_numbers_present():
    text = "Revenue grew 12% in 2024."
    available = {"extract": {}, "sentiment": {}, "factcheck": {}}
    plan = decide_plan(text, available, "analyze")
    assert "factcheck" in plan


def test_plan_skips_missing_skills():
    text = "Some text."
    available = {"extract": {}}  # only one skill on mesh
    plan = decide_plan(text, available, "analyze")
    assert plan == ["extract"]


def test_plan_honours_translate_to_zh_intent():
    text = "Hello world."
    available = {"extract": {}, "translate-en-zh": {}}
    plan = decide_plan(text, available, "translate-to-zh")
    assert plan[-1] == "translate-en-zh"


def test_has_numbers_or_dates_detector():
    assert has_numbers_or_dates("grew 12%")
    assert has_numbers_or_dates("in 2024")
    assert not has_numbers_or_dates("no digits here")
