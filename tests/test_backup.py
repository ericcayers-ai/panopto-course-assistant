"""§11 packaging: environment checker + portable backup/restore round-trip."""
from __future__ import annotations

from pathlib import Path

import pytest

from app import backup, core, database


def test_environment_report_shape(tmp_path: Path):
    rep = backup.environment_report(tmp_path)
    assert rep["ready_for_core"] is True
    assert "engines" in rep and "optional" in rep
    assert isinstance(rep["missing"], list)
    assert rep["python"]


def _seed(tmp: Path):
    item = core.LectureItem(title="Week1_Intro", url="u",
                            pub_date="Mon, 09 Mar 2026 02:13:40 GMT")
    core.write_outputs(item, [{"start": 0, "end": 5, "text": "hi"}], "hi",
                       core.output_dir_for(tmp, item, "week"), ["txt", "json"], 30, {})


def test_backup_excludes_secrets(tmp_path: Path):
    _seed(tmp_path)
    (tmp_path / ".secrets.json").write_text("{}")
    (tmp_path / ".secrets.key").write_bytes(b"key")
    res = backup.create_backup(tmp_path)
    import zipfile
    with zipfile.ZipFile(res["path"]) as zf:
        names = zf.namelist()
    assert not any(".secrets" in n for n in names)
    assert any(n.endswith(".txt") for n in names)


def test_backup_restore_roundtrip(tmp_path: Path):
    src = tmp_path / "src"; src.mkdir()
    _seed(src)
    res = backup.create_backup(src)
    # restore into a fresh, empty dir
    dest = tmp_path / "dest"
    out = backup.restore_backup(res["path"], dest)
    assert out["restored"] >= 1
    assert any(p.name.endswith(".txt") for p in dest.rglob("*"))
    # re-restore is a safe no-op merge (files already present -> skipped)
    out2 = backup.restore_backup(res["path"], dest)
    assert out2["restored"] == 0 and out2["skipped"] >= 1


def test_db_restores_and_migrates(tmp_path: Path):
    # a backed-up DB re-opens and migrates forward on restore
    src = tmp_path / "src"; src.mkdir()
    d = database.Database(src / "course_assistant.db")
    d.create_course("COMPX234")
    d.close()
    res = backup.create_backup(src)
    dest = tmp_path / "dest"
    backup.restore_backup(res["path"], dest)
    d2 = database.Database(dest / "course_assistant.db")
    assert d2.count_courses() == 1
    assert d2.schema_version() == database.SCHEMA_VERSION
    d2.close()
