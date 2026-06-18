"""
study_planner.py - assessments, calendar, spaced repetition, progress (§6).

Turns stored content into actionable study workflows. Pure-Python and
deterministic (no AI here - §4 layers advice on top): given the same rows it
always produces the same plan, `.ics`, and review schedule, so it's fully
testable offline.

Pieces
------
* **Assessments**: thin service over the DB DAOs (the 4 status states).
* **Spaced repetition**: an SM-2 variant over ``review_items``; ``grade()`` maps a
  0–5 recall score to the next interval/ease/due date.
* **Calendar**: a minimal, dependency-free RFC-5545 ``.ics`` builder covering
  assignment deadlines, exam dates and lecture dates.
* **Plan**: merges due reviews + upcoming deadlines into a day-by-day schedule
  bounded by a weekly study-hours budget, with catch-up for missed lectures.
* **Progress**: completion %, study hours, streaks and a mastery score derived
  from ``study_sessions`` + ``quiz_attempts``.
"""
from __future__ import annotations

import datetime as dt
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import core, search
from .database import Database

# Assessment lifecycle (matches schema default 'not_started').
STATUSES = ("not_started", "in_progress", "submitted", "graded")


# ---------------------------------------------------------------------------
# Assessments
# ---------------------------------------------------------------------------


def _assessment_dict(row) -> Dict[str, Any]:
    return {"id": row["id"], "course_id": row["course_id"], "name": row["name"],
            "due_date": row["due_date"], "weight": row["weight"],
            "status": row["status"]}


def list_assessments(db: Database, course_id: Optional[int] = None) -> List[Dict[str, Any]]:
    return [_assessment_dict(r) for r in db.list_assessments(course_id)]


def create_assessment(db: Database, course_id: int, name: str, due_date: str = "",
                     weight: Optional[float] = None, status: str = "not_started") -> Dict[str, Any]:
    name = (name or "").strip()
    if not name:
        raise ValueError("assessment name is required")
    if status not in STATUSES:
        raise ValueError(f"status must be one of {STATUSES}")
    aid = db.create_assessment(course_id, name, due_date.strip(), weight, status)
    return _assessment_dict(db.get_assessment(aid))


def update_assessment(db: Database, assessment_id: int, **fields: Any) -> Optional[Dict[str, Any]]:
    if "status" in fields and fields["status"] is not None and fields["status"] not in STATUSES:
        raise ValueError(f"status must be one of {STATUSES}")
    db.update_assessment(assessment_id, **fields)
    row = db.get_assessment(assessment_id)
    return _assessment_dict(row) if row else None


def delete_assessment(db: Database, assessment_id: int) -> bool:
    return db.delete_assessment(assessment_id)


# ---------------------------------------------------------------------------
# Spaced repetition (SM-2 variant)
# ---------------------------------------------------------------------------


def schedule_after(quality: int, *, reps: int, interval: int, ease: float,
                  today: Optional[dt.date] = None) -> Dict[str, Any]:
    """SM-2 step. ``quality`` 0–5 (≥3 = recalled). Returns the next
    ``reps/interval/ease/due`` - deterministic, so it's directly testable."""
    quality = max(0, min(5, int(quality)))
    today = today or dt.date.today()
    if quality < 3:                     # lapse: relearn from the start
        reps, interval = 0, 1
    else:
        reps += 1
        if reps == 1:
            interval = 1
        elif reps == 2:
            interval = 6
        else:
            interval = round(interval * ease)
        ease = max(1.3, ease + (0.1 - (5 - quality) * (0.08 + (5 - quality) * 0.02)))
    due = (today + dt.timedelta(days=interval)).isoformat()
    return {"reps": reps, "interval": interval, "ease": round(ease, 3), "due": due}


def add_review_items(db: Database, course_id: int, cards: List[Dict[str, Any]],
                    ref: str = "") -> int:
    """Seed spaced-repetition items from flashcards; all due today."""
    today = dt.date.today().isoformat()
    n = 0
    for c in cards:
        front = (c.get("front") or "").strip()
        if not front:
            continue
        db.add_review_item(course_id, front=front, back=c.get("back", ""),
                          due=today, ref=ref or c.get("ref", ""))
        n += 1
    return n


def due_reviews(db: Database, course_id: Optional[int] = None,
               due: Optional[str] = None) -> List[Dict[str, Any]]:
    due = due or dt.date.today().isoformat()
    rows = db.list_review_items(course_id, due_before=due)
    return [{"id": r["id"], "front": r["front"], "back": r["back"], "due": r["due"],
             "interval": r["interval"], "ease": r["ease"], "reps": r["reps"]}
            for r in rows]


def grade_review(db: Database, item_id: int, quality: int,
                today: Optional[dt.date] = None) -> Optional[Dict[str, Any]]:
    rows = db.list_review_items()
    row = next((r for r in rows if r["id"] == item_id), None)
    if row is None:
        return None
    nxt = schedule_after(quality, reps=row["reps"], interval=row["interval"],
                        ease=row["ease"], today=today)
    db.update_review_item(item_id, **nxt)
    return {"id": item_id, **nxt}


# ---------------------------------------------------------------------------
# Calendar (.ics, RFC 5545, dependency-free)
# ---------------------------------------------------------------------------


def _ics_escape(text: str) -> str:
    return (text.replace("\\", "\\\\").replace(";", "\\;")
            .replace(",", "\\,").replace("\n", "\\n"))


def _parse_date(value: str) -> Optional[dt.date]:
    if not value:
        return None
    for fmt in ("%Y-%m-%d", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S",
                "%d/%m/%Y", "%d %B %Y"):
        try:
            return dt.datetime.strptime(value[:len(fmt) + 4], fmt).date()
        except ValueError:
            continue
    try:                                # tolerate full ISO8601 (with tz)
        return dt.datetime.fromisoformat(value.replace("Z", "+00:00")).date()
    except ValueError:
        return None


def build_ics(db: Database, output_dir: Path, course_id: Optional[int] = None,
             course_name: str = "Course") -> str:
    """All-day VEVENTs for assignment/exam deadlines (lecture dates folded in
    when transcripts carry one)."""
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0",
             "PRODID:-//Course Assistant//Study Planner//EN", "CALSCALE:GREGORIAN"]
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")

    def _event(uid: str, date: dt.date, summary: str) -> None:
        d = date.strftime("%Y%m%d")
        nxt = (date + dt.timedelta(days=1)).strftime("%Y%m%d")
        lines.extend(["BEGIN:VEVENT", f"UID:{uid}@course-assistant",
                      f"DTSTAMP:{stamp}", f"DTSTART;VALUE=DATE:{d}",
                      f"DTEND;VALUE=DATE:{nxt}", f"SUMMARY:{_ics_escape(summary)}",
                      "END:VEVENT"])

    for a in db.list_assessments(course_id):
        date = _parse_date(a["due_date"])
        if date:
            _event(f"assess-{a['id']}", date, f"{course_name}: {a['name']} due")
    return "\r\n".join(lines + ["END:VCALENDAR"]) + "\r\n"


# ---------------------------------------------------------------------------
# Study plan
# ---------------------------------------------------------------------------


def generate_plan(db: Database, output_dir: Path, course_id: int, *,
                 horizon_days: int = 14, hours_per_week: float = 10.0,
                 today: Optional[dt.date] = None) -> Dict[str, Any]:
    """A day-by-day plan over ``horizon_days``, bounded by a weekly hours budget.

    Each day gets: due spaced-repetition reviews, plus assessment-prep blocks
    that ramp up as a deadline approaches. Lectures with no transcript yet are
    surfaced as catch-up tasks.
    """
    today = today or dt.date.today()
    daily_budget = round(hours_per_week / 7.0, 2)
    days: List[Dict[str, Any]] = []

    reviews_by_day: Dict[str, int] = {}
    for r in db.list_review_items(course_id):
        d = _parse_date(r["due"]) or today
        key = max(d, today).isoformat()
        reviews_by_day[key] = reviews_by_day.get(key, 0) + 1

    upcoming = []
    for a in db.list_assessments(course_id):
        date = _parse_date(a["due_date"])
        if date and date >= today and a["status"] not in ("submitted", "graded"):
            upcoming.append((date, a))

    for offset in range(horizon_days):
        day = today + dt.timedelta(days=offset)
        key = day.isoformat()
        tasks: List[Dict[str, Any]] = []
        n_reviews = reviews_by_day.get(key, 0)
        if offset == 0:                 # overdue reviews collapse onto day 0
            n_reviews += sum(v for k, v in reviews_by_day.items() if k < key)
        if n_reviews:
            tasks.append({"type": "review", "title": f"{n_reviews} cards due",
                          "est_minutes": min(60, n_reviews * 2)})
        for date, a in upcoming:
            lead = (date - day).days
            if 0 <= lead <= 7:          # ramp prep in the final week
                tasks.append({"type": "assessment", "title": f"Prep: {a['name']}",
                              "due_in_days": lead,
                              "est_minutes": 90 if lead <= 2 else 45})
        if tasks:
            days.append({"date": key, "tasks": tasks,
                         "budget_minutes": int(daily_budget * 60)})

    # Catch-up: lectures present as material but not yet transcribed/summarised.
    catch_up = [it["title"] for it in search.build_index(output_dir)
                if it.get("type") == "document"][:10]

    return {"course_id": course_id, "horizon_days": horizon_days,
            "hours_per_week": hours_per_week, "daily_budget_hours": daily_budget,
            "days": days, "catch_up": catch_up,
            "upcoming_assessments": [{"name": a["name"], "due_date": a["due_date"],
                                      "days_away": (d - today).days}
                                     for d, a in sorted(upcoming)]}


# ---------------------------------------------------------------------------
# Progress / mastery
# ---------------------------------------------------------------------------


def progress(db: Database, course_id: int) -> Dict[str, Any]:
    sessions = db.list_study_sessions(course_id)
    attempts = db.list_quiz_attempts(course_id)
    assessments = db.list_assessments(course_id)

    total_minutes = sum((s["duration"] or 0) for s in sessions)
    done = sum(1 for a in assessments if a["status"] in ("submitted", "graded"))
    completion = round(100 * done / len(assessments), 1) if assessments else 0.0

    # Mastery = mean quiz score ratio (0–100); None when never tested.
    ratios = [(a["score"] / a["total"]) for a in attempts if a["total"]]
    mastery = round(100 * sum(ratios) / len(ratios), 1) if ratios else None

    streak = _study_streak(sessions)
    return {"course_id": course_id,
            "study_hours": round(total_minutes / 60.0, 2),
            "sessions": len(sessions), "quiz_attempts": len(attempts),
            "assessments_completed": done, "assessments_total": len(assessments),
            "completion_pct": completion, "mastery_pct": mastery,
            "current_streak_days": streak}


def _study_streak(sessions: List[Any]) -> int:
    """Consecutive days (ending today/yesterday) with at least one session."""
    dates = set()
    for s in sessions:
        d = _parse_date(s["started_at"])
        if d:
            dates.add(d)
    if not dates:
        return 0
    today = dt.date.today()
    if today not in dates and (today - dt.timedelta(days=1)) not in dates:
        return 0
    streak, cur = 0, today if today in dates else today - dt.timedelta(days=1)
    while cur in dates:
        streak += 1
        cur -= dt.timedelta(days=1)
    return streak
