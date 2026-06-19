"""
nextup.py - "what should I study next?" (v3).

Merges the signals the app already tracks into one ranked to-do list:
* spaced-repetition cards that are due (``review_items``)
* assessments that are upcoming or overdue (``assessments``)
* lectures with no extractive summary yet (a quick win before exporting)

Each action carries a numeric ``priority`` (higher = sooner) so the frontend can
just render them top-down. Pure over DB rows + the library; ``now`` is injectable.
"""
from __future__ import annotations

import datetime as dt
from pathlib import Path
from typing import Any, Dict, List, Optional

from .database import Database


def _parse(ts: str) -> Optional[dt.datetime]:
    raw = (ts or "").strip()
    if not raw:
        return None
    try:
        return dt.datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except Exception:
        pass
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y"):
        try:
            return dt.datetime.strptime(raw, fmt).replace(tzinfo=dt.timezone.utc)
        except Exception:
            continue
    return None


def compute(db: Database, output_dir: Optional[Path] = None, *,
            course_id: Optional[int] = None, now: Optional[dt.datetime] = None,
            limit: int = 10) -> Dict[str, Any]:
    now = now or dt.datetime.now(dt.timezone.utc)
    actions: List[Dict[str, Any]] = []

    # -- due spaced-repetition cards --------------------------------------
    due = db.list_review_items(course_id, due_before=now.isoformat())
    if due:
        actions.append({
            "kind": "review",
            "priority": 100,
            "title": f"Review {len(due)} due card{'s' if len(due) != 1 else ''}",
            "detail": "Spaced-repetition cards are due now.",
            "count": len(due),
            "goto": "study",
        })

    # -- assessments: overdue (highest) or approaching --------------------
    for a in db.list_assessments(course_id):
        a = dict(a)
        if (a.get("status") or "") == "done":
            continue
        when = _parse(a.get("due_date", ""))
        if not when:
            continue
        days = (when.date() - now.date()).days
        if days < 0:
            priority, detail = 120, f"Overdue by {-days} day{'s' if -days != 1 else ''}."
        elif days == 0:
            priority, detail = 110, "Due today."
        elif days <= 3:
            priority, detail = 90, f"Due in {days} day{'s' if days != 1 else ''}."
        elif days <= 7:
            priority, detail = 70, f"Due in {days} days."
        elif days <= 14:
            priority, detail = 50, f"Due in {days} days."
        else:
            continue
        actions.append({
            "kind": "assessment",
            "priority": priority,
            "title": a.get("name") or "Assessment",
            "detail": detail,
            "due_date": a.get("due_date", ""),
            "days_until": days,
            "goto": "study",
        })

    # -- lectures missing an extractive summary (a quick prep win) ---------
    if output_dir is not None:
        try:
            missing = [g["title"] for g in unsummarized_groups(output_dir)]
        except Exception:
            missing = []
        if missing:
            actions.append({
                "kind": "summarize",
                "priority": 40,
                "title": f"Summarize {len(missing)} lecture"
                         f"{'s' if len(missing) != 1 else ''}",
                "detail": "These transcripts have no summary yet.",
                "count": len(missing),
                "examples": missing[:5],
                "goto": "export",
            })

    actions.sort(key=lambda x: x["priority"], reverse=True)
    return {"count": len(actions), "actions": actions[:limit],
            "generated_at": now.isoformat()}


def unsummarized_groups(output_dir: Path) -> List[Dict[str, Any]]:
    """Lectures whose group has no ``summary`` format - i.e. nothing summarised yet."""
    from . import core
    out: List[Dict[str, Any]] = []
    for g in core.list_transcripts(output_dir):
        if not core._is_transcript_group(g):
            continue
        if "summary" in g["formats"]:
            continue
        out.append({"title": core._title_from_stem(g["stem"]), "folder": g["folder"]})
    return out
