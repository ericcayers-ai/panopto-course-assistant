"""Study workspace features: notes folders/sets, essay grader, study modes, tracker."""
from __future__ import annotations

import importlib
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import essay_grader, notes_workspace, study_modes
from app.database import Database, SCHEMA_VERSION


@pytest.fixture()
def db(tmp_path: Path) -> Database:
    return Database(tmp_path / "course_assistant.db")


@pytest.fixture()
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("PANOPTO_OUTPUT", str(tmp_path))
    import app.main as main
    importlib.reload(main)
    return TestClient(main.app), main, tmp_path


def test_schema_version_includes_notes_workspace(db: Database):
    assert db.schema_version() == SCHEMA_VERSION >= 7
    db.create_note_folder(None, "Week 3")
    assert db.list_note_folders()


def test_notes_folder_session_and_flashcard_set(db: Database):
    cid = db.create_course("BIOL 101", code="BIOL101")
    fid = db.create_note_folder(cid, "Week 3")
    nid = db.add_note(
        "week-3/lec.txt",
        "Signal transduction is the process by which a chemical signal is transmitted.\n"
        "A receptor is a protein that binds a ligand.\n"
        "Q: What is a second messenger?\nA: A molecule that relays signals inside a cell.\n",
        course_id=cid, title="Cell signalling", folder_id=fid, session_type="lecture",
    )
    note = db.get_note(nid)
    assert note["title"] == "Cell signalling"
    assert note["session_type"] == "lecture"
    assert note["folder_id"] == fid

    out = notes_workspace.create_set_from_note(db, nid, name="W3 cards")
    assert out["seeded"] >= 1
    assert out["set"]["name"] == "W3 cards"
    assert db.list_flashcard_sets(cid)


def test_import_text_file_as_note(db: Database, tmp_path: Path):
    cid = db.create_course("PHIL 105")
    src = tmp_path / "essay_notes.md"
    src.write_text("# Draft notes\n\nEthics is the study of moral principles.\n", encoding="utf-8")
    note = notes_workspace.import_file_as_note(
        db, src, course_id=cid, session_type="tutorial", title="Ethics tutorial")
    assert note["title"] == "Ethics tutorial"
    assert note["session_type"] == "tutorial"
    assert "Ethics" in note["body"]


def test_essay_grader_extractive():
    essay = (
        "This essay argues that clear thesis statements improve academic writing. "
        "First, a thesis guides the reader. Second, evidence must support each claim "
        "with examples drawn from the course readings. Finally, a conclusion restates "
        "the argument without introducing new material. " * 8
    )
    rubric = (
        "1. Thesis clarity\n"
        "2. Evidence and examples\n"
        "3. Structure and signposting\n"
        "4. Conclusion\n"
    )
    result = essay_grader.grade_essay(essay, rubric, title="Draft 2", save=False)
    assert 0 <= result["score"] <= 100
    assert 0 <= result["originality"] <= 100
    assert result["generated"] == "extractive"
    assert result["rubric"]
    assert result["strengths"]
    assert result["improvements"]


def test_study_modes_recall_slideshow_focus_tracker(db: Database):
    cid = db.create_course("STATS 108")
    db.add_review_item(cid, front="What is variance?", back="Spread of a distribution",
                       due="2099-01-01")
    db.add_review_item(cid, front="Define mean", back="Average of values",
                       due="2020-01-01")
    recall = study_modes.daily_recall(db, cid)
    assert recall["count"] >= 1
    assert recall["items"][0]["front"] == "Define mean"

    show = study_modes.slideshow(db, cid, limit=10)
    assert show["mode"] == "slideshow"
    assert show["count"] >= 1

    ticket = study_modes.start_focus(db, cid, minutes=25)
    assert ticket["minutes"] == 25
    done = study_modes.complete_focus(db, cid, minutes=25, started_at=ticket["started_at"])
    assert done["duration"] == 25

    db.create_assessment(cid, "Quiz 4", "2026-07-20", kind="quiz", week=4)
    db.create_assessment(cid, "Final exam", "2026-08-01", kind="exam", week=12)
    snap = study_modes.tracker_snapshot(db, cid)
    assert len(snap["by_kind"]["quiz"]) == 1
    assert len(snap["by_kind"]["exam"]) == 1
    assert snap["weeks"]


def test_api_study_workspace_surface(client):
    c, main, tmp = client
    course = c.post("/api/courses", json={"name": "BIOL 101", "code": "BIOL101"}).json()
    cid = course["id"]

    folder = c.post("/api/note-folders", json={"name": "Week 3", "course_id": cid}).json()
    assert folder["name"] == "Week 3"

    note = c.post("/api/notes", json={
        "body": "A ligand is a signalling molecule that binds a receptor.\n"
                "Transduction means converting one signal form into another.\n"
                "Q: Name a second messenger\nA: cAMP\n",
        "title": "Cell signalling",
        "session_type": "lecture",
        "folder_id": folder["id"],
        "course_id": cid,
        "path": "week-3/cell.txt",
    }).json()
    assert note["session_type"] == "lecture"
    assert note["title"] == "Cell signalling"

    ws = c.get("/api/notes/workspace").json()
    assert ws["counts"]["notes"] >= 1
    assert ws["counts"]["folders"] >= 1

    cards = c.post("/api/flashcard-sets/from-note",
                   json={"note_id": note["id"], "name": "W3 set"}).json()
    assert cards["seeded"] >= 1

    # Clear folder via PATCH null
    assert c.patch(f"/api/notes/{note['id']}", json={"folder_id": None}).status_code == 200
    cleared = c.get("/api/notes", params={"course": cid}).json()["notes"]
    assert any(n["id"] == note["id"] and n["folder_id"] is None for n in cleared)

    recall = c.get("/api/study/daily-recall").json()
    assert recall["mode"] == "daily_recall"
    assert recall["count"] >= 1

    show = c.get("/api/study/slideshow").json()
    assert show["count"] >= 1

    focus = c.post("/api/study/focus/start", json={"minutes": 10}).json()
    assert focus["minutes"] == 10
    done = c.post("/api/study/focus/complete",
                  json={"minutes": 10, "started_at": focus["started_at"]}).json()
    assert done["duration"] == 10

    a = c.post("/api/assessments", json={
        "name": "Lab report", "due_date": "2026-07-18", "kind": "assignment", "week": 3,
    }).json()
    assert a["kind"] == "assignment" and a["week"] == 3
    tracker = c.get("/api/study/tracker").json()
    assert any(x["name"] == "Lab report" for x in tracker["assessments"])

    grade = c.post("/api/essay/grade", json={
        "title": "Draft 2",
        "rubric": "1. Thesis\n2. Evidence\n3. Structure",
        "essay": ("A clear thesis opens this draft. Evidence from the readings supports "
                  "each paragraph. Structure is signposted throughout. " * 12),
        "save": True,
    }).json()
    assert "score" in grade and "originality" in grade
    assert grade["generated"] in ("extractive", "ai")
    assert c.get("/api/essay/grades").json()["grades"]

    # Marketing routes must not exist
    for path in ("/welcome", "/pricing", "/contact"):
        assert c.get(path).status_code == 404


def test_static_tree_has_no_marketing_site():
    static = Path(__file__).resolve().parents[1] / "static"
    assert not (static / "marketing").exists()
    # Guard against accidental reintroduction of external product branding.
    forbidden = ("afterhours",)
    for path in static.rglob("*"):
        if not path.is_file():
            continue
        if path.suffix.lower() not in {".html", ".js", ".css", ".md"}:
            continue
        text = path.read_text(encoding="utf-8", errors="ignore").lower()
        for token in forbidden:
            assert token not in text, f"{token!r} found in {path}"
