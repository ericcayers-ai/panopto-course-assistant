"""
backup.py - environment checker + portable backup/restore (§11).

Two non-technical-friendly safety nets:

* :func:`environment_report` - what's present/missing on this machine (Python,
  platform, transcription engines, optional deps, free disk), so the first-run
  wizard / "why is X disabled?" panel has one source of truth.
* :func:`create_backup` / :func:`restore_backup` - zip up *everything* (the DB +
  the whole library) into one portable file and put it back on another machine.
  Pairs with the §9 course archive and the §1 migrations (restore re-opens the DB,
  which migrates forward automatically).

Secrets are deliberately **excluded** from backups (they live in the OS keyring /
an encrypted sidecar) so a backup file is safe to copy around.
"""
from __future__ import annotations

import platform
import shutil
import sys
import zipfile
from pathlib import Path
from typing import Any, Dict

from . import transcribe

# Never bundle secret material or transient WAL files into a portable backup.
_EXCLUDE_NAMES = {".secrets.json", ".secrets.key", ".secret_names.json"}
_EXCLUDE_SUFFIXES = {".db-wal", ".db-shm"}


def _have(mod: str) -> bool:
    import importlib.util
    try:
        return importlib.util.find_spec(mod) is not None
    except Exception:
        return False


def environment_report(output_dir: Path) -> Dict[str, Any]:
    """A single snapshot of what this machine can do (present/missing)."""
    eng = transcribe.engine_status()
    try:
        usage = shutil.disk_usage(str(output_dir))
        free_gb = round(usage.free / (1024 ** 3), 1)
    except Exception:
        free_gb = None
    optional = {name: _have(mod) for name, mod in {
        "transcription (faster-whisper)": "faster_whisper",
        "transcription (whisper)": "whisper",
        "document conversion (markitdown)": "markitdown",
        "video download (yt-dlp)": "yt_dlp",
        "secret keyring": "keyring",
        "encryption (cryptography)": "cryptography",
        "GPU (torch)": "torch",
    }.items()}
    missing = [k for k, v in optional.items() if not v]
    return {
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "machine": platform.machine(),
        "output_dir": str(output_dir),
        "free_disk_gb": free_gb,
        "engines": eng["engines"],
        "any_engine": eng["any_engine"],
        "cuda": eng["cuda"],
        "optional": optional,
        "missing": missing,
        "ready_for_core": True,                 # core flow needs none of the above
        "ready_for_transcription": eng["any_engine"],
    }


def create_backup(output_dir: Path, dest_dir: Path | None = None,
                 name: str = "course-assistant-backup.zip") -> Dict[str, Any]:
    """Zip the DB + whole library into a single portable file (secrets excluded)."""
    output_dir = Path(output_dir)
    dest_dir = Path(dest_dir) if dest_dir else output_dir / "_backups"
    dest_dir.mkdir(parents=True, exist_ok=True)
    archive = dest_dir / name

    count = 0
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as zf:
        for p in sorted(output_dir.rglob("*")):
            if not p.is_file() or p == archive:
                continue
            if p.name in _EXCLUDE_NAMES or p.suffix in _EXCLUDE_SUFFIXES:
                continue
            if "_backups" in p.relative_to(output_dir).parts:
                continue
            zf.write(p, arcname=p.relative_to(output_dir).as_posix())
            count += 1
    return {"path": str(archive), "file_count": count}


def restore_backup(backup_path: Path, output_dir: Path, *,
                  overwrite: bool = False) -> Dict[str, Any]:
    """Unpack a backup into ``output_dir``. With ``overwrite=False`` (default),
    existing files are kept (a safe merge); set it to replace them."""
    backup_path = Path(backup_path)
    if not backup_path.is_file():
        raise FileNotFoundError(str(backup_path))
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    restored, skipped = 0, 0
    with zipfile.ZipFile(backup_path) as zf:
        for member in zf.namelist():
            # Guard against path traversal in a crafted archive.
            target = (output_dir / member).resolve()
            if not str(target).startswith(str(output_dir.resolve())):
                continue
            if target.exists() and not overwrite:
                skipped += 1
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(member) as src, open(target, "wb") as dst:
                shutil.copyfileobj(src, dst)
            restored += 1
    return {"restored": restored, "skipped": skipped, "output_dir": str(output_dir)}
