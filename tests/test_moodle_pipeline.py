"""End-to-end 'from the link alone' pipeline, driven by injected fetchers so the
whole flow is exercised offline (the real server needs a live session):

    parse course  ->  download resource files  ->  convert to Markdown (+images)
    ->  export a NotebookLM/AI folder,  with an include-images toggle (default on).
"""
from __future__ import annotations

import io
from pathlib import Path

import pytest

from app import core, sources
from app.imports import moodle_web, moodle_resources

PIL = pytest.importorskip("PIL")
from PIL import Image


def _pdf_with_image() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (60, 40), (40, 120, 200)).save(buf, "PDF")
    return buf.getvalue()


# A realistic combined-course page: resource + assignment activities and the
# Panopto block's duplicated itpc:// / https:// mp3+mp4 feeds.
MAIN_PAGE = """<html><head><title>Paper: COMPX201-26A - Data Structures | Moodle</title></head>
<body><h1>COMPX201-26A (HAM) - Data Structures and Algorithms</h1>
<div data-sectionname="Week 1"></div>
<a href="https://elearn.x/mod/resource/view.php?id=11"><span class="instancename">Lecture 1 Slides</span></a>
<a href="https://elearn.x/mod/resource/view.php?id=12"><span class="instancename">Assignment One Brief</span></a>
<a href="https://elearn.x/mod/forum/view.php?id=2"><span class="instancename">Announcements</span></a>
<a href="itpc://waikato.au.panopto.com/Panopto/Podcast/Podcast.ashx?courseid=abc&type=mp4">Video podcast(RSS)</a>
<a href="https://waikato.au.panopto.com/Panopto/Podcast/Podcast.ashx?courseid=abc&type=mp4">Video podcast(RSS)</a>
<a href="itpc://waikato.au.panopto.com/Panopto/Podcast/Podcast.ashx?courseid=abc&type=mp3">Audio podcast(RSS)</a>
<a href="https://waikato.au.panopto.com/Panopto/Podcast/Podcast.ashx?courseid=abc&type=mp3">Audio podcast(RSS)</a>
</body></html>"""


def _page_fetcher(url, cookie_header):
    if "view.php?id=" in url and "section" not in url:
        return MAIN_PAGE
    return "<html><body>empty</body></html>"


def _resource_fetcher(url, cookie_header):
    # every /mod/resource/ link 'redirects' to a PDF (with an embedded image)
    return _pdf_with_image(), "file.pdf", "application/pdf"


def test_panopto_feeds_deduped_and_normalised():
    feeds = sources.extract_panopto_feeds(MAIN_PAGE)
    # 4 raw links (itpc+https × mp3+mp4) collapse to 2 https feeds, mp4 first
    assert all(f.startswith("https://") for f in feeds)
    assert len(feeds) == 2
    assert "type=mp4" in feeds[0]


def test_activities_capture_download_urls():
    parsed = sources.parse_moodle_html(MAIN_PAGE)
    res = [a for a in parsed["activities"] if a["kind"] == "resource"]
    assert len(res) == 2
    assert all(a["url"].startswith("https://elearn.x/mod/resource/view.php?id=") for a in res)
    assert all(a["downloadable"] for a in res)
    # a forum is not downloadable
    forum = next(a for a in parsed["activities"] if a["kind"] == "forum")
    assert forum["downloadable"] is False


def test_full_pipeline_link_to_notebooklm(tmp_path: Path):
    out = tmp_path / "lib"
    out.mkdir()

    # 1) parse the course straight from its URL (cookies + page fetch injected)
    parsed = moodle_web.import_course(
        "https://elearn.x/course/view.php?id=77000",
        cookies="MoodleSession=abc", fetcher=_page_fetcher)
    assert parsed["code"] == "COMPX201-26A"
    assert len(parsed["panopto_feeds"]) == 2          # ready for transcription

    # 2) download the resource files with the session cookies
    res_dir = out / "_resources"
    dl = moodle_resources.download_resources(
        parsed["activities"], res_dir, cookies="MoodleSession=abc",
        fetcher=_resource_fetcher)
    assert dl["downloaded"] == 2
    names = {f["file"] for f in dl["files"]}
    assert "Lecture_1_Slides.pdf" in names and "Assignment_One_Brief.pdf" in names

    # 3) convert to Markdown WITH images attached (default)
    conv = core.convert_documents(res_dir, out, target="ai", combined=True,
                                  keep_images=True)
    assert conv["count"] == 2
    md = (out / "_docs" / "Lecture_1_Slides.md").read_text(encoding="utf-8")
    assert "## Images & diagrams" in md
    assert (out / "_docs" / "Lecture_1_Slides_assets.zip").is_file()

    # 4) export a NotebookLM folder from everything imported
    exp = core.export_all_sources(out, combined=True, course="COMPX201-26A")
    assert exp["count"] >= 1
    assert (out / "_notebooklm" / "everything_pack.md").exists()


def test_pipeline_include_images_toggle_off(tmp_path: Path):
    out = tmp_path / "lib"; out.mkdir()
    parsed = moodle_web.import_course(
        "https://elearn.x/course/view.php?id=77000",
        cookies="c", fetcher=_page_fetcher)
    res_dir = out / "_resources"
    moodle_resources.download_resources(parsed["activities"], res_dir,
                                       fetcher=_resource_fetcher)
    core.convert_documents(res_dir, out, target="ai", keep_images=False)
    # the toggle's effect: no image assets are extracted when it's off
    assert list((out / "_docs").glob("*_assets*")) == []
    # for contrast, the same input WITH images on does create assets (as a zip)
    out2 = tmp_path / "lib2"; out2.mkdir()
    core.convert_documents(res_dir, out2, target="ai", keep_images=True)
    assert list((out2 / "_docs").glob("*_assets.zip"))
