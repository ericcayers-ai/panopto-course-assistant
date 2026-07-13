"""Semester planner: paper outlines, schedule import, exports, announcements."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from app import database, paper_outlines, schedule_parser, task_schedule
from app.moodle_content import _extract_posts, discover_announcement_urls

FIX = Path(__file__).resolve().parent / "fixtures"


@pytest.fixture()
def db(tmp_path: Path):
    d = database.Database(tmp_path / "t.db")
    d.create_course("Tri B", code="TRI-B", semester="B", year=2026)
    yield d
    d.close()


def test_search_papers_from_fixture():
    raw = (FIX / "paper_pages" / "compx202.html").read_text(encoding="utf-8")
    parsed = paper_outlines.parse_waikato_page(raw)
    assert parsed["code"] == "COMPX202"
    assert "Mobile Computing" in parsed["title"]
    assert any("COMPX202-26B (HAM)" in i["code"] for i in parsed["instances"])
    assert parsed["outline_urls"]


def test_parse_outline_html_compx202():
    raw = (FIX / "paper_outlines" / "compx202-26b-ham.html").read_text(encoding="utf-8")
    out = paper_outlines.parse_outline_html(raw, paper_code="COMPX202-26B (HAM)")
    assert out["paper_code"] == "COMPX202-26B (HAM)"
    assert len(out["assessments"]) == 5
    names = {a["name"] for a in out["assessments"]}
    assert "Assignment One" in names
    assert out["assessments"][0]["weight"] == 15.0
    assert len(out["learning_outcomes"]) >= 2
    assert len(out["weekly_topics"]) >= 2
    assert out["staff"][0]["email"].endswith("@waikato.ac.nz")


def test_parse_outline_multiple_papers():
    for fname, n_assess in (
        ("csmax270-26b-ham.html", 5),
        ("compx225-26b-ham.html", 6),
    ):
        raw = (FIX / "paper_outlines" / fname).read_text(encoding="utf-8")
        out = paper_outlines.parse_outline_html(raw)
        assert len(out["assessments"]) == n_assess


def test_parse_notion_schedule_zip():
    zip_path = FIX / "schedule_zip" / (
        "ExportBlock-900a42fe-ad20-4c18-bc98-60b0fd695436-Part-1.zip"
    )
    if not zip_path.exists():
        # outer export may already be extracted
        csv = FIX / "schedule_zip" / (
            "2026 Class Schedule - Tri B 3804904e8e20808b85fee41fcedad6c4.csv"
        )
        parsed = schedule_parser.parse_notion_csv(csv.read_text(encoding="utf-8-sig"))
        assert len(parsed) >= 30
        assert any(t["subject"] == "COMPX202" for t in parsed)
        return
    parsed = schedule_parser.parse_notion_zip(zip_path)
    assert parsed["task_count"] >= 30
    subjects = set(parsed["subjects"])
    assert "COMPX202" in subjects
    assert "COMPX225" in subjects
    a1 = next(t for t in parsed["tasks"] if t["name"] == "Assignment One")
    assert a1["due_date"] == "2026-08-14"
    assert a1["weight"] == 15.0


def test_merge_schedule_tasks():
    outline = paper_outlines.parse_outline_html(
        (FIX / "paper_outlines" / "compx202-26b-ham.html").read_text(encoding="utf-8"),
        paper_code="COMPX202-26B (HAM)",
    )
    csv = FIX / "schedule_zip" / (
        "2026 Class Schedule - Tri B 3804904e8e20808b85fee41fcedad6c4.csv"
    )
    sched = schedule_parser.parse_notion_csv(csv.read_text(encoding="utf-8-sig"))
    merged = task_schedule.merge_tasks(
        outline_tasks=task_schedule.outline_to_tasks(outline),
        schedule_tasks=sched,
        paper_codes=["COMPX202"],
    )
    assert any(t["name"] == "Assignment One" for t in merged)
    assert all(t["subject"] in ("", "COMPX202") or "COMPX202" in t.get("paper_code", "")
               for t in merged if t.get("subject"))


def test_export_formats(tmp_path: Path):
    tasks = [
        {"id": "compx202-assignment-one", "subject": "COMPX202", "name": "Assignment One",
         "type": "Assignment", "due_date": "2026-08-14", "weight": 15,
         "status": "not_started", "priority": "", "source": "schedule", "tags": ["COMPX202"]},
    ]
    csv_text = task_schedule.export_notion_csv(tasks)
    assert "Assignment One" in csv_text
    assert "COMPX202" in csv_text
    dest = tmp_path / "obsidian.zip"
    task_schedule.export_obsidian_zip(tasks, dest, title="Tri B plan")
    import zipfile
    with zipfile.ZipFile(dest) as zf:
        names = zf.namelist()
        assert any(n.endswith("index.md") for n in names)
        assert any("/tasks/" in n for n in names)
        index = zf.read([n for n in names if n.endswith("index.md")][0]).decode()
        assert "[[tasks/compx202-assignment-one]]" in index


def test_export_calendar_ics(tmp_path: Path):
    outline = paper_outlines.parse_outline_html(
        (FIX / "paper_outlines" / "compx202-26b-ham.html").read_text(encoding="utf-8"),
        paper_code="COMPX202-26B (HAM)",
    )
    outline["outline_url"] = "https://paperoutlines.waikato.ac.nz/outline/COMPX202-26B%20(HAM)"
    csv = FIX / "schedule_zip" / (
        "2026 Class Schedule - Tri B 3804904e8e20808b85fee41fcedad6c4.csv"
    )
    sched = schedule_parser.parse_notion_csv(csv.read_text(encoding="utf-8-sig"))
    tasks = task_schedule.merge_tasks(
        outline_tasks=task_schedule.outline_to_tasks(outline),
        schedule_tasks=sched,
        paper_codes=["COMPX202"],
    )
    ics = task_schedule.export_calendar_ics(tasks, outlines=[outline], title="Tri B")
    assert "BEGIN:VCALENDAR" in ics and "END:VCALENDAR" in ics
    assert "BEGIN:VEVENT" in ics
    assert "COMPX202: Assignment One" in ics
    assert "CATEGORIES:COMPX202,assessment" in ics
    assert "X-COURSE-ASSISTANT-EVENT-TYPE:assessment" in ics
    assert "X-APPLE-CALENDAR-COLOR:#BE0403" in ics
    assert "DTSTART;VALUE=DATE:20260814" in ics
    assert "Weight: 15%" in ics
    assert "Teaching period" in ics or "teaching" in ics.lower()
    assert "BEGIN:VALARM" in ics

    events = task_schedule.calendar_events_from_plan(tasks, outlines=[outline])
    types = {e["event_type"] for e in events}
    assert "assessment" in types
    assert "exam" in types
    assert "teaching" in types

    gcal = task_schedule.export_google_calendar_csv(tasks, outlines=[outline])
    assert "Subject,Start Date" in gcal
    assert "Assignment One" in gcal or "COMPX202: Assignment One" in gcal


def test_normalize_event_type():
    assert task_schedule.normalize_event_type("Lab") == "lab"
    assert task_schedule.normalize_event_type("Written Test") == "exam"
    assert task_schedule.normalize_event_type("Assignment") == "assessment"
    assert task_schedule.normalize_event_type("Quiz 1") == "quiz"


def test_build_schedule_persists(db):
    outline = paper_outlines.parse_outline_html(
        (FIX / "paper_outlines" / "compx202-26b-ham.html").read_text(encoding="utf-8"),
        paper_code="COMPX202-26B (HAM)",
    )
    db.upsert_paper_outline("COMPX202-26B (HAM)", json.dumps(outline))
    csv = FIX / "schedule_zip" / (
        "2026 Class Schedule - Tri B 3804904e8e20808b85fee41fcedad6c4.csv"
    )
    sched = schedule_parser.parse_notion_csv(csv.read_text(encoding="utf-8-sig"))
    sid = db.create_class_schedule(1, "Tri B", json.dumps({"tasks": sched}))
    plan = task_schedule.build_schedule(
        db, 1, paper_codes=["COMPX202"], class_schedule_id=sid,
    )
    assert plan["task_count"] >= 1
    assert plan["id"]


def test_moodle_announcement_parser():
    raw = """
    <article id="p123">
      <div class="subject">Welcome to CSMAX270</div>
      <div class="author">Dr Smith</div>
      <div class="time">Monday, 13 July 2026, 9:00 AM</div>
      <div class="content">Please read the paper outline before week 1.</div>
    </article>
    """
    posts = _extract_posts(raw)
    assert len(posts) == 1
    assert posts[0]["title"] == "Welcome to CSMAX270"
    assert "Dr Smith" in posts[0]["author"]


def test_api_endpoints(tmp_path: Path, monkeypatch):
    import os
    os.environ["PANOPTO_OUTPUT"] = str(tmp_path)
    import importlib
    import app.main as main
    importlib.reload(main)
    from fastapi.testclient import TestClient
    client = TestClient(main.app)
    cr = client.post("/api/courses", json={"name": "Tri B", "code": "TRI-B", "semester": "B", "year": 2026})
    cid = cr.json()["id"]
    client.post(f"/api/courses/{cid}/activate")

    raw = (FIX / "paper_pages" / "compx202.html").read_text(encoding="utf-8")

    def fake_get(url):
        if "waikato.ac.nz/study/papers" in url:
            return raw
        raise Exception("offline")

    monkeypatch.setattr(paper_outlines, "_default_get", fake_get)
    r = client.get("/api/semester/papers/search?q=COMPX202")
    assert r.status_code == 200
    assert r.json()["results"][0]["code"] == "COMPX202"

    html = (FIX / "paper_outlines" / "compx202-26b-ham.html").read_text(encoding="utf-8")
    r = client.post("/api/semester/papers/fetch", json={"paper_code": "COMPX202-26B (HAM)", "html": html})
    assert r.status_code == 200
    assert len(r.json()["assessments"]) == 5

    zip_path = FIX / "schedule_zip" / (
        "2026 Class Schedule - Tri B 3804904e8e20808b85fee41fcedad6c4.csv"
    )
    # import via CSV wrapped as minimal zip-less path: use parse + direct DB in build test;
    # for API, upload the part zip if present else skip upload test
    part = FIX / "schedule_zip" / "ExportBlock-900a42fe-ad20-4c18-bc98-60b0fd695436-Part-1.zip"
    if part.exists():
        with part.open("rb") as fh:
            r = client.post("/api/semester/schedule/import", files={"file": ("sched.zip", fh, "application/zip")})
        assert r.status_code == 200
        sched_id = r.json()["id"]
        r = client.post("/api/semester/plan/build", json={
            "paper_codes": ["COMPX202"],
            "class_schedule_id": sched_id,
            "course_id": cid,
        })
        assert r.status_code == 200
        plan_id = r.json()["id"]
        r = client.get(f"/api/semester/plans/{plan_id}/export/notion.csv")
        assert r.status_code == 200
        assert "Assignment One" in r.text
        r = client.get(f"/api/semester/plans/{plan_id}/export/calendar.ics")
        assert r.status_code == 200
        assert "BEGIN:VCALENDAR" in r.text
        assert "COMPX202" in r.text
        assert "CATEGORIES:" in r.text

def test_parse_outer_notion_export_zip():
    """Notion sometimes wraps the Part-1 zip in an outer export archive."""
    outer = Path(r"C:\Users\ericc\Downloads\2dde6cf1-aca9-48f4-8390-ecab83df9ef2_ExportBlock-900a42fe-ad20-4c18-bc98-60b0fd695436.zip")
    if not outer.exists():
        pytest.skip("user Notion export zip not on disk")
    parsed = schedule_parser.parse_notion_zip(outer.read_bytes())
    assert parsed["task_count"] >= 10


def test_merge_tasks_accepts_full_paper_codes():
    csv = FIX / "schedule_zip" / (
        "2026 Class Schedule - Tri B 3804904e8e20808b85fee41fcedad6c4.csv"
    )
    sched = schedule_parser.parse_notion_csv(csv.read_text(encoding="utf-8-sig"))
    merged = task_schedule.merge_tasks(
        outline_tasks=[],
        schedule_tasks=sched,
        paper_codes=["COMPX202-26B", "COMPX225-26B"],
    )
    subjects = {t["subject"] for t in merged}
    assert "COMPX202" in subjects or "COMPX225" in subjects
