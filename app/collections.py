"""
collections.py - every artifact derived from one lecture, in one place (§17).

The app already knows how a lecture connects to its glossary terms, keywords,
citations, notes, tags, review cards and neighbouring lectures - but each of
those lived behind its own endpoint, so the UI rendered them as unrelated
panels. This module is the join: give it a lecture path and it returns the whole
collection.

Pure aggregation over modules that already exist (``lectures``, ``glossary``,
``keywords``, ``citations``, ``search``) plus the DB. No new extraction logic,
no I/O beyond what those modules already do, so it stays cheap and testable.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

from . import citations, core, glossary, keywords, lectures, search


def _find_group(output_dir: Path, path: str) -> Optional[Dict[str, Any]]:
    """Resolve a lecture by any of its file paths, or by ``folder/stem``."""
    for g in core.list_transcripts(output_dir):
        if not core._is_transcript_group(g):
            continue
        key = (g.get("folder", "") + "/" + g["stem"]).lstrip("/")
        if path == key or path == g["stem"] or path in g["formats"].values():
            return g
    return None


def for_lecture(output_dir: Path, path: str, *, db: Any = None,
                course_id: Optional[int] = None, course_name: str = "",
                term_limit: int = 12, keyword_limit: int = 10) -> Dict[str, Any]:
    """Everything linked to one lecture.

    Raises ``LookupError`` when ``path`` names no transcript - callers map that
    onto a 404.
    """
    group = _find_group(output_dir, path)
    if group is None:
        raise LookupError(f"no transcript for {path!r}")

    meta = lectures.lecture_meta(output_dir, group)
    if not meta.get("course"):
        meta["course"] = course_name
    text = lectures.lecture_text(output_dir, group)

    terms = glossary.extract_terms(text, limit=term_limit) if text else []
    words = keywords.keywords(text, limit=keyword_limit) if text else []
    phrases = keywords.key_phrases(text, limit=keyword_limit) if text else []

    # Notes and tags are attached to the exact file the user opened (the library
    # lets you view a lecture's .txt, .md or .json), so look them up under that
    # path. Only a folder/stem key falls back to the lecture's primary file.
    canonical = meta["path"]
    lookup = path if path in group["formats"].values() else canonical

    notes: List[Dict[str, Any]] = []
    tags: List[str] = []
    reviews = 0
    if db is not None:
        notes = [{"id": r["id"], "body": r["body"], "bookmark": bool(r["bookmark"]),
                  "timestamp_s": r["timestamp_s"], "created_at": r["created_at"]}
                 for r in db.list_notes(path=lookup)]
        tags = list(db.tags_for_path(lookup))
        # Review cards don't carry a lecture ref, so scope by course and let the
        # count stand for "cards you have scheduled", not "cards from this file".
        if course_id is not None:
            reviews = len(db.list_review_items(course_id=course_id))

    return {
        "path": lookup,
        "canonical_path": canonical,
        "lecture": meta,
        "formats": group["formats"],
        "glossary": terms,
        "keywords": words,
        "key_phrases": phrases,
        "citations": citations.cite_all(meta),
        "notes": notes,
        "tags": tags,
        "related": search.related(output_dir, lookup, limit=5),
        "counts": {
            "glossary": len(terms),
            "keywords": len(words),
            "key_phrases": len(phrases),
            "notes": len(notes),
            "tags": len(tags),
            "formats": len(group["formats"]),
            "related": 0,          # filled below; keeps the key order stable
            "reviews_due": reviews,
        },
    }


def build(output_dir: Path, path: str, **kw: Any) -> Dict[str, Any]:
    """``for_lecture`` with the ``related`` count filled in."""
    out = for_lecture(output_dir, path, **kw)
    out["counts"]["related"] = len(out["related"])
    return out
