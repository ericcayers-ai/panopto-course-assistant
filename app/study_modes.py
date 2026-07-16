"""study_modes.py - practice helpers, daily recall, slideshow, focus sessions.

Thin orchestration over review_items + study_sessions so the Study panel can
offer named modes without duplicating SM-2 logic from study_planner.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from . import core, study_planner
from .database import Database


def daily_recall(db: Database, course_id: Optional[int] = None,
                 limit: int = 20) -> Dict[str, Any]:
    """Cards due today (or overdue), shaped for a recall session UI."""
    due = study_planner.due_reviews(db, course_id, None)
    items = due[: max(1, min(int(limit or 20), 100))]
    return {
        "mode": "daily_recall",
        "count": len(items),
        "due_total": len(due),
        "items": items,
    }


def slideshow(db: Database, course_id: Optional[int] = None,
              set_id: Optional[int] = None,
              limit: int = 50) -> Dict[str, Any]:
    """Flip-through deck: a flashcard set if given, else due + recent cards."""
    if set_id is not None:
        row = db.get_flashcard_set(set_id)
        if row is None:
            raise ValueError("Flashcard set not found")
        ref = f"set:{set_id}"
        rows = db.query(
            "SELECT * FROM review_items WHERE ref=? ORDER BY id", (ref,))
        cards = [{"id": r["id"], "front": r["front"], "back": r["back"],
                  "due": r["due"], "ref": r["ref"]} for r in rows]
        return {
            "mode": "slideshow",
            "set_id": set_id,
            "name": row["name"],
            "count": len(cards),
            "cards": cards,
        }
    # Mix: due first, then newest.
    due = study_planner.due_reviews(db, course_id, None)
    if len(due) >= limit:
        cards = due[:limit]
    else:
        all_rows = db.list_review_items(course_id)
        seen = {c["id"] for c in due}
        extras = []
        for r in reversed(list(all_rows)):
            rid = r["id"]
            if rid in seen:
                continue
            extras.append({
                "id": rid, "front": r["front"], "back": r["back"],
                "due": r["due"], "ref": r["ref"],
            })
            if len(due) + len(extras) >= limit:
                break
        cards = due + extras
    return {
        "mode": "slideshow",
        "set_id": None,
        "name": "Review deck",
        "count": len(cards),
        "cards": cards,
    }


def start_focus(db: Database, course_id: int, *,
                minutes: int = 25,
                activity_type: str = "focus") -> Dict[str, Any]:
    """Log a focus / Lock In session start by recording planned duration later.

    Returns a session ticket the client completes via ``complete_focus``.
    """
    minutes = max(1, min(int(minutes or 25), 180))
    # We record on complete; ticket is just metadata for the UI.
    return {
        "mode": "focus",
        "course_id": course_id,
        "minutes": minutes,
        "activity_type": activity_type or "focus",
        "started_at": core.now_iso(),
    }


def complete_focus(db: Database, course_id: int, *,
                   minutes: int,
                   activity_type: str = "focus",
                   started_at: str = "") -> Dict[str, Any]:
    minutes = max(1, min(int(minutes or 1), 180))
    sid = db.log_study_session(
        course_id,
        started_at or core.now_iso(),
        minutes,
        activity_type or "focus",
    )
    return {
        "id": sid,
        "course_id": course_id,
        "duration": minutes,
        "activity_type": activity_type or "focus",
    }


def tracker_snapshot(db: Database, course_id: Optional[int] = None) -> Dict[str, Any]:
    """Course tracker payload: weeks, assignments, exams, upcoming list."""
    assessments = study_planner.list_assessments(db, course_id)
    by_kind: Dict[str, List[Dict[str, Any]]] = {
        "assignment": [], "exam": [], "quiz": [], "other": [],
    }
    weeks: Dict[int, List[Dict[str, Any]]] = {}
    for a in assessments:
        kind = (a.get("kind") or "assignment").lower()
        if kind not in by_kind:
            kind = "other"
        by_kind[kind].append(a)
        wk = a.get("week")
        if wk is not None:
            weeks.setdefault(int(wk), []).append(a)
    upcoming = [a for a in assessments
                if (a.get("status") or "") not in ("submitted", "graded")]
    progress = study_planner.progress(db, course_id) if course_id is not None else {}
    return {
        "assessments": assessments,
        "by_kind": by_kind,
        "weeks": [{"week": w, "items": weeks[w]} for w in sorted(weeks)],
        "upcoming": upcoming[:20],
        "progress": progress,
    }
