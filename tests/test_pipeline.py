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
