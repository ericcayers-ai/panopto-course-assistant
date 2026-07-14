"""Suite Sync FK regression + Master/Courses vault layout."""
from __future__ import annotations

import datetime as dt
import zipfile
from pathlib import Path

import pytest

from app import database, settings_store, suites, task_schedule


@pytest.fixture()
def db(tmp_path: Path):
    d = database.Database(tmp_path / "t.db")
    yield d
    d.close()


SAMPLE_TASKS = [
    {"id": "compx202-assignment-one", "subject": "COMPX202", "name": "Assignment One",
     "type": "Assignment", "due_date": "2026-08-14", "weight": 15,
     "status": "not_started", "source": "schedule", "tags": ["COMPX202"]},
    {"id": "japan332-essay", "subject": "JAPAN332", "name": "Essay",
     "type": "Assignment", "due_date": "2026-09-01", "weight": 20,
     "status": "not_started", "source": "outline", "tags": ["JAPAN332"]},
]
SAMPLE_OUTLINES = [
    {"paper_code": "COMPX202-26B (HAM)", "title": "Mobile Computing"},
    {"paper_code": "JAPAN332-26B (HAM)", "title": "Japanese Language 3"},
]
SAMPLE_EVENTS = [
    {"uid": "csmax275-lecture", "paper_code": "CSMAX275",
     "summary": "CSMAX275: Lecture", "event_type": "lecture",
     "start": dt.date(2026, 7, 20),
     "end": dt.date(2026, 7, 21)},
]
SAMPLE_ANNS = [
    {"title": "Welcome", "body": "First post", "author": "Lecturer",
     "posted_at": "2026-07-13"},
]


def test_create_task_schedule_rejects_invalid_course(db):
    with pytest.raises(ValueError, match="Invalid course_id"):
        db.create_task_schedule(0, "plan", "{}", "COMPX202", None)
    with pytest.raises(ValueError, match="Invalid course_id"):
        db.create_task_schedule(9999, "plan", "{}", "COMPX202", None)


def test_create_task_schedule_drops_stale_class_schedule_fk(db):
    cid = db.create_course("My course")
    # Non-existent class_schedule_id must not raise IntegrityError
    sid = db.create_task_schedule(cid, "plan", "{}", "COMPX202", class_schedule_id=424242)
    row = db.get_task_schedule(sid)
    assert row is not None
    assert row["class_schedule_id"] is None


def test_sync_semester_all_succeeds_without_active_course(db, monkeypatch, tmp_path):
    """Suite Sync used to pass course_id=0 and trip FOREIGN KEY."""
    import app.context as ctx
    ctx.OUTPUT_DIR = tmp_path

    def fake_outline(code):
        return {"paper_code": code, "title": code, "assessments": []}

    monkeypatch.setattr("app.paper_outlines.fetch_outline", fake_outline)
    monkeypatch.setattr(
        "app.task_schedule._resolve_outline",
        lambda _db, code: fake_outline(code),
    )
    monkeypatch.setattr(
        "app.task_schedule.write_export_artifacts",
        lambda *a, **k: {"ics": "", "obsidian_zip": ""},
    )

    # No courses yet — ensure_active_course creates one
    report = task_schedule.sync_semester_all(
        db, 0,
        paper_codes=["COMPX202"],
        name="Test plan",
    )
    assert report["plan_id"]
    plan = db.get_task_schedule(report["plan_id"])
    assert plan is not None
    assert db.get_course(plan["course_id"]) is not None
    assert settings_store.get_active_course(db) == plan["course_id"]


def test_obsidian_master_courses_layout(tmp_path: Path):
    built = suites.build_suite_tree(
        tmp_path / "out",
        format="obsidian",
        title="Tri B plan",
        tasks=SAMPLE_TASKS,
        outlines=SAMPLE_OUTLINES,
        moodle_events=SAMPLE_EVENTS,
        announcements=SAMPLE_ANNS,
        paper_codes=["COMPX202", "JAPAN332", "CSMAX275"],
    )
    root = Path(built["root"])
    assert (root / "README.md").exists()
    assert (root / "IMPORT.md").exists()
    assert (root / "Master" / "Study Plan2.md").exists()
    assert (root / "Master" / "Task Graphs" / "Semester Gantt.md").exists()
    assert (root / "Master" / "Task Graphs" / "Overview.canvas").exists()
    assert (root / "Master" / "Task Schedule" / "Timetable Sheet - Markdown ver.md").exists()
    assert (root / "Master" / "Calendar" / "semester.ics").exists()
    assert (root / ".obsidian" / "app.json").exists()
    assert (root / ".obsidian" / "core-plugins.json").exists()

    for code in ("COMPX202", "JAPAN332", "CSMAX275"):
        course = root / "Courses" / code
        assert course.is_dir()
        assert (course / "README.md").exists()
        assert (course / f"{code}_Mindmap.canvas").exists()
        for sub in suites.COURSE_SUBFOLDERS:
            assert (course / sub).is_dir()

    readme = (root / "README.md").read_text(encoding="utf-8")
    assert "Master/Study Plan2" in readme
    assert "Courses/COMPX202" in readme
    import_md = (root / "IMPORT.md").read_text(encoding="utf-8")
    assert "Obsidian" in import_md


def test_notion_and_onenote_have_import_and_master(tmp_path: Path):
    notion = suites.build_suite_tree(
        tmp_path / "n", format="notion", title="Tri B plan",
        tasks=SAMPLE_TASKS, outlines=SAMPLE_OUTLINES[:1],
        paper_codes=["COMPX202"],
    )
    nroot = Path(notion["root"])
    assert (nroot / "IMPORT.md").exists()
    assert "Notion" in (nroot / "IMPORT.md").read_text(encoding="utf-8")
    assert (nroot / "Master" / "Task Schedule" / "Tasks.csv").exists()
    assert (nroot / "Courses" / "COMPX202" / "README.md").exists()

    onenote = suites.build_suite_tree(
        tmp_path / "o", format="onenote", title="Tri B plan",
        tasks=SAMPLE_TASKS, outlines=SAMPLE_OUTLINES[:1],
        paper_codes=["COMPX202"],
    )
    oroot = Path(onenote["root"])
    assert (oroot / "IMPORT.md").exists()
    assert "OneNote" in (oroot / "IMPORT.md").read_text(encoding="utf-8")
    assert (oroot / "_onenote" / "manifest.json").exists()


def test_export_obsidian_zip_master_layout(tmp_path: Path):
    dest = tmp_path / "obsidian.zip"
    task_schedule.export_obsidian_zip(
        SAMPLE_TASKS, dest, title="Tri B plan",
        outlines=SAMPLE_OUTLINES, moodle_events=SAMPLE_EVENTS,
        announcements=SAMPLE_ANNS,
    )
    with zipfile.ZipFile(dest) as zf:
        names = set(zf.namelist())
    assert any("Master/Study Plan2.md" in n for n in names)
    assert any("Master/Calendar/semester.ics" in n for n in names)
    assert any("Courses/COMPX202/" in n for n in names)
    assert any(".obsidian/app.json" in n for n in names)


def test_ensure_active_course_creates_default(db):
    cid = settings_store.ensure_active_course(db, None)
    assert db.get_course(cid) is not None
    assert settings_store.get_active_course(db) == cid


def test_mirror_skips_unchanged(tmp_path: Path):
    src = tmp_path / "src"
    dest = tmp_path / "dest"
    (src / "Library").mkdir(parents=True)
    f = src / "Library" / "notes.md"
    f.write_text("hello", encoding="utf-8")
    first = suites.mirror_tree(src, dest)
    assert first["new_files"] == 1
    second = suites.mirror_tree(src, dest)
    assert second["new_files"] == 0
    assert second["skipped"]
    (src / "Library" / "new.md").write_text("fresh", encoding="utf-8")
    third = suites.mirror_tree(src, dest)
    assert third["new_files"] == 1
    assert (dest / "Library" / "new.md").exists()


def test_capability_matrix_shape():
    from app import browser_scrape
    api = browser_scrape.capability_matrix("api")
    browser = browser_scrape.capability_matrix("browser")
    assert api["mode"] == "api"
    assert browser["mode"] == "browser"
    assert len(api["matrix"]) >= 6
    assert api["matrix"][0]["capability"]
    assert "playwright_available" in api
    labels = {row["capability"] for row in api["matrix"]}
    assert "Paper code detection" in labels
    assert "Forums / discussions" in labels


def test_panopto_rss_fixture_discovery():
    from app import panopto_discover
    html = '''
    <div class="podcast-links">
      <a class="rssLink" href="/Panopto/Podcast/Podcast.ashx?id=abc&amp;type=mp4">Video podcast</a>
      <a href="https://waikato.au.panopto.com/Panopto/Podcast/Podcast.ashx?folder=xyz">other</a>
    </div>
    '''
    feeds = panopto_discover.feeds_from_html(html)
    assert any("Podcast.ashx" in f for f in feeds)
    result = panopto_discover.discover(moodle_html=html)
    assert result["count"] >= 1
    assert result["feeds"]


def test_paper_code_autofill():
    courses = [
        {"shortname": "COMPX202-26B (HAM)", "fullname": "Mobile Computing"},
        {"code": "", "name": "CSMAX275 Tri B"},
        {"fullname": "Unrelated Seminar"},
    ]
    codes = suites.detect_paper_codes_from_courses(courses)
    bases = {c.split("-")[0] for c in codes}
    assert "COMPX202" in bases
    assert "CSMAX275" in bases


def test_suite_settings_roundtrip(db):
    suites.set_destinations(db, {"obsidian": "C:/Vaults/Obsidian"})
    suites.set_enabled(db, ["obsidian", "notion"])
    suites.set_auto_sync(db, True)
    suites.set_last_sync(db, {"formats": ["obsidian"], "new_files": 2, "updated": 0})
    assert suites.get_destinations(db)["obsidian"].endswith("Obsidian")
    assert suites.get_enabled(db) == ["obsidian", "notion"]
    assert suites.get_auto_sync(db) is True
    assert suites.get_last_sync(db)["new_files"] == 2


def test_playwright_missing_is_soft_skipped(monkeypatch):
    from app import panopto_discover
    monkeypatch.setattr(
        "app.browser_scrape.playwright_available", lambda: False,
    )
    result = panopto_discover.discover(
        panopto_url="https://waikato.au.panopto.com/Panopto/Pages/Sessions/List.aspx",
        use_playwright=True,
    )
    assert result["feeds"] == []
    assert any(s.get("skipped") for s in result["steps"])


def test_suite_sync_to_destination(tmp_path: Path, db):
    """Sync mirrors a built suite into a configured destination folder."""
    dest = tmp_path / "ObsidianVault"
    dest.mkdir()
    suites.set_destinations(db, {"obsidian": str(dest)})
    suites.set_enabled(db, ["obsidian"])
    plan = {"tasks": SAMPLE_TASKS, "paper_codes": ["COMPX202", "JAPAN332"]}
    report = suites.sync_suites_to_destinations(
        db=db,
        plan_payload=plan,
        title="Tri B plan",
        outlines=SAMPLE_OUTLINES,
        announcements=SAMPLE_ANNS,
        library_dir=tmp_path / "lib",
        formats=["obsidian"],
        staging_dir=tmp_path / "_suites",
        push_live=False,
    )
    assert report["ok"]
    assert "obsidian" in report["destinations_written"]
    mirrored_root = dest / "tri-b-plan"
    assert (mirrored_root / "README.md").exists()
    assert (mirrored_root / "Master" / "Study Plan2.md").exists()
    assert (mirrored_root / "Courses" / "COMPX202" / "README.md").exists()
    report2 = suites.sync_suites_to_destinations(
        db=db,
        plan_payload=plan,
        title="Tri B plan",
        outlines=SAMPLE_OUTLINES,
        announcements=SAMPLE_ANNS,
        library_dir=tmp_path / "lib",
        formats=["obsidian"],
        staging_dir=tmp_path / "_suites2",
        push_live=False,
    )
    mirror = report2["destinations_written"]["obsidian"]["mirror"]
    assert mirror.get("new_files", 0) == 0 or mirror.get("skipped")
