"""Unit tests for paper-code extraction and calendar URL discovery."""
from __future__ import annotations

from app import browser_scrape, suites
from app.imports import moodle_api
from app.sources import _extract_course_code


def test_course_code_with_campus_suffixes():
    title = "COMPX225-26B (HAM) & (TGA) - Introduction to Databases"
    assert moodle_api._course_code(title) == "COMPX225-26B"
    assert _extract_course_code(title) == "COMPX225-26B"


def test_detect_paper_codes_from_waikato_style_titles():
    courses = [
        {"shortname": "COMPX225-26B (HAM)", "fullname": "Introduction to Databases"},
        {"fullname": "COMPX225-26B (HAM) & (TGA) - Introduction to Databases"},
    ]
    codes = suites.detect_paper_codes_from_courses(courses)
    assert codes == ["COMPX225-26B"]


def test_discover_calendar_url_from_html():
    html = '''
    <a href="/calendar/export_execute.php?userid=42&amp;authtoken=abc123&amp;preset_id=2">
      Export calendar
    </a>
    '''
    urls = browser_scrape._extract_calendar_urls(html, "https://moodle.test/")
    assert len(urls) == 1
    assert "authtoken=abc123" in urls[0]
    assert urls[0].startswith("https://moodle.test/calendar/export_execute.php")


def test_merge_paper_codes_preserves_existing(tmp_path, monkeypatch):
    """Connect/import must merge paper codes, not replace the saved list."""
    monkeypatch.setenv("PANOPTO_OUTPUT", str(tmp_path))
    import importlib
    import app.main as main
    importlib.reload(main)
    from app import context, moodle_jobs, settings_store

    settings_store.set(context.db, "semester.paper_codes", ["COMPX101-26A"])
    merged = moodle_jobs._merge_paper_codes(["COMPX225-26B", "COMPX101-26A"])
    assert merged == ["COMPX101-26A", "COMPX225-26B"]
    assert settings_store.get(context.db, "semester.paper_codes") == merged
