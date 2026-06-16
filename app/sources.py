"""
sources.py — parsers for non-transcript course material.

Currently:
  * Moodle course-page exports (the HTML you get from "save page" / a site
    downloader of a Moodle course, e.g. elearn.waikato.ac.nz).

These are deliberately stdlib-only (re + html) so they add no dependencies, and
written to tolerate the two Moodle course formats seen in the wild:
  * "weekly/onetopic" formats that tag sections with data-sectionname="…"
  * "topics" formats that render bare <h3>Section</h3> headings

The goal is to recover enough structure (course title, code, section/week
outline, week→topic map) to auto-fill the course name and to emit a clean
outline that can be fed to NotebookLM / other AI alongside the transcripts.
"""
from __future__ import annotations

import html
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import core

# ---------------------------------------------------------------------------
# Small HTML helpers (no external deps)
# ---------------------------------------------------------------------------

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")


def _clean(text: str) -> str:
    """Strip tags, unescape entities, collapse whitespace."""
    text = _TAG_RE.sub(" ", text or "")
    text = html.unescape(text)
    return _WS_RE.sub(" ", text).strip()


# Moodle / browser chrome headings that are never real course sections.
_CHROME_HEADINGS = {
    "notifications", "contacts", "fetching learning content...", "fetching learning content…",
    "navigation", "administration", "search", "calendar", "recent activity",
    "course dashboard", "skip to main content",
}


# ---------------------------------------------------------------------------
# Moodle course page
# ---------------------------------------------------------------------------

def find_moodle_course_file(path: Path) -> Optional[Path]:
    """Accept either the course HTML file itself or a mirror folder; return the
    course/view*.html file if found."""
    path = Path(path)
    if path.is_file() and path.suffix.lower() in (".html", ".htm"):
        return path
    if path.is_dir():
        # common location in these mirrors
        for candidate in (path / "course" / "view_php.html", path / "course" / "view.html"):
            if candidate.exists():
                return candidate
        hits = list(path.glob("course/view*.htm*")) or list(path.rglob("course/view*.htm*"))
        if hits:
            return hits[0]
    return None


def _extract_title(raw: str) -> str:
    # Prefer the page <h1>, then og:title, then <title> (minus the "… | Moodle" suffix).
    m = re.search(r'<h1[^>]*>(.*?)</h1>', raw, flags=re.I | re.S)
    if m:
        t = _clean(m.group(1))
        if t:
            return t
    m = re.search(r'<meta[^>]+property=["\']og:title["\'][^>]+content=["\']([^"\']+)', raw, flags=re.I)
    if m:
        return _clean(m.group(1))
    m = re.search(r"<title>(.*?)</title>", raw, flags=re.I | re.S)
    if m:
        t = _clean(m.group(1))
        t = re.sub(r"^\s*Paper:\s*", "", t, flags=re.I)
        t = re.sub(r"\s*\|\s*Moodle\s*$", "", t, flags=re.I)
        return t
    return ""


def _extract_course_code(title: str) -> str:
    # e.g. "COMPX201-26A", "MATHS135-25B", "DATAX121-25A"
    m = re.search(r"\b([A-Z]{3,6}\d{2,3}(?:-\d{2}[A-Z])?)\b", title or "")
    return m.group(1) if m else ""


def _extract_sections(raw: str) -> List[str]:
    # Format 1: data-sectionname="…"
    names = [_clean(m) for m in re.findall(r'data-sectionname="([^"]*)"', raw)]
    if not any(names):
        # Format 2: bare <h3>Section</h3> headings (no attributes = course content)
        names = [_clean(m) for m in re.findall(r"<h3>([^<]+)</h3>", raw)]
    out: List[str] = []
    seen = set()
    for n in names:
        n = n.strip()
        if not n or n.lower() in _CHROME_HEADINGS:
            continue
        if n.lower() in seen:
            continue
        seen.add(n.lower())
        out.append(n)
    return out


# Section names that are really just a date range (e.g. "7 - 11 July",
# "28 July - 1 August") carry no topic information.
_MONTHS = (
    r"jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|jul(?:y)?|"
    r"aug(?:ust)?|sep(?:tember)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?"
)
# Tested after separators have been collapsed to spaces, so the gap is \s/"to".
# Requires a month name on the second date to avoid eating topics like "2 3 4 Trees".
_DATE_RANGE_RE = re.compile(
    rf"^\d{{1,2}}\s*(?:{_MONTHS})?\s*(?:to\s+|\s)\d{{1,2}}\s*(?:{_MONTHS})$", re.I)
# Generic words left over once the numbered prefix is gone, e.g. "Week 1 Lecture".
_GENERIC_LEFTOVERS = re.compile(
    r"^(?:lecture|lectures|lec|lab|labs|tutorial|tutorials|tut|seminar|workshop|"
    r"class|classes|session|sessions|content|material|materials)$",
    re.I,
)


def _readable_topic(name: str) -> str:
    """Human-readable topic from a section name: drop week/lecture markers and
    leading separators, keep spaces (unlike core.infer_topic which slugifies).
    Returns "" when the section name carries no real topic (bare 'Week 3',
    a date range, or a single generic word like 'Lecture')."""
    t = re.sub(r"\b(?:week|wk|lecture|lect|lec|module|mod|unit|topic|lab|tutorial|tut)\s*0*\d+\b",
               "", name, flags=re.I)
    t = re.sub(r"[\-–—:]+", " ", t)
    t = _WS_RE.sub(" ", t).strip(" -–—:()")
    if not t or _DATE_RANGE_RE.match(t) or _GENERIC_LEFTOVERS.match(t):
        return ""
    return t


def _section_to_record(name: str) -> Dict[str, Any]:
    week = core.infer_number(name, "week")
    topic = _readable_topic(name)
    return {"name": name, "week": week, "topic": topic}


def parse_moodle_course(path: Path) -> Dict[str, Any]:
    """Parse a Moodle course export into a structured outline."""
    course_file = find_moodle_course_file(path)
    if not course_file:
        raise FileNotFoundError(
            "Could not find a Moodle course page (course/view_php.html) under that path."
        )
    raw = course_file.read_text(encoding="utf-8", errors="replace")
    title = _extract_title(raw)
    code = _extract_course_code(title)
    sections = [_section_to_record(n) for n in _extract_sections(raw)]
    week_topics = {s["week"]: s["topic"] for s in sections if s["week"] is not None and s["topic"]}

    return {
        "source_file": str(course_file),
        "title": title,
        "code": code,
        "section_count": len(sections),
        "sections": sections,
        "week_topics": week_topics,
        "outline_markdown": _outline_markdown(title, code, sections),
    }


def _outline_markdown(title: str, code: str, sections: List[Dict[str, Any]]) -> str:
    lines = [f"# {title or code or 'Course outline'}", ""]
    if code:
        lines.append(f"*Course code: {code}*")
        lines.append("")
    lines.append("## Course outline")
    lines.append("")
    if not sections:
        lines.append("_(No sections detected.)_")
    for s in sections:
        prefix = f"Week {s['week']}: " if s["week"] is not None else ""
        label = s["name"]
        lines.append(f"- {prefix}{label}" if prefix and not label.lower().startswith("week") else f"- {label}")
    return "\n".join(lines).rstrip() + "\n"


def save_outline(output_dir: Path, parsed: Dict[str, Any]) -> str:
    """Write the parsed course outline as a Markdown source under the output dir
    (so it can be exported to NotebookLM / used as an AI source). Returns rel path."""
    core.ensure_dir(output_dir)
    stem = core.safe_name(parsed.get("code") or parsed.get("title") or "course") + "_outline"
    target = output_dir / f"{stem}.md"
    target.write_text(parsed["outline_markdown"], encoding="utf-8")
    return target.relative_to(output_dir).as_posix()
