"""
study.py — export the transcript library as a Notion-importable study database.

Notion turns an imported CSV into a database: the first column becomes the
*Name* (title) property and the rest become text properties you can retype as
Select / Multi-select / Date / Number in one click. We emit clean, human-readable
columns and comma-separated tags (which Notion splits into a Multi-select after
you change that column's type).
"""
from __future__ import annotations

import csv
import io
import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import core

EXPORTS_DIRNAME = "_exports"

COLUMNS = ["Name", "Week", "Date", "Topic", "Duration", "Tags", "Status", "Formats", "Summary", "Folder"]


def _readable_tags(course: str, week: Optional[int], topic: str) -> str:
    tags: List[str] = []
    if course:
        tags.append(course)
    if week is not None:
        tags.append(f"Week {week}")
    if topic and topic.lower() != "uncategorized":
        tags.append(topic.replace("_", " "))
    return ", ".join(dict.fromkeys(tags))


def _summary_cell(output_dir: Path, group: Dict[str, Any], json_text: str) -> str:
    """A short summary for the Summary column: prefer an existing .summary.md,
    else a 3-sentence extractive summary of the transcript text."""
    fmts = group["formats"]
    if "summary" in fmts:
        try:
            raw = (output_dir / fmts["summary"]).read_text(encoding="utf-8", errors="replace")
            points = [ln.lstrip("- ").strip() for ln in raw.splitlines() if ln.startswith("- ")]
            if points:
                return " ".join(points)
        except Exception:
            pass
    if json_text:
        return " ".join(core.summarize_text(json_text, max_sentences=3))
    return ""


def _meta_for_group(output_dir: Path, group: Dict[str, Any]) -> Dict[str, Any]:
    """Pull title/week/date/duration/topic/text from the group's JSON if present."""
    fmts = group["formats"]
    data: Dict[str, Any] = {}
    if "json" in fmts:
        try:
            data = json.loads((output_dir / fmts["json"]).read_text(encoding="utf-8", errors="replace"))
        except Exception:
            data = {}
    stem = group["stem"]
    title = data.get("title") or re.sub(r"\s+", " ", stem.replace("_", " ")).strip()
    week = data.get("week")
    if week is None:
        week = core.infer_week(stem)
        if week is None:
            week = core.infer_week(group["folder"])
    topic = data.get("topic") or core.infer_topic(stem)
    return {
        "title": title,
        "week": week,
        "date": data.get("date") or "",
        "duration": data.get("duration_human") or "",
        "topic": topic,
        "text": data.get("text") or "",
    }


def build_study_rows(output_dir: Path, course: str = "") -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for g in core.list_transcripts(output_dir):
        m = _meta_for_group(output_dir, g)
        rows.append({
            "Name": m["title"],
            "Week": m["week"] if m["week"] is not None else "",
            "Date": m["date"],
            "Topic": (m["topic"] or "").replace("_", " ") if m["topic"] != "uncategorized" else "",
            "Duration": m["duration"],
            "Tags": _readable_tags(course, m["week"], m["topic"]),
            "Status": "Transcribed",
            "Formats": ", ".join(sorted(g["formats"])),
            "Summary": _summary_cell(output_dir, g, m["text"]),
            "Folder": g["folder"],
        })
    return rows


def rows_to_csv(rows: List[Dict[str, Any]]) -> str:
    out = io.StringIO()
    # lineterminator="\n" so write_text() doesn't turn csv's \r\n into \r\r\n
    # on Windows (which would insert blank rows in the imported database).
    w = csv.DictWriter(out, fieldnames=COLUMNS, extrasaction="ignore", lineterminator="\n")
    w.writeheader()
    for r in rows:
        w.writerow(r)
    return out.getvalue()


def write_study_database(output_dir: Path, course: str = "", filename: str = "study_database") -> Dict[str, Any]:
    rows = build_study_rows(output_dir, course)
    dest = core.ensure_dir(output_dir / EXPORTS_DIRNAME)
    csv_path = dest / f"{core.safe_name(filename)}.csv"
    csv_path.write_text(rows_to_csv(rows), encoding="utf-8")
    return {
        "count": len(rows),
        "csv": csv_path.relative_to(output_dir).as_posix(),
        "columns": COLUMNS,
        "preview": rows[:8],
    }
