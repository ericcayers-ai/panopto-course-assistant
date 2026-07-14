"""Tests for the renewal features: native-dialog routes, local-AI (Ollama)
status, exam cheat sheet, and the destructive 'remove course files' clear.

OUTPUT is pinned to a temp dir (via PANOPTO_OUTPUT) before importing app.main,
so these never touch the real ./transcripts.
"""
from __future__ import annotations

import importlib
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("PANOPTO_OUTPUT", str(tmp_path))
    import app.main as main
    main = importlib.reload(main)
    return TestClient(main.app), tmp_path


# ---------------------------------------------------------------------------
# core.clear_library - must remove content but preserve DB / secrets / backups
# ---------------------------------------------------------------------------

def test_clear_library_preserves_infrastructure(tmp_path: Path):
    from app import core
    # Content that must be removed.
    (tmp_path / "Week_01").mkdir()
    (tmp_path / "Week_01" / "lec.txt").write_text("transcript", encoding="utf-8")
    (tmp_path / "_docs").mkdir()
    (tmp_path / "_docs" / "slides.md").write_text("doc", encoding="utf-8")
    (tmp_path / "_notebooklm").mkdir()
    (tmp_path / "_notebooklm" / "course_pack.md").write_text("pack", encoding="utf-8")
    # Infrastructure that must survive.
    (tmp_path / "course_assistant.db").write_text("db", encoding="utf-8")
    (tmp_path / ".secrets.json").write_text("secret", encoding="utf-8")
    (tmp_path / ".secrets.key").write_text("key", encoding="utf-8")
    (tmp_path / "_backups").mkdir()
    (tmp_path / "_backups" / "b.zip").write_text("backup", encoding="utf-8")

    res = core.clear_library(tmp_path)

    assert res["files"] == 3                      # the three content files
    assert not (tmp_path / "Week_01").exists()
    assert not (tmp_path / "_docs").exists()
    assert not (tmp_path / "_notebooklm").exists()
    # Preserved:
    assert (tmp_path / "course_assistant.db").exists()
    assert (tmp_path / ".secrets.json").exists()
    assert (tmp_path / ".secrets.key").exists()
    assert (tmp_path / "_backups" / "b.zip").exists()


def test_clear_library_empty_is_safe(tmp_path: Path):
    from app import core
    res = core.clear_library(tmp_path / "does-not-exist")
    assert res == {"files": 0, "folders": 0}


def test_library_clear_endpoint(client):
    c, tmp_path = client
    (tmp_path / "Week_01").mkdir()
    (tmp_path / "Week_01" / "lec.txt").write_text("x", encoding="utf-8")
    r = c.post("/api/library/clear")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True and body["files"] >= 1
    assert not (tmp_path / "Week_01").exists()


# ---------------------------------------------------------------------------
# Native dialog routes - report availability without opening a real dialog
# ---------------------------------------------------------------------------

def test_pick_folder_reports_availability(client, monkeypatch):
    c, _ = client
    # Force the "no desktop dialog" path so the test never blocks on a real window.
    monkeypatch.setattr("app.nativeui.available", lambda: False)
    r = c.post("/api/pick-folder", json={"title": "x"})
    assert r.status_code == 200
    assert r.json() == {"path": None, "available": False}


def test_pick_save_reports_availability(client, monkeypatch):
    c, _ = client
    monkeypatch.setattr("app.nativeui.available", lambda: False)
    r = c.post("/api/pick-save", json={"title": "x", "default_name": "a.pdf", "ext": ".pdf"})
    assert r.status_code == 200
    assert r.json()["available"] is False


# ---------------------------------------------------------------------------
# Ollama status - never raises, reports a clean shape even when not installed
# ---------------------------------------------------------------------------

def test_ollama_status_shape(client):
    c, _ = client
    r = c.get("/api/ollama/status")
    assert r.status_code == 200
    body = r.json()
    for key in ("installed", "running", "host", "models", "install_url"):
        assert key in body
    assert isinstance(body["models"], list)


# ---------------------------------------------------------------------------
# Cheat sheet - works without LLM (extractive fallback); LLM preferred when configured
# ---------------------------------------------------------------------------

def test_cheatsheet_works_without_llm(client):
    c, _ = client
    r = c.post("/api/export/cheatsheet", json={"course": "X", "max_pages": 2})
    assert r.status_code == 200
    assert r.json().get("id")


def test_cheatsheet_render_respects_page_cap(tmp_path: Path):
    from app import cheatsheet
    md = "\n".join(f"## Topic {i}\n- point {i} with some text\n- another point {i}"
                   for i in range(150))
    out = tmp_path / "sheet.pdf"
    info = cheatsheet.render_pdf(md, out, title="Test", max_pages=2)
    assert out.exists()
    assert info["pages"] <= 2
    assert info["truncated"] is True


def _seed_cs(tmp_path: Path, title: str, text: str):
    from app import core
    it = core.LectureItem(title=title, url="u", duration=600)
    core.write_outputs(it, [{"start": 0, "end": 6, "text": text}], text,
                       core.output_dir_for(tmp_path, it, "week"), ["txt", "json"], 30,
                       {"course": "X"})


def test_cheatsheet_condense_is_per_lecture(tmp_path: Path, monkeypatch):
    # map-reduce: each lecture is summarised separately, not in one giant prompt
    from app import cheatsheet, llm
    _seed_cs(tmp_path, "Week1 Networking",
             "The transport layer provides reliable delivery between hosts. " * 10)
    calls = {"n": 0}

    def fake_complete(prompt, system="", config=None):
        calls["n"] += 1
        assert len(prompt) < 8000             # never the whole course in one prompt
        return "- Transport layer gives reliable delivery\n- Ports multiplex connections"

    monkeypatch.setattr(llm, "complete", fake_complete)
    md = cheatsheet.condense(tmp_path, "X", 2, {"provider": "ollama", "model": "m"})
    assert calls["n"] >= 1
    assert "## 1 · Week1 Networking" in md
    assert "Transport layer gives reliable delivery" in md


def test_cheatsheet_extractive_fallback_when_model_empty(tmp_path: Path, monkeypatch):
    # model returns nothing usable -> build falls back rather than yielding an empty sheet
    from pathlib import Path as _P
    from app import cheatsheet, llm
    _seed_cs(tmp_path, "Week1 Networking",
             "The transport layer provides reliable delivery between hosts. "
             "It uses port numbers to multiplex connections. Routing forwards packets. " * 6)
    monkeypatch.setattr(llm, "complete", lambda *a, **k: "")
    res = cheatsheet.build(tmp_path, course="X", max_pages=1,
                           config={"provider": "ollama", "model": "m"})
    assert res["generated"] == "extractive"
    assert _P(res["path"]).exists() and _P(res["path"]).stat().st_size > 0


def test_practice_exam_api_validation(client):
    c, _ = client
    r = c.post("/api/export/practice-exam", json={
        "course": "COMPX999", "n": 20, "difficulty": "bogus",
    })
    assert r.status_code == 400


def test_practice_exam_api_queues_job(client):
    c, tmp = client
    from app import practice_exam
    text = "TCP is reliable. UDP is fast. " * 40
    from app import core
    it = core.LectureItem(title="Lecture 1", url="u", duration=600)
    core.write_outputs(
        it, [{"start": 0, "end": 6, "text": text}], text,
        core.output_dir_for(tmp, it, "week01"), ["txt", "json"], 30,
        {"course": "COMPX999"},
    )
    r = c.post("/api/export/practice-exam", json={
        "course": "COMPX999", "n": 10, "types": ["mcq", "short"],
        "formats": ["md"],
    })
    assert r.status_code == 200
    assert r.json().get("id")


def test_flashcards_seed_review_items(client, monkeypatch):
    """Flashcard job should seed review_items for Study practice quiz."""
    import time
    from app import ai, llm, settings_store, study_planner
    c, tmp = client
    text = "TCP provides reliable delivery between hosts. " * 10
    from app import core
    it = core.LectureItem(title="Week1", url="u", duration=600)
    core.write_outputs(
        it, [{"start": 0, "end": 6, "text": text}], text,
        core.output_dir_for(tmp, it, "week01"), ["txt", "json"], 30,
        {"course": "COMPX999"},
    )
    import app.main as main
    db = main.context.db
    cid = db.create_course("Test", code="COMPX999")
    settings_store.set_active_course(db, cid)
    cards = [{"front": "What is TCP?", "back": "Transport protocol", "tags": []}]
    monkeypatch.setattr(llm, "is_enabled", lambda cfg: True)
    monkeypatch.setattr(ai, "generate_flashcards", lambda *a, **k: {
        "cards": cards, "generated": "extractive",
    })
    r = c.post("/api/flashcards/generate", json={
        "course": "COMPX999", "deck": "testdeck", "max_cards": 5,
    })
    assert r.status_code == 200
    job_id = r.json()["id"]
    for _ in range(50):
        j = c.get(f"/api/jobs/{job_id}").json()
        if j["status"] in ("done", "failed", "error"):
            break
        time.sleep(0.05)
    j = c.get(f"/api/jobs/{job_id}").json()
    assert j["status"] == "done"
    seeded = study_planner.due_reviews(db, course_id=cid)
    assert any("TCP" in row["front"] for row in seeded)
