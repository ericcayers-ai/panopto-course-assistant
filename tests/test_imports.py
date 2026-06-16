"""§7 import expansion: live Moodle URL crawl, folder import, preflight."""
from __future__ import annotations

from pathlib import Path

import pytest

from app import database
from app.imports import moodle_web, folder as folder_import, preflight


# --- Moodle live-URL importer (offline via injected fetcher) ---------------

MAIN_PAGE = """<html><head><title>Paper: COMPX234-26A - Systems and Networks | Moodle</title></head>
<body><h1>COMPX234-26A (HAM) &amp; (TGA) - Systems and Networks</h1>
<div data-sectionname="Lecture Material"></div>
<a href="https://elearn.waikato.ac.nz/course/section.php?id=597496">Lecture materials</a>
<a href="https://elearn.waikato.ac.nz/course/section.php?id=597497">Assignments</a>
<a href="/mod/forum/view.php?id=1"><span class="instancename">Announcements</span></a>
<a href="https://x/Panopto/Podcast/Embed/abc.xml">Video podcast(RSS)</a>
</body></html>"""

SECTION_ASSIGN = """<html><body>
<div data-sectionname="Assignments"></div>
<a href="/mod/assign/view.php?id=9"><span class="instancename">A1: Concurrent Programming</span></a>
<a href="/mod/assign/view.php?id=10"><span class="instancename">A2: Networked System</span></a>
</body></html>"""

SECTION_LEC = """<html><body><div data-sectionname="Week 1 Intro"></div></body></html>"""


def _fake_fetcher(pages):
    def fetch(url, cookie_header):
        for frag, html in pages.items():
            if frag in url:
                return html
        return "<html><body>empty</body></html>"
    return fetch


def test_import_course_crawls_sections_and_feeds():
    fetcher = _fake_fetcher({
        "view.php": MAIN_PAGE,
        "597497": SECTION_ASSIGN,
        "597496": SECTION_LEC,
    })
    res = moodle_web.import_course(
        "https://elearn.waikato.ac.nz/course/view.php?id=77547",
        cookies="MoodleSession=abc123", fetcher=fetcher)
    assert res["code"] == "COMPX234-26A"
    assert res["pages_fetched"] == 3
    # activities merged from the crawled assignment section
    names = [a["name"] for a in res["activities"]]
    assert any("A1: Concurrent Programming" in n for n in names)
    # panopto feed discovered
    assert any("Panopto" in f for f in res["panopto_feeds"])


def test_import_course_rejects_non_course_url():
    with pytest.raises(moodle_web.MoodleWebError):
        moodle_web.import_course("https://elearn.waikato.ac.nz/my/", fetcher=lambda u, c: "")


def test_import_course_detects_logged_out():
    login = "<html><body><form id=\"login\">log in</form></body></html>"
    with pytest.raises(moodle_web.MoodleWebError):
        moodle_web.import_course(
            "https://x/course/view.php?id=1", fetcher=lambda u, c: login)


def test_parse_cookies_formats():
    assert moodle_web.parse_cookies("a=1; b=2") == "a=1; b=2"
    netscape = "# Netscape\nelearn\tTRUE\t/\tTRUE\t0\tMoodleSession\txyz"
    assert "MoodleSession=xyz" in moodle_web.parse_cookies(netscape)


# --- Folder import + preflight ---------------------------------------------


def _make_tree(root: Path):
    (root / "Week1").mkdir(parents=True)
    (root / "Week1" / "Lecture1_Intro.pdf").write_bytes(b"%PDF-1.4 fake")
    (root / "Week1" / "Lecture1.srt").write_text("1\n00:00 --> 00:01\nhi\n")
    (root / "Week2_TCP.mp4").write_bytes(b"\x00\x00fake video")
    (root / "_docs").mkdir()
    (root / "_docs" / "ignoreme.md").write_text("derived output")


def test_folder_scan_categorizes_and_skips_outputs(tmp_path):
    _make_tree(tmp_path)
    manifest = folder_import.scan(tmp_path)
    kinds = manifest["counts"]
    assert kinds.get("document") == 1 and kinds.get("subtitle") == 1
    assert kinds.get("media") == 1
    # our own _docs output folder is skipped
    assert all("_docs" not in it["rel"] for it in manifest["items"])
    # week inferred from path/title
    pdf = next(it for it in manifest["items"] if it["ext"] == ".pdf")
    assert pdf["week"] == 1


def test_import_folder_indexes_documents(tmp_path):
    db = database.Database(tmp_path / "t.db")
    cid = db.create_course("C")
    src = tmp_path / "src"; src.mkdir()
    _make_tree(src)
    res = folder_import.import_folder(db, tmp_path, src, course_id=cid)
    assert res["indexed"] == 2                     # pdf + srt
    assert res["media_pending_transcription"] == 1
    assert db.count_documents(cid) == 2
    db.close()


def test_preflight_reports_expected_output_and_warnings(tmp_path):
    _make_tree(tmp_path)
    pf = preflight.preflight_folder(tmp_path)
    assert pf["ok"] is True
    assert pf["expected_output"]["media_to_transcribe"] == 1
    assert pf["expected_output"]["documents_indexed"] == 2
    assert "dependencies" in pf
