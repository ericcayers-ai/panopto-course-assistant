"""Suite engine, mirror sync, capabilities, Panopto discovery, paper codes."""
from __future__ import annotations

import datetime as dt
import json
import zipfile
from pathlib import Path

import pytest

from app import database, suites, task_schedule
from app import panopto_discover
from app import browser_scrape


@pytest.fixture()
def db(tmp_path: Path):
    d = database.Database(tmp_path / "t.db")
    d.create_course("Tri B", code="TRI-B", semester="B", year=2026)
    yield d
    d.close()


SAMPLE_TASKS = [
    {"id": "compx202-assignment-one", "subject": "COMPX202", "name": "Assignment One",
     "type": "Assignment", "due_date": "2026-08-14", "weight": 15,
     "status": "not_started", "source": "schedule", "tags": ["COMPX202"]},
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


def test_obsidian_suite_tree_layout(tmp_path: Path):
    built = suites.build_suite_tree(
        tmp_path / "out",
        format="obsidian",
        title="Tri B plan",
        tasks=SAMPLE_TASKS,
        outlines=SAMPLE_OUTLINES,
        moodle_events=SAMPLE_EVENTS,
        announcements=SAMPLE_ANNS,
    )
    root = Path(built["root"])
    assert (root / "README.md").exists()
    assert (root / "Study Plan.md").exists()
    assert (root / "Semester Gantt.md").exists()
    assert (root / "Calendar" / "semester.ics").exists()
    assert (root / "Calendar" / "google-calendar.csv").exists()
    assert (root / "Study Timetable" / "Timetable Sheet - Markdown ver.md").exists()
    assert (root / "papers" / "japan332.md").exists()
    assert (root / "Guide" / "JAPAN332 Gantt.md").exists()
    assert (root / "Announcements" / "index.md").exists()
    assert (root / "Library").is_dir()
    assert (root / "Forums").is_dir()
    readme = (root / "README.md").read_text(encoding="utf-8")
    assert "[[Semester Gantt]]" in readme
    assert "[[papers/japan332|JAPAN332]]" in readme


def test_notion_suite_includes_csvs(tmp_path: Path):
    built = suites.build_suite_tree(
        tmp_path / "out",
        format="notion",
        title="Tri B plan",
        tasks=SAMPLE_TASKS,
        outlines=SAMPLE_OUTLINES,
        announcements=SAMPLE_ANNS,
    )
    root = Path(built["root"])
    assert (root / "Tasks.csv").exists()
    assert (root / "Lectures.csv").exists()
    assert (root / "Announcements.csv").exists()
    assert (root / "IMPORT.md").exists()
    assert "Assignment One" in (root / "Tasks.csv").read_text(encoding="utf-8")


def test_onenote_suite_html_pack(tmp_path: Path):
    built = suites.build_suite_tree(
        tmp_path / "out",
        format="onenote",
        title="Tri B plan",
        tasks=SAMPLE_TASKS,
        outlines=SAMPLE_OUTLINES[:1],
    )
    root = Path(built["root"])
    assert (root / "manifest.json").exists()
    assert (root / "_onenote").is_dir()
    assert list((root / "_onenote").rglob("*.html"))


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


def test_export_obsidian_zip_via_suite(tmp_path: Path):
    dest = tmp_path / "obsidian.zip"
    task_schedule.export_obsidian_zip(
        SAMPLE_TASKS, dest, title="Tri B plan",
        outlines=SAMPLE_OUTLINES, moodle_events=SAMPLE_EVENTS,
        announcements=SAMPLE_ANNS,
    )
    with zipfile.ZipFile(dest) as zf:
        names = set(zf.namelist())
    root = "tri-b-plan"
    assert f"{root}/README.md" in names
    assert any("Calendar/semester.ics" in n for n in names)
    assert any("Semester Gantt.md" in n for n in names)


def test_capability_matrix_shape():
    api = browser_scrape.capability_matrix("api")
    browser = browser_scrape.capability_matrix("browser")
    assert api["mode"] == "api"
    assert browser["mode"] == "browser"
    assert len(api["matrix"]) >= 6
    assert api["matrix"][0]["capability"]


def test_panopto_rss_fixture_discovery():
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
    assert suites.get_destinations(db)["obsidian"].endswith("Obsidian")
    assert suites.get_enabled(db) == ["obsidian", "notion"]
    assert suites.get_auto_sync(db) is True


def test_suite_sync_to_destination(tmp_path: Path, db):
    """Sync mirrors a built suite into a configured destination folder."""
    dest = tmp_path / "ObsidianVault"
    dest.mkdir()
    suites.set_destinations(db, {"obsidian": str(dest)})
    suites.set_enabled(db, ["obsidian"])
    plan = {"tasks": SAMPLE_TASKS, "paper_codes": ["COMPX202"]}
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
    # Second sync should skip unchanged files
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

