"""§7 Moodle resource downloader: direct files, HTML→pluginfile hop, naming,
skip of non-document types — all offline via an injected fetcher."""
from __future__ import annotations

from pathlib import Path

import pytest

from app.imports import moodle_resources as mr


ACTIVITIES = [
    {"name": "Lecture 1 Slides", "kind": "resource", "downloadable": True,
     "url": "https://x/mod/resource/view.php?id=1"},
    {"name": "Assignment Brief", "kind": "assign", "downloadable": True,
     "url": "https://x/mod/assign/view.php?id=2"},
    {"name": "An intro page", "kind": "resource", "downloadable": True,
     "url": "https://x/mod/resource/view.php?id=3"},
    {"name": "Announcements", "kind": "forum", "downloadable": False,
     "url": "https://x/mod/forum/view.php?id=4"},
]


def _fetcher(url, cookies):
    if "id=1" in url:
        return b"%PDF-1.4 slides", "slides.pdf", "application/pdf"
    if "id=2" in url:
        return b"DOCX-bytes", "brief.docx", \
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    if "id=3" in url:   # an HTML intro page that links to the real file
        return (b'<html><a href="https://x/pluginfile.php/9/mod_resource/content/notes.pdf">x</a></html>',
                "view.html", "text/html")
    if "pluginfile.php" in url:
        return b"%PDF real notes", "notes.pdf", "application/pdf"
    return b"", "", ""


def test_downloads_files_and_follows_html_hop(tmp_path: Path):
    res = mr.download_resources(ACTIVITIES, tmp_path, cookies="c", fetcher=_fetcher)
    assert res["downloaded"] == 3                       # forum skipped (not downloadable)
    names = {f["file"] for f in res["files"]}
    # named from the activity, extension from the file/content-type
    assert "Lecture_1_Slides.pdf" in names
    assert "Assignment_Brief.docx" in names
    assert "An_intro_page.pdf" in names                 # followed HTML → pluginfile
    for f in res["files"]:
        assert (tmp_path / f["file"]).exists()


def test_skips_unconvertible_types(tmp_path: Path):
    acts = [{"name": "Video", "kind": "resource", "downloadable": True,
             "url": "https://x/mod/resource/view.php?id=9"}]
    res = mr.download_resources(
        acts, tmp_path, fetcher=lambda u, c: (b"\x00", "clip.mp4", "video/mp4"))
    assert res["downloaded"] == 0
    assert any("Video" in s for s in res["skipped"])


def test_errors_recorded_not_raised(tmp_path: Path):
    def boom(url, cookies):
        raise mr.ResourceError("403 Forbidden")
    res = mr.download_resources(ACTIVITIES[:1], tmp_path, fetcher=boom)
    assert res["downloaded"] == 0 and res["errors"][0]["error"] == "403 Forbidden"
