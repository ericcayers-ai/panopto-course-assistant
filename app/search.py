"""
search.py — unified library index + search (§2).

Turns the categorised filesystem listing (``core.list_library``) into a single
flat, **filterable / sortable** index with inferred metadata (week, topic, type,
tags, mtime), and layers fuzzy + metadata-aware search and related-content on
top. Computed on demand from disk (the DB index is a future optimisation); fast
enough for a typical course.
"""
from __future__ import annotations

import difflib
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import core

# Category -> a coarse "type" used for the type filter / badges.
_CATEGORY_TYPE = {
    "transcripts": "transcript",
    "documents": "document",
    "notion": "notion",
    "exports": "export",
    "others": "other",
}


def _mtime(output_dir: Path, rel: str) -> float:
    try:
        return (output_dir / rel).stat().st_mtime
    except OSError:
        return 0.0


def _tags_for(title: str, week: Optional[int], topic: str, ftype: str) -> List[str]:
    tags = [ftype]
    if week is not None:
        tags.append(f"week-{week}")
    if topic and topic != "uncategorized":
        tags.append(topic)
    # crude keyword tags: capitalised terms / acronyms in the title
    for m in re.findall(r"\b([A-Z]{2,}|[A-Z][a-z]{3,})\b", title or ""):
        t = m.lower()
        if t not in tags:
            tags.append(t)
    return tags


def build_index(output_dir: Path) -> List[Dict[str, Any]]:
    """Flatten the library into indexed items with inferred metadata."""
    lib = core.list_library(output_dir)
    items: List[Dict[str, Any]] = []
    for category, entries in lib["categories"].items():
        ftype = _CATEGORY_TYPE.get(category, category)
        for e in entries:
            if category == "transcripts":          # transcript groups
                title = e["stem"]
                folder = e.get("folder", "")
                rel = (e["formats"].get("txt") or e["formats"].get("md")
                       or next(iter(e["formats"].values()), ""))
            else:                                   # file entries
                title = Path(e["name"]).stem
                folder = str(Path(e["path"]).parent)
                rel = e["path"]
            week = core.infer_week(title)
            topic = core.infer_topic(title)
            items.append({
                "title": title,
                "path": rel,
                "folder": folder,
                "type": ftype,
                "week": week,
                "topic": topic,
                "tags": _tags_for(title, week, topic, ftype),
                "mtime": _mtime(output_dir, rel),
            })
    return items


def _matches_filters(item: Dict[str, Any], week: Optional[int], ftype: str,
                    tag: str) -> bool:
    if week is not None and item["week"] != week:
        return False
    if ftype and item["type"] != ftype:
        return False
    if tag and tag.lower() not in [t.lower() for t in item["tags"]]:
        return False
    return True


def _sort_key(sort: str):
    if sort == "name":
        return (lambda it: it["title"].lower()), False
    if sort == "week":
        return (lambda it: (it["week"] is None, it["week"] or 0, it["title"].lower())), False
    # default: newest first by mtime
    return (lambda it: it["mtime"]), True


def library_view(output_dir: Path, *, week: Optional[int] = None, ftype: str = "",
                tag: str = "", q: str = "", sort: str = "date") -> Dict[str, Any]:
    """Filtered + sorted flat index. ``q`` does a cheap title/tag substring match
    (full-text content search is :func:`search`)."""
    items = build_index(output_dir)
    needle = (q or "").strip().lower()
    if needle:
        items = [it for it in items
                 if needle in it["title"].lower()
                 or any(needle in t.lower() for t in it["tags"])]
    items = [it for it in items if _matches_filters(it, week, ftype, tag)]
    key, reverse = _sort_key(sort)
    items.sort(key=key, reverse=reverse)
    by_type: Dict[str, int] = {}
    for it in items:
        by_type[it["type"]] = by_type.get(it["type"], 0) + 1
    return {"count": len(items), "by_type": by_type, "items": items}


def search(output_dir: Path, query: str, *, week: Optional[int] = None,
           ftype: str = "", fuzzy: bool = True) -> List[Dict[str, Any]]:
    """Full-text search with metadata filters and a fuzzy title fallback.

    Exact substring hits (with content snippets) come first via the existing
    transcript search; if none match and ``fuzzy`` is on, fall back to fuzzy
    title matching across the whole index so near-miss queries still find things.
    """
    query = (query or "").strip()
    if not query:
        return []
    index = build_index(output_dir)
    by_path = {it["path"]: it for it in index}

    results: List[Dict[str, Any]] = []
    for r in core.search_transcripts(output_dir, query):
        meta = by_path.get(r["file"], {})
        if not _matches_filters({"week": meta.get("week"), "type": meta.get("type", "transcript"),
                                 "tags": meta.get("tags", [])}, week, ftype, ""):
            continue
        results.append({**r, "week": meta.get("week"), "type": meta.get("type", "transcript"),
                        "via": "exact"})

    if not results and fuzzy:
        scored = []
        ql = query.lower()
        for it in index:
            if not _matches_filters(it, week, ftype, ""):
                continue
            ratio = difflib.SequenceMatcher(None, ql, it["title"].lower()).ratio()
            # also reward a partial token overlap so "transport" ~ "Transport Layer"
            if any(ql in t.lower() for t in [it["title"], *it["tags"]]):
                ratio = max(ratio, 0.8)
            if ratio >= 0.55:
                scored.append((ratio, it))
        scored.sort(key=lambda x: x[0], reverse=True)
        for ratio, it in scored[:20]:
            results.append({
                "file": it["path"], "lecture": it["title"], "folder": it["folder"],
                "count": 0, "snippets": [f"~{int(ratio * 100)}% title match"],
                "week": it["week"], "type": it["type"], "via": "fuzzy",
            })
    return results


# Built-in saved views: each ``query`` is a parameter bag for :func:`library_view`.
# Users can add their own (persisted in the saved_views table); these always exist.
BUILTIN_VIEWS: List[Dict[str, Any]] = [
    {"name": "Recent Imports", "builtin": True, "query": {"sort": "date"}},
    {"name": "By Week", "builtin": True, "query": {"sort": "week"}},
    {"name": "All Transcripts", "builtin": True, "query": {"ftype": "transcript"}},
    {"name": "Documents", "builtin": True, "query": {"ftype": "document"}},
    {"name": "Notion", "builtin": True, "query": {"ftype": "notion"}},
    {"name": "Exam Revision", "builtin": True, "query": {"q": "exam", "sort": "week"}},
    {"name": "Assignments", "builtin": True, "query": {"q": "assignment"}},
]


def related(output_dir: Path, path: str, limit: int = 5) -> List[Dict[str, Any]]:
    """Items related to ``path``: same week, then same topic, then same type."""
    index = build_index(output_dir)
    target = next((it for it in index if it["path"] == path), None)
    if target is None:
        return []
    scored = []
    for it in index:
        if it["path"] == path:
            continue
        score = 0
        if target["week"] is not None and it["week"] == target["week"]:
            score += 3
        if target["topic"] and it["topic"] == target["topic"]:
            score += 2
        if it["type"] == target["type"]:
            score += 1
        shared = set(t.lower() for t in target["tags"]) & set(t.lower() for t in it["tags"])
        score += len(shared)
        if score > 0:
            scored.append((score, it))
    scored.sort(key=lambda x: x[0], reverse=True)
    return [it for _, it in scored[:limit]]
