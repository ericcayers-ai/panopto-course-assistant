"""Tests for generalized title inference, organise modes, and the Moodle parser."""
from __future__ import annotations

from pathlib import Path

import pytest

from app import core, sources


# ---------------------------------------------------------------------------
# Generalized sequence inference
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "title,kind,n",
    [
        ("Week 3 Intro", "week", 3),
        ("Wk_04 notes", "week", 4),
        ("Lecture 7", "lecture", 7),
        ("Lec-2 recap", "lecture", 2),
        ("Module 5", "module", 5),
        ("Mod 1", "module", 1),
        ("Unit 9", "unit", 9),
        ("Session 6", "session", 6),
        ("Lab 2", "lab", 2),
        ("Nothing here", "week", None),
    ],
)
def test_infer_number(title, kind, n):
    assert core.infer_number(title, kind) == n


def test_infer_sequence_priority():
    # week beats lecture when both present
    assert core.infer_sequence("Week 2 Lecture 5") == ("week", 2)
    assert core.infer_sequence("Lecture 5 recap") == ("lecture", 5)
    assert core.infer_sequence("Module 3 stuff") == ("module", 3)
    assert core.infer_sequence("no markers") is None


def test_infer_week_still_works():
    assert core.infer_week("Week_1_OS_Basics") == 1
    assert core.infer_week("nope") is None


@pytest.mark.parametrize(
    "title,mode,folder",
    [
        ("Lecture 3 Graphs", "lecture", "Lecture_03"),
        ("Module 4 Trees", "module", "Module_04"),
        ("Week 2 X", "week", "Week_02"),
        ("Lecture 5", "auto", "Lecture_05"),     # auto falls through to lecture
        ("Week 9 Transport", "auto", "Week_09"), # auto prefers week
        ("Random talk", "lecture", "unparsed-lecture"),
    ],
)
def test_organization_folder_modes(title, mode, folder):
    item = core.LectureItem(title=title, url="u")
    assert core.organization_folder(item, mode) == folder


def test_auto_falls_back_to_date_then_uncategorized():
    dated = core.LectureItem(title="Guest talk", url="u", pub_date="Mon, 09 Mar 2026 02:13:40 GMT")
    assert core.organization_folder(dated, "auto") == "2026-03-09"
    undated = core.LectureItem(title="Guest talk", url="u")
    assert core.organization_folder(undated, "auto") == "uncategorized"


# ---------------------------------------------------------------------------
# Moodle parser — unit (synthetic HTML, both formats)
# ---------------------------------------------------------------------------

WEEKLY_HTML = """<html><head><title>Paper: COMPX201-26A (HAM) - Data Structures | Moodle</title></head>
<body>
<h1 class="h2 mb-0">COMPX201-26A (HAM) - Data Structures &amp; Algorithms</h1>
<div data-sectionname="Introduction"></div>
<div data-sectionname="Week 1: Stacks and Queues"></div>
<div data-sectionname="Week 2 - Linked Lists"></div>
<h3>Notifications</h3>
</body></html>"""

TOPICS_HTML = """<html><head><title>Paper: COMPX234-26A - Systems and Networks | Moodle</title></head>
<body>
<h1 class="h2 mb-0">COMPX234-26A (HAM) &amp; (TGA) - Systems and Networks</h1>
<h3>Introduction</h3>
<h3>Lecture Material</h3>
<h3>Assignments</h3>
<h3>Fetching learning content...</h3>
<h3 class="h6 px-2">Contacts</h3>
</body></html>"""


def test_parse_weekly_format(tmp_path: Path):
    f = tmp_path / "course.html"
    f.write_text(WEEKLY_HTML, encoding="utf-8")
    p = sources.parse_moodle_course(f)
    assert p["code"] == "COMPX201-26A"
    assert "Data Structures" in p["title"]
    names = [s["name"] for s in p["sections"]]
    assert names == ["Introduction", "Week 1: Stacks and Queues", "Week 2 - Linked Lists"]
    assert p["week_topics"][1] == "Stacks and Queues"
    assert p["week_topics"][2] == "Linked Lists"
    assert "Notifications" not in names  # chrome filtered


def test_parse_topics_format(tmp_path: Path):
    f = tmp_path / "course.html"
    f.write_text(TOPICS_HTML, encoding="utf-8")
    p = sources.parse_moodle_course(f)
    assert p["code"] == "COMPX234-26A"
    names = [s["name"] for s in p["sections"]]
    assert "Lecture Material" in names
    assert "Fetching learning content..." not in names  # placeholder filtered
    assert "Contacts" not in names                       # has class attr, not bare h3


def test_find_course_file_in_folder(tmp_path: Path):
    (tmp_path / "course").mkdir()
    (tmp_path / "course" / "view_php.html").write_text(WEEKLY_HTML, encoding="utf-8")
    p = sources.parse_moodle_course(tmp_path)
    assert p["code"] == "COMPX201-26A"


def test_parse_missing_file(tmp_path: Path):
    with pytest.raises(FileNotFoundError):
        sources.parse_moodle_course(tmp_path)


def test_save_outline(tmp_path: Path):
    f = tmp_path / "course.html"
    f.write_text(WEEKLY_HTML, encoding="utf-8")
    parsed = sources.parse_moodle_course(f)
    out = tmp_path / "out"
    rel = sources.save_outline(out, parsed)
    assert rel.endswith("_outline.md")
    assert "Course outline" in (out / rel).read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Moodle parser — integration against the real example exports (if present)
# ---------------------------------------------------------------------------

EXAMPLES = Path(__file__).resolve().parents[2] / "vibecodeapp"


@pytest.mark.skipif(not EXAMPLES.exists(), reason="example courses not present")
def test_parse_all_real_examples():
    folders = sorted(EXAMPLES.glob("examplecourse-*"))
    if not folders:
        pytest.skip("no example courses")
    for folder in folders:
        p = sources.parse_moodle_course(folder)
        assert p["title"], f"no title for {folder.name}"
        assert p["code"], f"no code for {folder.name}"
        assert p["section_count"] >= 1, f"no sections for {folder.name}"
