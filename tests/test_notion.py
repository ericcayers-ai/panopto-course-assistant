"""Tests for the Notion HTML-export -> Markdown converter."""
from __future__ import annotations

from pathlib import Path

import pytest

from app import notion

PAGE = """<!DOCTYPE html><html><head><title>Study Notes</title><style>.x{}</style></head>
<body><article class="page sans">
<header><h1 class="page-title">Study Notes</h1></header>
<div class="page-body">
<h2>Networking</h2>
<p>This is <strong>bold</strong>, <em>italic</em> and <a href="https://ex.com">a link</a>.</p>
<ul class="bulleted-list"><li>First</li><li>Second</li></ul>
<ol class="numbered-list"><li>Step one</li><li>Step two</li></ol>
<blockquote>Remember this.</blockquote>
<pre class="code"><code>print("hi")</code></pre>
<h3>Glossary</h3>
<table class="collection-content"><thead><tr><th>Term</th><th>Meaning</th></tr></thead>
<tbody><tr><td>TCP</td><td>reliable</td></tr><tr><td>UDP</td><td>fast</td></tr></tbody></table>
</div></article></body></html>"""


def test_title_and_headings():
    md, title = notion.html_to_markdown(PAGE)
    assert title == "Study Notes"
    assert md.startswith("# Study Notes")
    assert "## Networking" in md
    assert "### Glossary" in md


def test_inline_formatting():
    md, _ = notion.html_to_markdown(PAGE)
    assert "**bold**" in md
    assert "*italic*" in md
    assert "[a link](https://ex.com)" in md


def test_lists_are_tight():
    md, _ = notion.html_to_markdown(PAGE)
    assert "- First\n- Second" in md
    assert "1. Step one\n2. Step two" in md


def test_blockquote_and_code():
    md, _ = notion.html_to_markdown(PAGE)
    assert "> Remember this." in md
    assert "```" in md and 'print("hi")' in md


def test_table_markdown():
    md, _ = notion.html_to_markdown(PAGE)
    assert "| Term | Meaning |" in md
    assert "| --- | --- |" in md
    assert "| TCP | reliable |" in md


def test_style_script_stripped():
    md, _ = notion.html_to_markdown(PAGE)
    assert ".x{}" not in md


def test_no_title_falls_back_to_body():
    md, title = notion.html_to_markdown("<body><p>Just text.</p></body>")
    assert title == ""
    assert md.strip() == "Just text."


def test_convert_single_file(tmp_path: Path):
    src = tmp_path / "My Page abcdef0123456789abcdef0123456789.html"
    src.write_text(PAGE, encoding="utf-8")
    out = tmp_path / "out"
    res = notion.convert_notion_export(src, out)
    assert res["count"] == 1
    # 32-char hash stripped from filename
    assert res["files"][0].endswith("My_Page.md")
    assert "Study Notes" in (out / res["files"][0]).read_text(encoding="utf-8")


def test_convert_folder_combined(tmp_path: Path):
    export = tmp_path / "export"
    export.mkdir()
    (export / "Page One.html").write_text(PAGE, encoding="utf-8")
    (export / "Page Two.html").write_text(PAGE.replace("Study Notes", "Other"), encoding="utf-8")
    (export / "image.png").write_bytes(b"\x89PNG")  # asset, must be ignored
    out = tmp_path / "out"
    res = notion.convert_notion_export(export, out, combined=True)
    assert res["count"] == 2
    assert res["combined"]
    assert "Notion export" in (out / res["combined"]).read_text(encoding="utf-8")


def test_convert_missing_path(tmp_path: Path):
    with pytest.raises(FileNotFoundError):
        notion.convert_notion_export(tmp_path / "nope", tmp_path / "out")


def test_convert_no_html(tmp_path: Path):
    (tmp_path / "a.txt").write_text("x", encoding="utf-8")
    with pytest.raises(ValueError):
        notion.convert_notion_export(tmp_path, tmp_path / "out")


def test_notion_output_excluded_from_transcripts(tmp_path: Path):
    from app import core
    src = tmp_path / "Page.html"
    src.write_text(PAGE, encoding="utf-8")
    notion.convert_notion_export(src, tmp_path)
    # _notion/ is internal -> not listed as a transcript
    assert core.list_transcripts(tmp_path) == []


def _make_zip(path: Path, members: dict) -> Path:
    import zipfile
    with zipfile.ZipFile(path, "w") as zf:
        for name, data in members.items():
            zf.writestr(name, data)
    return path


def test_convert_notion_zip(tmp_path: Path):
    z = _make_zip(tmp_path / "export.zip", {
        "Study Notes 3174904e8e2080d4b094fa0d5ce3c8dc.html": PAGE,
        "image.png": b"\x89PNG fake",
    })
    res = notion.convert_notion_export(z, tmp_path / "out", combined=True)
    assert res["count"] == 1
    # the 32-char Notion hash and any wrapper folders are stripped from the path
    assert res["files"][0] == "_notion/Study_Notes.md"
    assert "Study Notes" in (tmp_path / "out" / res["files"][0]).read_text(encoding="utf-8")


def test_convert_notion_nested_zip(tmp_path: Path):
    # Notion wraps big exports: outer.zip -> ExportBlock-*.zip -> page.html
    inner = _make_zip(tmp_path / "ExportBlock-abc-Part-1.zip", {"Page.html": PAGE})
    outer = tmp_path / "compx.zip"
    import zipfile
    with zipfile.ZipFile(outer, "w") as zf:
        zf.write(inner, arcname="ExportBlock-abc-Part-1.zip")
    res = notion.convert_notion_export(outer, tmp_path / "out2")
    assert res["count"] == 1
    assert res["files"][0] == "_notion/Page.md"   # wrapper + _unzipped stripped
