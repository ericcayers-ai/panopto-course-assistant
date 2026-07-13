"""Fetch and parse Moodle calendar ICS exports."""
from __future__ import annotations

import datetime as dt
import re
from typing import Any, Callable, Dict, List, Optional
from urllib.parse import parse_qs, urlparse

from .core import now_iso
from .errors import AppError

Fetcher = Callable[[str], str]

MOODLE_CALENDAR_SECRET = "moodle_calendar_url"


class MoodleCalendarError(AppError):
    category = "invalid_source"
    status_code = 400


def mask_calendar_url(url: str) -> str:
    """Return a display-safe URL with authtoken redacted."""
    if not url:
        return ""
    return re.sub(
        r"(authtoken=)[^&]+",
        r"\1••••••••",
        url,
        flags=re.I,
    )


def _unfold_ics(text: str) -> str:
    lines: List[str] = []
    for line in text.replace("\r\n", "\n").split("\n"):
        if line.startswith((" ", "\t")) and lines:
            lines[-1] += line[1:]
        else:
            lines.append(line)
    return "\n".join(lines)


def _parse_ics_datetime(value: str) -> Optional[dt.date]:
    value = (value or "").strip()
    if not value:
        return None
    if len(value) == 8 and value.isdigit():
        try:
            return dt.datetime.strptime(value, "%Y%m%d").date()
        except ValueError:
            return None
    if "T" in value:
        value = value.split("T", 1)[0]
    if len(value) >= 10 and value[4] == "-":
        try:
            return dt.date.fromisoformat(value[:10])
        except ValueError:
            pass
    if len(value) >= 8:
        try:
            return dt.datetime.strptime(value[:8], "%Y%m%d").date()
        except ValueError:
            pass
    return None


def _ics_unescape(text: str) -> str:
    return (text.replace("\\n", "\n").replace("\\,", ",")
            .replace("\\;", ";").replace("\\\\", "\\"))


def parse_ics(text: str) -> List[Dict[str, Any]]:
    """Parse VEVENT blocks from an ICS calendar feed."""
    events: List[Dict[str, Any]] = []
    block: Dict[str, str] = {}
    in_event = False
    for line in _unfold_ics(text).split("\n"):
        line = line.strip()
        if line == "BEGIN:VEVENT":
            in_event = True
            block = {}
            continue
        if line == "END:VEVENT":
            in_event = False
            summary = _ics_unescape(block.get("SUMMARY", ""))
            desc = _ics_unescape(block.get("DESCRIPTION", ""))
            start_raw = block.get("DTSTART", "")
            end_raw = block.get("DTEND", "")
            start = _parse_ics_datetime(start_raw)
            end = _parse_ics_datetime(end_raw)
            if not start:
                continue
            if end and end > start:
                end_exclusive = end
            else:
                end_exclusive = start + dt.timedelta(days=1)
            uid = block.get("UID", summary)
            categories = [c.strip() for c in block.get("CATEGORIES", "").split(",") if c.strip()]
            events.append({
                "uid": uid,
                "summary": summary,
                "description": desc,
                "start": start,
                "end": end_exclusive,
                "all_day": "VALUE=DATE" in start_raw or len(start_raw) == 8,
                "categories": categories,
                "location": _ics_unescape(block.get("LOCATION", "")),
                "source": "moodle_calendar",
            })
            continue
        if in_event and ":" in line:
            key, _, val = line.partition(":")
            prop = key.split(";", 1)[0].upper()
            block[prop] = val
    return events


def _default_get(url: str) -> str:
    import requests
    r = requests.get(url, timeout=30)
    r.raise_for_status()
    return r.text


def fetch_calendar(url: str, *, http_get: Optional[Fetcher] = None) -> List[Dict[str, Any]]:
    """Download and parse a Moodle calendar ICS URL."""
    if not (url or "").strip():
        raise MoodleCalendarError("Calendar URL is required")
    parsed = urlparse(url)
    if "authtoken" not in parse_qs(parsed.query):
        raise MoodleCalendarError("Calendar URL must include an authtoken parameter")
    getter = http_get or _default_get
    try:
        raw = getter(url)
    except Exception as e:
        raise MoodleCalendarError(f"Could not fetch calendar: {e}") from e
    if "BEGIN:VCALENDAR" not in raw:
        raise MoodleCalendarError("Response is not a valid ICS calendar")
    return parse_ics(raw)


def _guess_paper_code(summary: str, paper_codes: List[str]) -> str:
    upper = summary.upper()
    bases = {c.upper().split("-")[0] for c in paper_codes}
    for base in sorted(bases, key=len, reverse=True):
        if base and base in upper:
            return base
    m = re.search(r"\b([A-Z]{2,10}\d{2,4})\b", upper)
    return m.group(1) if m else ""


def calendar_events_to_tasks(events: List[Dict[str, Any]], *,
                             paper_codes: Optional[List[str]] = None) -> List[Dict[str, Any]]:
    """Convert Moodle calendar events into schedule-compatible task rows."""
    codes = paper_codes or []
    tasks: List[Dict[str, Any]] = []
    for ev in events:
        summary = ev.get("summary", "")
        paper = _guess_paper_code(summary, codes)
        start = ev["start"]
        end = ev["end"]
        due = start.isoformat()
        name = summary
        if paper and summary.upper().startswith(paper):
            name = summary[len(paper):].lstrip(": -")
        typ = "Event"
        lower = summary.lower()
        if "lecture" in lower:
            typ = "Lecture"
        elif "tutorial" in lower or "tut" in lower:
            typ = "Tutorial"
        elif "lab" in lower:
            typ = "Lab"
        elif "exam" in lower or "test" in lower:
            typ = "Exam"
        elif "assignment" in lower:
            typ = "Assignment"
        tid = re.sub(r"[^\w]+", "-", f"{paper}-{summary}-{due}".lower()).strip("-")
        tasks.append({
            "id": tid or f"moodle-{len(tasks)}",
            "subject": paper,
            "paper_code": paper,
            "name": name or summary,
            "type": typ,
            "due_date": due,
            "end_date": (end - dt.timedelta(days=1)).isoformat() if end > start + dt.timedelta(days=1) else due,
            "weight": None,
            "status": "not_started",
            "priority": "",
            "source": "moodle_calendar",
            "tags": [paper] if paper else [],
            "location": ev.get("location", ""),
            "description": ev.get("description", ""),
        })
    return tasks


def events_to_calendar_rows(events: List[Dict[str, Any]],
                            paper_codes: Optional[List[str]] = None) -> List[Dict[str, Any]]:
    """Normalise parsed ICS events for calendar/gantt export."""
    codes = paper_codes or []
    rows: List[Dict[str, Any]] = []
    for ev in events:
        summary = ev.get("summary", "")
        paper = _guess_paper_code(summary, codes)
        lower = summary.lower()
        if "exam" in lower or "test" in lower:
            et = "exam"
        elif "lecture" in lower:
            et = "lecture"
        elif "lab" in lower:
            et = "lab"
        elif "tutorial" in lower or "tut" in lower:
            et = "tutorial"
        elif any(k in lower for k in ("assignment", "quiz", "project")):
            et = "assessment"
        else:
            et = "other"
        uid = re.sub(r"[^\w]+", "-", (ev.get("uid") or summary).lower()).strip("-")
        rows.append({
            "uid": uid or f"moodle-{len(rows)}",
            "summary": summary,
            "description": ev.get("description", ""),
            "start": ev["start"],
            "end": ev["end"],
            "all_day": ev.get("all_day", True),
            "categories": ev.get("categories", []),
            "event_type": et,
            "paper_code": paper,
            "location": ev.get("location", ""),
            "alarm": et in ("assessment", "exam", "deadline"),
        })
    return rows


def merge_calendar_into_tasks(tasks: List[Dict[str, Any]],
                              calendar_tasks: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Merge Moodle calendar rows: fill missing dates, add new events."""
    by_key: Dict[str, Dict[str, Any]] = {}
    for t in tasks:
        by_key[t.get("id") or ""] = dict(t)

    for ct in calendar_tasks:
        name_key = (ct.get("name") or "").lower()
        paper = (ct.get("subject") or "").upper()
        matched = False
        for tid, row in by_key.items():
            if (row.get("subject") or "").upper() != paper:
                continue
            if name_key and name_key in (row.get("name") or "").lower():
                if ct.get("due_date") and not row.get("due_date"):
                    row["due_date"] = ct["due_date"]
                row["source"] = "merged"
                matched = True
                break
        if not matched and ct.get("due_date"):
            by_key[ct["id"]] = ct

    out = list(by_key.values())
    out.sort(key=lambda r: (r.get("due_date") or "9999-12-31", r.get("subject", ""), r.get("name", "")))
    return out
