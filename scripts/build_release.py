"""Create a distributable Course Assistant release ZIP."""

from __future__ import annotations

import argparse
import re
import shutil
import tempfile
import zipfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
VERSION_FILE = PROJECT_ROOT / "app" / "__init__.py"
EXCLUDED_PARTS = {
    ".claude",
    ".cursor",
    ".git",
    ".pytest_cache",
    ".venv",
    "__pycache__",
    "agent-transcripts",
    "dist",
    "graphify-out",
    "tests",
    "transcripts",
    "_moodledl_ref",
}
EXCLUDED_FILES = {"Moodle-DL-main.zip"}


def read_version(version_file: Path = VERSION_FILE) -> str:
    """Return the package version declared in app/__init__.py."""
    match = re.search(
        r"^__version__\s*=\s*['\"]([^'\"]+)['\"]",
        version_file.read_text(encoding="utf-8"),
        re.MULTILINE,
    )
    if not match:
        raise ValueError(f"Could not find __version__ in {version_file}")
    return match.group(1)


def _ignore_release_files(_directory: str, names: list[str]) -> set[str]:
    ignored = {name for name in names if name in EXCLUDED_PARTS or name in EXCLUDED_FILES}
    ignored.update(name for name in names if name.endswith(".pyc"))
    return ignored


def build_release(output_dir: Path | None = None) -> Path:
    """Build the release archive and return its path."""
    version = read_version()
    output_dir = output_dir or PROJECT_ROOT / "dist"
    output_dir.mkdir(parents=True, exist_ok=True)
    archive = output_dir / f"CourseAssistant-v{version}.zip"

    with tempfile.TemporaryDirectory(prefix="course-assistant-release-") as temp_dir:
        staging_root = Path(temp_dir)
        app_root = staging_root / "CourseAssistant"
        launcher = PROJECT_ROOT / "installandrun.bat"
        if not launcher.is_file():
            raise FileNotFoundError(f"Release launcher missing: {launcher}")
        shutil.copy2(launcher, staging_root / "installandrun.bat")
        shutil.copytree(PROJECT_ROOT, app_root, ignore=_ignore_release_files)

        with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as zip_file:
            for path in sorted(staging_root.rglob("*")):
                if path.is_file():
                    zip_file.write(path, path.relative_to(staging_root).as_posix())

    return archive


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, help="Directory for the release ZIP")
    parser.add_argument(
        "--portable", action="store_true",
        help="Also build the Windows portable onedir ZIP (CourseAssistant.exe + runtime)",
    )
    parser.add_argument(
        "--portable-skip-pip", action="store_true",
        help="With --portable: skip venv install (layout smoke only)",
    )
    args = parser.parse_args()
    archive = build_release(args.output_dir)
    print(archive)
    if args.portable:
        # Import sibling module when invoked as ``python scripts/build_release.py``.
        import importlib.util
        portable_path = Path(__file__).resolve().parent / "build_windows_portable.py"
        spec = importlib.util.spec_from_file_location("build_windows_portable", portable_path)
        mod = importlib.util.module_from_spec(spec)
        assert spec and spec.loader
        spec.loader.exec_module(mod)
        print(mod.build_portable_zip(args.output_dir, skip_pip=args.portable_skip_pip))


if __name__ == "__main__":
    main()
