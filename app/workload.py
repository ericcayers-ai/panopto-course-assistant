"""
workload.py - study-load estimator (v3).

Turns transcript length into a realistic time budget: how long it takes to *read*
each lecture transcript and to *actively review* it (re-reading + note-taking is
slower than first-pass reading). Grouped by week so a student can see where the
heavy material sits. Pure arithmetic over word counts - no models, no network.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

from . import lectures

# Words per minute. Reading is first-pass comprehension; review is slower,
# deliberate re-reading with note-taking.
READ_WPM = 200
REVIEW_WPM = 90


def _minutes(words: int, wpm: int) -> float:
    return round(words / wpm, 1) if words else 0.0


def estimate(output_dir: Path, *, read_wpm: int = READ_WPM,
             review_wpm: int = REVIEW_WPM) -> Dict[str, Any]:
    """Per-lecture and per-week read/review time estimates, plus course totals."""
    read_wpm = max(1, read_wpm)
    review_wpm = max(1, review_wpm)
    items: List[Dict[str, Any]] = []
    for lec in lectures.iter_lectures(output_dir):
        words = len((lec.get("text") or "").split())
        if not words:
            continue
        items.append({
            "title": lec["title"],
            "week": lec["week"],
            "words": words,
            "read_min": _minutes(words, read_wpm),
            "review_min": _minutes(words, review_wpm),
        })

    weeks: Dict[Any, Dict[str, Any]] = {}
    for it in items:
        wk = it["week"] if it["week"] is not None else "Unsorted"
        bucket = weeks.setdefault(wk, {"week": wk, "lectures": 0, "words": 0,
                                       "read_min": 0.0, "review_min": 0.0})
        bucket["lectures"] += 1
        bucket["words"] += it["words"]
        bucket["read_min"] = round(bucket["read_min"] + it["read_min"], 1)
        bucket["review_min"] = round(bucket["review_min"] + it["review_min"], 1)

    def _wk_sort(b: Dict[str, Any]):
        return (b["week"] == "Unsorted", b["week"] if isinstance(b["week"], int) else 0)

    by_week = sorted(weeks.values(), key=_wk_sort)
    total_words = sum(it["words"] for it in items)
    return {
        "lectures": len(items),
        "total_words": total_words,
        "total_read_min": round(sum(it["read_min"] for it in items), 1),
        "total_review_min": round(sum(it["review_min"] for it in items), 1),
        "read_wpm": read_wpm,
        "review_wpm": review_wpm,
        "by_week": by_week,
        "items": items,
    }


def humanize_minutes(minutes: float) -> str:
    minutes = int(round(minutes))
    if minutes < 60:
        return f"{minutes}m"
    h, m = divmod(minutes, 60)
    return f"{h}h {m}m" if m else f"{h}h"
