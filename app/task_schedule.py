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


def _mermaid_safe(text: str) -> str:
    """Sanitize labels for Mermaid gantt task names."""
    text = re.sub(r"[:;#]", " -", text or "")
    return text.strip() or "Task"


def _gantt_status_tags(event_type: str, status: str = "") -> str:
    tags: List[str] = []
    if event_type == "exam":
        tags.append("crit")
    st = (status or "").lower().replace(" ", "_")
    if st in ("done", "completed", "complete"):
        tags.append("done")
    elif st in ("in_progress", "started", "active"):
        tags.append("active")
    return ",".join(tags)


def _gantt_task_id(prefix: str, idx: int) -> str:
    base = re.sub(r"[^\w]", "", prefix)[:20] or "t"
    return f"{base}{idx}"


def build_mermaid_gantt(
    tasks: List[Dict[str, Any]],
    *,
    outlines: Optional[List[Dict[str, Any]]] = None,
    moodle_events: Optional[List[Dict[str, Any]]] = None,
    title: str = "Semester plan",
) -> str:
    """Build an Obsidian-native Mermaid gantt chart grouped by paper code."""
    events = calendar_events_from_plan(tasks, outlines=outlines)
    if moodle_events:
        seen = {e["uid"] for e in events}
        for ev in moodle_events:
            if ev.get("uid") not in seen:
                events.append(ev)
                seen.add(ev["uid"])
    events.sort(key=lambda e: (e["start"], e.get("paper_code", ""), e["summary"]))

    status_by_id: Dict[str, str] = {t.get("id", ""): t.get("status", "") for t in tasks}
    lines = [
        "```mermaid",
        "gantt",
        f"    title {_mermaid_safe(title)}",
        "    dateFormat YYYY-MM-DD",
        "    axisFormat %b %d",
    ]

    sections: Dict[str, List[Dict[str, Any]]] = {}
    for ev in events:
        section = (ev.get("paper_code") or "General").upper() or "General"
        sections.setdefault(section, []).append(ev)

    if not sections:
        lines.append("    section Plan")
        lines.append("    No dated events :milestone, nodate, 2026-01-01, 1d")
    else:
        idx = 0
        for section in sorted(sections):
            lines.append(f"    section {section}")
            for ev in sections[section]:
                idx += 1
                start = ev["start"]
                end = ev["end"]
                span_days = (end - start).days
                if span_days <= 1:
                    end_expr = "1d"
                else:
                    end_expr = end.isoformat()
                label = _mermaid_safe(ev["summary"])
                if ev.get("paper_code") and label.upper().startswith(ev["paper_code"]):
                    label = _mermaid_safe(label.split(":", 1)[-1].strip())
                tid = _gantt_task_id(ev.get("uid", label), idx)
                tags = _gantt_status_tags(ev.get("event_type", "other"),
                                          status_by_id.get(ev.get("uid", ""), ""))
                if tags:
                    lines.append(f"    {label} :{tags}, {tid}, {start.isoformat()}, {end_expr}")
                else:
                    lines.append(f"    {label} :{tid}, {start.isoformat()}, {end_expr}")

    lines.append("```")
    return "\n".join(lines) + "\n"


def _timetable_markdown(tasks: List[Dict[str, Any]]) -> str:
    lines = [
        "| # | Paper | Type | Task | Due | Weight | Status | Source |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for i, t in enumerate(tasks, 1):
        w = f"{t['weight']}%" if t.get("weight") is not None else ""
        lines.append(
            f"| {i} | {t.get('subject', '')} | {t.get('type', '')} | "
            f"{t.get('name', '')} | {t.get('due_date', '')} | {w} | "
            f"{t.get('status', '')} | {t.get('source', '')} |"
        )
    return "\n".join(lines) + "\n"


def _outline_note(outline: Dict[str, Any]) -> str:
    code = outline.get("paper_code", "")
    base = code.split("-")[0] if code else ""
    tags = f"compx234, {base.lower()}, reference" if base else "semester, reference"
    lines = [
        "---",
        f"tags: [{tags}]",
        f"aliases: [{code} Outline]",
        "---",
        "",
        f"# {outline.get('title') or code}",
        "",
        f"Paper code: {code}",
        "",
    ]
    if outline.get("assessments"):
        lines += ["## Assessments", ""]
        for a in outline["assessments"]:
            w = f" ({a['weight']}%)" if a.get("weight") is not None else ""
            due = f" — due {a['due_date']}" if a.get("due_date") else ""
            lines.append(f"- {a.get('name', '')}{w}{due}")
        lines.append("")
    if outline.get("key_dates"):
        lines += ["## Key dates", ""]
        for kd in outline["key_dates"]:
            lines.append(f"- {kd.get('label', '')}")
        lines.append("")
    if outline.get("weekly_topics"):
        lines += ["## Weekly topics", ""]
        for wt in outline["weekly_topics"][:20]:
            lines.append(f"- Week {wt.get('week', '')}: {wt.get('topic', '')}")
        lines.append("")
    return "\n".join(lines)


def _announcement_note(row: Dict[str, Any]) -> str:
    title = row.get("title", "Announcement")
    slug = _slug(title)[:40] or "announcement"
    lines = [
        "---",
        "tags: [announcement, moodle]",
        "---",
        "",
        f"# {title}",
        "",
        f"*{row.get('author', '')} · {row.get('posted_at', '')}*",
        "",
        row.get("body", ""),
    ]
    return slug, "\n".join(lines)


def export_obsidian_zip(
    tasks: List[Dict[str, Any]],
    dest: Path,
    *,
    title: str = "Semester plan",
    outlines: Optional[List[Dict[str, Any]]] = None,
    moodle_events: Optional[List[Dict[str, Any]]] = None,
    announcements: Optional[List[Dict[str, Any]]] = None,
) -> Path:
    """Write an Obsidian vault zip via the shared suite engine."""
    import tempfile
    from . import suites

    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        built = suites.build_suite_tree(
            Path(tmp),
            format="obsidian",
            title=title,
            tasks=tasks,
            outlines=outlines or [],
            moodle_events=moodle_events,
            announcements=announcements,
        )
        suites.zip_suite(Path(built["root"]), dest)
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


def write_export_artifacts(
    plan_id: int,
    tasks: List[Dict[str, Any]],
    *,
    output_dir: Path,
    title: str,
    outlines: Optional[List[Dict[str, Any]]] = None,
    moodle_events: Optional[List[Dict[str, Any]]] = None,
    announcements: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, str]:
    """Persist ICS and Obsidian zip export artifacts under _semester/."""
    out_dir = Path(output_dir) / "_semester"
    out_dir.mkdir(parents=True, exist_ok=True)
    ics_path = out_dir / f"plan-{plan_id}.ics"
    ics_path.write_text(
        export_calendar_ics(tasks, outlines=outlines, title=title),
        encoding="utf-8",
    )
    zip_path = out_dir / f"obsidian-plan-{plan_id}.zip"
    export_obsidian_zip(
        tasks, zip_path, title=title, outlines=outlines,
        moodle_events=moodle_events, announcements=announcements,
    )
    return {"ics": str(ics_path), "obsidian_zip": str(zip_path)}


def sync_semester_all(
    db: Database,
    course_id: int,
    *,
    paper_codes: List[str],
    class_schedule_id: Optional[int] = None,
    schedule_bytes: Optional[bytes] = None,
    schedule_filename: str = "",
    calendar_url: Optional[str] = None,
    moodle_announcements_url: str = "",
    moodle_cookies: str = "",
    name: str = "",
    http_get=None,
) -> Dict[str, Any]:
    """One-click refresh: outlines, schedule, Moodle calendar, announcements, plan, exports."""
    from . import moodle_calendar, moodle_content, schedule_parser
    from .paper_outlines import PaperOutlineError, fetch_outline

    steps: List[Dict[str, str]] = []
    errors: List[str] = []
    moodle_cal_events: List[Dict[str, Any]] = []

    from . import settings_store
    course_id = settings_store.ensure_active_course(db, course_id)

    outlines: List[Dict[str, Any]] = []
    for code in paper_codes:
        try:
            try:
                outline = fetch_outline(code)
            except PaperOutlineError:
                outline = _resolve_outline(db, code)
            pc = outline.get("paper_code") or code
            db.upsert_paper_outline(pc, json.dumps(outline), title=outline.get("title", ""))
            outlines.append(outline)
        except Exception as e:
            errors.append(f"outline:{code}: {e}")
    steps.append({"step": "outlines", "status": "ok" if outlines else "error",
                  "detail": f"{len(outlines)} of {len(paper_codes)} refreshed"})

    sched_id = class_schedule_id
    if schedule_bytes:
        try:
            fname = (schedule_filename or "").lower()
            if fname.endswith(".csv"):
                text = schedule_bytes.decode("utf-8-sig", errors="replace")
                tasks_parsed = schedule_parser.parse_notion_csv(text)
                parsed = {
                    "name": Path(fname).stem,
                    "task_count": len(tasks_parsed),
                    "subjects": sorted({t["subject"] for t in tasks_parsed if t["subject"]}),
                    "tasks": tasks_parsed,
                }
            else:
                parsed = schedule_parser.parse_notion_zip(schedule_bytes)
            sched_id = db.create_class_schedule(
                course_id, parsed.get("name", "Class schedule"),
                json.dumps(parsed), source_path=schedule_filename,
            )
            steps.append({"step": "schedule", "status": "ok",
                          "detail": f"Imported {parsed.get('task_count', 0)} tasks"})
        except Exception as e:
            errors.append(f"schedule: {e}")
            steps.append({"step": "schedule", "status": "error", "detail": str(e)})
    elif sched_id:
        row = db.get_class_schedule(sched_id)
        n = 0
        if row:
            n = json.loads(row["schedule_json"]).get("task_count", 0)
        steps.append({"step": "schedule", "status": "ok", "detail": f"Using schedule ({n} tasks)"})
    else:
        steps.append({"step": "schedule", "status": "skipped", "detail": "No class schedule"})

    if calendar_url:
        try:
            raw_events = moodle_calendar.fetch_calendar(calendar_url, http_get=http_get)
            moodle_cal_events = moodle_calendar.events_to_calendar_rows(raw_events, paper_codes)
            steps.append({"step": "moodle_calendar", "status": "ok",
                          "detail": f"{len(moodle_cal_events)} events"})
        except Exception as e:
            errors.append(f"moodle_calendar: {e}")
            steps.append({"step": "moodle_calendar", "status": "error", "detail": str(e)})
    else:
        steps.append({"step": "moodle_calendar", "status": "skipped",
                      "detail": "No calendar URL configured"})

    if moodle_announcements_url:
        try:
            fetched = moodle_content.fetch_announcements(moodle_announcements_url, moodle_cookies)
            ann_count = db.replace_moodle_announcements(
                course_id, fetched.get("moodle_course_id", ""),
                fetched.get("announcements", []),
            )
            steps.append({"step": "announcements", "status": "ok", "detail": f"{ann_count} stored"})
        except Exception as e:
            errors.append(f"announcements: {e}")
            steps.append({"step": "announcements", "status": "error", "detail": str(e)})
    else:
        steps.append({"step": "announcements", "status": "skipped", "detail": "No Moodle URL"})

    schedule_tasks: List[Dict[str, Any]] = []
    if sched_id:
        row = db.get_class_schedule(sched_id)
        if row:
            schedule_tasks = json.loads(row["schedule_json"]).get("tasks", [])

    outline_tasks: List[Dict[str, Any]] = []
    outlines_used: List[str] = []
    for o in outlines:
        outline_tasks.extend(outline_to_tasks(o))
        outlines_used.append(o.get("paper_code", ""))

    tasks = merge_tasks(outline_tasks=outline_tasks, schedule_tasks=schedule_tasks,
                        paper_codes=paper_codes)
    if moodle_cal_events:
        cal_tasks = moodle_calendar.calendar_events_to_tasks(
            [{"summary": e["summary"], "start": e["start"], "end": e["end"],
              "description": e.get("description", ""), "location": e.get("location", ""),
              "uid": e["uid"]} for e in moodle_cal_events],
            paper_codes=paper_codes,
        )
        tasks = moodle_calendar.merge_calendar_into_tasks(tasks, cal_tasks)

    label = name or f"Semester plan ({', '.join(paper_codes[:3])})"
    payload = {
        "paper_codes": paper_codes,
        "outlines_used": outlines_used,
        "class_schedule_id": sched_id,
        "task_count": len(tasks),
        "tasks": tasks,
        "generated_at": now_iso(),
        "moodle_calendar_events": len(moodle_cal_events),
    }
    plan_id = db.create_task_schedule(
        course_id, label, json.dumps(payload), ",".join(paper_codes), sched_id,
    )

    ann_rows = [
        {"title": r["title"], "body": r["body"], "author": r["author"],
         "posted_at": r["posted_at"]}
        for r in db.list_moodle_announcements(course_id)
    ]
    from . import context
    artifacts = write_export_artifacts(
        plan_id, tasks, output_dir=context.OUTPUT_DIR, title=label,
        outlines=outlines, moodle_events=moodle_cal_events or None,
        announcements=ann_rows or None,
    )
    steps.append({"step": "exports", "status": "ok", "detail": "ICS + Obsidian updated"})

    suite_report: Dict[str, Any] = {}
    try:
        from . import suites
        if suites.get_auto_sync(db) and suites.get_destinations(db):
            suite_report = suites.sync_suites_to_destinations(
                db=db,
                plan_payload=payload,
                title=label,
                outlines=outlines,
                announcements=ann_rows or None,
                moodle_events=moodle_cal_events or None,
                library_dir=context.OUTPUT_DIR,
                staging_dir=context.OUTPUT_DIR / "_suites",
                push_live=True,
            )
            suites.set_last_sync(db, {
                "plan_id": plan_id,
                "formats": suite_report.get("formats") or [],
                "new_files": suite_report.get("new_files", 0),
                "updated": suite_report.get("updated", 0),
                "at": now_iso(),
            })
            steps.append({
                "step": "suites",
                "status": "ok",
                "detail": f"Wrote {', '.join(suite_report.get('formats') or [])}",
            })
    except Exception as e:
        errors.append(f"suites: {e}")
        steps.append({"step": "suites", "status": "error", "detail": str(e)})

    return {
        "ok": not errors,
        "plan_id": plan_id,
        "name": label,
        "task_count": len(tasks),
        "steps": steps,
        "errors": errors,
        "artifacts": artifacts,
        "timeline": semester_timeline(tasks),
        "suites": suite_report,
    }
