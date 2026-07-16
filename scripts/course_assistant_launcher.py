"""Thin Windows launcher for the portable onedir layout.

Frozen as ``CourseAssistant.exe`` beside ``runtime\\`` and the app tree.
Sets ``CA_ROOT`` / ``OPEN_BROWSER`` and execs ``runtime\\python.exe run.py``.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


def _root() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    # Dev: scripts/ → project root
    return Path(__file__).resolve().parent.parent


def main() -> int:
    root = _root()
    runtime = root / "runtime"
    py = runtime / "python.exe"
    if not py.is_file():
        # venv-style layout
        py = runtime / "Scripts" / "python.exe"
    run_py = root / "run.py"
    if not py.is_file():
        print(f"Course Assistant: runtime Python not found under {runtime}", file=sys.stderr)
        return 1
    if not run_py.is_file():
        print(f"Course Assistant: run.py missing in {root}", file=sys.stderr)
        return 1

    env = os.environ.copy()
    env["CA_ROOT"] = str(root)
    env["CA_PORTABLE"] = "1"
    env.setdefault("OPEN_BROWSER", "1")
    # Prefer writable library beside the install; context falls back to LOCALAPPDATA.
    env.setdefault("PANOPTO_OUTPUT", str(root / "transcripts"))

    return subprocess.call([str(py), str(run_py)], cwd=str(root), env=env)


if __name__ == "__main__":
    raise SystemExit(main())
