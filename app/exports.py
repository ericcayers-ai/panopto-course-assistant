"""
exports.py - preset-driven, scoped export engine (§9).

A thin aggregator over the existing exporters in :mod:`app.core` (NotebookLM,
all-sources AI pack, subtitle/format generation), :mod:`app.flashcards` (Anki)
and :mod:`app.study` (Notion CSV). It adds three things the roadmap asks for:

* **Presets** - ``revision | ai | exam | notion | anki | archive`` bundle a set of
  targets so a student picks an intent, not a format.
* **Scope** - ``lecture | week | topic | course | all`` selects which lectures feed
  the export (computed from the §2 index).
* **Preview** - list exactly what *would* be written, writing nothing, so the user
  confirms first.

Plus a portable **course archive** (zip of metadata + library + settings) that
round-trips with §1 import and §11 backup/restore.
"""
from __future__ import annotations

import json
import zipfile
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import core, flashcards, study, search

SCOPES = ("lecture", "week", "topic", "course", "all")

# Each preset maps to the underlying export targets it runs.
PRESET_TARGETS: Dict[str, List[str]] = {
    "revision": ["notebooklm", "flashcards"],
    "ai": ["all_sources"],
    "exam": ["notebooklm", "flashcards", "subtitles"],
    "notion": ["notion_csv"],
    "anki": ["flashcards"],
    "archive": ["archive"],
    "suites": ["obsidian_suite", "notion_suite", "onenote_suite"],
}

# Single targets are also addressable directly.
ALL_TARGETS = ["notebooklm", "all_sources", "flashcards", "subtitles",
               "notion_csv", "archive",
               "obsidian_suite", "notion_suite", "onenote_suite"]

def _selection_for_scope(output_dir: Path, scope: str, target: str = "") -> Optional[List[str]]:
    """Lecture stems (``folder/stem``) feeding the export for a scope.

    ``None`` means "everything" (course/all). Lecture/week/topic narrow it using
    the §2 index, so every exporter shares one scoping rule.
    """
    if scope in ("course", "all"):
        return None
    items = [it for it in search.build_index(output_dir) if it["type"] == "transcript"]
    if scope == "lecture" and target:
        items = [it for it in items if it["path"] == target or it["title"] == target]
    elif scope == "week" and target:
        try:
            wk = int(target)
            items = [it for it in items if it["week"] == wk]
        except ValueError:
            items = []
    elif scope == "topic" and target:
        items = [it for it in items if (it["topic"] or "").lower() == target.lower()]
    out: List[str] = []
    for it in items:
        stem = Path(it["path"]).stem
        folder = str(Path(it["path"]).parent).replace("\\", "/")
        out.append(f"{folder}/{stem}".strip("/") if folder not in (".", "") else stem)
    return out


def resolve_targets(preset: str = "", target: str = "") -> List[str]:
    if preset:
        if preset not in PRESET_TARGETS:
            raise ValueError(f"unknown preset {preset!r}; choose from {list(PRESET_TARGETS)}")
        return PRESET_TARGETS[preset]
    if target:
        if target not in ALL_TARGETS:
            raise ValueError(f"unknown target {target!r}; choose from {ALL_TARGETS}")
        return [target]
    raise ValueError("specify a preset or a target")


def preview(output_dir: Path, *, preset: str = "", target: str = "",
           scope: str = "course", scope_target: str = "", course: str = "") -> Dict[str, Any]:
    """List what an export would produce - writes nothing."""
    if scope not in SCOPES:
        raise ValueError(f"scope must be one of {SCOPES}")
    targets = resolve_targets(preset, target)
    selection = _selection_for_scope(output_dir, scope, scope_target)
    n_lectures = (len(selection) if selection is not None
                  else len([it for it in search.build_index(output_dir)
                            if it["type"] == "transcript"]))
    artifacts = []
    for t in targets:
        artifacts.append({"target": t, "estimated_items": _estimate(t, n_lectures, output_dir)})
    return {"preset": preset, "targets": targets, "scope": scope,
            "lectures_in_scope": n_lectures, "artifacts": artifacts,
            "writes_nothing": True}


def _estimate(target: str, n_lectures: int, output_dir: Path) -> int:
    if target in ("notebooklm", "subtitles"):
        return n_lectures
    if target == "flashcards":
        return n_lectures  # ~one deck section per lecture
    if target in ("all_sources", "notion_csv", "archive"):
        return 1
    if target in ("obsidian_suite", "notion_suite", "onenote_suite"):
        return 1
    return 0

def export(output_dir: Path, *, preset: str = "", target: str = "",
          scope: str = "course", scope_target: str = "", course: str = "",
          db: Any = None, course_id: Optional[int] = None) -> Dict[str, Any]:
    """Run an export. Returns the artifacts written per target."""
    if scope not in SCOPES:
        raise ValueError(f"scope must be one of {SCOPES}")
    targets = resolve_targets(preset, target)
    selection = _selection_for_scope(output_dir, scope, scope_target)
    results: Dict[str, Any] = {}

    for t in targets:
        if t == "notebooklm":
            results[t] = core.export_notebooklm(output_dir, selection=selection,
                                               combined=True, course=course)
        elif t == "all_sources":
            results[t] = core.export_all_sources(output_dir, combined=True, course=course)
        elif t == "subtitles":
            results[t] = core.export_formats(output_dir, formats=["srt", "vtt"])
        elif t == "flashcards":
            cards = flashcards.generate_from_library(output_dir, selection=selection,
                                                    course=course)
            results[t] = flashcards.write_deck(output_dir, cards, deck="export")
        elif t == "notion_csv":
            results[t] = study.write_study_database(output_dir, course=course)
        elif t == "archive":
            results[t] = course_archive(output_dir, db=db, course_id=course_id, course=course)
        elif t in ("obsidian_suite", "notion_suite", "onenote_suite"):
            from . import suites
            fmt = t.replace("_suite", "")
            dest = output_dir / "_suites" / fmt
            results[t] = suites.build_suite_tree(
                dest, format=fmt, title=course or "Semester plan",
                library_dir=output_dir,
            )
    if db is not None and course_id is not None:
        for t, r in results.items():
            path = r.get("dest") or r.get("combined") or r.get("path") or ""
            try:
                db.execute(
                    "INSERT INTO exports(course_id, type, path, created_at) VALUES(?,?,?,?)",
                    (course_id, f"{preset or target}:{t}", str(path), core.now_iso()))
            except Exception:
                pass
    return {"preset": preset, "targets": targets, "scope": scope, "results": results}


def course_archive(output_dir: Path, *, db: Any = None, course_id: Optional[int] = None,
                  course: str = "") -> Dict[str, Any]:
    """Portable ``.zip`` = course metadata + library files + settings, so a course
    can move to another machine and re-import losslessly (§9/§11)."""
    dest = core.ensure_dir(output_dir / "_exports")
    name = core.safe_name(course or f"course_{course_id or 'all'}") + "_archive.zip"
    archive_path = dest / name

    manifest: Dict[str, Any] = {"course": course, "course_id": course_id,
                                "created_at": core.now_iso(), "files": []}
    if db is not None and course_id is not None:
        row = db.get_course(course_id)
        if row:
            manifest["metadata"] = {k: row[k] for k in row.keys()}
        manifest["transcripts"] = [dict(r) for r in db.list_transcripts(course_id)]
        manifest["documents"] = [dict(r) for r in db.list_documents(course_id)]
        manifest["assessments"] = [dict(r) for r in db.list_assessments(course_id)]

    library = core.list_library(output_dir)
    with zipfile.ZipFile(archive_path, "w", zipfile.ZIP_DEFLATED) as zf:
        seen: set = set()
        for cat in library.get("categories", {}).values():
            for entry in cat:
                # transcripts expose a {format: relpath} map; everything else a single path.
                rels = list((entry.get("formats") or {}).values())
                if entry.get("path"):
                    rels.append(entry["path"])
                for rel in rels:
                    if not rel or rel in seen:
                        continue
                    p = output_dir / rel
                    if p.is_file() and p != archive_path:
                        zf.write(p, arcname=rel)
                        manifest["files"].append(rel)
                        seen.add(rel)
        # Bundle extracted image assets so they travel with the Markdown.
        # New format: a single *_assets.zip per document (preferred).
        for zip_f in output_dir.rglob("*_assets.zip"):
            if not zip_f.is_file():
                continue
            rel = zip_f.relative_to(output_dir).as_posix()
            if rel in seen:
                continue
            zf.write(zip_f, arcname=rel)
            manifest["files"].append(rel)
            seen.add(rel)
        # Legacy format: *_assets/ directory (kept for backwards-compat).
        for assets in output_dir.rglob("*_assets"):
            if not assets.is_dir():
                continue
            for img in assets.rglob("*"):
                if not img.is_file():
                    continue
                rel = img.relative_to(output_dir).as_posix()
                if rel in seen:
                    continue
                zf.write(img, arcname=rel)
                manifest["files"].append(rel)
                seen.add(rel)
        zf.writestr("manifest.json", json.dumps(manifest, indent=2, default=str))

    return {"path": archive_path.relative_to(output_dir).as_posix(),
            "dest": str(archive_path), "file_count": len(manifest["files"])}
