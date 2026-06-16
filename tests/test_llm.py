"""§4 optional AI: provider abstraction (offline, no network) + every feature's
dependency-free fallback, and the AI path exercised with a mocked provider."""
from __future__ import annotations

from pathlib import Path

import pytest

from app import ai, core, llm
from app.database import Database


def _seed(tmp: Path, title: str, text: str):
    item = core.LectureItem(title=title, url="u",
                            pub_date="Mon, 09 Mar 2026 02:13:40 GMT")
    core.write_outputs(item, [{"start": 0, "end": 5, "text": text}], text,
                       core.output_dir_for(tmp, item, "week"), ["txt", "json"], 30, {})


# -- provider abstraction ---------------------------------------------------

def test_complete_requires_a_provider():
    with pytest.raises(llm.LLMError):
        llm.complete("hi", config={"provider": "none"})


def test_config_roundtrip_and_enabled(tmp_path: Path):
    db = Database(tmp_path / "d.db")
    llm.set_config(db, 1, {"provider": "ollama", "temperature": 0.7})
    cfg = llm.get_config(db, 1)
    assert cfg["provider"] == "ollama" and cfg["temperature"] == 0.7
    assert cfg["model"] == llm.DEFAULT_MODELS["ollama"]   # default filled in
    assert llm.is_enabled(cfg) is True
    # cloud provider with no key is not enabled
    assert llm.is_enabled({"provider": "openai"}) is False


def test_detect_does_not_touch_network():
    d = llm.detect()
    assert "anthropic" in d["providers"] and "ollama" in d["providers"]


# -- features: extractive fallback + AI path --------------------------------

def test_summarize_extractive_then_ai(tmp_path: Path, monkeypatch):
    _seed(tmp_path, "Week1_Intro",
          "Networking is the study of connected systems. "
          "Protocols define rules. Layers separate concerns. Packets carry data.")
    off = ai.summarize(tmp_path, "course", config={"provider": "none"})
    assert off["generated"] == "extractive" and off["summary"]

    monkeypatch.setattr(llm, "complete", lambda *a, **k: "AI SUMMARY")
    on = ai.summarize(tmp_path, "course", config={"provider": "ollama", "model": "x"})
    assert on["generated"] == "ai" and "AI SUMMARY" in on["summary"]


def test_flashcards_ai_parses_json(tmp_path: Path, monkeypatch):
    _seed(tmp_path, "Week1_TCP", "TCP is a reliable transport protocol.")
    monkeypatch.setattr(llm, "complete",
                        lambda *a, **k: '[{"front":"What is TCP?","back":"reliable","type":"qa"}]')
    out = ai.generate_flashcards(tmp_path, config={"provider": "ollama", "model": "x"})
    assert out["generated"] == "ai"
    assert out["cards"][0]["front"] == "What is TCP?"


def test_quiz_extractive_produces_questions(tmp_path: Path):
    _seed(tmp_path, "Week2_Protocols",
          "TCP is a reliable transport protocol. UDP is a connectionless datagram protocol. "
          "HTTP is an application layer protocol. DNS is a naming system for hosts.")
    out = ai.generate_quiz(tmp_path, "course", config={"provider": "none"}, n=6)
    assert out["generated"] == "extractive"
    assert out["questions"]
    assert all("question" in q and "answer" in q for q in out["questions"])


def test_chat_extractive_with_citations(tmp_path: Path):
    _seed(tmp_path, "Week3_TCP",
          "The three-way handshake establishes a TCP connection between hosts.")
    out = ai.chat(tmp_path, "handshake", config={"provider": "none"})
    assert out["generated"] == "extractive"
    assert out["citations"] and out["confidence"] in ("low", "medium", "high")
    assert "handshake" in out["answer"].lower()


def test_chat_ai_path_includes_citations(tmp_path: Path, monkeypatch):
    _seed(tmp_path, "Week3_TCP",
          "The three-way handshake establishes a TCP connection between hosts.")
    monkeypatch.setattr(llm, "complete", lambda *a, **k: "It is a 3-step handshake [1].")
    out = ai.chat(tmp_path, "handshake", config={"provider": "ollama", "model": "x"})
    assert out["generated"] == "ai" and "[1]" in out["answer"]
    assert out["citations"]


def test_chat_empty_query():
    out = ai.chat(Path("."), "   ", config={"provider": "none"})
    assert out["answer"] == "" and out["citations"] == []
