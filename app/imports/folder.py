"""
imports/folder.py — recursive, structure-preserving folder import (§7).

Point at a folder of mixed course material and get back a categorised manifest:
documents, media (for transcription), subtitles (reused as-is, no transcription),
and a per-file week/topic guess from the path. ``scan`` is pure (no writes) so it
doubles as the preflight; ``import_folder`` records documents into the DB index.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

from .. import core

MEDIA_EXTS = {".mp4", ".mkv", ".mov", ".avi", ".webm", ".m4v",
              ".mp3", ".m4a", ".wav", ".aac", ".flac", ".ogg"}
SUBTITLE_EXTS = {".srt", ".vtt"}
DOC_EXTS = {e.lower() for e in core.DOC_EXTS}

# Folders that are never course content (our own outputs / VCS / system junk).
_SKIP_DIRS = {"_docs", "_flashcards", "_exports", ".git", "__pycache__", "node_modules"}


def _categorize(ext: str) -> str:
    ext = ext.lower()
    if ext in MEDIA_EXTS:
        return "media"
    if ext in SUBTITLE_EXTS:
        return "subtitle"
    if ext in DOC_EXTS:
        return "document"
    return "other"


def scan(folder: Path, *, include_subfolders: bool = True) -> Dict[str, Any]:
    """Walk ``folder`` and classify every file. No writes — safe as a preflight."""
    folder = Path(folder)
    if not folder.is_dir():
        raise NotADirectoryError(f"Not a folder: {folder}")
    items: List[Dict[str, Any]] = []
    paths = folder.rglob("*") if include_subfolders else folder.glob("*")
    for p in sorted(paths):
        if not p.is_file():
            continue
        if any(part in _SKIP_DIRS for part in p.relative_to(folder).parts):
            continue
        kind = _categorize(p.suffix)
        if kind == "other":
            continue
        rel = p.relative_to(folder).as_posix()
        title = p.stem
        items.append({
            "path": str(p), "rel": rel, "title": title, "kind": kind,
            "ext": p.suffix.lower(),
            "size": p.stat().st_size,
            "week": core.infer_week(rel) or core.infer_week(title),
            "topic": core.infer_topic(title),
        })
    counts: Dict[str, int] = {}
    for it in items:
        counts[it["kind"]] = counts.get(it["kind"], 0) + 1
    total_size = sum(it["size"] for it in items)
    return {"folder": str(folder), "count": len(items), "counts": counts,
            "total_size": total_size, "items": items}


def import_folder(db, output_dir: Path, folder: Path, *, course_id: Optional[int],
                 include_subfolders: bool = True) -> Dict[str, Any]:
    """Record documents/subtitles from a folder into the DB index. Media files are
    *listed* (with a transcription hint) but not transcribed here — that stays an
    explicit, queued job. Returns the scan manifest plus how many were indexed."""
    manifest = scan(folder, include_subfolders=include_subfolders)
    indexed = 0
    for it in manifest["items"]:
        if it["kind"] in ("document", "subtitle"):
            db.insert_document(course_id, title=it["title"], path=it["path"],
                             type=it["kind"], import_source="folder")
            indexed += 1
    manifest["indexed"] = indexed
    manifest["media_pending_transcription"] = manifest["counts"].get("media", 0)
    return manifest
