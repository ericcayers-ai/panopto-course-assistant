"""Paper outline search and parsing for Waikato paper outlines."""
from __future__ import annotations

import html as html_lib
import json
import re
from typing import Any, Callable, Dict, List, Optional
from urllib.parse import quote, urljoin

from .core import now_iso
from .errors import AppError

WAIKATO_STUDY = "https://www.waikato.ac.nz/study/papers"
OUTLINE_SITE = "https://paperoutlines.waikato.ac.nz"
API_PREFIX = "https://uow-func-net-currmngmt-offmngmt-aue-prod.azurewebsites.net"

HttpGet = Callable[[str], str]


class PaperOutlineError(AppError):
    category = "invalid_source"
    status_code = 400


def _default_get(url: str) -> str:
    import requests
    r = requests.get(url, timeout=45, headers={"User-Agent": "Mozilla/5.0 CourseAssistant"})
    r.raise_for_status()
    return r.text


def _slugify(code: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (code or "").lower())


def _normalise_code(raw: str) -> str:
    raw = (raw or "").strip().upper()
    raw = re.sub(r"\s+", " ", raw)
    return raw


def _outline_code_from_url(url: str) -> str:
    m = re.search(r"/outline/([^?#]+)", url or "", re.I)
    return html_lib.unescape(m.group(1)).strip() if m else ""


def outline_url(paper_code: str) -> str:
    code = _normalise_code(paper_code)
    return f"{OUTLINE_SITE}/outline/{quote(code, safe=' ()')}"


def waikato_paper_url(code: str, year: int = 2026) -> str:
    slug = _slugify(code.split("-")[0] if "-" in code else code)
    return f"{WAIKATO_STUDY}/{slug}/{year}/"


def search_papers(query: str, *, year: int = 2026,
                  http_get: Optional[HttpGet] = None) -> List[Dict[str, Any]]:
    """Search Waikato study pages by paper code fragment."""
    query = _normalise_code(query)
    if not query:
        return []
    fetch = http_get or _default_get
    slug = _slugify(query)
    if not slug:
        return []
    try:
        raw = fetch(waikato_paper_url(slug, year))
    except Exception:
        return []
    parsed = parse_waikato_page(raw, source_url=waikato_paper_url(slug, year))
    if parsed["code"] and parsed["code"].startswith(query[:4]):
        return [parsed]
    if parsed["code"]:
        return [parsed]
    return []


def parse_waikato_page(raw: str, *, source_url: str = "") -> Dict[str, Any]:
    """Extract predescriptor metadata from a Waikato study paper page."""
    title = ""
    code = ""
    description = ""
    instances: List[Dict[str, Any]] = []

    for block in re.findall(r'<script[^>]+type="application/ld\+json"[^>]*>(.*?)</script>',
                            raw, re.I | re.S):
        try:
            data = json.loads(block.strip())
        except json.JSONDecodeError:
            continue
        if data.get("@type") == "Course":
            title = (data.get("name") or "").split(" - ", 1)[-1].strip()
            code = (data.get("courseCode") or "").strip()
            description = (data.get("description") or "").strip()
            for inst in data.get("hasCourseInstance") or []:
                if not isinstance(inst, dict):
                    continue
                name = (inst.get("name") or "").strip()
                instances.append({
                    "code": name,
                    "location": inst.get("location") or "",
                    "start_date": inst.get("startDate") or "",
                    "end_date": inst.get("endDate") or "",
                    "outline_url": outline_url(name) if name else "",
                })

    if not code:
        m = re.search(r"<h1[^>]*>\s*([A-Z]{2,10}\d{2,4})\s*</h1>", raw, re.I)
        if m:
            code = m.group(1).upper()

    # Teaching-period table fallback when JSON-LD is sparse.
    if not instances:
        for m in re.finditer(
            r'paper-page-table__item-title">([^<]+)</span>.*?'
            r'href="(https://paperoutlines[^"]+)"',
            raw, re.I | re.S,
        ):
            inst_code = html_lib.unescape(m.group(1)).strip()
            instances.append({
                "code": inst_code,
                "location": "",
                "start_date": "",
                "end_date": "",
                "outline_url": html_lib.unescape(m.group(2)),
            })

    return {
        "code": code,
        "title": title or code,
        "description": description,
        "source_url": source_url,
        "instances": instances,
        "outline_urls": [i["outline_url"] for i in instances if i.get("outline_url")],
    }


def _try_api_outline(paper_code: str, http_get: HttpGet) -> Optional[Dict[str, Any]]:
    """Best-effort fetch from the public paper-outlines Azure Functions API."""
    quoted = quote(paper_code, safe="")
    paths = [
        f"/api/GetPublishedOutline?paperCode={quoted}",
        f"/api/GetOutline?paperCode={quoted}",
        f"/api/PaperOutline/GetPublished?paperCode={quoted}",
        f"/api/PaperOutline/GetOutlineByPaperCode?paperCode={quoted}",
    ]
    for path in paths:
        try:
            raw = http_get(API_PREFIX + path)
            data = json.loads(raw)
            if isinstance(data, dict) and data:
                return data
        except Exception:
            continue
    return None


def parse_outline_html(raw: str, *, paper_code: str = "") -> Dict[str, Any]:
    """Parse a rendered paper-outline HTML page into structured fields."""
    text = html_lib.unescape(re.sub(r"<(script|style)[^>]*>.*?</\1>", "", raw, flags=re.I | re.S))
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.I)
    text = re.sub(r"</(p|div|tr|li|h\d)>", "\n", text, flags=re.I)
    plain = re.sub(r"<[^>]+>", " ", text)
    plain = re.sub(r"[ \t]+", " ", plain)
    plain = re.sub(r"\n\s*\n+", "\n", plain)

    code = paper_code or _outline_code_from_url(raw)
    title = ""
    m = re.search(r"(?:Paper code|Paper Code)\s*[:\-]?\s*([A-Z]{2,10}\d{3,4})", plain)
    if m and not code:
        code = m.group(1)
    m = re.search(r"(?:Paper title|Paper Title)\s*[:\-]?\s*(.+)", plain)
    if m:
        title = m.group(1).split("\n", 1)[0].strip()
    if not title:
        m = re.search(r"<title>([^<]+)</title>", raw, re.I)
        if m:
            title = re.sub(r"\s*[-|].*$", "", m.group(1)).strip()

    assessments = _parse_assessments(raw, plain)
    staff = _parse_staff(plain)
    outcomes = _parse_section_list(plain, "Learning outcomes", ("Assessment",))
    if not outcomes:
        outcomes = _parse_html_list(raw, "Learning outcomes")
    weekly = _parse_weekly_topics(raw, plain)
    key_dates = _parse_key_dates(plain)

    semester = ""
    sm = re.search(r"(\d{2}[ABX])\s*\(([A-Z]{3,4})\)", code)
    if sm:
        semester = sm.group(1)

    return {
        "paper_code": code,
        "title": title or code,
        "semester": semester,
        "campus": sm.group(2) if sm else "",
        "assessments": assessments,
        "staff": staff,
        "learning_outcomes": outcomes,
        "weekly_topics": weekly,
        "key_dates": key_dates,
        "source": "html",
        "fetched_at": now_iso(),
    }


def _parse_assessments(raw: str, plain: str) -> List[Dict[str, Any]]:
    items: List[Dict[str, Any]] = []
    # Table rows: name | weight | due | description
    for row in re.findall(r"<tr[^>]*>(.*?)</tr>", raw, re.I | re.S):
        if "assessment" in row.lower() and "weight" in row.lower() and "<th" in row.lower():
            continue
        cells = [re.sub(r"<[^>]+>", " ", c).strip()
                 for c in re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", row, re.I | re.S)]
        cells = [re.sub(r"\s+", " ", html_lib.unescape(c)).strip() for c in cells if c.strip()]
        if len(cells) < 2:
            continue
        name, weight, due, desc = cells[0], "", "", ""
        for c in cells[1:]:
            if re.search(r"\d+\s*%", c):
                weight = c
            elif re.search(r"\d{4}-\d{2}-\d{2}|\d{1,2}\s+\w+\s+\d{4}|week\s+\d+", c, re.I):
                due = c
            elif not desc:
                desc = c
        if name.lower() in ("assessment", "name", "item", "component"):
            continue
        w = _parse_weight(weight)
        if w is not None or due or any(k in name.lower() for k in
                                       ("assignment", "test", "exam", "quiz", "lab", "project")):
            items.append({"name": name, "weight": w, "due_date": due, "description": desc})

    if items:
        return items

    # Bullet list under an Assessment heading.
    section = _extract_section(plain, "Assessment", ("Learning outcomes", "Staff", "Timetable"))
    for line in section.splitlines():
        line = line.strip(" -•\t")
        if not line:
            continue
        wm = re.search(r"(\d+(?:\.\d+)?)\s*%", line)
        dm = re.search(r"(?:due|by)\s*[:\-]?\s*(.+)$", line, re.I)
        name = re.sub(r"\s*[\(\-].*$", "", line).strip()
        if len(name) < 3:
            continue
        items.append({
            "name": name,
            "weight": float(wm.group(1)) if wm else None,
            "due_date": dm.group(1).strip() if dm else "",
            "description": line,
        })
    return items


def _parse_weight(value: str) -> Optional[float]:
    m = re.search(r"(\d+(?:\.\d+)?)\s*%", value or "")
    return float(m.group(1)) if m else None


def _parse_staff(plain: str) -> List[Dict[str, str]]:
    section = _extract_section(plain, "Staff", ("Assessment", "Learning", "Timetable", "Key dates"))
    staff: List[Dict[str, str]] = []
    for line in section.splitlines():
        line = line.strip(" -•\t")
        if not line or len(line) < 4:
            continue
        email = ""
        em = re.search(r"[\w.+-]+@waikato\.ac\.nz", line, re.I)
        if em:
            email = em.group(0)
        role = "Coordinator" if "coordinator" in line.lower() else "Staff"
        name = re.sub(r"[\w.+-]+@waikato\.ac\.nz", "", line, flags=re.I).strip(" -:")
        if name:
            staff.append({"name": name, "role": role, "email": email})
    return staff


def _parse_section_list(plain: str, start: str, end_markers: tuple) -> List[str]:
    section = _extract_section(plain, start, end_markers)
    out: List[str] = []
    for line in section.splitlines():
        line = line.strip(" -•\t")
        if line and len(line) > 5:
            out.append(line)
    return out


def _parse_weekly_topics(raw: str, plain: str) -> List[Dict[str, Any]]:
    topics: List[Dict[str, Any]] = []
    for row in re.findall(r"<tr[^>]*>(.*?)</tr>", raw, re.I | re.S):
        cells = [re.sub(r"<[^>]+>", " ", c).strip()
                 for c in re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", row, re.I | re.S)]
        cells = [re.sub(r"\s+", " ", html_lib.unescape(c)).strip() for c in cells if c.strip()]
        if len(cells) >= 2 and re.match(r"week\s*\d+", cells[0], re.I):
            topics.append({"week": cells[0], "topic": cells[1]})
    if topics:
        return topics
    section = _extract_section(plain, "Timetable", ("Staff", "Assessment", "Key dates"))
    for line in section.splitlines():
        m = re.match(r"(week\s*\d+)\s*[:\-]?\s*(.+)", line.strip(), re.I)
        if m:
            topics.append({"week": m.group(1), "topic": m.group(2).strip()})
    return topics


def _parse_key_dates(plain: str) -> List[Dict[str, str]]:
    section = _extract_section(plain, "Key dates", ("Staff", "Assessment", "Timetable"))
    dates: List[Dict[str, str]] = []
    for line in section.splitlines():
        line = line.strip(" -•\t")
        if not line:
            continue
        dates.append({"label": line, "date": ""})
    return dates


def _parse_html_list(raw: str, heading: str) -> List[str]:
    m = re.search(
        rf"<h2[^>]*>\s*{re.escape(heading)}\s*</h2>\s*<ul>(.*?)</ul>",
        raw, re.I | re.S,
    )
    if not m:
        return []
    return [re.sub(r"<[^>]+>", "", li).strip()
            for li in re.findall(r"<li[^>]*>(.*?)</li>", m.group(1), re.I | re.S)
            if re.sub(r"<[^>]+>", "", li).strip()]


def _extract_section(plain: str, start: str, end_markers: tuple) -> str:
    pattern = re.compile(
        rf"{re.escape(start)}[^\n]*\n(.*?)(?:(?:{'|'.join(re.escape(e) for e in end_markers)})|\Z)",
        re.I | re.S,
    )
    m = pattern.search(plain)
    return m.group(1).strip() if m else ""


def _merge_api_payload(api: Dict[str, Any], html_parsed: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(html_parsed)
    out["api"] = api
    # Common API field names
    for key, target in (
        ("assessmentItems", "assessments"),
        ("assessments", "assessments"),
        ("learningOutcomes", "learning_outcomes"),
        ("staffContacts", "staff"),
        ("weeklyTopics", "weekly_topics"),
        ("paperTitle", "title"),
        ("paperCode", "paper_code"),
    ):
        val = api.get(key)
        if val and not out.get(target):
            out[target] = val
    return out


def fetch_outline(paper_code: str, *, http_get: Optional[HttpGet] = None,
                  html: str = "") -> Dict[str, Any]:
    """Fetch and parse a paper outline by code, URL, or supplied HTML."""
    fetch = http_get or _default_get
    code = _outline_code_from_url(paper_code) or _normalise_code(paper_code)
    if not code:
        raise PaperOutlineError("paper code or outline URL is required")

    parsed: Dict[str, Any] = {}
    if html:
        parsed = parse_outline_html(html, paper_code=code)
    else:
        api_data = _try_api_outline(code, fetch)
        outline_html = ""
        try:
            outline_html = fetch(outline_url(code))
        except Exception:
            outline_html = ""
        if outline_html and "blazor-error-ui" not in outline_html[:500].lower():
            parsed = parse_outline_html(outline_html, paper_code=code)
        elif api_data:
            parsed = {
                "paper_code": code,
                "title": api_data.get("paperTitle") or code,
                "semester": "",
                "campus": "",
                "assessments": [],
                "staff": [],
                "learning_outcomes": [],
                "weekly_topics": [],
                "key_dates": [],
                "source": "api",
                "fetched_at": now_iso(),
            }
            parsed = _merge_api_payload(api_data, parsed)
        else:
            # Fall back to Waikato predescriptor page for metadata + outline links.
            base_code = code.split("-")[0]
            waikato = parse_waikato_page(fetch(waikato_paper_url(base_code)), source_url="")
            parsed = {
                "paper_code": code,
                "title": waikato.get("title") or code,
                "semester": "",
                "campus": "",
                "description": waikato.get("description", ""),
                "assessments": [],
                "staff": [],
                "learning_outcomes": [],
                "weekly_topics": [],
                "key_dates": [],
                "instances": waikato.get("instances", []),
                "outline_urls": waikato.get("outline_urls", []),
                "source": "waikato_page",
                "fetched_at": now_iso(),
                "note": "Full outline HTML was not available; import HTML or try again later.",
            }
        if api_data:
            parsed = _merge_api_payload(api_data, parsed)

    parsed["paper_code"] = parsed.get("paper_code") or code
    parsed["outline_url"] = outline_url(code)
    return parsed
