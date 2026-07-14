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

    assert "install.bat" in names
    assert "CourseAssistant/run.py" in names
