"""Flashcard quality + LLM-path robustness (the 'incoherent mess' regression).

Covers: the extractive heuristic rejecting sentence-fragment subjects, tolerant
JSON parsing of small-model output, Ollama output-token budgeting, and the
flashcard generator scaling max_tokens to the requested deck size.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from app import ai, core, flashcards, llm


# -- extractive heuristic quality -------------------------------------------

def test_extract_cards_rejects_fragment_subjects():
    text = (
        "And so the idea is like we send a request to the web server. "
        "But it is more about a personalized experience. "
        "If you are thinking about IP addresses you encode them. "
        "Like these three are kind of like reserved bits. "
        "TCP is a reliable transport protocol that guarantees ordered delivery. "
        "A deadlock is a situation where two processes wait on each other forever.")
    fronts = [c["front"] for c in flashcards.extract_cards(text, ["t"], max_cards=20)]
    # the good definitions survive
    assert "What is TCP?" in fronts
    assert any("deadlock" in f.lower() for f in fronts)
    # none of the conversational fragments become cards
    bad = ("nd so", "and so", "but it", "if you", "these three", "like ")
    assert not any(any(b in f.lower() for b in bad) for f in fronts), fronts


def test_extract_cards_collapses_stutter():
    # "invalid invalid bit" (a transcription stutter) must not appear doubled
    cards = flashcards.extract_cards(
        "An invalid invalid bit is stored in the page table entry.", ["t"])
    assert all("invalid invalid" not in c["front"] for c in cards)


# -- tolerant JSON parsing --------------------------------------------------

def test_parse_json_array_strips_code_fences():
    raw = '```json\n[{"front":"Q1","back":"A1"},{"front":"Q2","back":"A2"}]\n```'
    assert len(ai._parse_json_array(raw)) == 2


def test_parse_json_array_recovers_truncated_output():
    # model hit its token cap mid-third-object: the two complete cards survive
    raw = '[{"front":"Q1","back":"A1"}, {"front":"Q2","back":"A2"}, {"front":"Q3","ba'
    cards = ai._parse_json_array(raw)
    assert [c["front"] for c in cards] == ["Q1", "Q2"]


def test_parse_json_array_handles_preamble_and_jsonl():
    raw = 'Sure! Here are your cards:\n{"front":"Q1","back":"A1"}\n{"front":"Q2","back":"A2"}'
    assert len(ai._parse_json_array(raw)) == 2


def test_parse_json_array_quiz_key():
    raw = '[{"question":"What is X?","answer":"Y","options":["Y","Z"]}]'
    qs = ai._parse_json_array(raw, key="question")
    assert len(qs) == 1 and qs[0]["answer"] == "Y"


def test_parse_json_array_normalizes_alternative_keys():
    # the real llama3.2:3b failure: it returned question/answer, not front/back
    raw = '[{"question":"What is TCP?","answer":"A transport protocol"}]'
    cards = ai._parse_json_array(raw)             # default key="front"
    assert len(cards) == 1
    assert cards[0]["front"] == "What is TCP?"
    assert cards[0]["back"] == "A transport protocol"
    # term/definition shape too
    raw2 = '[{"term":"Latency","definition":"the delay before transfer"}]'
    assert ai._parse_json_array(raw2)[0]["front"] == "Latency"


def test_parse_json_array_unwraps_object_wrapper():
    # model wrapped the array in an object: {"flashcards":[...]}
    raw = '{"flashcards":[{"front":"Q1","back":"A1"},{"front":"Q2","back":"A2"}]}'
    assert len(ai._parse_json_array(raw)) == 2


# -- Ollama output-token budget ---------------------------------------------

def test_ollama_sets_num_predict(monkeypatch):
    captured = {}

    def fake_http(url, payload, headers, timeout=120):
        captured["payload"] = payload
        return {"response": "ok"}

    monkeypatch.setattr(llm, "_http_json", fake_http)
    llm._complete_ollama("p", "s", {"model": "m", "max_tokens": 3000, "temperature": 0.2})
    assert captured["payload"]["options"]["num_predict"] == 3000
    assert "format" not in captured["payload"]                # not requested here


def test_ollama_json_format_passthrough(monkeypatch):
    captured = {}

    def fake_http(url, payload, headers, timeout=120):
        captured["payload"] = payload
        return {"response": "[]"}

    monkeypatch.setattr(llm, "_http_json", fake_http)
    llm._complete_ollama("p", "s", {"model": "m", "format": "json"})
    assert captured["payload"]["format"] == "json"


# -- generator scales token budget to deck size -----------------------------

_LONG = ("The transport layer provides reliable delivery of data between hosts. "
         "It uses port numbers to multiplex connections. The network layer routes "
         "packets across multiple links using addresses. ") * 6   # > 200 chars -> a chunk


def _seed_long(tmp_path: Path):
    it = core.LectureItem(title="Week1 Intro", url="u", duration=600)
    core.write_outputs(it, [{"start": 0, "end": 6, "text": _LONG}], _LONG,
                       core.output_dir_for(tmp_path, it, "week"), ["txt", "json"], 30,
                       {"course": "C"})


def test_generate_flashcards_scales_tokens(tmp_path: Path, monkeypatch):
    _seed_long(tmp_path)
    captured = {}

    def fake_complete(prompt, system="", config=None):
        captured["config"] = config
        return '[{"front":"What is the transport layer?","back":"It delivers data","type":"qa"}]'

    monkeypatch.setattr(ai.llm, "is_enabled", lambda cfg: True)
    monkeypatch.setattr(ai.llm, "complete", fake_complete)
    out = ai.generate_flashcards(tmp_path, course="C", max_cards=50,
                                 config={"provider": "ollama", "model": "m", "max_tokens": 1024})
    assert out["generated"] == "ai"
    assert captured["config"]["format"] == "json"          # JSON mode requested
    assert captured["config"]["max_tokens"] > 1024         # scaled up for the deck


def test_generate_flashcards_reports_unparsable_reason(tmp_path: Path, monkeypatch):
    _seed_long(tmp_path)
    monkeypatch.setattr(ai.llm, "is_enabled", lambda cfg: True)
    monkeypatch.setattr(ai.llm, "complete", lambda *a, **k: "sorry, I cannot do that")
    out = ai.generate_flashcards(tmp_path, course="C", max_cards=10,
                                 config={"provider": "ollama", "model": "m"})
    assert out["generated"] == "extractive"
    assert "larger model" in out["reason"].lower()          # specific, not "unavailable"


def test_generate_flashcards_reports_no_model_reason(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(ai.llm, "is_enabled", lambda cfg: False)
    out = ai.generate_flashcards(tmp_path, course="C", config={"provider": "none"})
    assert out["generated"] == "extractive"
    assert "configured" in out["reason"].lower()
