"""
lectures.py - shared read model over the transcript library (v3).

Several v3 study features (glossary, keywords, workload, study guide, citations)
need the same thing: each lecture's title / week / topic / date plus its full
transcript text, read from the file-based library. This centralises that read so
the feature modules stay small and consistent with ``study.py``'s existing logic.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, List

from . import core


def _read(output_dir: Path, rel: str) -> str:
    try:
        return (output_dir / rel).read_text(encoding="utf-8", errors="replace")
    except Exception:
        return ""


# Leading grouped timestamp like "[00:12:30]  " that render_txt prefixes per block.
_TS_PREFIX = re.compile(r"(?m)^\s*\[\d{1,2}:\d{2}:\d{2}\]\s*")


def _strip_md(text: str) -> str:
    """Drop the markdown title, metadata bullets and per-segment timestamp headings,
    leaving the transcript prose."""
    kept = []
    for ln in text.splitlines():
        s = ln.strip()
        if not s or s.startswith("#") or s.startswith("- **"):
            continue
        kept.append(s)
    return " ".join(kept)


def lecture_text(output_dir: Path, group: Dict[str, Any]) -> str:
    """Clean transcript prose for a group, preferring the de-timestamped JSON
    ``text`` field; falls back to a timestamp-stripped ``.txt`` or ``.md``.

    The raw ``.txt``/``.md`` carry ``[hh:mm:ss]`` markers and headings that would
    pollute keyword/glossary extraction, so the JSON's clean ``text`` wins."""
    fmts = group["formats"]
    if "json" in fmts:
        try:
            data = json.loads(_read(output_dir, fmts["json"]))
            t = (data.get("text") or "").strip()
            if t:
                return t
        except Exception:
            pass
    if "txt" in fmts:
        t = _read(output_dir, fmts["txt"])
        if t.strip():
            return _TS_PREFIX.sub("", t).strip()
    if "md" in fmts:
        t = _read(output_dir, fmts["md"])
        if t.strip():
            return _strip_md(t)
    return ""


def lecture_meta(output_dir: Path, group: Dict[str, Any]) -> Dict[str, Any]:
    """Title / week / topic / date / duration for a lecture group, preferring the
    rich JSON sidecar and falling back to inference from the filename."""
    fmts = group["formats"]
    data: Dict[str, Any] = {}
    if "json" in fmts:
        try:
            data = json.loads(_read(output_dir, fmts["json"]))
        except Exception:
            data = {}
    stem = group["stem"]
    title = data.get("title") or re.sub(r"\s+", " ", stem.replace("_", " ")).strip()
    week = data.get("week")
    if week is None:
        week = core.infer_week(stem)
        if week is None:
            week = core.infer_week(group.get("folder", ""))
    topic = data.get("topic") or core.infer_topic(stem)
    return {
        "stem": stem,
        "folder": group.get("folder", ""),
        "title": title,
        "week": week,
        "topic": topic,
        "date": data.get("date") or "",
        "duration": data.get("duration_human") or data.get("duration") or "",
        "course": data.get("course") or "",
        "video_url": data.get("video_url") or data.get("source_video") or "",
        "path": (fmts.get("txt") or fmts.get("md") or fmts.get("json")
                 or next(iter(fmts.values()), "")),
    }


def iter_lectures(output_dir: Path, *, with_text: bool = True) -> List[Dict[str, Any]]:
    """Every *real* transcript (documents excluded) as a metadata dict, optionally
    carrying the full ``text``. One entry per lecture, folder/stem ordered."""
    out: List[Dict[str, Any]] = []
    for g in core.list_transcripts(output_dir):
        if not core._is_transcript_group(g):
            continue
        m = lecture_meta(output_dir, g)
        if with_text:
            m["text"] = lecture_text(output_dir, g)
        out.append(m)
    return out
