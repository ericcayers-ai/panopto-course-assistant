from __future__ import annotations

import subprocess
import sys
import zipfile
from pathlib import Path


def test_release_zip_has_root_installer_and_application(tmp_path: Path) -> None:
    project_root = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        [
            sys.executable,
            str(project_root / "scripts" / "build_release.py"),
            "--output-dir",
            str(tmp_path),
        ],
        cwd=project_root,
        check=True,
        capture_output=True,
        text=True,
    )

    archive = Path(result.stdout.strip())
    assert archive.is_file()
    with zipfile.ZipFile(archive) as release_zip:
        names = set(release_zip.namelist())

    assert "installandrun.bat" in names
    assert "install.bat" not in names
    assert "CourseAssistant/run.py" in names
    assert "CourseAssistant/requirements-browser.txt" in names


def test_portable_zip_layout_smoke(tmp_path: Path) -> None:
    """Structure check for Windows onedir ZIP (skip heavy venv+pip)."""
    project_root = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        [
            sys.executable,
            str(project_root / "scripts" / "build_windows_portable.py"),
            "--output-dir",
            str(tmp_path),
            "--skip-pip",
        ],
        cwd=project_root,
        check=True,
        capture_output=True,
        text=True,
    )
    archive = Path(result.stdout.strip().splitlines()[-1])
    assert archive.is_file()
    assert "windows" in archive.name.lower()

    with zipfile.ZipFile(archive) as zf:
        names = set(zf.namelist())

    assert "run.py" in names
    assert "CourseAssistant.cmd" in names
    assert "CourseAssistant.exe" in names
    assert any(n.startswith("app/") and n.endswith(".py") for n in names)
    assert any(n.startswith("static/") for n in names)
    assert any(n.startswith("runtime/") for n in names)
    assert "requirements.txt" in names
    assert "requirements-transcribe.txt" in names
