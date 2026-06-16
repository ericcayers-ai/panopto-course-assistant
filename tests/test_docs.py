"""Tests for the versatile MarkItDown document converter (convert_documents).

A fake converter is injected so these run without markitdown installed.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from app import core


def fake_converter(path: str) -> str:
    return f"# Converted\n\nContent of {Path(path).name}\n"


def _make_docs(folder: Path):
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "slides.pdf").write_bytes(b"%PDF-1.4 fake")
    (folder / "deck.pptx").write_bytes(b"PK fake pptx")
    sub = folder / "week2"
    sub.mkdir()
    (sub / "notes.docx").write_bytes(b"PK fake docx")
    (folder / "ignore.png").write_bytes(b"\x89PNG")  # not a doc type


def test_convert_ai_target(tmp_path):
    src = tmp_path / "course"
    _make_docs(src)
    out = tmp_path / "out"
    res = core.convert_documents(src, out, converter=fake_converter, target="ai")
    assert res["count"] == 3  # pdf, pptx, docx (png ignored)
    assert all(f["md"].startswith("_docs/") for f in res["files"] if not f.get("error"))
    # nested structure preserved
    assert any("week2/" in f["md"] for f in res["files"])
    # files exist and excluded from transcript listing (_docs is internal)
    assert core.list_transcripts(out) == []


def test_convert_combined_pack(tmp_path):
    src = tmp_path / "course"
    _make_docs(src)
    out = tmp_path / "out"
    res = core.convert_documents(src, out, converter=fake_converter, combined=True)
    assert res["combined"] == "_docs/documents_pack.md"
    pack = (out / res["combined"]).read_text(encoding="utf-8")
    assert "# Documents" in pack


def test_convert_ext_filter(tmp_path):
    src = tmp_path / "course"
    _make_docs(src)
    out = tmp_path / "out"
    res = core.convert_documents(src, out, exts=[".pdf"], converter=fake_converter)
    assert res["count"] == 1
    assert res["files"][0]["md"].endswith("slides.md")


def test_convert_no_subfolders(tmp_path):
    src = tmp_path / "course"
    _make_docs(src)
    out = tmp_path / "out"
    res = core.convert_documents(src, out, include_subfolders=False, converter=fake_converter)
    # docx in week2/ excluded
    assert res["count"] == 2


def test_convert_copy_target(tmp_path):
    src = tmp_path / "Slides"
    _make_docs(src)
    res = core.convert_documents(src, tmp_path, converter=fake_converter, target="copy")
    copy_root = tmp_path / "Slides_copy"
    assert copy_root.is_dir()
    assert (copy_root / "slides.md").exists()
    assert res["combined"] is None  # combined only for ai target


def test_convert_single_file(tmp_path):
    f = tmp_path / "report.docx"
    f.write_bytes(b"PK fake")
    out = tmp_path / "out"
    res = core.convert_documents(f, out, converter=fake_converter)
    assert res["count"] == 1
    assert res["files"][0]["md"] == "_docs/report.md"


def test_convert_unsupported_single_file(tmp_path):
    f = tmp_path / "image.png"
    f.write_bytes(b"\x89PNG")
    with pytest.raises(ValueError):
        core.convert_documents(f, tmp_path, converter=fake_converter)


def test_convert_overwrite(tmp_path):
    f = tmp_path / "a.pdf"
    f.write_bytes(b"%PDF fake")
    out = tmp_path / "out"
    core.convert_documents(f, out, converter=fake_converter)
    target = out / "_docs" / "a.md"
    target.write_text("STALE", encoding="utf-8")
    # without overwrite -> keeps existing
    core.convert_documents(f, out, converter=fake_converter, overwrite=False)
    assert target.read_text(encoding="utf-8") == "STALE"
    # with overwrite -> regenerated
    core.convert_documents(f, out, converter=fake_converter, overwrite=True)
    assert "Converted" in target.read_text(encoding="utf-8")


def test_convert_missing_path(tmp_path):
    with pytest.raises(FileNotFoundError):
        core.convert_documents(tmp_path / "nope", tmp_path, converter=fake_converter)


def test_convert_empty_folder(tmp_path):
    (tmp_path / "empty").mkdir()
    with pytest.raises(ValueError):
        core.convert_documents(tmp_path / "empty", tmp_path, converter=fake_converter)


def test_pdf_tree_backcompat(tmp_path):
    src = tmp_path / "Slides"
    _make_docs(src)
    pairs = core.convert_documents(  # sanity: pdf-only copy
        src, tmp_path, exts=[".pdf"], converter=fake_converter, target="copy")
    assert pairs["count"] == 1
