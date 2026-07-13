"""Merge paper outlines and class schedules into exportable task plans."""
from __future__ import annotations

import csv
import datetime as dt
import io
import json
import re
import zipfile
from pathlib import Path
from typing import Any, Dict, List, Optional

from .core import now_iso
from .database import Database
from .paper_outlines import fetch_outline


def _slug(text: str) -> str:
    return re.sub(r"[^\w]+", "-", (text or "").strip()).strip("-").lower()


def _task_id(task: Dict[str, Any]) -> str:
    parts = [task.get("subject", ""), task.get("type", ""), task.get("name", ""),
             task.get("due_date", "")]
    return _slug("-".join(parts))


def merge_tasks(*, outline_tasks: List[Dict[str, Any]],
                schedule_tasks: List[Dict[str, Any]],
                paper_codes: Optional[List[str]] = None) -> List[Dict[str, Any]]:
    """Combine outline assessments with imported class-schedule rows."""
    merged: Dict[str, Dict[str, Any]] = {}
    codes = {c.upper() for c in (paper_codes or [])}
    bases = {c.split("-")[0] for c in codes if c}

    for t in schedule_tasks:
        subject = (t.get("subject") or "").upper()
        sub_base = subject.split("-")[0] if subject else ""
        if codes and subject and subject not in codes and sub_base not in bases:
            continue
        tid = _task_id(t)
        merged[tid] = {
            "id": tid,
            "subject": subject or t.get("subject", ""),
            "name": t.get("name", ""),
            "type": t.get("type", ""),
            "due_date": t.get("due_date", ""),
            "weight": t.get("weight"),
            "status": t.get("status", "not_started"),
            "priority": t.get("priority", ""),
            "source": "schedule",
            "tags": [subject] if subject else [],
        }

    for t in outline_tasks:
        code = (t.get("paper_code") or t.get("subject") or "").upper()
        if codes and code and code.split("-")[0] not in {c.split("-")[0] for c in codes}:
            continue
        name = t.get("name", "")
        tid = _task_id({"subject": code, "type": t.get("type", "Assessment"),
                        "name": name, "due_date": t.get("due_date", "")})
        if tid in merged:
            row = merged[tid]
            if t.get("weight") is not None and row.get("weight") is None:
                row["weight"] = t["weight"]
            if t.get("due_date") and not row.get("due_date"):
                row["due_date"] = t["due_date"]
            row["source"] = "merged"
            continue
        merged[tid] = {
            "id": tid,
            "subject": code.split("-")[0] if code else "",
            "paper_code": code,
            "name": name,
            "type": t.get("type", "Assessment"),
            "due_date": t.get("due_date", ""),
            "weight": t.get("weight"),
            "status": "not_started",
            "priority": "",
            "source": "outline",
            "tags": [code] if code else [],
        }

    out = list(merged.values())
    out.sort(key=lambda r: (r.get("due_date") or "9999-12-31", r.get("subject", ""), r.get("name", "")))
    return out


def outline_to_tasks(outline: Dict[str, Any]) -> List[Dict[str, Any]]:
    code = outline.get("paper_code", "")
    tasks = []
    for a in outline.get("assessments") or []:
        tasks.append({
            "paper_code": code,
            "subject": code.split("-")[0] if code else "",
            "name": a.get("name", ""),
            "type": "Assessment",
            "due_date": a.get("due_date", ""),
            "weight": a.get("weight"),
            "description": a.get("description", ""),
        })
    return tasks

def _resolve_outline(db: Database, code: str) -> Dict[str, Any]:
    """Load a cached outline or fetch by exact code / base paper code."""
    row = db.get_paper_outline(code)
    if row:
        return json.loads(row["outline_json"])
    base = code.split("-")[0].strip()
    for r in db.list_paper_outlines():
        pc = r["paper_code"] or ""
        if pc == code or pc.startswith(base + "-"):
            return json.loads(r["outline_json"])
    return fetch_outline(code)


def build_schedule(db: Database, course_id: int, *,
                     paper_codes: List[str],
                     class_schedule_id: Optional[int] = None,
                     name: str = "") -> Dict[str, Any]:
    """Generate and persist a merged task schedule."""
    schedule_tasks: List[Dict[str, Any]] = []
    if class_schedule_id:
        row = db.get_class_schedule(class_schedule_id)
        if row:
            schedule_tasks = json.loads(row["schedule_json"]).get("tasks", [])

    outline_tasks: List[Dict[str, Any]] = []
    outlines_used: List[str] = []
    for code in paper_codes:
        outline = _resolve_outline(db, code)
        pc = outline.get("paper_code") or code
        db.upsert_paper_outline(pc, json.dumps(outline), title=outline.get("title", ""))
        outline_tasks.extend(outline_to_tasks(outline))
        outlines_used.append(pc)

    tasks = merge_tasks(outline_tasks=outline_tasks, schedule_tasks=schedule_tasks,
                        paper_codes=paper_codes)
    payload = {
        "paper_codes": paper_codes,
        "outlines_used": outlines_used,
        "class_schedule_id": class_schedule_id,
        "task_count": len(tasks),
        "tasks": tasks,
        "generated_at": now_iso(),
    }
    label = name or f"Semester plan ({', '.join(paper_codes[:3])})"
    sid = db.create_task_schedule(course_id, label, json.dumps(payload),
                                  ",".join(paper_codes), class_schedule_id)
    return {"id": sid, "name": label, **payload}


def export_notion_csv(tasks: List[Dict[str, Any]]) -> str:
    """Notion-importable CSV for a task database."""
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["Name", "Subject", "Type", "Due", "Weight", "Status", "Priority", "Source", "Tags"])
    for t in tasks:
        tags = ";".join(t.get("tags") or [])
        writer.writerow([
            t.get("name", ""),
            t.get("subject", ""),
            t.get("type", ""),
            t.get("due_date", ""),
            f"{t['weight']}%" if t.get("weight") is not None else "",
            t.get("status", ""),
            t.get("priority", ""),
            t.get("source", ""),
            tags,
        ])
    return buf.getvalue()


def export_obsidian_zip(tasks: List[Dict[str, Any]], dest: Path, *,
                        title: str = "Semester plan") -> Path:
    """Write an Obsidian mini-vault with wikilinked task notes."""
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    root = _slug(title) or "semester-plan"

    with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as zf:
        subjects = sorted({t.get("subject", "") for t in tasks if t.get("subject")})
        index_lines = [f"# {title}", "", "## Timeline", ""]
        for t in tasks:
            due = t.get("due_date") or "no-date"
            link = f"[[tasks/{t['id']}]]"
            index_lines.append(f"- {due} · {link} · {t.get('type', '')} · {t.get('weight') or ''}%")

        index_lines += ["", "## Papers", ""]
        for s in subjects:
            index_lines.append(f"- [[papers/{_slug(s)}|{s}]]")

        zf.writestr(f"{root}/index.md", "\n".join(index_lines) + "\n")

        for s in subjects:
            paper_tasks = [t for t in tasks if t.get("subject") == s]
            lines = [f"# {s}", "", f"#paper/{_slug(s)}", ""]
            for t in paper_tasks:
                lines.append(f"- [[tasks/{t['id']}|{t.get('name', '')}]] ({t.get('due_date', 'TBD')})")
            zf.writestr(f"{root}/papers/{_slug(s)}.md", "\n".join(lines) + "\n")

        for t in tasks:
            tags = ["#task"]
            if t.get("subject"):
                tags.append(f"#paper/{_slug(t['subject'])}")
            if t.get("type"):
                tags.append(f"#type/{_slug(t['type'])}")
            body = [
                f"# {t.get('name', 'Task')}",
                "",
                " ".join(tags),
                "",
                f"- Subject: [[papers/{_slug(t.get('subject', ''))}|{t.get('subject', '')}]]",
                f"- Type: {t.get('type', '')}",
                f"- Due: {t.get('due_date', '')}",
                f"- Weight: {t.get('weight', '')}%",
                f"- Status: {t.get('status', '')}",
                f"- Source: {t.get('source', '')}",
            ]
            zf.writestr(f"{root}/tasks/{t['id']}.md", "\n".join(body) + "\n")

    return dest


def semester_timeline(tasks: List[Dict[str, Any]], *,
                      today: Optional[dt.date] = None) -> List[Dict[str, Any]]:
    """Group tasks by week for the UI timeline."""
    today = today or dt.date.today()
    buckets: Dict[str, List[Dict[str, Any]]] = {}
    for t in tasks:
        due = t.get("due_date") or ""
        label = "Unscheduled"
        if due:
            try:
                d = dt.date.fromisoformat(due[:10])
                week_start = d - dt.timedelta(days=d.weekday())
                label = week_start.isoformat()
            except ValueError:
                label = due
        buckets.setdefault(label, []).append(t)
    return [{"week_start": k, "tasks": buckets[k]} for k in sorted(buckets)]


# ---------------------------------------------------------------------------
# Calendar export (.ics + optional Google Calendar CSV)
# ---------------------------------------------------------------------------

_TYPE_COLORS: Dict[str, str] = {
    "assessment": "#BE0403",   # Waikato red
    "exam": "#A32F22",
    "quiz": "#7A5F0C",
    "lab": "#3E6E2E",
    "tutorial": "#A8530A",     # copper signal
    "lecture": "#53584F",
    "deadline": "#8C4408",
    "teaching": "#3E6E2E",
    "other": "#8E9587",
}

_PAPER_PALETTE = (
    "#BE0403", "#A8530A", "#3E6E2E", "#53584F", "#1B4F72",
    "#6C3483", "#117A65", "#B7950B", "#884EA0", "#2874A6",
)


def _ics_escape(text: str) -> str:
    return (text.replace("\\", "\\\\").replace(";", "\\;")
            .replace(",", "\\,").replace("\n", "\\n"))


def _parse_flexible_date(value: str) -> Optional[dt.date]:
    value = (value or "").strip().strip('"')
    if not value or re.search(r"\bN/A\b", value, re.I):
        return None
    if len(value) >= 10 and value[4] == "-":
        try:
            return dt.date.fromisoformat(value[:10])
        except ValueError:
            pass
    for fmt in (
        "%B %d, %Y", "%b %d, %Y", "%d %B %Y", "%d %b %Y",
        "%Y-%m-%d", "%d/%m/%Y",
    ):
        try:
            return dt.datetime.strptime(value, fmt).date()
        except ValueError:
            continue
    return None


def normalize_event_type(task_type: str) -> str:
    """Map schedule/outline labels to a stable calendar event type."""
    t = (task_type or "").strip().lower()
    if any(k in t for k in ("assignment", "project", "deliverable", "milestone",
                            "homework", "宿題", "practical")):
        return "assessment"
    if "quiz" in t or "クイズ" in t:
        return "quiz"
    if any(k in t for k in ("written test", "practical test", "exam", "テスト")):
        return "exam"
    if "test" in t:
        return "exam"
    if "lab" in t:
        return "lab"
    if "tutorial" in t or re.search(r"\btut\b", t):
        return "tutorial"
    if "lecture" in t:
        return "lecture"
    if "deadline" in t:
        return "deadline"
    if "teaching" in t:
        return "teaching"
    return "other"


def paper_color(paper_code: str) -> str:
    code = (paper_code or "").upper().split("-")[0]
    if not code:
        return _TYPE_COLORS["other"]
    return _PAPER_PALETTE[hash(code) % len(_PAPER_PALETTE)]


def event_color(event_type: str, paper_code: str = "") -> str:
    """Type-first colour; paper hash used when type is generic."""
    et = event_type or "other"
    if et in ("other", "deadline") and paper_code:
        return paper_color(paper_code)
    return _TYPE_COLORS.get(et, _TYPE_COLORS["other"])


def _task_description(task: Dict[str, Any], *, outline_url: str = "") -> str:
    lines: List[str] = []
    paper = task.get("paper_code") or task.get("subject") or ""
    if paper:
        lines.append(f"Paper: {paper}")
    if task.get("type"):
        lines.append(f"Type: {task['type']}")
    if task.get("weight") is not None:
        w = task["weight"]
        wtxt = f"{int(w)}" if w == int(w) else str(w)
        lines.append(f"Weight: {wtxt}%")
    if task.get("description"):
        lines.append(f"Details: {task['description']}")
    if task.get("status"):
        lines.append(f"Status: {task['status']}")
    if task.get("priority"):
        lines.append(f"Priority: {task['priority']}")
    if task.get("source"):
        lines.append(f"Source: {task['source']}")
    if outline_url:
        lines.append(f"Outline: {outline_url}")
    return "\\n".join(lines)


def _parse_key_date_event(label: str, *, paper_code: str = "",
                          outline_url: str = "") -> Optional[Dict[str, Any]]:
    if not label or re.search(r"\bN/A\b", label, re.I):
        return None
    m = re.match(r"^([^:]+):\s*(.+)$", label.strip())
    if not m:
        return None
    name, rest = m.group(1).strip(), m.group(2).strip()
    event_type = "teaching" if "teaching" in name.lower() else "exam" if "exam" in name.lower() else "other"
    if " - " in rest:
        start_s, end_s = rest.split(" - ", 1)
        start = _parse_flexible_date(start_s.strip())
        end = _parse_flexible_date(end_s.strip())
        if start and end:
            return {
                "uid": _slug(f"{paper_code}-{name}-{start.isoformat()}"),
                "summary": f"{paper_code}: {name}" if paper_code else name,
                "description": _ics_escape(
                    f"Period: {rest}" + (f"\\nOutline: {outline_url}" if outline_url else "")
                ),
                "start": start,
                "end": end + dt.timedelta(days=1),
                "all_day": True,
                "categories": [c for c in (paper_code, event_type, name.lower()) if c],
                "event_type": event_type,
                "paper_code": paper_code,
                "location": "",
                "alarm": False,
            }
    single = _parse_flexible_date(rest)
    if single:
        return {
            "uid": _slug(f"{paper_code}-{name}-{single.isoformat()}"),
            "summary": f"{paper_code}: {name}" if paper_code else name,
            "description": _ics_escape(label + (f"\\nOutline: {outline_url}" if outline_url else "")),
            "start": single,
            "end": single + dt.timedelta(days=1),
            "all_day": True,
            "categories": [c for c in (paper_code, event_type) if c],
            "event_type": event_type,
            "paper_code": paper_code,
            "location": "",
            "alarm": False,
        }
    return None


def calendar_events_from_plan(
    tasks: List[Dict[str, Any]],
    *,
    outlines: Optional[List[Dict[str, Any]]] = None,
) -> List[Dict[str, Any]]:
    """Build normalised calendar events from merged tasks and outline key dates."""
    events: List[Dict[str, Any]] = []
    seen_uids: set[str] = set()

    outline_urls: Dict[str, str] = {}
    for outline in outlines or []:
        code = outline.get("paper_code") or ""
        url = outline.get("outline_url") or ""
        if code:
            outline_urls[code.upper()] = url
            outline_urls[code.upper().split("-")[0]] = url

    for t in tasks:
        due = _parse_flexible_date(t.get("due_date", ""))
        if not due:
            continue
        paper = (t.get("paper_code") or t.get("subject") or "").upper()
        base_paper = paper.split("-")[0] if paper else ""
        event_type = normalize_event_type(t.get("type", ""))
        uid = t.get("id") or _task_id(t)
        if uid in seen_uids:
            uid = f"{uid}-{due.isoformat()}"
        seen_uids.add(uid)
        outline_url = outline_urls.get(paper) or outline_urls.get(base_paper, "")
        name = t.get("name", "Task")
        summary = f"{base_paper}: {name}" if base_paper else name
        categories = [c for c in (base_paper, event_type, (t.get("type") or "").strip()) if c]
        events.append({
            "uid": uid,
            "summary": summary,
            "description": _task_description(t, outline_url=outline_url),
            "start": due,
            "end": due + dt.timedelta(days=1),
            "all_day": True,
            "categories": categories,
            "event_type": event_type,
            "paper_code": base_paper,
            "location": t.get("location", ""),
            "alarm": event_type in ("assessment", "exam", "deadline"),
        })

    for outline in outlines or []:
        code = outline.get("paper_code") or ""
        base = code.split("-")[0] if code else ""
        url = outline.get("outline_url") or outline_urls.get(code.upper(), "")
        for kd in outline.get("key_dates") or []:
            ev = _parse_key_date_event(
                kd.get("label", ""), paper_code=base, outline_url=url,
            )
            if ev and ev["uid"] not in seen_uids:
                seen_uids.add(ev["uid"])
                events.append(ev)

    events.sort(key=lambda e: (e["start"], e.get("paper_code", ""), e["summary"]))
    return events


def _ics_datetime_stamp(when: Optional[dt.datetime] = None) -> str:
    when = when or dt.datetime.now(dt.timezone.utc)
    return when.strftime("%Y%m%dT%H%M%SZ")


def _ics_date_range(start: dt.date, end: dt.date) -> tuple[str, str]:
    return start.strftime("%Y%m%d"), end.strftime("%Y%m%d")


def _append_vevent(lines: List[str], event: Dict[str, Any], *, stamp: str) -> None:
    color = event_color(event.get("event_type", "other"), event.get("paper_code", ""))
    lines.append("BEGIN:VEVENT")
    lines.append(f"UID:{event['uid']}@course-assistant")
    lines.append(f"DTSTAMP:{stamp}")
    ds, de = _ics_date_range(event["start"], event["end"])
    lines.append(f"DTSTART;VALUE=DATE:{ds}")
    lines.append(f"DTEND;VALUE=DATE:{de}")
    lines.append(f"SUMMARY:{_ics_escape(event['summary'])}")
    if event.get("description"):
        lines.append(f"DESCRIPTION:{_ics_escape(event['description'].replace(chr(92) + 'n', chr(10)))}")
    if event.get("location"):
        lines.append(f"LOCATION:{_ics_escape(event['location'])}")
    cats = ",".join(_ics_escape(c) for c in event.get("categories") or [])
    if cats:
        lines.append(f"CATEGORIES:{cats}")
    lines.append(f"COLOR:{color}")
    lines.append(f"X-APPLE-CALENDAR-COLOR:{color}")
    lines.append(f"X-COURSE-ASSISTANT-EVENT-TYPE:{event.get('event_type', 'other')}")
    if event.get("paper_code"):
        lines.append(f"X-COURSE-ASSISTANT-PAPER:{event['paper_code']}")
        lines.append(f"X-COURSE-ASSISTANT-PAPER-COLOR:{paper_color(event['paper_code'])}")
    if event.get("alarm"):
        lines.extend([
            "BEGIN:VALARM",
            "TRIGGER:-P1D",
            "ACTION:DISPLAY",
            "DESCRIPTION:Due tomorrow",
            "END:VALARM",
        ])
    lines.append("END:VEVENT")


def export_calendar_ics(
    tasks: List[Dict[str, Any]],
    *,
    outlines: Optional[List[Dict[str, Any]]] = None,
    title: str = "Semester plan",
) -> str:
    """RFC 5545 calendar with categories, colours, and rich descriptions."""
    events = calendar_events_from_plan(tasks, outlines=outlines)
    stamp = _ics_datetime_stamp()
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//Course Assistant//Semester Planner//EN",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        f"X-WR-CALNAME:{_ics_escape(title)}",
    ]
    for event in events:
        _append_vevent(lines, event, stamp=stamp)
    lines.append("END:VCALENDAR")
    return "\r\n".join(lines) + "\r\n"


def export_google_calendar_csv(
    tasks: List[Dict[str, Any]],
    *,
    outlines: Optional[List[Dict[str, Any]]] = None,
) -> str:
    """Google Calendar import CSV (secondary format)."""
    events = calendar_events_from_plan(tasks, outlines=outlines)
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow([
        "Subject", "Start Date", "Start Time", "End Date", "End Time",
        "All Day Event", "Description", "Location", "Private",
    ])
    for ev in events:
        start = ev["start"]
        end = ev["end"] - dt.timedelta(days=1)
        desc = (ev.get("description") or "").replace("\\n", "\n")
        writer.writerow([
            ev["summary"],
            start.strftime("%m/%d/%Y"),
            "",
            end.strftime("%m/%d/%Y"),
            "",
            "True",
            desc,
            ev.get("location", ""),
            "False",
        ])
    return buf.getvalue()
