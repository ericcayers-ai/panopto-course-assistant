"""API-layer tests for the v3 study toolkit routes (streak, next-up, glossary,
keywords, workload, study guide, citations, practice quiz, notes, tags).

PANOPTO_OUTPUT is pinned to a temp dir before importing app.main, as in test_api.
"""
from __future__ import annotations

import importlib
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def app_ctx(tmp_path, monkeypatch):
    monkeypatch.setenv("PANOPTO_OUTPUT", str(tmp_path))
    import app.main as main
    main = importlib.reload(main)
    return TestClient(main.app), main, tmp_path


def _seed(tmp_path: Path):
    from app import core
    for title, sents in [
        ("Week1 Networking", [
            "TCP is a reliable transport protocol that guarantees delivery.",
            "Latency refers to the delay before a transfer begins.",
            "A router forwards packets between networks."]),
        ("Week2 Routing", [
            "Bandwidth means the maximum data transfer rate of a link.",
            "A routing table stores the paths used to forward packets."]),
    ]:
        it = core.LectureItem(title=title, url="u", duration=600,
                              pub_date="Mon, 09 Mar 2026 02:13:40 GMT")
        segs = [{"start": i * 6, "end": i * 6 + 6, "text": s}
                for i, s in enumerate(sents)]
        core.write_outputs(it, segs, " ".join(sents),
                           core.output_dir_for(tmp_path, it, "week"),
                           ["txt", "json", "md"], 30, {"course": "CS234"})


def _course(c: TestClient) -> int:
    cid = c.post("/api/courses", json={"name": "CS234"}).json()["id"]
    c.post(f"/api/courses/{cid}/activate")
    return cid


def test_streak_and_study_session(app_ctx):
    c, main, _ = app_ctx
    _course(c)
    r = c.get("/api/streak")
    assert r.status_code == 200
    assert r.json()["current_streak"] == 0
    c.post("/api/study-sessions", json={"duration": 35})
    s = c.get("/api/streak?goal=30").json()
    assert s["today_minutes"] == 35.0 and s["goal_met"] is True
    assert s["current_streak"] == 1


def test_next_up(app_ctx):
    c, main, tmp = app_ctx
    _seed(tmp)
    cid = _course(c)
    main.db.create_assessment(cid, "Exam", due_date="2000-01-01", status="not_started")
    actions = c.get("/api/next-up").json()["actions"]
    kinds = [a["kind"] for a in actions]
    assert "assessment" in kinds and "summarize" in kinds


def test_glossary_and_export(app_ctx):
    c, main, tmp = app_ctx
    _seed(tmp)
    _course(c)
    g = c.get("/api/glossary").json()
    names = {t["term"].lower() for t in g["terms"]}
    assert "tcp" in names
    exp = c.post("/api/export/glossary", json={}).json()
    assert (tmp / exp["markdown"]).is_file()


def test_keywords_and_workload(app_ctx):
    c, main, tmp = app_ctx
    _seed(tmp)
    kw = c.get("/api/keywords").json()
    assert kw["keywords"] and "term" in kw["keywords"][0]
    wl = c.get("/api/workload").json()
    assert wl["lectures"] == 2 and wl["total_words"] > 0


def test_study_guide_and_export(app_ctx):
    c, main, tmp = app_ctx
    _seed(tmp)
    _course(c)
    guide = c.get("/api/study-guide").json()
    assert "# Study Guide" in guide["markdown"] and guide["lectures"] == 2
    exp = c.post("/api/export/study-guide", json={}).json()
    assert (tmp / exp["path"]).is_file()


def test_citations(app_ctx):
    c, main, tmp = app_ctx
    _seed(tmp)
    groups = c.get("/api/transcripts").json()["items"]
    path = next(iter(groups[0]["formats"].values()))
    r = c.get("/api/citations", params={"path": path})
    assert r.status_code == 200
    cits = r.json()["citations"]
    assert "apa" in cits and "bibtex" in cits
    assert c.get("/api/citations", params={"path": "nope.txt"}).status_code == 404


def test_practice_quiz_flow(app_ctx):
    c, main, _ = app_ctx
    cid = _course(c)
    for f, b in [("Q1", "A1"), ("Q2", "A2"), ("Q3", "A3"), ("Q4", "A4")]:
        main.db.add_review_item(cid, f, b, due="2026-06-10T00:00:00+00:00")
    quiz = c.get("/api/practice-quiz", params={"count": 4, "seed": 3}).json()
    assert quiz["count"] == 4
    answers = [q["answer_index"] for q in quiz["questions"]]
    graded = c.post("/api/practice-quiz/grade",
                    json={"questions": quiz["questions"], "answers": answers}).json()
    assert graded["score"] == 4 and graded["pct"] == 100
    # the attempt was recorded
    assert main.db.list_quiz_attempts(cid)


def test_notes_crud_api(app_ctx):
    c, main, _ = app_ctx
    _course(c)
    created = c.post("/api/notes", json={"path": "week-1/lec.txt", "body": "idea",
                                         "timestamp_s": 12.0, "bookmark": True}).json()
    nid = created["id"]
    assert created["bookmark"] is True and created["timestamp_s"] == 12.0
    listed = c.get("/api/notes", params={"path": "week-1/lec.txt"}).json()["notes"]
    assert len(listed) == 1 and listed[0]["body"] == "idea"
    assert c.patch(f"/api/notes/{nid}", json={"body": "edited"}).status_code == 200
    assert c.get("/api/notes", params={"path": "week-1/lec.txt"}).json()["notes"][0]["body"] == "edited"
    assert c.delete(f"/api/notes/{nid}").status_code == 200
    assert c.get("/api/notes", params={"path": "week-1/lec.txt"}).json()["notes"] == []
    assert c.delete(f"/api/notes/{nid}").status_code == 404


def test_tags_api(app_ctx):
    c, main, _ = app_ctx
    _course(c)
    c.post("/api/tags", json={"path": "week-1/lec.txt", "name": "Exam"})
    out = c.post("/api/tags", json={"path": "week-1/lec.txt", "name": "Important"}).json()
    assert set(out["tags"]) == {"Exam", "Important"}
    assert c.get("/api/tags", params={"path": "week-1/lec.txt"}).json()["tags"]
    all_tags = {t["name"]: t["count"] for t in c.get("/api/tags").json()["tags"]}
    assert all_tags["Exam"] == 1
    rem = c.delete("/api/tags", params={"path": "week-1/lec.txt", "name": "Exam"}).json()
    assert rem["tags"] == ["Important"]
    assert c.post("/api/tags", json={"path": "p", "name": "  "}).status_code == 400
