"""Writable OUTPUT_DIR selection for portable / read-only installs."""
from __future__ import annotations

from pathlib import Path

from app import context


def test_default_output_dir_falls_back_when_env_unwritable(tmp_path, monkeypatch):
    bad = tmp_path / "readonly-out"
    bad.mkdir()
    app_root = tmp_path / "app-root"
    good = tmp_path / "CourseAssistant" / "transcripts"
    monkeypatch.setenv("PANOPTO_OUTPUT", str(bad))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))

    def probe(p: Path) -> bool:
        resolved = Path(p).resolve()
        # Env path and beside-app path are both unwritable (e.g. read-only media).
        return resolved not in {bad.resolve(), (app_root / "transcripts").resolve()}

    monkeypatch.setattr(context, "_probe_writable", probe)
    chosen = context._default_output_dir(app_root)
    assert chosen == good.resolve()


def test_default_output_dir_prefers_writable_env(tmp_path, monkeypatch):
    out = tmp_path / "lib"
    monkeypatch.setenv("PANOPTO_OUTPUT", str(out))
    chosen = context._default_output_dir(tmp_path / "app-root")
    assert chosen == out.resolve()
    assert out.is_dir()
