"""Parse Notion database-export zips into class schedule tasks."""
from __future__ import annotations

import csv
import datetime as dt
import io
import re
import zipfile
from pathlib import Path
from typing import Any, Dict, List, Union

from .errors import AppError


class ScheduleParseError(AppError):
    category = "invalid_source"
    status_code = 400


_DATE_FORMATS = (
    "%B %d, %Y", "%b %d, %Y", "%d %B %Y", "%d %b %Y",
    "%Y-%m-%d", "%d/%m/%Y",
)


def _parse_date(value: str) -> str:
    value = (value or "").strip().strip('"')
    if not value:
        return ""
    for fmt in _DATE_FORMATS:
        try:
            return dt.datetime.strptime(value, fmt).date().isoformat()
        except ValueError:
            continue
    return value


def _parse_weight(value: str) -> float | None:
    m = re.search(r"(\d+(?:\.\d+)?)\s*%", value or "")
    return float(m.group(1)) if m else None


def parse_notion_csv(text: str) -> List[Dict[str, Any]]:
    """Parse a Notion tasks CSV (flat or grouped export)."""
    text = text.lstrip("\ufeff")
    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames:
        raise ScheduleParseError("CSV has no header row")
    fields = {f.lower().strip(): f for f in reader.fieldnames}
    tasks: List[Dict[str, Any]] = []
    for row in reader:
        subject = (row.get(fields.get("subject", "Subject")) or "").strip()
        task = (row.get(fields.get("task", "Task")) or "").strip()
        typ = (row.get(fields.get("type", "Type")) or "").strip()
        due_raw = (row.get(fields.get("due date", "Due Date")) or "").strip()
        weight_raw = (row.get(fields.get("grade total", "Grade Total")) or "").strip()
        status = (row.get(fields.get("status", "Status")) or "").strip()
        priority = (row.get(fields.get("priority", "Priority")) or "").strip()
        if not task and not subject:
            continue
        tasks.append({
            "subject": subject,
            "name": task,
            "type": typ,
            "due_date": _parse_date(due_raw),
            "weight": _parse_weight(weight_raw),
            "status": status or "not_started",
            "priority": priority,
            "source": "notion_csv",
        })
    return tasks


def _open_notion_zip(source: Union[str, Path, bytes]) -> zipfile.ZipFile:
    """Open a Notion export zip, unwrapping a single nested Part-1 zip if needed."""
    if isinstance(source, bytes):
        raw = source
    else:
        raw = Path(source).read_bytes()
    zf = zipfile.ZipFile(io.BytesIO(raw))
    if any(n.lower().endswith(".csv") for n in zf.namelist()):
        return zf
    nested = [n for n in zf.namelist() if n.lower().endswith(".zip")]
    if len(nested) == 1:
        return zipfile.ZipFile(io.BytesIO(zf.read(nested[0])))
    return zf


def parse_notion_zip(source: Union[str, Path, bytes]) -> Dict[str, Any]:
    """Read a Notion export zip and return all parsed schedule tasks."""
    zf = _open_notion_zip(source)

    csv_names = [n for n in zf.namelist() if n.lower().endswith(".csv")]
    if not csv_names:
        raise ScheduleParseError("No CSV files found in the zip export")

    all_tasks: List[Dict[str, Any]] = []
    files: List[str] = []
    for name in csv_names:
        raw = zf.read(name).decode("utf-8-sig", errors="replace")
        tasks = parse_notion_csv(raw)
        if tasks:
            all_tasks.extend(tasks)
            files.append(name)

    # De-duplicate identical rows across _all and filtered exports.
    seen = set()
    unique: List[Dict[str, Any]] = []
    for t in all_tasks:
        key = (t["subject"], t["name"], t["type"], t["due_date"])
        if key not in seen:
            seen.add(key)
            unique.append(t)

    unique.sort(key=lambda t: (t["due_date"] or "9999", t["subject"], t["name"]))
    subjects = sorted({t["subject"] for t in unique if t["subject"]})
    return {
        "name": Path(csv_names[0]).stem,
        "files": files,
        "task_count": len(unique),
        "subjects": subjects,
        "tasks": unique,
    }
