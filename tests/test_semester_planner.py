"""Semester planner: paper outlines, schedule import, exports, announcements."""
from __future__ import annotations

import datetime as dt
import io
import json
import zipfile
from pathlib import Path

import pytest

from app import database, moodle_calendar, paper_outlines, schedule_parser, task_schedule
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
    with zipfile.ZipFile(dest) as zf:
        names = zf.namelist()
        assert any(n.endswith("README.md") for n in names)
        assert any("Master/Task Graphs/Semester Gantt.md" in n for n in names)
        assert any("Master/Study Plan2.md" in n for n in names)
        assert any("Master/Task Schedule/" in n for n in names)
        assert any("Courses/COMPX202/" in n for n in names)
        assert any("Guide/COMPX202 Gantt.md" in n for n in names)
        readme = zf.read([n for n in names if n.endswith("/README.md") and "Courses/" not in n][0]).decode()
        assert "Master/Study Plan2" in readme
        gantt = zf.read([n for n in names if "Semester Gantt.md" in n][0]).decode()
        assert "```mermaid" in gantt
        assert "gantt" in gantt
        assert "dateFormat YYYY-MM-DD" in gantt
        assert "section COMPX202" in gantt
        assert "2026-08-14" in gantt


def test_obsidian_export_preserves_complete_vault_layout(tmp_path: Path):
    tasks = [
        {"id": "compx202-assignment-one", "subject": "COMPX202", "name": "Assignment One",
         "type": "Assignment", "due_date": "2026-08-14", "weight": 15,
         "status": "not_started", "source": "schedule"},
    ]
    outlines = [
        {"paper_code": "COMPX202-26B (HAM)", "title": "Mobile Computing"},
        # An outline with no task must still receive paper and Guide notes.
        {"paper_code": "JAPAN332-26B (HAM)", "title": "Japanese Language 3"},
    ]
    moodle_events = [
        {"uid": "csmax275-lecture", "paper_code": "CSMAX275",
         "summary": "CSMAX275: Lecture", "event_type": "lecture",
         "start": dt.date(2026, 7, 20),
         "end": dt.date(2026, 7, 21)},
    ]
    announcements = [
        {"title": "Welcome", "body": "First post", "author": "Lecturer",
         "posted_at": "2026-07-13"},
    ]
    dest = tmp_path / "obsidian.zip"

    task_schedule.export_obsidian_zip(
        tasks, dest, title="Tri B plan", outlines=outlines,
        moodle_events=moodle_events, announcements=announcements,
    )

    root = "tri-b-plan"
    with zipfile.ZipFile(dest) as zf:
        names = set(zf.namelist())
        expected_folders = {
            f"{root}/Master/",
            f"{root}/Master/Task Schedule/",
            f"{root}/Master/Task Graphs/",
            f"{root}/Master/Calendar/",
            f"{root}/Courses/",
            f"{root}/Courses/COMPX202/Guide/",
            f"{root}/Courses/COMPX202/Lectures/",
            f"{root}/Courses/JAPAN332/Guide/",
            f"{root}/Courses/CSMAX275/Guide/",
        }
        expected_files = {
            f"{root}/README.md",
            f"{root}/IMPORT.md",
            f"{root}/Master/Study Plan2.md",
            f"{root}/Master/Task Graphs/Semester Gantt.md",
            f"{root}/Master/Task Graphs/Overview.canvas",
            f"{root}/Master/Task Schedule/Timetable Sheet - Markdown ver.md",
            f"{root}/Master/Calendar/semester.ics",
            f"{root}/.obsidian/app.json",
            f"{root}/Courses/COMPX202/README.md",
            f"{root}/Courses/COMPX202/COMPX202_Mindmap.canvas",
            f"{root}/Courses/JAPAN332/README.md",
            f"{root}/Courses/CSMAX275/README.md",
            f"{root}/Courses/COMPX202/Guide/COMPX202 Gantt.md",
            f"{root}/Master/Task Schedule/compx202-assignment-one.md",
        }
        assert expected_folders <= names
        assert expected_files <= names
        assert len([
            name for name in names
            if "/Master/Task Schedule/" in name and name.endswith(".md")
            and not name.endswith("Timetable Sheet - Markdown ver.md")
        ]) >= len(tasks)
        readme = zf.read(f"{root}/README.md").decode()
        assert "Courses/JAPAN332" in readme
        assert "Master/Study Plan2" in readme
        assert "IMPORT" in readme


def test_build_mermaid_gantt(tmp_path: Path):
    outline = paper_outlines.parse_outline_html(
        (FIX / "paper_outlines" / "compx202-26b-ham.html").read_text(encoding="utf-8"),
        paper_code="COMPX202-26B (HAM)",
    )
    csv = FIX / "schedule_zip" / (
        "2026 Class Schedule - Tri B 3804904e8e20808b85fee41fcedad6c4.csv"
    )
    sched = schedule_parser.parse_notion_csv(csv.read_text(encoding="utf-8-sig"))
    tasks = task_schedule.merge_tasks(
        outline_tasks=task_schedule.outline_to_tasks(outline),
        schedule_tasks=sched,
        paper_codes=["COMPX202"],
    )
    gantt = task_schedule.build_mermaid_gantt(tasks, outlines=[outline], title="Tri B 2026")
    assert "gantt" in gantt
    assert "dateFormat YYYY-MM-DD" in gantt
    assert "axisFormat %b %d" in gantt
    assert "section COMPX202" in gantt
    assert "2026-08-14" in gantt
    assert "crit" in gantt  # exam from outline key dates or written test


def test_parse_moodle_calendar_ics():
    raw = (FIX / "moodle_calendar.ics").read_text(encoding="utf-8")
    events = moodle_calendar.parse_ics(raw)
    assert len(events) == 3
    assert events[0]["summary"].startswith("COMPX202")
    assert events[0]["start"].isoformat() == "2026-07-14"
    rows = moodle_calendar.events_to_calendar_rows(events, ["COMPX202", "COMPX225"])
    papers = {r["paper_code"] for r in rows}
    assert "COMPX202" in papers
    assert "COMPX225" in papers
    assert any(r["event_type"] == "exam" for r in rows)


def test_mask_calendar_url():
    url = "https://elearn.waikato.ac.nz/calendar/export_execute.php?userid=1&authtoken=SECRET123&preset_what=all"
    masked = moodle_calendar.mask_calendar_url(url)
    assert "SECRET123" not in masked
    assert "authtoken=" in masked


def test_sync_all_endpoint(tmp_path: Path, monkeypatch):
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

    html = (FIX / "paper_outlines" / "compx202-26b-ham.html").read_text(encoding="utf-8")
    client.post("/api/semester/papers/fetch", json={"paper_code": "COMPX202-26B (HAM)", "html": html})

    part = FIX / "schedule_zip" / "ExportBlock-900a42fe-ad20-4c18-bc98-60b0fd695436-Part-1.zip"
    sched_id = None
    if part.exists():
        with part.open("rb") as fh:
            r = client.post("/api/semester/schedule/import", files={"file": ("sched.zip", fh, "application/zip")})
        sched_id = r.json()["id"]

    ics = (FIX / "moodle_calendar.ics").read_text(encoding="utf-8")
    monkeypatch.setattr(moodle_calendar, "_default_get", lambda url: ics)

    r = client.post("/api/semester/sync-all", json={
        "paper_codes": ["COMPX202"],
        "class_schedule_id": sched_id,
        "calendar_url": "https://elearn.waikato.ac.nz/calendar/export_execute.php?userid=1&authtoken=test",
    })
    assert r.status_code == 200
    body = r.json()
    assert body["plan_id"]
    assert body["task_count"] >= 1
    steps = {s["step"]: s["status"] for s in body["steps"]}
    assert steps.get("outlines") == "ok"
    assert steps.get("exports") == "ok"
    assert body["artifacts"]["ics"]
    assert Path(body["artifacts"]["ics"]).exists()


def test_calendar_url_secret_endpoint(tmp_path: Path):
    import os
    os.environ["PANOPTO_OUTPUT"] = str(tmp_path)
    import importlib
    import app.main as main
    importlib.reload(main)
    from fastapi.testclient import TestClient
    client = TestClient(main.app)

    r = client.put("/api/semester/moodle/calendar-url", json={
        "url": "https://elearn.waikato.ac.nz/calendar/export_execute.php?userid=1&authtoken=abc123",
    })
    assert r.status_code == 200
    assert r.json()["stored"] is True
    assert "abc123" not in r.json()["masked_url"]

    r = client.get("/api/semester/moodle/calendar-url")
    assert r.json()["configured"] is True
    assert "abc123" not in r.json()["masked_url"]


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
        r = client.get(f"/api/semester/plans/{plan_id}/export/google-calendar.csv")
        assert r.status_code == 200
        assert "Assignment One" in r.text
        r = client.get(f"/api/semester/plans/{plan_id}/export/obsidian.zip")
        assert r.status_code == 200
        assert r.headers["content-type"] == "application/zip"
        assert r.content.startswith(b"PK")
        with zipfile.ZipFile(io.BytesIO(r.content)) as vault:
            names = vault.namelist()
            assert any(name.endswith("/README.md") for name in names)
            assert any("/Master/Task Schedule/" in name for name in names)
            assert any("/Courses/" in name and "/Guide/" in name for name in names)
            assert any("Master/Study Plan2.md" in name for name in names)
            assert any(".obsidian/app.json" in name for name in names)

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
