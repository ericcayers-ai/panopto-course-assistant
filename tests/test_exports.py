"""§9 export engine: presets, scopes, preview-writes-nothing, course archive."""
from __future__ import annotations

import zipfile
from pathlib import Path

import pytest

from app import core, database, exports as export_engine


def _seed(tmp: Path, title: str, text: str = "content here", organize: str = "week"):
    item = core.LectureItem(title=title, url="u",
                            pub_date="Mon, 09 Mar 2026 02:13:40 GMT")
    core.write_outputs(item, [{"start": 0, "end": 5, "text": text}], text,
                       core.output_dir_for(tmp, item, organize),
                       ["txt", "json", "summary"], 30, {})


def test_resolve_targets_and_unknown():
    assert export_engine.resolve_targets(preset="revision") == ["notebooklm", "flashcards"]
    assert export_engine.resolve_targets(target="archive") == ["archive"]
    with pytest.raises(ValueError):
        export_engine.resolve_targets(preset="nonsense")


def test_preview_writes_nothing(tmp_path: Path):
    _seed(tmp_path, "Week1_Intro")
    _seed(tmp_path, "Week2_TCP")
    before = {p.name for p in tmp_path.rglob("*")}
    pv = export_engine.preview(tmp_path, preset="revision", scope="course")
    assert pv["writes_nothing"] is True
    assert pv["lectures_in_scope"] == 2
    assert {a["target"] for a in pv["artifacts"]} == {"notebooklm", "flashcards"}
    after = {p.name for p in tmp_path.rglob("*")}
    assert before == after                       # nothing new on disk


def test_scope_week_narrows_selection(tmp_path: Path):
    _seed(tmp_path, "Week1_Intro")
    _seed(tmp_path, "Week2_TCP")
    pv = export_engine.preview(tmp_path, preset="revision", scope="week", scope_target="2")
    assert pv["lectures_in_scope"] == 1


def test_export_notebooklm_runs(tmp_path: Path):
    _seed(tmp_path, "Week1_Intro")
    out = export_engine.export(tmp_path, target="notebooklm", scope="course",
                              course="COMPX234")
    assert out["results"]["notebooklm"]["count"] == 1


def test_export_tolerates_corrupt_transcript_json(tmp_path: Path):
    """An empty/corrupt .json (e.g. an interrupted transcription) must not 500
    the export - it falls back to the .txt source."""
    _seed(tmp_path, "Week1_Intro", "the transmission control protocol")
    # corrupt the json output for that lecture
    for p in tmp_path.rglob("*.json"):
        p.write_text("", encoding="utf-8")
    nb = core.export_notebooklm(tmp_path, combined=True, course="COMPX234")
    assert nb["count"] == 1                       # recovered from .txt fallback
    fmt = core.export_formats(tmp_path, formats=["srt"])
    assert isinstance(fmt, dict)                   # did not raise


def test_course_archive_roundtrips(tmp_path: Path):
    db = database.Database(tmp_path / "t.db")
    cid = db.create_course("COMPX234", code="COMPX234")
    _seed(tmp_path, "Week1_Intro")
    res = export_engine.course_archive(tmp_path, db=db, course_id=cid, course="COMPX234")
    archive = tmp_path / res["path"]
    assert archive.exists()
    with zipfile.ZipFile(archive) as zf:
        names = zf.namelist()
        assert "manifest.json" in names
        assert any(n.endswith(".txt") for n in names)   # library file bundled
    db.close()
