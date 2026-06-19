"""
streak.py - study streak & daily goal (v3).

A light motivation layer derived entirely from rows the app already stores
(``study_sessions`` minutes + ``quiz_attempts``). Computes the current and longest
run of consecutive active days and today's progress toward a minutes goal. No new
data, no network. ``now`` is injectable so the logic is deterministic in tests.
"""
from __future__ import annotations

import datetime as dt
from typing import Any, Dict, Optional, Set

from .database import Database

DEFAULT_GOAL_MINUTES = 30


def _date(ts: str) -> Optional[dt.date]:
    try:
        return dt.datetime.fromisoformat((ts or "").replace("Z", "+00:00")).date()
    except Exception:
        return None


def _current_streak(days: Set[dt.date], today: dt.date) -> int:
    # Today doesn't have to be active yet (you might study later) - start the count
    # from today if active, else from yesterday so an unfinished today isn't punished.
    cursor = today if today in days else today - dt.timedelta(days=1)
    streak = 0
    while cursor in days:
        streak += 1
        cursor -= dt.timedelta(days=1)
    return streak


def _longest_streak(days: Set[dt.date]) -> int:
    if not days:
        return 0
    best = run = 1
    ordered = sorted(days)
    for prev, cur in zip(ordered, ordered[1:]):
        run = run + 1 if (cur - prev).days == 1 else 1
        best = max(best, run)
    return best


def compute(db: Database, *, course_id: Optional[int] = None,
            now: Optional[dt.datetime] = None,
            goal_minutes: int = DEFAULT_GOAL_MINUTES) -> Dict[str, Any]:
    now = now or dt.datetime.now(dt.timezone.utc)
    today = now.date()
    goal_minutes = max(1, goal_minutes)

    sessions = [dict(r) for r in db.list_study_sessions(course_id)]
    quizzes = [dict(r) for r in db.list_quiz_attempts(course_id)]

    active_days: Set[dt.date] = set()
    minutes_by_day: Dict[dt.date, float] = {}
    for s in sessions:
        d = _date(s.get("started_at", ""))
        if d:
            active_days.add(d)
            minutes_by_day[d] = minutes_by_day.get(d, 0.0) + float(s.get("duration") or 0)
    for q in quizzes:
        d = _date(q.get("taken_at", ""))
        if d:
            active_days.add(d)

    today_minutes = round(minutes_by_day.get(today, 0.0), 1)
    return {
        "current_streak": _current_streak(active_days, today),
        "longest_streak": _longest_streak(active_days),
        "active_today": today in active_days,
        "active_days": len(active_days),
        "today_minutes": today_minutes,
        "goal_minutes": goal_minutes,
        "goal_met": today_minutes >= goal_minutes,
        "goal_pct": min(100, round(today_minutes / goal_minutes * 100)),
        "today_sessions": sum(1 for s in sessions
                              if _date(s.get("started_at", "")) == today),
        "today_quizzes": sum(1 for q in quizzes
                             if _date(q.get("taken_at", "")) == today),
        "total_minutes": round(sum(minutes_by_day.values()), 1),
    }
